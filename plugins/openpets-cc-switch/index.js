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
let chatTimeout = null;

function toBase64URL(str) {
    const encoder = new TextEncoder();
    const bytes = encoder.encode(str);
    let binString = "";
    for (let i = 0; i < bytes.length; i++) {
        binString += String.fromCharCode(bytes[i]);
    }
    const b64 = btoa(binString);
    return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function fromBase64URL(b64url) {
    if (!b64url) return '';
    try {
        let b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
        while (b64.length % 4) {
            b64 += '=';
        }
        const binString = atob(b64);
        const bytes = new Uint8Array(binString.length);
        for (let i = 0; i < binString.length; i++) {
            bytes[i] = binString.charCodeAt(i);
        }
        const decoder = new TextDecoder();
        return decoder.decode(bytes);
    } catch (e) {
        return b64url;
    }
}

function makeSafeRequestPayload(id, prompt) {
    let text = prompt;
    let payload = `${id}|${text}`;
    let b64 = toBase64URL(payload);
    while (b64.length > 200 && text.length > 0) {
        text = text.substring(0, text.length - 1);
        payload = `${id}|${text}`;
        b64 = toBase64URL(payload);
    }
    return b64;
}

function parseResponsePayload(cleanVal, expectedId) {
    if (!cleanVal) return null;
    const decodedVal = fromBase64URL(cleanVal);
    if (!decodedVal) return null;
    
    // 1. Try pipe-delimited format first (e.g. "requestId|reply")
    if (decodedVal.includes('|')) {
        const idx = decodedVal.indexOf('|');
        const resId = decodedVal.substring(0, idx);
        if (resId.length <= 10) {  // simple validation for generated id length
            const reply = decodedVal.substring(idx + 1);
            if (resId === expectedId) {
                return reply;
            }
        }
    }
    
    // 2. Try JSON fallback
    try {
        const resData = JSON.parse(decodedVal);
        if (resData && resData.id === expectedId) {
            return resData.response;
        }
    } catch (e) {}
    
    return null;
}

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
    
    // Write request to KV using a compact, safe Base64URL payload
    const safeReq = makeSafeRequestPayload(requestId, prompt);
    const writeUrl = `https://keyvalue.immanuel.co/api/KeyVal/UpdateValue/${syncAppKey}/chat_request/${safeReq}`;
    
    try {
        const writeRes = await ctx.net.fetch(writeUrl, { method: "POST" });
        if (!writeRes.ok) {
            throw new Error(`Failed to write request: ${writeRes.status}`);
        }
    } catch (err) {
        ctx.log.error("Failed to send chat request to KV", err);
        await updateBubbleText("连接桥接服务失败了... 😭");
        try {
            if (ctx.pet && typeof ctx.pet.react === 'function') {
                await ctx.pet.react("error");
            }
        } catch (e) {}
        return;
    }
    
    // Start polling response
    let pollCount = 0;
    const maxPolls = 60; // 60 seconds timeout
    
    const pollResponse = async () => {
        if (pollCount >= maxPolls) {
            await updateBubbleText("思考超时了，AI 脑子冻结了 ❄️");
            try {
                if (ctx.pet && typeof ctx.pet.react === 'function') {
                    await ctx.pet.react("idle");
                }
            } catch (e) {}
            
            restoreTimeout = setTimeout(async () => {
                restoreTimeout = null;
                await checkUsage();
            }, 7000);
            return;
        }
        
        pollCount++;
        const readUrl = `https://keyvalue.immanuel.co/api/KeyVal/GetValue/${syncAppKey}/chat_response`;
        
        try {
            const response = await ctx.net.fetch(readUrl);
            if (response.ok) {
                const cleanVal = parseKVValue(response.text);
                if (cleanVal) {
                    const reply = parseResponsePayload(cleanVal, requestId);
                    if (reply !== null) {
                        const finalReply = reply || "我不晓得说什么呢~";
                        try {
                            if (ctx.pet && typeof ctx.pet.react === 'function') {
                                    await ctx.pet.react("success");
                            }
                        } catch (e) {}
                        
                        await updateBubbleText(wrapText(finalReply));
                        
                        restoreTimeout = setTimeout(async () => {
                            restoreTimeout = null;
                            await checkUsage();
                        }, 10000);
                        return;
                    }
                }
            }
        } catch (pollErr) {
            ctx.log.error("Error polling chat response:", pollErr);
        }
        
        chatTimeout = setTimeout(pollResponse, 500);
    };
    
    pollResponse();
}

async function handleUsageAlert(ctx, deltaTokens, deltaCost, checkUsage) {
    const requestId = Math.random().toString(36).substr(2, 7);
    
    if (chatTimeout) clearTimeout(chatTimeout);
    if (restoreTimeout) {
        clearTimeout(restoreTimeout);
        restoreTimeout = null;
    }
    
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
    
    // Write request to KV using a compact, safe Base64URL payload
    const safeReq = makeSafeRequestPayload(requestId, `[USAGE_ALERT] delta_tokens: ${deltaTokens}, delta_cost: ${deltaCost}`);
    const writeUrl = `https://keyvalue.immanuel.co/api/KeyVal/UpdateValue/${syncAppKey}/chat_request/${safeReq}`;
    
    try {
        const writeRes = await ctx.net.fetch(writeUrl, { method: "POST" });
        if (!writeRes.ok) {
            throw new Error(`Failed to write request: ${writeRes.status}`);
        }
    } catch (err) {
        ctx.log.error("Failed to send chat request to KV", err);
        await checkUsage();
        return;
    }
    
    let pollCount = 0;
    const maxPolls = 45;
    
    const pollResponse = async () => {
        if (pollCount >= maxPolls) {
            await checkUsage();
            return;
        }
        
        pollCount++;
        const readUrl = `https://keyvalue.immanuel.co/api/KeyVal/GetValue/${syncAppKey}/chat_response`;
        
        try {
            const response = await ctx.net.fetch(readUrl);
            if (response.ok) {
                const cleanVal = parseKVValue(response.text);
                if (cleanVal) {
                    const reply = parseResponsePayload(cleanVal, requestId);
                    if (reply) {
                        await updateBubbleText(wrapText(reply));
                        
                        restoreTimeout = setTimeout(async () => {
                            restoreTimeout = null;
                            await checkUsage();
                        }, 7000);
                        return;
                    }
                }
            }
        } catch (pollErr) {
            ctx.log.error("Error polling chat response:", pollErr);
        }
        
        chatTimeout = setTimeout(pollResponse, 500);
    };
    
    pollResponse();
}

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
        // Fallback for old 6-part format
        const range = parts[0];
        const tokens = parseInt(parts[1], 10);
        const cost = parseFloat(parts[2].replace('-', '.'));
        const hitRate = parseFloat(parts[3].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost) && !isNaN(hitRate)) {
            return { range, tokens, cost, hitRate };
        }
    } else if (parts.length === 4) {
        const range = parts[0];
        const tokens = parseInt(parts[1], 10);
        const cost = parseFloat(parts[2].replace('-', '.'));
        const hitRate = parseFloat(parts[3].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost) && !isNaN(hitRate)) {
            return { range, tokens, cost, hitRate };
        }
    } else if (parts.length === 3) {
        const range = parts[0];
        const tokens = parseInt(parts[1], 10);
        const cost = parseFloat(parts[2].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost)) {
            return { range, tokens, cost, hitRate: 0.0 };
        }
    } else if (parts.length === 2) {
        // Backward compatibility for old 2-part format
        const tokens = parseInt(parts[0], 10);
        const cost = parseFloat(parts[1].replace('-', '.'));
        if (!isNaN(tokens) && !isNaN(cost)) {
            return { range: "today", tokens, cost, hitRate: 0.0 };
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
                
                const { range, tokens, cost, hitRate } = parsed;
                
                // Skip if range mismatch (waiting for python agent to update range value)
                if (range !== tokenRange) {
                    ctx.log.info(`KV range '${range}' does not match configured '${tokenRange}' yet, waiting for agent sync...`);
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
                    
                    await handleUsageAlert(ctx, deltaTokens, deltaCost, checkUsage);
                    
                    lastTokens = tokens;
                    lastCost = cost;
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
                ctx.log.error("Error in checkUsage poll execution:", err);
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
