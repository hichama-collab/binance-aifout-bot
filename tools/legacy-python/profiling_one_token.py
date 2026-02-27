# testws.py
# Usage:
#   ./.venv/bin/python testws.py
# Then paste either a Binance URL or a symbol like: ABCDUSDC

import asyncio
import json
import re
import sys
import time

import websockets

def parseSymbol(userInput: str) -> str:
    s = userInput.strip()

    # If user pasted a URL, try to extract a symbol like ABCDUSDC or ABCDUSDT
    # We prefer USDC since you trade Spot USDC.
    m = re.search(r'([A-Z0-9]{3,}USDC)\b', s.upper())
    if m:
        return m.group(1)

    m = re.search(r'([A-Z0-9]{3,}USDT)\b', s.upper())
    if m:
        # allow USDT too, but you can reject later if needed
        return m.group(1)

    # Otherwise assume it's already a symbol
    sym = s.upper().replace("/", "").replace("-", "").replace(" ", "")
    if not re.fullmatch(r'[A-Z0-9]{6,20}', sym):
        raise ValueError("Entrée invalide. Donne une URL Binance ou un symbol type ABCDUSDC.")
    return sym

async def readWs(url: str, onMessage):
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, ping_timeout=15) as ws:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    onMessage(msg)
        except Exception:
            await asyncio.sleep(0.5)

async def main():
    try:
        userInput = input("URL ou symbol: ").strip()
        symbol = parseSymbol(userInput)
    except Exception as e:
        print(f"ERR: {e}")
        sys.exit(1)

    symLower = symbol.lower()
    miniUrl = f"wss://stream.binance.com:9443/ws/{symLower}@miniTicker"
    bookUrl = f"wss://stream.binance.com:9443/ws/{symLower}@bookTicker"

    state = {
        "last": None,
        "bid": None,
        "ask": None,
        "bidQty": None,
        "askQty": None,
        "tLastPrint": 0.0,
    }

    def onMini(msg):
        # miniTicker: c = last
        if "c" in msg:
            state["last"] = msg["c"]

    def onBook(msg):
        # bookTicker: b/B = best bid/qty, a/A = best ask/qty
        if "b" in msg:
            state["bid"] = msg["b"]
        if "B" in msg:
            state["bidQty"] = msg["B"]
        if "a" in msg:
            state["ask"] = msg["a"]
        if "A" in msg:
            state["askQty"] = msg["A"]

    async def printer():
        while True:
            now = time.time()
            if now - state["tLastPrint"] >= 0.2:
                state["tLastPrint"] = now
                if state["last"] and state["bid"] and state["ask"]:
                    print(
                        f"{symbol}  last={state['last']}  bid={state['bid']}({state['bidQty']})  ask={state['ask']}({state['askQty']})"
                    )
            await asyncio.sleep(0.05)

    print(f"SYMBOL: {symbol}")
    print(f"WS miniTicker: {miniUrl}")
    print(f"WS bookTicker: {bookUrl}")
    print("CTRL+C pour arrêter.")

    await asyncio.gather(
        readWs(miniUrl, onMini),
        readWs(bookUrl, onBook),
        printer(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

