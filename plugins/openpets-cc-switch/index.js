let lastTotalTokens = 0;
let lastTotalCost = 0;

// Defensive helper to trigger animations
async function petReact(ctx, reaction) {
    if (!ctx.pet) return;
    try {
        if (typeof ctx.pet.react === 'function') {
            await ctx.pet.react(reaction);
        }
    } catch (err) {
        ctx.log.error("Failed to trigger pet reaction:", err);
    }
}

// Defensive helper to make the pet speak
async function petSpeak(ctx, text) {
    if (!ctx.pet) return;
    try {
        if (typeof ctx.pet.speak === 'function') {
            await ctx.pet.speak(text);
        }
    } catch (err) {
        ctx.log.error("Failed to make pet speak:", err);
    }
}

// Update plugin status line in the control panel
async function setPluginStatus(ctx, state, text) {
    if (!ctx.status) return;
    try {
        // status.set takes string or { text, tone }
        await ctx.status.set({ text: text, tone: state });
    } catch (err) {
        ctx.log.error("Failed to set plugin status:", err);
    }
}

async function checkUsage(ctx) {
    // Read settings from context config
    const config = await ctx.config.get() || {};
    const port = config.agentPort || 15722;
    const costThreshold = config.costThreshold !== undefined ? Number(config.costThreshold) : 0.1;
    const tokenThreshold = config.tokenThreshold !== undefined ? Number(config.tokenThreshold) : 5000;

    const url = `http://127.0.0.1:${port}/api/usage?range=today`;

    try {
        // Query local Python Agent using context net API
        const response = await ctx.net.fetch(url);
        if (!response.ok) {
            await setPluginStatus(ctx, "error", "Agent API 请求失败");
            await petReact(ctx, "error");
            await petSpeak(ctx, "本地 Agent API 请求失败了，快帮我检查一下！");
            return;
        }

        // Parse JSON response safely
        const data = typeof response.json === 'object' && response.json !== null 
                     ? response.json 
                     : JSON.parse(response.text);

        const summary = data.summary || {};
        
        // Sum up tokens: input + output + cache read + cache creation
        const currentTokens = (summary.input_tokens || 0) 
                            + (summary.output_tokens || 0) 
                            + (summary.cache_read_tokens || 0) 
                            + (summary.cache_creation_tokens || 0);
        const currentCost = summary.total_cost || 0;

        await setPluginStatus(ctx, "success", `运行正常 - 今日已用 ${currentTokens} tk`);

        if (lastTotalTokens === 0) {
            // First tick, record baseline
            lastTotalTokens = currentTokens;
            lastTotalCost = currentCost;
            return;
        }

        const deltaTokens = currentTokens - lastTotalTokens;
        const deltaCost = currentCost - lastTotalCost;

        if (deltaTokens > 0) {
            // Check thresholds and react
            if (deltaCost >= costThreshold) {
                // High expense warning
                await petReact(ctx, "error");
                await petSpeak(ctx, `刚才这发大模型调用消耗了 ${deltaTokens} 个 tokens，吃掉了我 $${deltaCost.toFixed(4)} 的饭钱！😭`);
            } else if (deltaTokens >= tokenThreshold) {
                // Large context usage
                await petReact(ctx, "thinking");
                await petSpeak(ctx, `哇，你这一下灌了 ${deltaTokens} 个 tokens，脑壳要算烧了！⚡`);
            } else {
                // Normal usage
                const replies = [
                    `消耗了 ${deltaTokens} tokens，老铁继续努力！`,
                    `叮咚！用量增加 ${deltaTokens}，搬砖愉快！`,
                    `代码执行完毕，消耗了 ${deltaTokens} tokens。`
                ];
                await petReact(ctx, "success");
                await petSpeak(ctx, replies[Math.floor(Math.random() * replies.length)]);
            }

            lastTotalTokens = currentTokens;
            lastTotalCost = currentCost;
        }
    } catch (e) {
        await setPluginStatus(ctx, "error", "连接 Agent 失败");
        await petReact(ctx, "error");
        await petSpeak(ctx, "无法读取 CC Switch 统计，请确认本地 agent.py 是否启动！");
        ctx.log.error("Fetch error in checkUsage:", e);
    }
}

// Define the plugin start and stop definitions
const pluginDefinition = {
    async start(ctx) {
        ctx.log.info("CC Switch Companion plugin started.");
        lastTotalTokens = 0;
        lastTotalCost = 0;

        // Run initial check
        await checkUsage(ctx);

        // Read pollInterval config to set schedule
        const config = await ctx.config.get() || {};
        const intervalMap = {
            "10s": 10000,
            "15s": 15000,
            "30s": 30000,
            "60s": 60000
        };
        const intervalMs = intervalMap[config.pollInterval] || 15000;

        // Register schedule programmatically in SDK v3
        if (ctx.schedule && typeof ctx.schedule.every === 'function') {
            await ctx.schedule.every('pollUsage', intervalMs, () => checkUsage(ctx));
            ctx.log.info(`Scheduled pollUsage every ${intervalMs}ms.`);
        }
    },

    async stop(ctx) {
        ctx.log.info("CC Switch Companion plugin stopped.");
    }
};

// Export the register style for official bundling/harness compatibility
module.exports = {
    register(api) {
        if (api && typeof api.register === 'function') {
            api.register(pluginDefinition);
        }
    }
};

// Support top-level browser global execution
if (typeof OpenPetsPlugin !== 'undefined' && typeof OpenPetsPlugin.register === 'function') {
    OpenPetsPlugin.register(pluginDefinition);
}
