#!/bin/bash
#
# Double-click this file in Finder to have the admin tool start
# automatically whenever you log in, and stay running in the background.
# Then http://localhost:5001 is just a browser bookmark — nothing to launch.
#
# Double-click it again to turn that off.
#
# It works by installing a LaunchAgent (macOS's standard way of running
# something at login) at:
#   ~/Library/LaunchAgents/org.wearepossible.datavizadmin.plist
#
# The server only ever listens on 127.0.0.1, so it isn't reachable from
# anywhere else on the network.

set -u

LABEL="org.wearepossible.datavizadmin"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/datavizadmin.log"
URL="http://localhost:5001"

cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"

echo "Possible Dataviz Library — Admin at login"
echo "========================================="
echo

finish() {
  echo
  read -r -p "Press Return to close this window."
  exit "${1:-0}"
}

# Both launchctl generations: bootstrap/bootout on modern macOS, with
# load/unload as a fallback on older versions.
unload_agent() {
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
}

load_agent() {
  launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null
}

# ── Already installed? Then this is the off switch ──
if [ -f "$PLIST" ]; then
  echo "The admin tool is currently set to start at login."
  echo
  read -r -p "Turn that off? [y/N] " answer
  case "$answer" in
    [Yy]*)
      unload_agent
      rm -f "$PLIST"
      echo
      echo "Done — it won't start at login any more."
      echo "You can still start it any time with \"Dataviz Admin.command\"."
      ;;
    *)
      echo
      echo "Left as it is. The admin tool is at $URL"
      ;;
  esac
  finish 0
fi

# ── Find a Python to use (same order as Dataviz Admin.command) ──
PYTHON=""
for candidate in ".venv/bin/python" "venv/bin/python" "$(command -v python3 || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    PYTHON="$(cd "$(dirname "$candidate")" && pwd)/$(basename "$candidate")"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Couldn't find Python 3 on this Mac."
  echo "Install it with:  brew install python"
  finish 1
fi

if ! "$PYTHON" -c "import flask, boto3, dotenv" >/dev/null 2>&1; then
  echo "Installing the Python packages the admin tool needs (one-off)…"
  echo
  if ! "$PYTHON" -m pip install -r requirements.txt; then
    echo
    echo "That didn't work. Try:  $PYTHON -m pip install -r requirements.txt"
    finish 1
  fi
  echo
fi

# ── Write the LaunchAgent ──
# KeepAlive restarts the server if it ever exits, so the bookmark always
# works.  Homebrew's bin directories go on PATH explicitly because
# LaunchAgents start with a minimal environment and text extraction shells
# out to Tesseract.
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$PROJECT_DIR/admin/admin.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
PLIST_END

unload_agent   # in case an older copy is still loaded
if ! load_agent; then
  echo "Couldn't register the login item with launchctl."
  echo "Log file, if it helps: $LOG"
  finish 1
fi

# Give it a couple of seconds to bind the port, then check it's really up
echo "Starting it now…"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -o /dev/null --max-time 1 "$URL"; then
    break
  fi
  sleep 1
done

if curl -s -o /dev/null --max-time 2 "$URL"; then
  echo
  echo "Done. The admin tool is running and will start again at every login."
  echo "Bookmark this:  $URL"
  echo
  echo "Opening it now so you can bookmark it."
  open "$URL"
  echo
  echo "To turn this off later, double-click this file again."
else
  echo
  echo "The login item is installed, but the server didn't answer on $URL."
  echo "Check the log for what went wrong:"
  echo "  $LOG"
fi

finish 0
