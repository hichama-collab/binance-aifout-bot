import os
import time
import json
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *

# ⬇️ Chargement des clés depuis .env
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# 🔧 Paramètres personnalisables
PAIR = "FHEUSDC"
BUY_PRICE = 0.0425
SELL_PRICE = 0.0495
SLEEP = 15  # en secondes (pause entre chaque vérif)

# 📦 Initialisation client Binance
client = Client(API_KEY, API_SECRET)

# 📁 Fichier log
LOG_FILE = "logs.txt"

def log(msg):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} {msg}\n")
    print(f"{timestamp} {msg}")

def get_open_orders():
    try:
        return client.get_open_orders(symbol=PAIR)
    except Exception as e:
        log(f"[ERREUR] get_open_orders: {e}")
        return []

def get_balance(asset):
    try:
        data = client.get_asset_balance(asset=asset)
        return float(data['free']) if data else 0.0
    except Exception as e:
        log(f"[ERREUR] get_balance({asset}): {e}")
        return 0.0

def place_limit_buy(usdc_balance):
    qty = round(usdc_balance / BUY_PRICE, 2)
    try:
        order = client.order_limit_buy(
            symbol=PAIR,
            quantity=qty,
            price=f"{BUY_PRICE:.5f}"
        )
        log(f"🟢 Achat placé : {qty} FHE à {BUY_PRICE}")
        return order
    except Exception as e:
        log(f"[ERREUR] achat: {e}")
        return None

def place_limit_sell(fhe_balance):
    qty = round(fhe_balance, 2)
    try:
        order = client.order_limit_sell(
            symbol=PAIR,
            quantity=qty,
            price=f"{SELL_PRICE:.5f}"
        )
        log(f"🔴 Vente placée : {qty} FHE à {SELL_PRICE}")
        return order
    except Exception as e:
        log(f"[ERREUR] vente: {e}")
        return None

def check_filled(order_id):
    try:
        order = client.get_order(symbol=PAIR, orderId=order_id)
        return order['status'] == 'FILLED'
    except Exception as e:
        log(f"[ERREUR] check_filled: {e}")
        return False

def main_loop():
    log("=== BOT LANCÉ ===")
    while True:
        orders = get_open_orders()

        buy_orders = [o for o in orders if o['side'] == 'BUY']
        sell_orders = [o for o in orders if o['side'] == 'SELL']

        if buy_orders:
            log("⏳ Attente exécution de l'achat...")
        elif sell_orders:
            log("⏳ Attente exécution de la vente...")
        else:
            fhe = get_balance("FHE")
            usdc = get_balance("USDC")

            if fhe > 0.1:
                place_limit_sell(fhe)
            elif usdc * BUY_PRICE > 0.1:
                place_limit_buy(usdc)
            else:
                log("⚠️ Solde insuffisant (USDC ou FHE)")

        time.sleep(SLEEP)

if __name__ == "__main__":
    main_loop()

