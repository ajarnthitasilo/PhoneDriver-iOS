#!/usr/bin/env bash
# Build & run Appium WebDriverAgent on the connected iPhone 11
set -euo pipefail

WDA_DIR="${WDA_DIR:-$HOME/Tools/WebDriverAgent}"
UDID="${UDID:-00008030-0015489C3444802E}"
TEAM_ID="${TEAM_ID:-8PS7CND77B}"

if [[ ! -d "$WDA_DIR/WebDriverAgent.xcodeproj" ]]; then
  echo "Cloning WebDriverAgent into $WDA_DIR ..."
  mkdir -p "$(dirname "$WDA_DIR")"
  git clone --depth 1 https://github.com/appium/WebDriverAgent.git "$WDA_DIR"
  # Unique bundle IDs (com.facebook.* cannot be registered on personal teams)
  PBX="$WDA_DIR/WebDriverAgent.xcodeproj/project.pbxproj"
  sed -i '' \
    -e 's/com\.facebook\.WebDriverAgentLib/com.thitasilo.WebDriverAgentLib/g' \
    -e 's/com\.facebook\.WebDriverAgentRunner/com.thitasilo.WebDriverAgentRunner/g' \
    "$PBX"
fi

echo "Device UDID: $UDID"
echo "Team ID:     $TEAM_ID"
echo "WDA dir:     $WDA_DIR"
echo
echo "NOTE: Free Apple IDs allow only 3 sideloaded apps."
echo "This iPhone currently has: ResilNet, FormForge, WristMind."
echo "Delete one (or use a paid Developer account) before WDA can install."
echo "On the iPhone: trust Developer App if prompted."
echo

cd "$WDA_DIR"
xcodebuild \
  -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination "id=$UDID" \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  CODE_SIGN_STYLE=Automatic \
  test
