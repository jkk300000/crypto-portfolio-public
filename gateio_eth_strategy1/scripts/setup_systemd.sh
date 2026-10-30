#!/usr/bin/env bash
# systemd 서비스 등록 (Ubuntu 24.04)
# 사용: sudo bash scripts/setup_systemd.sh [홈디렉터리_사용자명]
# 예: 프로젝트가 /home/myuser/gateio_supertrend_eth 이면 — sudo bash scripts/setup_systemd.sh myuser

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
# 서비스 템플릿: %i=사용자명, %h=홈. 실제 경로는 아래에서 PROJECT_ROOT 로 덮어씀.
SERVICE_FILE="/etc/systemd/system/gateio-supertrend.service"
cat "${PROJECT_ROOT}/scripts/gateio-supertrend.service" | \
  sed -e "s|%i|$SERVICE_USER|g" -e "s|%h|$USER_HOME|g" | \
  sudo tee "$SERVICE_FILE" >/dev/null
# 실제 프로젝트 경로 사용 (어디에 있든 동작)
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|" "$SERVICE_FILE"
sudo sed -i "s|EnvironmentFile=.*|EnvironmentFile=$PROJECT_ROOT/.env|" "$SERVICE_FILE"
sudo sed -i "s|ExecStart=.*|ExecStart=$PROJECT_ROOT/.venv/bin/python $PROJECT_ROOT/main.py|" "$SERVICE_FILE"

sudo systemctl daemon-reload
echo "서비스 등록됨: gateio-supertrend.service"
echo "  시작: sudo systemctl start gateio-supertrend"
echo "  중지: sudo systemctl stop gateio-supertrend"
echo "  부팅 시 자동 시작: sudo systemctl enable gateio-supertrend"
echo "  로그: journalctl -u gateio-supertrend -f"
