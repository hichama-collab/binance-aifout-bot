import threading
import websocket
import json
import time


class Stream:
    def __init__(self, cfg, symbol: str):
        self.cfg = cfg
        self.symbol = symbol.lower()

        self.bestBid = 0.0
        self.bestAsk = 0.0
        self.lastUpdate = 0.0
        self.tickSeq = 0

        # URL FIXEE (ne jamais utiliser cfg ici)
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker"

        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._thread = None

    def _make_ws(self):
        return websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )

    def on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            bid = float(data["b"])
            ask = float(data["a"])
            now = time.time()
            with self._lock:
                self.bestBid = bid
                self.bestAsk = ask
                self.lastUpdate = now
                self.tickSeq += 1
        except Exception:
            # keep last good values
            return

    def on_error(self, ws, err):
        print("WS_ERROR", err)

    def on_close(self, ws, *args):
        print("WS_CLOSED")

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def runner():
            backoff = float(getattr(self.cfg, "wsReconnectBackoffSec", 1.0))
            while not self._stop.is_set():
                ws = None
                try:
                    ws = self._make_ws()
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    print("WS_RUN_EXCEPTION", type(e).__name__, str(e))
                if self._stop.is_set():
                    break
                time.sleep(backoff)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

        # attendre premiere donnee
        for _ in range(50):
            b, a = self.bestBidAsk()
            if b > 0 and a > 0:
                return
            time.sleep(0.1)

    def stop(self):
        self._stop.set()

    def bestBidAsk(self):
        stale_sec = float(getattr(self.cfg, "wsStaleSec", 3.0))
        now = time.time()
        with self._lock:
            b = self.bestBid
            a = self.bestAsk
            lu = self.lastUpdate
        if b <= 0 or a <= 0:
            return 0.0, 0.0
        if lu <= 0:
            return 0.0, 0.0
        if (now - lu) > stale_sec:
            return 0.0, 0.0
        return b, a

    def snapshot(self):
        stale_sec = float(getattr(self.cfg, "wsStaleSec", 3.0))
        now = time.time()
        with self._lock:
            b = self.bestBid
            a = self.bestAsk
            lu = self.lastUpdate
            seq = self.tickSeq
        if b <= 0 or a <= 0 or lu <= 0:
            return 0.0, 0.0, 0.0, 0
        if (now - lu) > stale_sec:
            return 0.0, 0.0, 0.0, 0
        return b, a, lu, seq
