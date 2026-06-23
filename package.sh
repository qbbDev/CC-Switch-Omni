#!/bin/bash
# Set exit on error
set -e

echo "=========================================================="
echo "           CC Switch Omni - macOS 一键打包构建工具        "
echo "=========================================================="
echo ""

# Get script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 1. Environment check
echo "⏳ [1/4] 正在检查构建环境..."
if ! command -v node &> /dev/null; then
    echo "❌ 错误: 未检测到 Node.js，请先安装 Node.js (https://nodejs.org/)"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "⚠️  警告: 本机未检测到 python3，虽然不影响应用打包，但应用运行需要本机支持 python3。"
fi
echo "✅ 环境检查通过！"
echo ""

# 2. Dependency cleanup and installation
echo "⏳ [2/4] 正在安装 NPM 依赖包..."
npm install
echo "✅ 依赖包安装完成！"
echo ""

# 3. Clean previous build directory
if [ -d "dist" ]; then
    echo "⏳ [3/4] 正在清理历史构建产物..."
    rm -rf dist
    echo "✅ 清理完成！"
else
    echo "⏳ [3/4] 无历史构建产物需清理，跳过。"
fi
echo ""

# 4. Packaging Application
echo "⏳ [4/4] 正在执行打包构建 (electron-builder)..."
echo "🛠️  正在构建 Apple Silicon (arm64) 版本..."
npx electron-builder --mac dmg --arm64
echo "🛠️  正在构建 Intel (x64) 版本..."
npx electron-builder --mac dmg --x64
echo ""

echo "=========================================================="
echo "🎉 构建打包完成！产物已输出到 dist/ 目录："
echo "=========================================================="
echo ""
echo "🚀 目标打包位置: $DIR/dist"
echo ""
if [ -d "dist/mac-arm64" ]; then
    echo "👉 [Apple Silicon M1/M2/M3/M4 系列 Mac] 使用："
    echo "   - 安装包: dist/CC Switch Omni-1.0.0-arm64.dmg"
    echo "   - 绿色软件: dist/mac-arm64/CC Switch Omni.app"
    echo ""
fi
if [ -d "dist/mac" ]; then
    echo "👉 [Intel 系列 Mac] 使用："
    echo "   - 安装包: dist/CC Switch Omni-1.0.0.dmg"
    echo "   - 绿色软件: dist/mac/CC Switch Omni.app"
    echo ""
fi
echo "=========================================================="
