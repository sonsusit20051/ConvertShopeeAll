#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo APP_DIR=/opt/convertshopee APP_USER=convertshopee bash deploy/scripts/update_app.sh

APP_DIR="${APP_DIR:-/opt/convertshopee}"
APP_USER="${APP_USER:-convertshopee}"
BRANCH="${BRANCH:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

sudo -u "${APP_USER}" bash -lc "
  set -euo pipefail
  cd '${APP_DIR}'
  git fetch origin '${BRANCH}'
  git checkout '${BRANCH}'
  git pull --ff-only origin '${BRANCH}'
  .venv/bin/pip install -r requirements.txt
"

systemctl restart convertshopee

echo "Update completed."
echo "Check service:"
echo "  systemctl status convertshopee"
