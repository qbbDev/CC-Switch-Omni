let bubbleHandle = null;
let lastTokens = 0;
let lastCost = 0.0;
let restoreTimeout = null;
let pollTimeout = null;
let isFirstRun = true;
let syncAppKey = "cc_switch_sync_default";
let tokenRange = "today";
let costThreshold = 0.1;
let tokenThreshold = 5000;
let pollIntervalMs = 15000;
let unsubscribeConfig = null;
let currentBubbleText = "";

function parseKVValue(raw) {
    if (!raw) return "";
    let clean = raw.trim();
    if (clean.startsWith('"') && clean.endsWith('"')) {
        try {
            clean = JSON.parse(clean);
        } catch (e) {
            clean = clean.slice(1, -1);
        }
    }
    return clean;
}

function parseKVString(val) {
    if (!val) return null;
    const parts = val.split('_');
    if (parts.length === 6) {
        const range = parts[0];
        const tokens = parseInt(parts[1], 10);
        const cost = parseFloat(parts[2].replace('-', '.'));
        const hitRate = parseFloat(parts[3].replace('-', '.'));
        const remTokensPct = parseFloat(parts[4].replace('-', '.'));
        const remCostPct = parseFloat(parts[5].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost) && !isNaN(hitRate) && !isNaN(remTokensPct) && !isNaN(remCostPct)) {
            return { range, tokens, cost, hitRate, remTokensPct, remCostPct };
        }
    } else if (parts.length === 3) {
        const range = parts[0];
        const tokens = parseInt(parts[1], 10);
        const cost = parseFloat(parts[2].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost)) {
            return { range, tokens, cost, hitRate: 0.0, remTokensPct: 100.0, remCostPct: 100.0 };
        }
    } else if (parts.length === 2) {
        // Backward compatibility for old 2-part format
        const tokens = parseInt(parts[0], 10);
        const cost = parseFloat(parts[1].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost)) {
            return { range: "today", tokens, cost, hitRate: 0.0, remTokensPct: 100.0, remCostPct: 100.0 };
        }
    }
    return null;
}

function getProgressBar(percent) {
    const totalSteps = 10;
    const filledSteps = Math.min(totalSteps, Math.max(0, Math.round(percent / 10)));
    const emptySteps = totalSteps - filledSteps;
    return "█".repeat(filledSteps) + "░".repeat(emptySteps);
}

function getStatsText(tokens, cost, hitRate, remTokensPct, remCostPct) {
    const labelMap = {
        "today": "今日已使用",
        "1d": "24h已使用",
        "7d": "7天已使用",
        "14d": "14天已使用",
        "30d": "30天已使用"
    };
    const rangeLabel = labelMap[tokenRange] || "今日已使用";
    const tokensFormatted = tokens.toLocaleString('en-US');
    
    // Default values if undefined
    hitRate = hitRate || 0.0;
    remCostPct = remCostPct !== undefined ? remCostPct : 100.0;
    
    const hitBar = getProgressBar(hitRate);
    const costBar = getProgressBar(remCostPct);
    
    return `${rangeLabel}: ${tokensFormatted} 点\n` +
           `花费: $${cost.toFixed(4)}\n` +
           `缓存命中: ${hitBar} ${hitRate.toFixed(1)}%\n` +
           `额度剩余: ${costBar} ${remCostPct.toFixed(1)}%`;
}

const pluginDefinition = {
    async start(ctx) {
        ctx.log.info("CC Switch Companion plugin started.");
        
        // Reset state
        bubbleHandle = null;
        lastTokens = 0;
        lastCost = 0.0;
        restoreTimeout = null;
        pollTimeout = null;
        isFirstRun = true;
        currentBubbleText = "";
        
        // Fetch config & subscribe to changes
        const setupConfig = (config) => {
            config = config || {};
            const newSyncAppKey = config.syncAppKey || "cc_switch_sync_default";
            const newTokenRange = config.tokenRange || "today";
            
            // If syncAppKey or tokenRange changed, reset the baseline on next poll
            if (newSyncAppKey !== syncAppKey || newTokenRange !== tokenRange) {
                isFirstRun = true;
            }
            
            syncAppKey = newSyncAppKey;
            tokenRange = newTokenRange;
            costThreshold = config.costThreshold !== undefined ? Number(config.costThreshold) : 0.1;
            tokenThreshold = config.tokenThreshold !== undefined ? Number(config.tokenThreshold) : 5000;
            
            const intervalMap = {
                "10s": 10000,
                "15s": 15000,
                "30s": 30000,
                "60s": 60000
            };
            pollIntervalMs = intervalMap[config.pollInterval] || 15000;
        };
        
        const initialConfig = await ctx.config.get() || {};
        setupConfig(initialConfig);
        
        if (ctx.config.onChange) {
            unsubscribeConfig = ctx.config.onChange((newConfig) => {
                setupConfig(newConfig);
                ctx.log.info("CC Switch config updated in real-time:", newConfig);
            });
        }
        
        // Helper to update bubble text robustly
        const updateBubble = async (text) => {
            // Avoid redundant renders if the text content hasn't changed
            if (text === currentBubbleText && bubbleHandle) {
                return;
            }
            
            if (!bubbleHandle) {
                try {
                    // Try to use pinned bubble (requires pet:pin)
                    bubbleHandle = await ctx.ui.bubble({
                        text: text,
                        pin: true,
                        tone: "info"
                    });
                    currentBubbleText = text;
                } catch (err) {
                    ctx.log.error("Failed to create pinned bubble, trying sticky fallback:", err);
                    try {
                        // Fallback to standard sticky bubble
                        bubbleHandle = await ctx.ui.bubble({
                            text: text,
                            sticky: true,
                            tone: "info"
                        });
                        currentBubbleText = text;
                    } catch (fallbackErr) {
                        ctx.log.error("Failed to create fallback sticky bubble:", fallbackErr);
                    }
                }
            } else {
                try {
                    await bubbleHandle.update({ text: text });
                    currentBubbleText = text;
                } catch (err) {
                    ctx.log.error("Failed to update bubble handle in-place, recreating:", err);
                    bubbleHandle = null;
                    currentBubbleText = "";
                    await updateBubble(text);
                }
            }
        };
        
        // Polling logic
        const checkUsage = async () => {
            const url = `https://keyvalue.immanuel.co/api/KeyVal/GetValue/${syncAppKey}/usage`;
            try {
                const response = await ctx.net.fetch(url);
                if (!response.ok) {
                    ctx.log.warn("KV store GET returned non-OK status:", response.status);
                    return;
                }
                
                const cleanVal = parseKVValue(response.text);
                const parsed = parseKVString(cleanVal);
                if (!parsed) return;
                
                const { range, tokens, cost, hitRate, remTokensPct, remCostPct } = parsed;
                
                // Skip if range mismatch (waiting for python agent to update range value)
                if (range !== tokenRange) {
                    ctx.log.info(`KV range '${range}' does not match configured '${tokenRange}' yet, waiting for agent sync...`);
                    return;
                }
                
                if (isFirstRun) {
                    lastTokens = tokens;
                    lastCost = cost;
                    isFirstRun = false;
                    await updateBubble(getStatsText(tokens, cost, hitRate, remTokensPct, remCostPct));
                    return;
                }
                
                if (tokens > lastTokens) {
                    const deltaTokens = tokens - lastTokens;
                    const deltaCost = cost - lastCost;
                    
                    let message = "";
                    let reaction = "success";
                    
                    const deltaTokensFormatted = deltaTokens.toLocaleString('en-US');
                    if (deltaCost >= costThreshold) {
                        reaction = "error";
                        message = `刚才这发大模型调用\n消耗了 ${deltaTokensFormatted} 点，\n吃掉了我 $${deltaCost.toFixed(4)} 的饭钱！😭`;
                    } else if (deltaTokens >= tokenThreshold) {
                        reaction = "thinking";
                        message = `哇，你这一下灌了\n${deltaTokensFormatted} 点，\n脑壳要算烧了！⚡`;
                    } else {
                        const replies = [
                            `消耗了 ${deltaTokensFormatted} 点，\n老铁继续努力！`,
                            `叮咚！用量增加 ${deltaTokensFormatted} 点，\n搬砖愉快！`,
                            `代码执行完毕，\n消耗了 ${deltaTokensFormatted} 点。`
                        ];
                        reaction = "success";
                        message = replies[Math.floor(Math.random() * replies.length)];
                    }
                    
                    // Trigger pet reaction
                    if (ctx.pet && typeof ctx.pet.react === 'function') {
                        try {
                            await ctx.pet.react(reaction);
                        } catch (reactErr) {
                            ctx.log.error("Failed to react:", reactErr);
                        }
                    }
                    
                    // Update the persistent bubble to show the warning message
                    await updateBubble(message);
                    
                    // Revert back to total stats after 7 seconds
                    if (restoreTimeout) clearTimeout(restoreTimeout);
                    restoreTimeout = setTimeout(async () => {
                        restoreTimeout = null;
                        await updateBubble(getStatsText(tokens, cost, hitRate, remTokensPct, remCostPct));
                    }, 7000);
                    
                    lastTokens = tokens;
                    lastCost = cost;
                } else {
                    // Update stats if they dropped (midnight reset) or if no warning is active
                    if (tokens < lastTokens) {
                        lastTokens = tokens;
                        lastCost = cost;
                    }
                    if (!restoreTimeout) {
                        await updateBubble(getStatsText(tokens, cost, hitRate, remTokensPct, remCostPct));
                    }
                }
            } catch (err) {
                ctx.log.error("Error in checkUsage poll execution:", err);
            }
        };
        
        const poll = async () => {
            await checkUsage();
            pollTimeout = setTimeout(poll, pollIntervalMs);
        };
        
        // Start polling loop
        poll();
    },

    async stop(ctx) {
        ctx.log.info("CC Switch Companion plugin stopped.");
        
        if (unsubscribeConfig) {
            unsubscribeConfig();
            unsubscribeConfig = null;
        }
        if (pollTimeout) {
            clearTimeout(pollTimeout);
            pollTimeout = null;
        }
        if (restoreTimeout) {
            clearTimeout(restoreTimeout);
            restoreTimeout = null;
        }
        if (bubbleHandle) {
            try {
                await bubbleHandle.dismiss();
            } catch (err) {
                ctx.log.error("Failed to dismiss bubble during stop:", err);
            }
            bubbleHandle = null;
        }
        currentBubbleText = "";
    }
};

// Export register function for ES module loading (official SDK v3 entry point style)
export function register(OpenPetsPlugin) {
    if (OpenPetsPlugin && typeof OpenPetsPlugin.register === 'function') {
        OpenPetsPlugin.register(pluginDefinition);
    }
}

// Fallback for direct script tag loading where OpenPetsPlugin global is present
if (typeof OpenPetsPlugin !== 'undefined' && typeof OpenPetsPlugin.register === 'function') {
    OpenPetsPlugin.register(pluginDefinition);
}
