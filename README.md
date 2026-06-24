# CC Switch Omni

CC Switch Omni 是一款专为 `cc-switch` 开发的多设备使用统计聚合面板（Electron 应用）。它可以跨局域网聚合多个设备的 Token 使用量、请求数、消费金额及延迟等数据，为您提供直观的可视化图表与数据看板。

> [!NOTE]
> `cc-switch` 是一款大模型网关/代理代理工具，本应用通过在各设备上运行轻量级 Python 代理（Agent），读取本地 `cc-switch.db` 数据，并通过网络聚合展示。

## 功能特性

- **多设备聚合**：支持添加局域网内多个运行 `cc-switch` 的设备，统一进行数据清洗与多维度聚合。
- **直观可视化**：使用 Chart.js 构建动态使用趋势图，清晰展示 Token 消耗、请求次数和消费金额走势。
- **悬浮窗卡片**：支持为指定设备或项目生成独立的极简悬浮小组件，置顶显示，随时查看统计数据。
- **本地轻量代理**：后台内置 Python HTTP 服务（默认端口 25722），只读安全查询本地 SQLite 数据库。
- **开箱即用**：提供一键运行脚本及 macOS 打包工具，兼容 Apple Silicon (arm64) 与 Intel (x64) 架构。
- **🐶 OpenPets 桌面宠物联动**：集成桌面宠物挂件插件，支持在宠物头顶常驻展示今日/24h/7天等的 Token 消耗、花费及缓存命中率进度条，并在发生新请求时触发情绪反馈。

## 项目结构

```text
├── agent.py          # 后台 Python 代理，提供统一的 SQLite 查询与 HTTP API 接口
├── main.js           # Electron 主进程文件，负责窗口管理、进程通信和代理生命周期
├── preload.js        # Electron 渲染进程预加载脚本，桥接底层 API 与 Web 页面
├── index.html        # 主看板界面（HTML/CSS/JS），集成数据展示与设备管理
├── widget.html       # 悬浮组件小窗口界面
├── package.json      # 项目元数据与依赖配置
├── run.sh            # 本地一键启动测试脚本
├── package.sh        # macOS App 构建与打包脚本
├── build/            # 存放图标素材与脚本的目录
├── dist/             # 打包输出目录（自动生成）
└── plugins/          # 桌面挂件插件目录
    └── openpets-cc-switch/
        ├── index.js             # 插件入口逻辑（轮询云端 KV 并控制气泡与反馈）
        ├── openpets.plugin.json # 插件描述与配置选项定义说明
        └── panel.html           # 插件内置的本地用量可视化图表看板
```

## 🐶 OpenPets 桌面宠物集成

### 1. 运行原理
由于桌面客户端沙箱安全限制，JS 插件无法直接读取本地 SQLite 数据库。因此本系统通过数据桥接实现通讯：
1. **本地 Python Agent** (`agent.py`) 后台轮询本地数据库，自动计算总用量、费用及缓存命中率。
2. Agent 将经过脱敏与编码的数据同步至免配置的 HTTPS 键值桥接服务（`keyvalue.immanuel.co`）。
3. **OpenPets 插件** (`plugins/openpets-cc-switch`) 从云端获取对应 `syncAppKey` 的最新数据，并以**常驻气泡（Pinned Speech Bubble）**的形式显示在宠物头部，同时支持大额增量调用的情绪反馈和 7 秒警告回复。
4. 插件内嵌面板 (`panel.html`) 可以直接请求本地的 Python Agent 接口（默认端口 `25722`）展示美观的可视化图表看板。
5. **气泡防覆盖恢复**：插件内置对气泡 `onDismiss` 的事件监听，当宠物执行内置的进食、玩耍等交互导致用量气泡被覆盖时，会在下一次轮询时（10-15s）自动重构并重新占领常驻槽位。

### 2. 插件安装与配置
1. 打开您的 **OpenPets 桌面客户端**。
2. 导航到 **Plugins（插件管理）** -> **开发者工具 / 加载本地插件**。
3. 选择当前项目目录下的 `plugins/openpets-cc-switch` 文件夹进行加载并启用。
4. 点击插件设置，您可以配置：
   - **本地 Agent 端口**：默认 `25722`。
   - **数据更新频率**：默认 15s。
   - **大额报警阈值**：单次花费/Token 预警。
   - **同步通道 AppKey**：与 `agent.py` 读取的本地 `openpets-plugin-state.json` 自动同步，保证多设备隔离。
   - **Token 统计区间**：支持“今日”、“最近24h”、“最近7天”等，宠物头顶会对应展示。

## 快速开始

### 运行环境要求

1. **Node.js** (建议 v18 及以上)
2. **Python 3** (应用运行需要本机支持 python3)

### 1. 本地启动开发测试

在项目根目录下，直接运行一键启动脚本：

```bash
chmod +x run.sh
./run.sh
```

或者手动安装并启动：

```bash
npm install
npm start
```

### 2. 构建与打包 (macOS)

若要打包成 `.dmg` 安装包或 `.app` 绿色版软件，可以运行：

```bash
chmod +x package.sh
./package.sh
```

打包产物将输出到 `dist/` 目录中：
- 针对 Apple Silicon (M1/M2/M3/M4 系列 Mac) 的安装包位于 `dist/CC Switch Omni-1.0.0-arm64.dmg`。
- 针对 Intel 芯片 Mac 的安装包位于 `dist/CC Switch Omni-1.0.0.dmg`。

## 配置与数据安全

- **数据只读**：后台 Python 代理使用 SQLite 的只读模式 (`mode=ro`) 连接数据库，确保不会污染或损坏您的 `cc-switch` 原始数据。
- **本地存储**：设备列表及卡片偏好设置保存在 Electron 的用户应用配置中，不会上传至任何第三方服务器。

## 许可证

Copyright © 2026 CC Switch Omni. All rights reserved.
