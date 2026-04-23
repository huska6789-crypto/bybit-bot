import time
import sys
import os
from pybit.unified_trading import HTTP
from threading import Thread, Lock
from dotenv import load_dotenv

# 1. Soo kicinta furayaasha qarsoon ee faylka .env
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')

# Hubi haddii furayaasha la helay
if not API_KEY or not API_SECRET:
    print("CILAD: API Keys-ka lagama helin faylka .env! Fadlan hubi faylkaaga .env")
    sys.exit()

try:
    # Xidhidhka Bybit
    session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, recv_window=10000)
    print("LOG: Xidhidhka Bybit waa guul.")
except Exception as e:
    print(f"CILAD BILOWGA AH: {e}")
    sys.exit()

# 2. Parametros de Trading (Wax ka beddeli kartid haddii aad u baahato)
TRADE_SIZE = 15      # Lacagta hal mar la galayo ($15)
MAX_SLOTS = 1        # Imisa lacag oo isku mar furan (1 coin)
TRAILING_START = 1.015     
TRAILING_DROP = 0.003      
BREAKEVEN_TRIGGER = 1.005  
TIME_LIMIT_SEC = 3600 

active_trades = {}
lock = Lock()

# 3. RSI Calculation (Hubinta haddii suuqu fiican yahay)
def get_rsi(symbol):
    try:
        klines = session.get_kline(category="spot", symbol=symbol, interval="5", limit=20)
        data = klines['result']['list']
        if not data or len(data) < 2: return 50
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

# 4. Monitoring Logic (Ilaalinta faa'iidada iyo khasaaraha)
def monitor_trade(symbol, entry_price, qty):
    highest_price = entry_price
    start_time = time.time()
    sl_price = entry_price * 0.98  # Stop Loss (2%)
    is_breakeven = False
    print(f"LOG: Ilaalinta {symbol} | Qiimaha: {entry_price}")
    while True:
        time.sleep(5)
        try:
            ticker = session.get_tickers(category="spot", symbol=symbol)
            curr_price = float(ticker['result']['list'][0]['lastPrice'])
            
            if curr_price > highest_price: highest_price = curr_price
            
            # Breakeven (Inaan khasaare lagu bixin)
            if not is_breakeven and curr_price >= entry_price * BREAKEVEN_TRIGGER:
                sl_price = entry_price
                is_breakeven = True
            
            # Trailing Take Profit (Ilaalinta faa'iidada sii kordheysa)
            if highest_price >= entry_price * TRAILING_START:
                if curr_price <= highest_price * (1 - TRAILING_DROP):
                    session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                    print(f"WIN: {symbol} waa la iibiyey faa'iido ahaan!")
                    break
            
            # Stop Loss
            if curr_price <= sl_price:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                print(f"EXIT: {symbol} Stop Loss ayaa dhacay.")
                break
            
            # Time Limit (Hal saac ka dib haddii wax dhici waayaan)
            if time.time() - start_time > TIME_LIMIT_SEC:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=qty)
                print(f"EXIT: {symbol} Waqtigii ayaa ka dhacay.")
                break
        except Exception as e:
            print(f"CILAD ILAALINTA {symbol}: {e}")
            time.sleep(10)
            
    with lock:
        if symbol in active_trades: del active_trades[symbol]

# 5. Main Engine (Matoorka Bot-ka)
def engine():
    print("--- BOT-KU HADDA WUU SHAQAYNAYAA ---") 
    while True:
        try:
            with lock:
                if len(active_trades) < MAX_SLOTS:
                    resp = session.get_tickers(category="spot")
                    if resp.get('retCode') != 0:
                        print(f"CILAD API BYBIT: {resp.get('retMsg')}")
                        time.sleep(60)
                        continue
                    
                    tickers = resp['result']['list']
                    candidates = []
                    for t in tickers:
                        if t['symbol'].endswith('USDT'):
                            vol = float(t['volume24h']) * float(t['lastPrice'])
                            change = float(t['price24hPcnt']) * 100
                            # Kaliya fiiri lacagaha mugga leh (Volume > 50k)
                            if vol > 50000 and change > 0.5:
                                candidates.append((t['symbol'], change, float(t['lastPrice'])))
                    
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    
                    for symbol, change, price in candidates:
                        if symbol not in active_trades and get_rsi(symbol) < 70:
                            # Amarka Iibsashada (BUY)
                            order = session.place_order(
                                category="spot", 
                                symbol=symbol, 
                                side="Buy", 
                                orderType="Market", 
                                qty=str(TRADE_SIZE),
                                marketUnit="quoteCoin" 
                            )
                            if order.get('retCode') == 0:
                                print(f"BUY SUCCESS: {symbol} ayaa lagu iibsaday {price}")
                                with lock: active_trades[symbol] = True
                                time.sleep(2)
                                # Soo saar tirada saxda ah ee la iibsaday
                                executions = session.get_executions(category="spot", symbol=symbol, limit=1)
                                final_qty = executions['result']['list'][0]['execQty']
                                Thread(target=monitor_trade, args=(symbol, price, final_qty), daemon=True).start()
                                break
            time.sleep(30) # Sug 30 ilbiriqsi ka hor inta aanad mar kale suuqa baarin
        except Exception as e:
            print(f"LOG: Engine Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    engine()
