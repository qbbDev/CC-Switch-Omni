#!/bin/bash
# One-click start script for CC Switch Agent data source

# Navigate to the script's directory
cd "$(dirname "$0")"

echo "=== CC Switch Agent Runner ==="

# Check for python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未检测到 python3，请先安装 Python 3。"
    exit 1
fi

# Run the uploader
echo "Launching CC Switch Local Uploader..."
python3 -u local_uploader.py
