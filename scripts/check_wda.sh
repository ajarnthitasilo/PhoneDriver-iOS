#!/usr/bin/env bash
# Quick health check for WebDriverAgent on localhost:8100
set -euo pipefail

WDA_URL="${WDA_URL:-http://127.0.0.1:8100}"

echo "Checking WDA at ${WDA_URL}/status ..."
curl -fsS "${WDA_URL}/status" | python3 -m json.tool
echo
echo "OK — WDA is reachable."
