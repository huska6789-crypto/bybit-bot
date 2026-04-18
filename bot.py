import ccxt
import time

# ISKU XIRKA BYBIT
exchange = ccxt.bybit({
    'apiKey': 'jWEWm0Zr46VxlPPkmR',
    'secret': 'GlpSDeWclX5mVpnClqS2PNF36Ap06VOWUEjg',
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

def manage_trades():
    try:
        balance = exchange.fetch_balance()
        print(f"[{time.ctime()}] Bot-ku waa online...")
        print("Baaritaanka suuqa: ✅ Ma jiro wax qalad ah.")
    except Exception as e:
        print(f"⚠️ Qalad ayaa dhacay: {e}")

while True:
    manage_trades()
    time.sleep(30)

