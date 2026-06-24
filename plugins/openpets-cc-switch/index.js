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
    if (parts.length === 2) {
        const tokens = parseInt(parts[0], 10);
        const cost = parseFloat(parts[1].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost)) {
            return { tokens, cost };
        }
    }
    return null;
}

function getStatsText(tokens, cost) {
    const labelMap = {
        "today": "今日已使用",
        "1d": "24h已使用",
        "7d": "7天已使用",
        "14d": "14天已使用",
        "30d": "30天已使用"
    };
    const rangeLabel = labelMap[tokenRange] || "今日已使用";
    return `${rangeLabel}: ${tokens} 点 ($${cost.toFixed(4)})`;
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
                
                const { tokens, cost } = parsed;
                
                if (isFirstRun) {
                    lastTokens = tokens;
                    lastCost = cost;
                    isFirstRun = false;
                    await updateBubble(getStatsText(tokens, cost));
                    return;
                }
                
                if (tokens > lastTokens) {
                    const deltaTokens = tokens - lastTokens;
                    const deltaCost = cost - lastCost;
                    
                    let message = "";
                    let reaction = "success";
                    
                    if (deltaCost >= costThreshold) {
                        reaction = "error";
                        message = `刚才这发大模型调用消耗了 ${deltaTokens} 点，吃掉了我 $${deltaCost.toFixed(4)} 的饭钱！😭`;
                    } else if (deltaTokens >= tokenThreshold) {
                        reaction = "thinking";
                        message = `哇，你这一下灌了 ${deltaTokens} 点，脑壳要算烧了！⚡`;
                    } else {
                        const replies = [
                            `消耗了 ${deltaTokens} 点，老铁继续努力！`,
                            `叮咚！用量增加 ${deltaTokens} 点，搬砖愉快！`,
                            `代码执行完毕，消耗了 ${deltaTokens} 点。`
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
                        await updateBubble(getStatsText(tokens, cost));
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
                        await updateBubble(getStatsText(tokens, cost));
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
