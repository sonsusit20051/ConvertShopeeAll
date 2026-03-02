#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo DOMAIN=convert.example.com ADMIN_KEY='your-admin-pass' WORKER_TOKEN='your-worker-token' bash deploy/scripts/setup_ubuntu.sh

APP_USER="${APP_USER:-convertshopee}"
APP_GROUP="${APP_GROUP:-www-data}"
APP_DIR="${APP_DIR:-/opt/convertshopee}"
APP_PORT="${APP_PORT:-8790}"
DOMAIN="${DOMAIN:-example.com}"
REPO_URL="${REPO_URL:-https://github.com/sonsusit20051/ConvertShopeeAll.git}"
BRANCH="${BRANCH:-main}"
ADMIN_KEY="${ADMIN_KEY:-change_me_admin_key}"
WORKER_TOKEN="${WORKER_TOKEN:-change_me_worker_token}"
SESSION_SECRET="${SESSION_SECRET:-$(openssl rand -hex 32)}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

if [[ "${DOMAIN}" == "example.com" ]]; then
  echo "Please set DOMAIN before running."
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip nginx git curl certbot python3-certbot-nginx

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

mkdir -p "${APP_DIR}" /var/log/convertshopee
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}" /var/log/convertshopee

if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
fi

chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

sudo -u "${APP_USER}" bash -lc "
  set -euo pipefail
  cd '${APP_DIR}'
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
fi

upsert_env() {
  local key="$1"
  local value="$2"
  local env_file="${APP_DIR}/.env"
  grep -v "^${key}=" "${env_file}" > "${env_file}.tmp" || true
  printf "%s=%s\n" "${key}" "${value}" >> "${env_file}.tmp"
  mv "${env_file}.tmp" "${env_file}"
}

upsert_env "ADMIN_KEY" "${ADMIN_KEY}"
upsert_env "WORKER_TOKEN" "${WORKER_TOKEN}"
upsert_env "SESSION_SECRET" "${SESSION_SECRET}"
upsert_env "COOKIE_SECURE" "true"

cp "${APP_DIR}/deploy/systemd/convertshopee.service" /etc/systemd/system/convertshopee.service
sed -i "s|__APP_USER__|${APP_USER}|g" /etc/systemd/system/convertshopee.service
sed -i "s|__APP_GROUP__|${APP_GROUP}|g" /etc/systemd/system/convertshopee.service
sed -i "s|__APP_DIR__|${APP_DIR}|g" /etc/systemd/system/convertshopee.service
sed -i "s|__APP_PORT__|${APP_PORT}|g" /etc/systemd/system/convertshopee.service

cp "${APP_DIR}/deploy/nginx/convertshopee.conf" /etc/nginx/sites-available/convertshopee.conf
sed -i "s|__DOMAIN__|${DOMAIN}|g" /etc/nginx/sites-available/convertshopee.conf
sed -i "s|__APP_DIR__|${APP_DIR}|g" /etc/nginx/sites-available/convertshopee.conf
sed -i "s|__APP_PORT__|${APP_PORT}|g" /etc/nginx/sites-available/convertshopee.conf

ln -sf /etc/nginx/sites-available/convertshopee.conf /etc/nginx/sites-enabled/convertshopee.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now convertshopee
systemctl restart nginx

echo ""
echo "Deploy completed."
echo "HTTP URL: http://${DOMAIN}"
echo "Enable HTTPS:"
echo "  certbot --nginx -d ${DOMAIN}"
echo ""
echo "Useful commands:"
echo "  systemctl status convertshopee"
echo "  journalctl -u convertshopee -f"
