#!/usr/bin/env bash
# Ubuntu 24.04 LTS x64 — 프로젝트 설치 스크립트
# 사용: 프로젝트 루트에서 실행 — bash scripts/install_ubuntu.sh

set -e
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

echo "[1/4] 시스템 패키지 확인..."
if ! command -v python3 &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip
fi
PYTHON=$(which python3)
echo "  Python: $PYTHON ($($PYTHON --version 2>&1))"

echo "[2/4] 가상환경 생성: ${VENV_DIR}"
if [[ -d "${VENV_DIR}" ]]; then
    echo "  기존 .venv 사용"
else
    $PYTHON -m venv "${VENV_DIR}"
    echo "  생성 완료"
fi

echo "[3/4] pip 업그레이드 및 의존성 설치..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"

echo "[4/4] .env 설정 확인..."
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    echo "  .env 존재함 (수정 필요 시 직접 편집)"
else
    cat > "${PROJECT_ROOT}/.env" <<'ENVEOF'
# Gate.io API 키 (선물 권한 필요, 메인넷 키)
GATE_API_KEY=your_key
GATE_API_SECRET=your_secret

# 선택: Discord 알림 웹훅
DISCORD_WEBHOOK_URL=

# 선택: 테스트넷 사용 시 1 (별도 테스트넷 키 필요)
# GATE_USE_TESTNET=1

# 선택: 종목 스펙 오버라이드 (기본 BTC_USDT)
# CONTRACT_MULTIPLIER=0.0001
# PRICE_TICK=0.1
ENVEOF
    chmod 600 "${PROJECT_ROOT}/.env"
    echo "  .env 생성됨 → ${PROJECT_ROOT}/.env (GATE_API_KEY, GATE_API_SECRET 입력 후 사용)"
fi

echo ""
echo "설치 완료. 실행: bash scripts/run.sh 또는 .venv/bin/python main.py"
