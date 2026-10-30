#!/usr/bin/env bash
# Donchian BTC Gate.io bot — Ubuntu 설치 스크립트
# 사용: sudo bash install.sh
set -euo pipefail

SERVICE_NAME="donchian-btc"
APP_USER="${SUDO_USER:-$USER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/.venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] root 권한이 필요합니다: sudo bash install.sh"
  exit 1
fi

if [[ "$APP_USER" == "root" ]]; then
  echo "[WARN] root 계정으로 실행 중입니다. 전용 유저 사용을 권장합니다."
fi

echo "==> 1) 시스템 패키지 설치"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential

echo "==> 2) 가상환경 생성: $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
fi

echo "==> 3) Python 의존성 설치"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 4) .env 준비"
if [[ ! -f "$APP_DIR/.env" ]]; then
  if [[ -f "$APP_DIR/.env.example" ]]; then
    sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    echo "    .env 생성됨 — API 키/웹훅을 반드시 수정하세요: $APP_DIR/.env"
  else
    echo "[ERROR] .env.example 이 없습니다."
    exit 1
  fi
else
  echo "    기존 .env 유지"
  chmod 600 "$APP_DIR/.env"
fi

echo "==> 5) systemd 서비스 등록: $SERVICE_NAME"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Donchian BTC Gate.io Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$APP_DIR/.env
ExecStart=$VENV_DIR/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
# journald 로 로그 수집
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo
echo "============================================"
echo " 설치 완료"
echo "============================================"
echo " 앱 경로 : $APP_DIR"
echo " 서비스  : $SERVICE_NAME"
echo
echo " 1) 설정 편집"
echo "    nano $APP_DIR/.env"
echo
echo " 2) 서비스 시작"
echo "    sudo systemctl start $SERVICE_NAME"
echo
echo " 3) 상태 / 로그 (journal)"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    journalctl -u $SERVICE_NAME -f"
echo "    journalctl -u $SERVICE_NAME -n 100 --no-pager"
echo
echo " 중지/재시작"
echo "    sudo systemctl stop $SERVICE_NAME"
echo "    sudo systemctl restart $SERVICE_NAME"
echo "============================================"
