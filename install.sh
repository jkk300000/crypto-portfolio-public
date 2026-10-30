#!/usr/bin/env bash
# 컨트롤러 + 4전략 우분투 설치
# 사용: sudo bash install.sh
# 시작: sudo bash install.sh --start
set -euo pipefail

START_AFTER=0
if [[ "${1:-}" == "--start" ]]; then
  START_AFTER=1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] root 권한이 필요합니다: sudo bash install.sh"
  exit 1
fi

APP_USER="${SUDO_USER:-$USER}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PY="$VENV_DIR/bin/python"
ENGINE_DIR="$APP_DIR/portfolio_engine"
ALLOC_PATH="$ENGINE_DIR/allocation.json"
EVENTS_PATH="$ENGINE_DIR/events.jsonl"

ETH_DIR="$APP_DIR/gateio_supertrend_eth"
EMA_DIR="$APP_DIR/gateio_btc_ema_rsi_trail"
SR_DIR="$APP_DIR/gateio_btc_sr_ibs_supertrend"
DC_DIR="$APP_DIR/돈치안채널_btc"

SERVICES=(
  portfolio-controller
  portfolio-eth
  portfolio-ema
  portfolio-sr
  portfolio-dc
)

if [[ "$APP_USER" == "root" ]]; then
  echo "[WARN] root 계정으로 설치 중입니다. 전용 유저를 권장합니다."
fi

for d in "$ENGINE_DIR" "$ETH_DIR" "$EMA_DIR" "$SR_DIR" "$DC_DIR"; do
  if [[ ! -d "$d" ]]; then
    echo "[ERROR] 폴더 없음: $d"
    exit 1
  fi
done

echo "==> 1) 시스템 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJ="$(python3 -c 'import sys; print(sys.version_info[0])')"
PY_MIN="$(python3 -c 'import sys; print(sys.version_info[1])')"
if [[ "$PY_MAJ" -lt 3 || "$PY_MIN" -lt 10 ]]; then
  echo "[ERROR] Python 3.10 이상이 필요합니다. 현재: $PY_VER"
  exit 1
fi

echo "==> 2) 가상환경: $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
fi
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

ensure_env() {
  local dest="$1"
  local example="$2"
  if [[ ! -f "$dest" ]]; then
    if [[ -f "$example" ]]; then
      sudo -u "$APP_USER" cp "$example" "$dest"
      echo "    생성: $dest  — 키를 넣으세요"
    else
      sudo -u "$APP_USER" touch "$dest"
      echo "    빈 파일 생성: $dest"
    fi
  else
    echo "    유지: $dest"
  fi
  chmod 600 "$dest"
  chown "$APP_USER:$APP_USER" "$dest"
}

echo "==> 3) .env"
ensure_env "$ENGINE_DIR/.env" "$ENGINE_DIR/.env.example"
ensure_env "$ETH_DIR/.env" "$ETH_DIR/.env.example"
ensure_env "$EMA_DIR/.env" "$EMA_DIR/.env.example"
ensure_env "$SR_DIR/.env" "$SR_DIR/.env.example"
ensure_env "$DC_DIR/.env" "$DC_DIR/.env.example"

write_unit() {
  local name="$1"
  local desc="$2"
  local workdir="$3"
  local exec_start="$4"
  local envfile="$5"
  local sleeve="$6"
  local unit="/etc/systemd/system/${name}.service"
  cat > "$unit" <<EOF
[Unit]
Description=$desc
After=network-online.target
Wants=network-online.target
PartOf=portfolio.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$workdir
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=LANG=C.UTF-8
Environment=PORTFOLIO_ALLOC_PATH=$ALLOC_PATH
Environment=PORTFOLIO_EVENTS_PATH=$EVENTS_PATH
Environment=PORTFOLIO_SLEEVE=$sleeve
EnvironmentFile=-$envfile
Environment=DRY_RUN=0
ExecStart=$exec_start
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$name

[Install]
WantedBy=portfolio.target
EOF
}

echo "==> 4) systemd"
write_unit \
  portfolio-controller \
  "Portfolio book controller (weights, transfer, Kakao)" \
  "$APP_DIR" \
  "$PY -m portfolio_engine" \
  "$ENGINE_DIR/.env" \
  book

write_unit \
  portfolio-eth \
  "ETH SuperTrend+BB" \
  "$ETH_DIR" \
  "$PY $ETH_DIR/main.py" \
  "$ETH_DIR/.env" \
  eth

write_unit \
  portfolio-ema \
  "BTC EMA200+RSI Trail" \
  "$EMA_DIR" \
  "$PY $EMA_DIR/main.py" \
  "$EMA_DIR/.env" \
  ema

write_unit \
  portfolio-sr \
  "BTC SR+IBS+ST" \
  "$SR_DIR" \
  "$PY $SR_DIR/main.py" \
  "$SR_DIR/.env" \
  sr

write_unit \
  portfolio-dc \
  "BTC Donchian" \
  "$DC_DIR" \
  "$PY $DC_DIR/main.py" \
  "$DC_DIR/.env" \
  dc

write_unit \
  portfolio-dashboard \
  "Portfolio live position dashboard" \
  "$APP_DIR" \
  "$PY -m portfolio_engine.dashboard" \
  "$ENGINE_DIR/.env" \
  book

cat > /etc/systemd/system/portfolio.target <<EOF
[Unit]
Description=Gate book: controller + 4 strategies + dashboard
After=network-online.target
Wants=network-online.target
Wants=portfolio-controller.service portfolio-eth.service portfolio-ema.service portfolio-sr.service portfolio-dc.service portfolio-dashboard.service

[Install]
WantedBy=multi-user.target
EOF

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENGINE_DIR/.env" "$ETH_DIR/.env" "$EMA_DIR/.env" "$SR_DIR/.env" "$DC_DIR/.env"

if [[ -f /etc/systemd/system/portfolio-sqz.service ]]; then
  echo "==> 이전 portfolio-sqz 서비스 제거"
  systemctl stop portfolio-sqz.service 2>/dev/null || true
  systemctl disable portfolio-sqz.service 2>/dev/null || true
  rm -f /etc/systemd/system/portfolio-sqz.service
fi

systemctl daemon-reload
systemctl enable portfolio.target

if [[ "$START_AFTER" -eq 1 ]]; then
  echo "==> 5) 시작"
  systemctl start portfolio.target
else
  echo "==> 5) 서비스는 등록만 함. 자동 시작 안 함"
fi

echo
echo "============================================"
echo " 설치 완료"
echo "============================================"
echo " 경로     $APP_DIR"
echo " 유저     $APP_USER"
echo " 가상환경 $VENV_DIR"
echo
echo " 1) 키 확인"
echo "    nano $ENGINE_DIR/.env"
echo "    nano $ETH_DIR/.env"
echo "    nano $EMA_DIR/.env"
echo "    nano $SR_DIR/.env"
echo "    nano $DC_DIR/.env"
echo
echo " 2) 전체 시작 / 중지"
echo "    sudo systemctl start portfolio.target"
echo "    sudo systemctl stop portfolio.target"
echo
echo " 3) 개별 시작 / 중지 (컨트롤러는 전략을 켜고 끄지 않음)"
echo "    sudo systemctl start portfolio-eth"
echo "    sudo systemctl stop portfolio-eth"
echo "    sudo systemctl restart portfolio-controller"
echo
echo " 4) 상태 / 로그"
echo "    systemctl status portfolio.target"
echo "    journalctl -u portfolio-controller -f"
echo "    journalctl -u portfolio-eth -u portfolio-ema -u portfolio-sr -u portfolio-dc -f"
echo
echo " 5) 대시보드 (서버 로컬만. SSH 터널 필요)"
echo "    ssh -L 8787:127.0.0.1:8787 유저@서버"
echo "    브라우저: http://127.0.0.1:8787"
echo "    journalctl -u portfolio-dashboard -f"
echo "============================================"
