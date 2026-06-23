let lastTotalTokens = 0;
let lastTotalCost = 0;

// Defensive wrappers for OpenPets SDK APIs
function petSpeak(ctx, text) {
    if (!ctx.pet) return;
    try {
        if (typeof ctx.pet.say === 'function') {
            ctx.pet.say(text);
        } else if (typeof ctx.pet.speak === 'function') {
            ctx.pet.speak(text);
        } else {
            console.log("[CC Switch Plugin] Pet speak:", text);
        }
    } catch (err) {
        console.error("[CC Switch Plugin] Error speaking:", err);
    }
}

function petAnimate(ctx, anim) {
    if (!ctx.pet) return;
    try {
        if (typeof ctx.pet.triggerAnimation === 'function') {
            ctx.pet.triggerAnimation(anim);
        } else if (typeof ctx.pet.playAnimation === 'function') {
            ctx.pet.playAnimation(anim);
        } else if (typeof ctx.pet.interact === 'function') {
            ctx.pet.interact(anim);
        } else {
            console.log("[CC Switch Plugin] Pet animation:", anim);
        }
    } catch (err) {
        console.error("[CC Switch Plugin] Error animating:", err);
    }
}

function petSetStatus(ctx, status) {
    if (!ctx.pet) return;
    try {
        if (typeof ctx.pet.setStatus === 'function') {
            ctx.pet.setStatus(status);
        } else if (typeof ctx.pet.setStatusPin === 'function') {
            ctx.pet.setStatusPin(status);
        }
    } catch (err) {
        console.error("[CC Switch Plugin] Error setting status:", err);
    }
}

async function checkUsage(ctx) {
    const settings = ctx.settings || {};
    const port = settings.agentPort || 15722;
    const costThreshold = settings.costThreshold !== undefined ? Number(settings.costThreshold) : 0.1;
    const tokenThreshold = settings.tokenThreshold !== undefined ? Number(settings.tokenThreshold) : 5000;

    try {
        // Fetch token metrics from local Python Agent
        const response = await fetch(`http://127.0.0.1:${port}/api/usage?range=today`);
        if (!response.ok) {
            petSetStatus(ctx, "error");
            petAnimate(ctx, "dizzy");
            petSpeak(ctx, "本地 Agent API 请求失败了，快帮我检查一下！");
            return;
        }

        const data = await response.json();
        const summary = data.summary || {};
        
        // Sum up tokens: input + output + cache read + cache creation
        const currentTokens = (summary.input_tokens || 0) 
                            + (summary.output_tokens || 0) 
                            + (summary.cache_read_tokens || 0) 
                            + (summary.cache_creation_tokens || 0);
        const currentCost = summary.total_cost || 0;

        petSetStatus(ctx, "ok");

        if (lastTotalTokens === 0) {
            // Record initial state on startup to prevent massive dump on first tick
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
                petAnimate(ctx, "sad");
                petSpeak(ctx, `刚才这发大模型调用消耗了 ${deltaTokens} 个 tokens，吃掉了我 $${deltaCost.toFixed(4)} 的私房钱！😭`);
            } else if (deltaTokens >= tokenThreshold) {
                // Large context usage
                petAnimate(ctx, "speak");
                petSpeak(ctx, `哇，你这一下灌了 ${deltaTokens} 个 tokens，脑壳要算烧了！⚡`);
            } else {
                // Normal usage
                const replies = [
                    `消耗了 ${deltaTokens} tokens，老板继续努力！`,
                    `叮咚！用量增加 ${deltaTokens}，搬砖愉快！`,
                    `小块代码执行完毕，消耗了 ${deltaTokens} tokens。`
                ];
                petAnimate(ctx, "happy");
                petSpeak(ctx, replies[Math.floor(Math.random() * replies.length)]);
            }

            lastTotalTokens = currentTokens;
            lastTotalCost = currentCost;
        }
    } catch (e) {
        petSetStatus(ctx, "error");
        petAnimate(ctx, "dizzy");
        petSpeak(ctx, "无法读取 CC Switch 统计，请确认本地 agent.py 是否启动！");
        console.error("[CC Switch Plugin] Fetch error:", e);
    }
}

module.exports = {
    async onActivate(ctx) {
        console.log("[CC Switch Plugin] Activating CC Switch Companion plugin...");
        lastTotalTokens = 0;
        lastTotalCost = 0;
        
        // Run initial check immediately
        await checkUsage(ctx);

        // Bind schedule trigger listeners dynamically
        if (ctx.schedules && typeof ctx.schedules.on === 'function') {
            ctx.schedules.on('pollUsage', () => checkUsage(ctx));
        } else if (ctx.onSchedule) {
            ctx.onSchedule('pollUsage', () => checkUsage(ctx));
        }
    },

    async onDeactivate(ctx) {
        console.log("[CC Switch Plugin] Deactivating plugin.");
    },

    // Supported top-level callback for scheduler triggers
    async onSchedule(ctx, scheduleName) {
        if (scheduleName === 'pollUsage') {
            await checkUsage(ctx);
        }
    }
};
