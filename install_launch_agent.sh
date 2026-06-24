#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"
WORKSPACE_DIR="$(pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.ccswitch.aggregator.plist"

echo "=== CC Switch Agent LaunchAgent Installer ==="

# Create the plist content
cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ccswitch.aggregator</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>-u</string>
        <string>$WORKSPACE_DIR/agent.py</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$WORKSPACE_DIR/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$WORKSPACE_DIR/agent.log</string>
</dict>
</plist>
EOF

echo "✓ Created LaunchAgent plist at $PLIST_PATH"

# Unload if already loaded
launchctl unload "$PLIST_PATH" 2>/dev/null

# Load the launch agent
launchctl load "$PLIST_PATH"

echo "✓ Loaded LaunchAgent. The agent will now run silently in the background on startup!"
echo "You can check logs at: $WORKSPACE_DIR/agent.log"
