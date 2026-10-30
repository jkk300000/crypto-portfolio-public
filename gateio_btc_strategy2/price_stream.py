"""
Gate.io 선물 WebSocket 가격 스트림.
"""
import json
import logging
import threading
import time
from typing import Optional

import websocket

import config

logger = logging.getLogger(__name__)


class PriceStream:
    def __init__(self, contract: str = None, settle: str = None):
        self.contract = contract or config.CONTRACT
        self.settle = settle or config.SETTLE
        self._last_price: Optional[float] = None
        self._lock = threading.Lock()
        self._ws = None
        self._running = False
        self._thread = None
        base = getattr(config, "GATE_WS_BASE", "wss://fx-ws.gateio.ws/v4/ws").rstrip("/")
        self._ws_url = f"{base}/{self.settle}"

    def get_last_price(self) -> Optional[float]:
        with self._lock:
            return self._last_price

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("channel") == "futures.tickers":
                result = data.get("result")
                if isinstance(result, list):
                    for ticker in result:
                        if isinstance(ticker, dict) and ticker.get("contract") == self.contract:
                            last_str = ticker.get("last")
                            if last_str:
                                with self._lock:
                                    self._last_price = float(last_str)
        except Exception:
            return

    def _on_error(self, ws, error):
        logger.warning("WebSocket 오류: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket 종료: code=%s msg=%s", close_status_code, close_msg)

    def _on_open(self, ws):
        subscribe_msg = {
            "time": int(time.time()),
            "channel": "futures.tickers",
            "event": "subscribe",
            "payload": [self.contract],
        }
        ws.send(json.dumps(subscribe_msg))

    def _run_websocket(self):
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                    header={"X-Gate-Size-Decimal": "1"},
                )
                self._ws.run_forever()
            except Exception as e:
                logger.warning("WebSocket 실행 오류: %s", e)
            if self._running:
                time.sleep(3)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_websocket, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=2)
