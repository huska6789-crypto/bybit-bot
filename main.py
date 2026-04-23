import time
import sys
from pybit.unified_trading import HTTP
from threading import Thread, Lock

# 1. Configuración de API
API_KEY = "1aw6be1DbXqkVMX3ok"
API_SECRET = "dx5KZRtwtyYFCBYUTmfyuUGxYN9ZavHKg7789" 

try:
    session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, recv_window=10000)
    print("LOG: Xidhidhka Bybit waa guul.")
except Exception as e:
    print(f"CILAD BILOWGA AH: {e}")
    sys.exit()

# 2. Parametros de Trading
TRADE_SIZE = 15
MAX_SLOTS = 1
TRAILING_START = 1.015     
TRAILING_DROP = 0.003      
BREAKEVEN_TRIGGER = 1.005  
TIME_LIMIT_SEC = 3600 

active_trades = {}
lock = Lock()

# 3. RSI Calculation (SAXAN)
def get_rsi(symbol):
    try:
        klines = session.get_kline(category="spot", symbol=symbol, interval="5", limit=20)
        data = klines['result']['list']
        if not data or len(data) < 2:
            return 50
        closes = [float(k[4]) for k in data] 
        closes.reverse()
        diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d for d in diffs if d > 0]
        losses = [-d for d in diffs if d < 0]
        avg_gain = sum(gains) / len(diffs) if gains else 0
        avg_loss = sum(losses) / len(diffs) if losses else 0
        if avg_loss == 0: return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except Exception:
        return 50

def get_qty_precision(step):
    step_str = str(step)
    if '.' in step_str:
        return len(step_str.split('.')[1].rstrip('0'))
    return 0

# 4. Monitoring Logic
def monitor_trade(symbol, entry_price, qty):
    highest_price = entry_price
    start_time = time.time()
    sl_price = entry_price * 0.98
    is_breakeven = False
    print(f"LOG: Ilaalinta {symbol} | Qiimaha: {entry_price}")
    while True:
        time.sleep(5)
        try:
            ticker = session.get_tickers(category="spot", symbol=symbol)
            curr_price = float(ticker['result']['list'][0]['lastPrice'])
            if curr_price > highest_price:
                highest_price = curr_price
            if not is_breakeven and curr_price >= entry_price * BREAKEVEN_TRIGGER:
                sl_price = entry_price
                is_breakeven = True
            if highest_price >= entry_price * TRAILING_START:
                if curr_price <= highest_price * (1 - TRAILING_DROP):
                    session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                    print(f"WIN: {symbol} waa la iibiyey!")
                    break
            if curr_price <= sl_price:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                print(f"EXIT: {symbol} Stop Loss.")
                break
            if time.time() - start_time > TIME_LIMIT_SEC:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                break
        except Exception:
            time.sleep(10)
    with lock:
        if symbol in active_trades: del active_trades[symbol]

# 5. Main Engine (SAXAN)
def engine():
    print("--- BOT STARTED ---") 
    while True:
        try:
            with lock:
                if len(active_trades) < MAX_SLOTS:
                    resp = session.get_tickers(category="spot")
                    tickers = resp['result']['list']
                    candidates = []
                    for t in tickers:
                        if t['symbol'].endswith('USDT'):
                            vol = float(t['volume24h']) * float(t['lastPrice'])
                            change = float(t['price24hPcnt']) * 100
                            if vol > 50000 and change > 0.5:
                                candidates.append((t['symbol'], change, float(t['lastPrice'])))
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    for symbol, change, price in candidates:
                        if symbol not in active_trades and get_rsi(symbol) < 70:
                            instr = session.get_instruments_info(category="spot", symbol=symbol)
                            lot = instr['result']['list'][0]['lotSizeFilter']
                            step = lot['basePrecision'] or lot['qtyStep']
                            decimals = get_qty_precision(step)
                            qty = round((TRADE_SIZE / price), int(decimals))
                            order = session.place_order(category="spot", symbol=symbol, side="Buy", orderType="Market", qty=qty)
                            if order.get('retCode') == 0:
                                print(f"BUY: {symbol} at {price}")
                                with lock: active_trades[symbol] = True
                                Thread(target=monitor_trade, args=(symbol, price, qty), daemon=True).start()
                                break
            time.sleep(30)
        except Exception as e:
            print(f"Engine Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    engine()
