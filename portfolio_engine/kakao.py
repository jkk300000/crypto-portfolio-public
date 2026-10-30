# -*- coding: utf-8 -*-
"""KakaoTalk 'send to me' plus optional webhook relay."""
from __future__ import annotations

import json
import logging
import os

import requests

from .config import ROOT
from .env import load_dotenv

log = logging.getLogger(__name__)
TOKEN_PATH = ROOT / "kakao_token.json"


def _tokens() -> dict:
    load_dotenv()
    data = {}
    if TOKEN_PATH.exists():
        try:
            data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["access"] = (data.get("access") or os.getenv("KAKAO_ACCESS_TOKEN") or "").strip()
    data["refresh"] = (data.get("refresh") or os.getenv("KAKAO_REFRESH_TOKEN") or "").strip()
    data["rest"] = (os.getenv("KAKAO_REST_KEY") or "").strip()
    data["secret"] = (os.getenv("KAKAO_CLIENT_SECRET") or "").strip()
    return data


def _save(access: str, refresh: str) -> None:
    TOKEN_PATH.write_text(
        json.dumps({"access": access, "refresh": refresh}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _refresh(tok: dict) -> str:
    if not tok["rest"] or not tok["refresh"]:
        return tok["access"]
    payload = {
        "grant_type": "refresh_token",
        "client_id": tok["rest"],
        "refresh_token": tok["refresh"],
    }
    if tok.get("secret"):
        payload["client_secret"] = tok["secret"]
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=payload,
        timeout=10,
    )
    if resp.status_code >= 400:
        log.warning("kakao refresh failed: %s", resp.text[:200])
        return tok["access"]
    body = resp.json()
    access = (body.get("access_token") or tok["access"]).strip()
    refresh = (body.get("refresh_token") or tok["refresh"]).strip()
    _save(access, refresh)
    return access


def _memo(access: str, text: str) -> bool:
    payload = {
        "object_type": "text",
        "text": text[:200],
        "link": {
            "web_url": "https://www.gate.io",
            "mobile_web_url": "https://www.gate.io",
        },
    }
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access}"},
        data={"template_object": json.dumps(payload, ensure_ascii=False)},
        timeout=10,
    )
    if resp.status_code == 401:
        return False
    if resp.status_code >= 400:
        log.warning("kakao memo failed: %s", resp.text[:200])
        return False
    return True


def _webhook(text: str) -> bool:
    load_dotenv()
    url = (os.getenv("KAKAO_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    try:
        resp = requests.post(url, json={"text": text, "content": text}, timeout=10)
        return resp.status_code < 400
    except requests.RequestException as exc:
        log.warning("kakao webhook failed: %s", exc)
        return False


def send_kakao(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if _webhook(text):
        return True
    tok = _tokens()
    access = tok["access"]
    if not access and tok["refresh"]:
        access = _refresh(tok)
    if not access:
        return False
    if _memo(access, text):
        return True
    access = _refresh(tok)
    if access and _memo(access, text):
        return True
    return False
