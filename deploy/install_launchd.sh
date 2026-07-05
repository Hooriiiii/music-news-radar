#!/bin/bash
# Installe (ou réinstalle) les deux agents launchd :
#   - com.musicnewsradar.pipeline : ingestion + scoring + alertes, toutes les 30 min
#   - com.musicnewsradar.digest   : digest mail quotidien à 8h30
#
# Usage : bash deploy/install_launchd.sh
# Désinstallation : launchctl bootout gui/$(id -u)/com.musicnewsradar.pipeline
#                   launchctl bootout gui/$(id -u)/com.musicnewsradar.digest
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/music-news-radar"
mkdir -p "$PLIST_DIR" "$LOG_DIR"

for name in com.musicnewsradar.pipeline com.musicnewsradar.digest; do
  launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
  cp "$DEPLOY_DIR/$name.plist" "$PLIST_DIR/"
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DIR/$name.plist"
  echo "installé : $name"
done

echo
launchctl list | grep musicnewsradar || true
