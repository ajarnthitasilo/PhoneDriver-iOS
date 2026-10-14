#!/usr/bin/env bash
# Forward device WDA port 8100 to localhost
set -euo pipefail
export PATH="$HOME/Tools/pmd3-venv12/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"
WDA_PORT="${WDA_PORT:-8100}"

if command -v pymobiledevice3 >/dev/null 2>&1; then
  echo "Forwarding with pymobiledevice3 on :${WDA_PORT}"
  echo "If iOS 17+ and this fails, run in another terminal: sudo pymobiledevice3 remote start-tunnel"
  exec pymobiledevice3 usbmux forward "$WDA_PORT" "$WDA_PORT"
elif command -v iproxy >/dev/null 2>&1; then
  echo "Forwarding with iproxy on :${WDA_PORT}"
  exec iproxy "$WDA_PORT" "$WDA_PORT"
else
  echo "Install pymobiledevice3 or libimobiledevice (iproxy) first." >&2
  exit 1
fi
