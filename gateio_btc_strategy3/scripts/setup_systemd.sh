#!/usr/bin/env bash
# systemd 서비스 등록 (Ubuntu 24.04)
# 사용: sudo bash scripts/setup_systemd.sh [홈디렉터리_사용자명]

set -e
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
SERVICE_USER="${1:-$SUDO_USER}"
if [[ -z "$SERVICE_USER" ]]; then
    echo "사용법: sudo bash scripts/setup_systemd.sh <username>"
    echo "  예: sudo bash scripts/setup_systemd.sh myuser"
    exit 1
fi

USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
SERVICE_FILE="/etc/systemd/system/gateio-btc-smi-ibs-supertrend.service"
cat "${PROJECT_ROOT}/scripts/gateio-btc-smi-ibs-supertrend.service" | \
  sed -e "s|%i|$SERVICE_USER|g" -e "s|%h|$USER_HOME|g" | \
  sudo tee "$SERVICE_FILE" >/dev/null

sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|" "$SERVICE_FILE"
sudo sed -i "s|EnvironmentFile=.*|EnvironmentFile=$PROJECT_ROOT/.env|" "$SERVICE_FILE"
sudo sed -i "s|ExecStart=.*|ExecStart=$PROJECT_ROOT/.venv/bin/python $PROJECT_ROOT/main.py|" "$SERVICE_FILE"

sudo systemctl daemon-reload
echo "서비스 등록됨: gateio-btc-smi-ibs-supertrend.service"
echo "  시작: sudo systemctl start gateio-btc-smi-ibs-supertrend"
echo "  중지: sudo systemctl stop gateio-btc-smi-ibs-supertrend"
echo "  부팅 시 자동 시작: sudo systemctl enable gateio-btc-smi-ibs-supertrend"
echo "  로그: journalctl -u gateio-btc-smi-ibs-supertrend -f"
