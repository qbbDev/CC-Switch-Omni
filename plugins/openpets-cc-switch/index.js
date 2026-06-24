let bubbleHandle = null;
let lastTokens = 0;
let lastCost = 0.0;
let restoreTimeout = null;
let pollTimeout = null;
let isFirstRun = true;
let syncAppKey = "cc_switch_sync_default";
let vpsUrl = "http://127.0.0.1:25722";
let tokenRange = "today";
let costThreshold = 0.1;
let tokenThreshold = 5000;
let pollIntervalMs = 15000;
let unsubscribeConfig = null;
let currentBubbleText = "";
let chatTimeout = null;
let isShowingAlert = false;

// Communication utilities have been migrated to synchronous direct VPS requests

function wrapText(text, maxLineLen = 11) {
    if (!text) return "";
    const lines = text.split('\n');
    const result = [];
    
    for (let line of lines) {
        let current = "";
        let count = 0;
        
        for (let i = 0; i < line.length; i++) {
            const char = line[i];
            current += char;
            
            // CJK characters count as 1, English/numbers/spaces count as 0.5
            const isFullWidth = char.charCodeAt(0) > 255;
            count += isFullWidth ? 1 : 0.5;
            
            if (count >= maxLineLen) {
                result.push(current);
                current = "";
                count = 0;
            }
        }
        if (current) {
            result.push(current);
        }
    }
    return result.join('\n');
}

async function handleChat(ctx, prompt, checkUsage) {
    const requestId = Math.random().toString(36).substr(2, 7);
    
    if (chatTimeout) clearTimeout(chatTimeout);
    if (restoreTimeout) {
        clearTimeout(restoreTimeout);
        restoreTimeout = null;
    }
    
    // Dismiss any existing bubble to avoid ghost handles
    if (bubbleHandle) {
        try {
            await bubbleHandle.dismiss();
        } catch (e) {}
        bubbleHandle = null;
        currentBubbleText = "";
    }
    
    isShowingAlert = true; // Lock bubble updates
    
    try {
        if (ctx.pet && typeof ctx.pet.react === 'function') {
            await ctx.pet.react("thinking");
        }
    } catch (e) {
        ctx.log.error("Failed to set thinking reaction", e);
    }
    
    // Helper to update bubble
    const updateBubbleText = async (text) => {
        if (text === currentBubbleText && bubbleHandle) return;
        if (!bubbleHandle) {
            try {
                bubbleHandle = await ctx.ui.bubble({ text: text, pin: true, tone: "info" });
                currentBubbleText = text;
                bubbleHandle.onDismiss((reason) => {
                    bubbleHandle = null;
                    currentBubbleText = "";
                });
            } catch (err) {
                try {
                    bubbleHandle = await ctx.ui.bubble({ text: text, sticky: true, tone: "info" });
                    currentBubbleText = text;
                    bubbleHandle.onDismiss((reason) => {
                        bubbleHandle = null;
                        currentBubbleText = "";
                    });
                } catch (fallbackErr) {}
            }
        } else {
            try {
                await bubbleHandle.update({ text: text });
                currentBubbleText = text;
            } catch (err) {
                bubbleHandle = null;
                currentBubbleText = "";
                await updateBubbleText(text);
            }
        }
    };

    await updateBubbleText("让我想想... ⚡");
    
    try {
        const chatUrl = `${vpsUrl.replace(/\/$/, '')}/api/chat`;
        ctx.log.info(`Sending chat request to VPS: ${chatUrl}`);
        const res = await ctx.net.fetch(chatUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                appKey: syncAppKey,
                prompt: prompt
            })
        });
        
        if (!res.ok) {
            throw new Error(`Server returned status ${res.status}`);
        }
        
        let data;
        if (res.json && typeof res.json === 'object') {
            data = res.json;
        } else {
            data = JSON.parse(res.text);
        }
        const reply = data.response || "我不晓得说什么呢~";
        ctx.log.info(`Received chat reply from VPS: ${reply}`);
        
        try {
            if (ctx.pet && typeof ctx.pet.react === 'function') {
                await ctx.pet.react("success");
            }
        } catch (e) {}
        
        await updateBubbleText(wrapText(reply));
        
        restoreTimeout = setTimeout(async () => {
            restoreTimeout = null;
            isShowingAlert = false; // Unlock
            await checkUsage();
        }, 10000);
        
    } catch (err) {
        ctx.log.error("Failed to send chat request to VPS", err);
        await updateBubbleText("连接中转服务失败了... 😭");
        try {
            if (ctx.pet && typeof ctx.pet.react === 'function') {
                await ctx.pet.react("error");
            }
        } catch (e) {}
        
        restoreTimeout = setTimeout(async () => {
            restoreTimeout = null;
            isShowingAlert = false; // Unlock
            await checkUsage();
        }, 5000);
    }
}

async function handleUsageAlert(ctx, deltaTokens, deltaCost, checkUsage) {
    const requestId = Math.random().toString(36).substr(2, 7);
    
    if (chatTimeout) clearTimeout(chatTimeout);
    if (restoreTimeout) {
        clearTimeout(restoreTimeout);
        restoreTimeout = null;
    }
    
    // Dismiss any existing bubble to avoid ghost handles
    if (bubbleHandle) {
        try {
            await bubbleHandle.dismiss();
        } catch (e) {}
        bubbleHandle = null;
        currentBubbleText = "";
    }
    
    isShowingAlert = true; // Lock bubble updates
    
    let reaction = "success";
    if (deltaCost >= costThreshold) {
        reaction = "error";
    } else if (deltaTokens >= tokenThreshold) {
        reaction = "thinking";
    }
    
    try {
        if (ctx.pet && typeof ctx.pet.react === 'function') {
            await ctx.pet.react(reaction);
        }
    } catch (e) {
        ctx.log.error("Failed to set reaction", e);
    }
    
    const updateBubbleText = async (text) => {
        if (text === currentBubbleText && bubbleHandle) return;
        if (!bubbleHandle) {
            try {
                bubbleHandle = await ctx.ui.bubble({ text: text, pin: true, tone: "info" });
                currentBubbleText = text;
                bubbleHandle.onDismiss((reason) => {
                    bubbleHandle = null;
                    currentBubbleText = "";
                });
            } catch (err) {
                try {
                    bubbleHandle = await ctx.ui.bubble({ text: text, sticky: true, tone: "info" });
                    currentBubbleText = text;
                    bubbleHandle.onDismiss((reason) => {
                        bubbleHandle = null;
                        currentBubbleText = "";
                    });
                } catch (fallbackErr) {}
            }
        } else {
            try {
                await bubbleHandle.update({ text: text });
                currentBubbleText = text;
            } catch (err) {
                bubbleHandle = null;
                currentBubbleText = "";
                await updateBubbleText(text);
            }
        }
    };

    await updateBubbleText(wrapText(`刚才那发消耗了 ${deltaTokens.toLocaleString()} 点... ⚡`));
    
    try {
        const chatUrl = `${vpsUrl.replace(/\/$/, '')}/api/chat`;
        ctx.log.info(`Sending usage alert request to VPS: ${chatUrl}`);
        const res = await ctx.net.fetch(chatUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                appKey: syncAppKey,
                prompt: "[USAGE_ALERT]",
                deltaTokens: deltaTokens,
                deltaCost: deltaCost
            })
        });
        
        if (!res.ok) {
            throw new Error(`Server returned status ${res.status}`);
        }
        
        let data;
        if (res.json && typeof res.json === 'object') {
            data = res.json;
        } else {
            data = JSON.parse(res.text);
        }
        const reply = data.response;
        ctx.log.info(`Received usage alert reply from VPS: ${reply}`);
        if (reply) {
            await updateBubbleText(wrapText(reply));
            
            restoreTimeout = setTimeout(async () => {
                restoreTimeout = null;
                isShowingAlert = false; // Unlock
                await checkUsage();
            }, 7000);
        } else {
            isShowingAlert = false; // Unlock
            await checkUsage();
        }
    } catch (err) {
        ctx.log.error("Failed to send chat request to VPS", err);
        isShowingAlert = false; // Unlock
        await checkUsage();
    }
}

function getProgressBar(percent) {
    const totalSteps = 10;
    const filledSteps = Math.min(totalSteps, Math.max(0, Math.round(percent / 10)));
    const emptySteps = totalSteps - filledSteps;
    return "█".repeat(filledSteps) + "░".repeat(emptySteps);
}

function getStatsText(tokens, cost, hitRate) {
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
    
    const hitBar = getProgressBar(hitRate);
    
    return `${rangeLabel}: ${tokensFormatted} 点\n` +
           `花费: $${cost.toFixed(4)}\n` +
           `缓存命中: ${hitBar} ${hitRate.toFixed(1)}%`;
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
            const newVpsUrl = config.vpsUrl || "http://127.0.0.1:25722";
            
            // If syncAppKey, tokenRange or vpsUrl changed, reset the baseline on next poll
            if (newSyncAppKey !== syncAppKey || newTokenRange !== tokenRange || newVpsUrl !== vpsUrl) {
                isFirstRun = true;
            }
            
            syncAppKey = newSyncAppKey;
            tokenRange = newTokenRange;
            vpsUrl = newVpsUrl;
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
                    bubbleHandle.onDismiss((reason) => {
                        ctx.log.info("CC Switch stats bubble dismissed, clearing handle. Reason:", reason);
                        bubbleHandle = null;
                        currentBubbleText = "";
                    });
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
                        bubbleHandle.onDismiss((reason) => {
                            ctx.log.info("CC Switch fallback bubble dismissed, clearing handle. Reason:", reason);
                            bubbleHandle = null;
                            currentBubbleText = "";
                        });
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
            if (isShowingAlert) {
                ctx.log.info("Alert or chat is active, skipping stats update poll.");
                return;
            }
            const url = `${vpsUrl.replace(/\/$/, '')}/api/usage/get?appKey=${syncAppKey}`;
            try {
                ctx.log.info(`Checking usage stats from VPS: ${url}`);
                const response = await ctx.net.fetch(url);
                if (!response.ok) {
                    ctx.log.warn("VPS usage GET returned non-OK status:", response.status);
                    return;
                }
                
                let stats;
                if (response.json && typeof response.json === 'object') {
                    stats = response.json;
                } else {
                    stats = JSON.parse(response.text);
                }
                ctx.log.info("Parsed VPS usage stats:", stats);
                if (!stats || stats.tokens === undefined) return;
                
                const { range, tokens, cost, hitRate } = stats;
                
                // Skip if range mismatch (waiting for local uploader to sync)
                if (range !== tokenRange) {
                    ctx.log.info(`VPS range '${range}' does not match configured '${tokenRange}' yet, waiting for uploader sync...`);
                    return;
                }
                
                if (isFirstRun) {
                    lastTokens = tokens;
                    lastCost = cost;
                    isFirstRun = false;
                    await updateBubble(getStatsText(tokens, cost, hitRate));
                    return;
                }
                
                if (tokens > lastTokens) {
                    const deltaTokens = tokens - lastTokens;
                    const deltaCost = cost - lastCost;
                    
                    lastTokens = tokens;
                    lastCost = cost;
                    
                    await handleUsageAlert(ctx, deltaTokens, deltaCost, checkUsage);
                } else {
                    // Update stats if they dropped (midnight reset) or if no warning is active
                    if (tokens < lastTokens) {
                        lastTokens = tokens;
                        lastCost = cost;
                    }
                    if (!restoreTimeout) {
                        await updateBubble(getStatsText(tokens, cost, hitRate));
                    }
                }
            } catch (err) {
                ctx.log.error("Failed to check usage stats from VPS", err);
            }
        };
        
        const poll = async () => {
            await checkUsage();
            pollTimeout = setTimeout(poll, pollIntervalMs);
        };
        
        // Start polling loop
        poll();

        // Register custom right-click commands
        if (ctx.commands && typeof ctx.commands.register === 'function') {
            try {
                // Command 1: chat with pet
                await ctx.commands.register({
                    id: "chat-with-pet",
                    title: "和 CC 助手聊聊天 💬",
                    form: {
                        fields: [
                            {
                                id: "message",
                                type: "textarea",
                                label: "你想对我说什么？",
                                required: true,
                                maxLength: 200
                            }
                        ],
                        submitLabel: "发送"
                    }
                }, async (values) => {
                    if (values && values.message) {
                        await handleChat(ctx, values.message, checkUsage);
                    }
                });

                // Command 2: simulate usage alert (test button)
                await ctx.commands.register({
                    id: "simulate-usage-alert",
                    title: "模拟用量吐槽测试 ⚡",
                    description: "随机模拟一次大模型调用，触发 AI 动态吐槽展示"
                }, async () => {
                    const simulatedTokens = Math.floor(Math.random() * 6000) + 1000;
                    const simulatedCost = parseFloat((Math.random() * 0.12 + 0.01).toFixed(4));
                    await handleUsageAlert(ctx, simulatedTokens, simulatedCost, checkUsage);
                });

                ctx.log.info("Commands registered.");
            } catch (cmdErr) {
                ctx.log.error("Failed to register commands:", cmdErr);
            }
        }
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
        if (chatTimeout) {
            clearTimeout(chatTimeout);
            chatTimeout = null;
        }
        if (ctx.commands && typeof ctx.commands.unregister === 'function') {
            try {
                await ctx.commands.unregister("chat-with-pet");
                await ctx.commands.unregister("simulate-usage-alert");
                ctx.log.info("Commands unregistered.");
            } catch (cmdErr) {
                ctx.log.error("Failed to unregister commands:", cmdErr);
            }
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
