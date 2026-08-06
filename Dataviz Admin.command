#!/bin/bash
#
# Double-click this file in Finder to start the admin tool.
#
# It starts the local Flask app and opens http://localhost:5001 in your
# browser.  Leave the Terminal window it opens alone while you work —
# closing that window stops the admin tool.
#
# If you'd rather it was always running without launching anything, use
# "Start Admin at Login.command" instead.

set -u

PORT=5001
URL="http://localhost:$PORT"

# Finder runs this from the user's home directory, so move to the folder
# the script itself lives in
cd "$(dirname "$0")" || exit 1

echo "Possible Dataviz Library — Admin"
echo "================================"
echo

# ── Already running? Just open the tab ──
# (True if the login item is installed, or a window is open elsewhere.)
if curl -s -o /dev/null --max-time 2 "$URL"; then
  echo "The admin tool is already running — opening it now."
  open "$URL"
  sleep 1
  exit 0
fi

# ── Find a Python to use ──
# Prefer a project virtualenv if there is one, else whatever python3 is on
# PATH (Homebrew's, usually).
PYTHON=""
for candidate in ".venv/bin/python" "venv/bin/python" "$(command -v python3 || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Couldn't find Python 3 on this Mac."
  echo "Install it with:  brew install python"
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

# ── Check the Python dependencies are installed ──
if ! "$PYTHON" -c "import flask, boto3, dotenv" >/dev/null 2>&1; then
  echo "Some Python packages are missing. Installing them now (one-off)…"
  echo
  if ! "$PYTHON" -m pip install -r requirements.txt; then
    echo
    echo "That didn't work. Try running this by hand:"
    echo "  $PYTHON -m pip install -r requirements.txt"
    echo
    read -r -p "Press Return to close this window."
    exit 1
  fi
  echo
fi

# ── Warn about missing .env rather than failing later on upload ──
if [ ! -f ".env" ]; then
  echo "Warning: no .env file found — image uploads to R2 will fail."
  echo "See the README for the keys it needs."
  echo
fi

echo "Starting the admin tool…"
echo "Your browser will open at $URL"
echo
echo "*** Keep this window open while you work. Close it to stop. ***"
echo

# DATAVIZ_OPEN_BROWSER makes admin.py open the tab once the port is bound
export DATAVIZ_OPEN_BROWSER=1
exec "$PYTHON" admin/admin.py
