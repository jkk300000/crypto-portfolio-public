"""Discord webhook notifier."""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class DiscordNotifier:
    def __init__(self, webhook_url: str = ""):
        self.webhook_url = (webhook_url or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, content: str, embed: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {"content": content}
        if embed:
            payload["embeds"] = [embed]
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.status_code >= 400:
                log.warning("discord webhook HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("discord webhook failed: %s", e)

    def bot_started(
        self,
        *,
        contract: str,
        timeframe: str,
        leverage: float,
        equity: float | None = None,
        has_position: bool = False,
    ) -> None:
        lines = [
            f"**계약**: `{contract}`",
            f"**타임프레임**: `{timeframe}`",
            f"**레버리지**: `{leverage}x` CROSS",
        ]
        if equity is not None:
            lines.append(f"**잔고**: `{equity:,.2f}` USDT")
        lines.append(f"**포지션**: `{'보유 중' if has_position else '없음'}`")
        self.send(
            "🟢 **Donchian 봇 시작**",
            embed={"title": "Bot Started", "description": "\n".join(lines), "color": 0x57F287},
        )

    def entry(
        self,
        *,
        direction: int,
        contract: str,
        size: int,
        entry: float,
        stop: float,
        tp1: float,
        tp2: float,
    ) -> None:
        side = "LONG 🟢" if direction == 1 else "SHORT 🔴"
        color = 0x57F287 if direction == 1 else 0xED4245
        desc = "\n".join(
            [
                f"**방향**: `{side}`",
                f"**계약**: `{contract}`",
                f"**수량**: `{size}` contracts",
                f"**진입가**: `{entry:,.2f}`",
                f"**SL**: `{stop:,.2f}`",
                f"**TP1**: `{tp1:,.2f}`",
                f"**TP2**: `{tp2:,.2f}`",
            ]
        )
        self.send(
            f"📥 **진입** — {side}",
            embed={"title": "Entry", "description": desc, "color": color},
        )
        try:
            from portfolio_events import emit

            emit("dc", "entry", desc.replace("`", ""))
        except Exception:
            pass

    def exit(
        self,
        *,
        direction: int,
        contract: str,
        reason: str,
        entry: float | None,
        exit_price: float | None = None,
    ) -> None:
        side = "LONG" if direction == 1 else "SHORT"
        lines = [
            f"**방향**: `{side}`",
            f"**계약**: `{contract}`",
            f"**사유**: `{reason}`",
        ]
        if entry is not None:
            lines.append(f"**진입가**: `{entry:,.2f}`")
        if exit_price is not None:
            lines.append(f"**청산가**: `{exit_price:,.2f}`")
        self.send(
            f"📤 **청산** — {side} ({reason})",
            embed={"title": "Exit", "description": "\n".join(lines), "color": 0xFEE75C},
        )
        try:
            from portfolio_events import emit

            emit("dc", "close", "\n".join(lines).replace("`", ""))
        except Exception:
            pass
