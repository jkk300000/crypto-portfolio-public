# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from pathlib import Path

from .config import BOT_ROOTS, ROOT, SLEEVES, SUB_UIDS


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def load_dotenv() -> None:
    for path in (ROOT / ".env", ROOT.parent / ".env"):
        for key, val in parse_env_file(path).items():
            os.environ.setdefault(key, val)


def main_keys() -> tuple[str, str]:
    load_dotenv()
    key = (os.getenv("GATE_MAIN_KEY") or os.getenv("GATE_API_KEY") or "").strip()
    secret = (os.getenv("GATE_MAIN_SECRET") or os.getenv("GATE_API_SECRET") or "").strip()
    if not key or not secret:
        raise RuntimeError("GATE_MAIN_KEY / GATE_MAIN_SECRET 이 필요합니다")
    return key, secret


def one_sub_uid(sleeve: str) -> str:
    load_dotenv()
    names = {
        "eth": "GATE_SUB_UID_ETH",
        "ema": "GATE_SUB_UID_EMA",
        "sr": "GATE_SUB_UID_SR",
        "dc": "GATE_SUB_UID_DC",
    }
    env_name = names[sleeve]
    legacy = os.getenv("GATE_SUB_UID_SQZ") if sleeve == "ema" else None
    uid = (os.getenv(env_name) or legacy or SUB_UIDS.get(sleeve) or "").strip()
    if not uid:
        raise RuntimeError(f"{env_name} 이 필요합니다")
    return uid


def sub_uids() -> dict[str, str]:
    load_dotenv()
    names = {
        "eth": "GATE_SUB_UID_ETH",
        "ema": "GATE_SUB_UID_EMA",
        "sr": "GATE_SUB_UID_SR",
        "dc": "GATE_SUB_UID_DC",
    }
    out = {}
    missing = []
    for sleeve, env_name in names.items():
        legacy = os.getenv("GATE_SUB_UID_SQZ") if sleeve == "ema" else None
        uid = (os.getenv(env_name) or legacy or SUB_UIDS.get(sleeve) or "").strip()
        if not uid:
            missing.append(env_name)
        else:
            out[sleeve] = uid
    if missing:
        raise RuntimeError("서브계정 UID 없음: " + ", ".join(missing))
    for k in SLEEVES:
        if k not in out:
            raise RuntimeError(f"missing uid {k}")
    return out


def sleeve_keys(sleeve: str) -> tuple[str, str]:
    data = parse_env_file(BOT_ROOTS[sleeve] / ".env")
    key = (data.get("GATE_API_KEY") or "").strip()
    secret = (data.get("GATE_API_SECRET") or "").strip()
    if not key or not secret:
        raise RuntimeError(f"{sleeve} .env 에 GATE_API_KEY / GATE_API_SECRET 이 필요합니다")
    return key, secret
