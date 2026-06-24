# CC Switch Omni - OpenPets 桌面监控插件

本分支是 `CC Switch Omni` 的桌面宠物挂件插件集成版本。它通过轻量级的本地 Python 代理与桌面宠物软件 OpenPets 联动，在您桌面的宠物头上以常驻气泡的方式展示 `cc-switch` 本地大模型的用量统计。

> [!NOTE]
> `cc-switch` 是一款大模型网关/代理工具，本插件读取本地 `cc-switch.db` 数据，并在桌面宠物上实时显示统计。

## 功能特性

- **🐱 桌面宠物常驻气泡**：在桌面宠物头顶以常驻气泡形式显示今日/24h/7天/14天/30天的 Token 消耗总量、花费金额及缓存命中率进度条（`缓存命中: [████░░░░░░] 42.5%`）。
- **⚡ 大模型增量提醒**：轮询检测到最新的大模型请求（Token 增加）时，宠物会做出拟真动作（如 `thinking` 思考、`error` 哭诉饭钱流失等），并在气泡内进行趣味吐槽展示，7 秒后自动恢复。
- **📊 趋势图表看板**：集成在插件设置面板内的精美数据看板，支持可视化展示 Token 消耗走势和热门模型使用排名排行。
- **🔒 安全只读**：本地 Python 代理使用 SQLite 只读模式 (`mode=ro`) 连接数据库，不对源数据进行写操作，保证数据安全。

## 项目结构

```text
├── agent.py          # 后台 Python 代理，提供 SQLite 查询 API 与云端 KV 同步
├── package.json      # 项目元数据与依赖配置
├── run.sh            # 本地一键启动测试脚本
├── install_launch_agent.sh # macOS 后台自动启动 Agent 守护进程安装脚本
└── plugins/          # 桌面挂件插件目录
    └── openpets-cc-switch/
        ├── index.js             # 插件核心逻辑（轮询云端 KV 并控制气泡与反馈）
        ├── openpets.plugin.json # 插件描述与配置选项定义说明
        └── panel.html           # 插件内置的本地用量可视化图表看板
```

## 🐶 桌面宠物集成原理与步骤

### 1. 运行原理
由于桌面客户端沙箱安全限制，JS 插件无法直接读取本地 SQLite 数据库。因此本系统通过数据桥接实现通讯：
1. **本地 Python Agent** (`agent.py`) 后台轮询本地数据库，自动计算总用量、费用及缓存命中率。
2. Agent 将经过脱敏与编码的数据同步至免配置的 HTTPS 键值桥接服务（`keyvalue.immanuel.co`）。
3. **OpenPets 插件** (`plugins/openpets-cc-switch`) 从云端获取对应 `syncAppKey` 的最新数据，并以**常驻气泡（Pinned Speech Bubble）**的形式显示在宠物头部，同时支持大额增量调用的情绪反馈 and 7 秒警告回复。
4. 插件内嵌面板 (`panel.html`) 可以直接请求本地的 Python Agent 接口（默认端口 `25722`）展示美观的可视化图表看板。
5. **气泡防覆盖恢复**：插件内置对气泡 `onDismiss` 的事件监听，当宠物执行内置的进食、玩耍等交互导致用量气泡被覆盖时，会在下一次轮询时（10-15s）自动重构并重新占领常驻槽位。

### 2. 快速开始与部署

#### 运行环境要求
1. **Node.js** (建议 v18 及以上)
2. **Python 3** (本地运行需要本机支持 python3)

#### 第一步：启动本地 Python Agent
在项目根目录下，直接运行一键启动脚本启动代理 API 服务：
```bash
chmod +x run.sh
./run.sh
```
或者，如果您想让它在后台默默守护运行，可以使用 launchd 安装脚本（仅限 macOS）：
```bash
chmod +x install_launch_agent.sh
./install_launch_agent.sh
```

#### 第二步：在 OpenPets 客户端中加载插件
1. 打开您的 **OpenPets 桌面客户端**。
2. 导航到 **Plugins（插件管理）** -> **开发者工具 / 加载本地插件**。
3. 选择当前项目目录下的 `plugins/openpets-cc-switch` 文件夹进行加载并启用。
4. 在插件设置页面中，您可以自定义配置以下属性：
   - **本地 Agent 端口**：默认 `25722`。
   - **数据更新频率**：默认 15s。
   - **大额报警阈值**：单次花费/Token 预警线。
   - **同步通道 AppKey**：与 `agent.py` 自动同步，保证多设备隔离。
   - **Token 统计区间**：支持“今日”、“最近24h”、“最近7天”等。

## 许可证

Copyright © 2026 CC Switch Omni. All rights reserved.
