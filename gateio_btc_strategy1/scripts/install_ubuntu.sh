#!/usr/bin/env bash
# Ubuntu — 프로젝트 설치 스크립트
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
    if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
        cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
        echo "  .env.example 복사 → .env (GATE_API_KEY, GATE_API_SECRET 등 수정 후 사용)"
    fi
fi

echo ""
echo "설치 완료. 실행: bash scripts/run.sh 또는 .venv/bin/python main.py"
echo "기본 DRY_RUN=1 입니다. 실거래는 .env에서 DRY_RUN=0"
