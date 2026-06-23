#!/bin/bash
# One-click start script for CC Switch Omni local source testing

# Navigate to the script's directory
cd "$(dirname "$0")"

echo "=== CC Switch Omni Local Runner ==="

# Check for node_modules
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

# Run the app
echo "Launching CC Switch Omni..."
npm start
