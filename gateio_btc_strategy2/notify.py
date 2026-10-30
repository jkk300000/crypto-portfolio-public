"""
Discord 웹훅 알림 유틸.
"""
import logging
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_VALID_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
)


def _send(content: str) -> bool:
    url = getattr(config, "DISCORD_WEBHOOK_URL", "") or ""
    if not url:
        logger.warning("디스코드 알림 미발송: DISCORD_WEBHOOK_URL 미설정 (.env 확인)")
        return False
    if not url.startswith(_VALID_WEBHOOK_PREFIXES):
        logger.warning("디스코드 알림 미발송: 웹훅 URL 형식이 올바르지 않음 (https://discord.com/api/webhooks/... 형태여야 함)")
        return False
    try:
        r = requests.post(url, json={"content": content[:2000]}, timeout=10)
        if r.status_code in (200, 204):
            return True
        logger.warning("디스코드 알림 실패: status=%s body=%s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.warning("디스코드 알림 전송 예외: %s", e)
        return False


def notify_discord(
    event: str,
    side: Optional[str] = None,
    entry_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    close_price: Optional[float] = None,
    size: Optional[str] = None,
    extra: Optional[str] = None,
    contract: Optional[str] = None,
) -> bool:
    def _f(x: Optional[float]) -> str:
        return f"{x:.1f}" if x is not None else "-"

    sym = contract or getattr(config, "CONTRACT", "") or "-"
    side_kr = {"long": "롱", "short": "숏"}.get(side or "", side or "-")

    def _book(kind: str, msg: str) -> None:
        try:
            from portfolio_events import emit

            emit("sr", kind, msg)
        except Exception as e:
            logger.warning("카카오 이벤트 기록 실패: %s", e)

    if event == "start":
        msg = f"**[시작] {sym}** SR+IBS+ST 자동매매 봇 실행됨"
        if extra:
            msg += f" | {extra}"
        return _send(msg)
    if event == "entry":
        msg = (
            f"**[진입] {sym}** {side_kr} | 수량 {size or '-'}\n"
            f"진입가: {_f(entry_price)} | 손절가: {_f(sl_price)} | 최종 익절가: {_f(tp_price)}"
        )
        _book("entry", msg)
        return _send(msg)
    if event == "be_add":
        msg = (
            f"**[본전 추가] {sym}** {side_kr} | 추가 수량 {size or '-'}\n"
            f"첫 진입가: {_f(entry_price)} | 손절=본전 {_f(sl_price)} | 익절 {_f(tp_price)}"
        )
        if extra:
            msg += f"\n{extra}"
        _book("be_add", msg)
        return _send(msg)
    if event == "be_stop":
        msg = (
            f"**[본전 스탑] {sym}** {side_kr} | 수량 {size or '-'}\n"
            f"손절을 첫 진입가 {_f(sl_price)} 로 이동 | 익절 {_f(tp_price)}"
        )
        if extra:
            msg += f" | {extra}"
        return _send(msg)
    if event == "supertrend_close":
        msg = (
            f"**[슈퍼트렌드 청산] {sym}** {side_kr} 청산 | 수량 {size or '-'}\n"
            f"청산가: {_f(close_price)}"
        )
        if extra:
            msg += f" | {extra}"
        _book("close", msg)
        return _send(msg)
    if event == "close":
        msg = (
            f"**[청산] {sym}** {side_kr} 청산 | 수량 {size or '-'}\n"
            f"청산가: {_f(close_price)}"
        )
        if extra:
            msg += f" | {extra}"
        _book("close", msg)
        return _send(msg)
    return False
