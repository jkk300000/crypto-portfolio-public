# -*- coding: utf-8 -*-
"""
카카오톡 나에게 보내기 알람을 테스트합니다.
기본은 한 통만 보내고, --all 이면 진입/청산/이체 샘플도 보냅니다.

  python -m portfolio_engine.test_kakao
  python -m portfolio_engine.test_kakao --all
  python -m portfolio_engine.test_kakao --message "직접 입력한 문구"
"""
from __future__ import annotations

import argparse
import os

from .env import load_dotenv
from .events import format_event
from .kakao import send_kakao


def _status() -> dict[str, str]:
    load_dotenv()
    return {
        "rest": (os.getenv("KAKAO_REST_KEY") or "").strip(),
        "secret": (os.getenv("KAKAO_CLIENT_SECRET") or "").strip(),
        "access": (os.getenv("KAKAO_ACCESS_TOKEN") or "").strip(),
        "refresh": (os.getenv("KAKAO_REFRESH_TOKEN") or "").strip(),
        "webhook": (os.getenv("KAKAO_WEBHOOK_URL") or "").strip(),
    }


def _samples() -> list[str]:
    return [
        format_event({
            "sleeve": "eth",
            "kind": "entry",
            "text": "ETH_USDT LONG 진입 테스트",
        }),
        format_event({
            "sleeve": "sr",
            "kind": "close",
            "text": "BTC_USDT 청산 테스트",
        }),
        format_event({
            "sleeve": "book",
            "kind": "transfer",
            "text": "돈치안 → SR 10.00 USDT 테스트",
        }),
    ]


def run(message: str | None, send_all: bool) -> int:
    tok = _status()
    print(f"REST key     {'있음' if tok['rest'] else '없음'}")
    print(f"client secret {'있음' if tok['secret'] else '없음'}")
    print(f"access token {'있음' if tok['access'] else '없음'}")
    print(f"refresh token {'있음' if tok['refresh'] else '없음'}")
    print(f"webhook      {'있음(선택)' if tok['webhook'] else '없음(필수 아님)'}")
    if not tok["access"] and not tok["refresh"] and not tok["webhook"]:
        print("KAKAO_ACCESS_TOKEN 또는 KAKAO_REFRESH_TOKEN 이 필요합니다")
        return 1

    texts = [message.strip()] if message and message.strip() else [
        "[테스트] 포트폴리오 컨트롤러 카카오 알람"
    ]
    if send_all:
        texts.extend(_samples())

    ok = 0
    for text in texts:
        sent = send_kakao(text)
        print(("전송 성공  " if sent else "전송 실패  ") + text.replace("\n", " / "))
        ok += int(sent)
    print(f"{ok}/{len(texts)} 통 전송")
    return 0 if ok == len(texts) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send a KakaoTalk test memo")
    p.add_argument("--message", help="custom text")
    p.add_argument("--all", action="store_true", help="also send entry/close/transfer samples")
    args = p.parse_args(argv)
    return run(message=args.message, send_all=args.all)


if __name__ == "__main__":
    raise SystemExit(main())
