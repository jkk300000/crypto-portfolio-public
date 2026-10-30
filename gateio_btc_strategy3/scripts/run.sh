#!/usr/bin/env bash
# 프로젝트 루트에서 실행 — bash scripts/run.sh

set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="${ROOT}/.venv"

if [[ ! -d "${VENV}" ]]; then
    echo "가상환경 없음. 먼저 실행: bash scripts/install_ubuntu.sh"
    exit 1
fi

if [[ ! -f "${ROOT}/.env" ]]; then
    echo ".env 없음. .env.example 참고해 GATE_API_KEY, GATE_API_SECRET 설정 후 실행하세요."
    exit 1
fi

exec "${VENV}/bin/python" "${ROOT}/main.py" "$@"
