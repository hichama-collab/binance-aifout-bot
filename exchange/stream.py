import threading
import websocket
import json
import time


class Stream:
    def __init__(self, cfg, symbol: str):
        self.symbol = symbol.lower()
        self.bestBid = 0.0
        self.bestAsk = 0.0

        # URL FIXÉE (ne jamais utiliser cfg ici)
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker"

        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

    def on_message(self, ws, msg):
        data = json.loads(msg)
        self.bestBid = float(data["b"])
        self.bestAsk = float(data["a"])

    def on_error(self, ws, err):
        print("WS_ERROR", err)

    def on_close(self, ws, *args):
        print("WS_CLOSED")

    def start(self):
        t = threading.Thread(target=self.ws.run_forever, daemon=True)
        t.start()

        # attendre première donnée
        for _ in range(50):
            if self.bestBid > 0 and self.bestAsk > 0:
                return
            time.sleep(0.1)

    def bestBidAsk(self):
        return self.bestBid, self.bestAsk

