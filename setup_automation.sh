#!/bin/bash

# Things-to-Notion Sync Automation Setup
# Run this script to set up automated syncing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_FILE="$HOME/Library/LaunchAgents/com.user.things-notion-sync.plist"

echo "Setting up Things-to-Notion sync automation..."

# Create LaunchAgent plist file
cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.things-notion-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.11/bin/python3</string>
        <string>$SCRIPT_DIR/main.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/tmp/things-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/things-sync.error.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF

echo "LaunchAgent created at: $PLIST_FILE"

# Load the LaunchAgent
launchctl load "$PLIST_FILE"
echo "✅ Automation enabled! Sync will run every 10 seconds but only when you use Notion."

echo ""
echo "Usage:"
echo "  Manual sync: python3 main.py"
echo "  Force sync:  python3 main.py --force"
echo "  Clear cache: python3 main.py --clear-cache"
echo "  Legacy mode: python3 main.py --legacy"
echo ""
echo "To disable automation:"
echo "  launchctl unload $PLIST_FILE"
echo ""
echo "Logs are saved to:"
echo "  Output: /tmp/things-sync.log"
echo "  Errors: /tmp/things-sync.error.log"