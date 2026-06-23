# CC Switch Omni

CC Switch Omni 是一款专为 `cc-switch` 开发的多设备使用统计聚合面板（Electron 应用）。它可以跨局域网聚合多个设备的 Token 使用量、请求数、消费金额及延迟等数据，为您提供直观的可视化图表与数据看板。

> [!NOTE]
> `cc-switch` 是一款大模型网关/代理代理工具，本应用通过在各设备上运行轻量级 Python 代理（Agent），读取本地 `cc-switch.db` 数据，并通过网络聚合展示。

## 功能特性

- **多设备聚合**：支持添加局域网内多个运行 `cc-switch` 的设备，统一进行数据清洗与多维度聚合。
- **直观可视化**：使用 Chart.js 构建动态使用趋势图，清晰展示 Token 消耗、请求次数和消费金额走势。
- **悬浮窗卡片**：支持为指定设备或项目生成独立的极简悬浮小组件，置顶显示，随时查看统计数据。
- **本地轻量代理**：后台内置 Python HTTP 服务（默认端口 15722），只读安全查询本地 SQLite 数据库。
- **开箱即用**：提供一键运行脚本及 macOS 打包工具，兼容 Apple Silicon (arm64) 与 Intel (x64) 架构。

## 项目结构

```text
├── agent.py          # 后台 Python 代理，提供统一 of SQLite 查询与 HTTP API 接口
├── main.js           # Electron 主进程文件，负责窗口管理、进程通信和代理生命周期
├── preload.js        # Electron 渲染进程预加载脚本，桥接底层 API 与 Web 页面
├── index.html        # 主看板界面（HTML/CSS/JS），集成数据展示与设备管理
├── widget.html       # 悬浮组件小窗口界面
├── package.json      # 项目元数据与依赖配置
├── run.sh            # 本地一键启动测试脚本
├── package.sh        # macOS App 构建与打包脚本
├── build/            # 存放图标素材与脚本的目录
└── dist/             # 打包输出目录（自动生成）
```

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
