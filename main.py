import time, math, sys
from pybit.unified_trading import HTTP
from threading import Thread, Lock

API_KEY = "lawFbe10bXqkYMX3ok"
API_SECRET = "dxSKZRtWftYFCBYUFhfyuUGxYN9ZavHKg7789"

try:
    session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, recv_window=10000)
    print("LOG: Xidhiidhka Bybit waa guul.")
except Exception as e:
    print(f"CILAD BILOWGA AH: {e}")
    sys.exit()

TRADE_SIZE = 10
MAX_SLOTS = 1
TRAILING_START = 1.03
TRAILING_DROP = 0.005
BREAKEVEN_TRIGGER = 1.015
TIME_LIMIT_SEC = 3600

active_trades = {}
lock = Lock()

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
        avg_gain = sum(gains) / len(diffs)
        avg_loss = sum(losses) / len(diffs)
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except Exception as e:
        print(f"LOG: RSI Error ({symbol}): {e}")
        return 50

def get_qty_precision(step):
    step_str = str(step)
    if '.' in step_str:
        return len(step_str.split('.')[1].rstrip('0'))
    return 0

def monitor_trade(symbol, entry_price, qty):
    highest_price = entry_price
    start_time = time.time()
    sl_price = entry_price * 0.98
    is_breakeven = False
    print(f"LOG: Bilaabaya ilaalinta {symbol} | Entry: {entry_price} | Qty: {qty}")
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
                profit_pct = ((curr_price - entry_price) / entry_price) * 100
                print(f"ACTION: {symbol} +{profit_pct:.2f}% - Stop Loss = Breakeven ({entry_price})")
            if highest_price >= entry_price * TRAILING_START:
                if curr_price <= highest_price * (1 - TRAILING_DROP):
                    session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=str(qty))
                    profit = (curr_price - entry_price) * qty
                    print(f"WIN: {symbol} - Trailing Profit! Qiimaha: {curr_price} | Faa'iido: ${profit:.3f}")
                    break
            if curr_price <= sl_price:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=str(qty))
                loss = (curr_price - entry_price) * qty
                print(f"EXIT: {symbol} - Stop Loss. Qiimaha: {curr_price} | P&L: ${loss:.3f}")
                break
            if time.time() - start_time > TIME_LIMIT_SEC:
                session.place_order(category="spot", symbol=symbol, side="Sell", orderType="Market", qty=str(qty))
                pnl = (curr_price - entry_price) * qty
                print(f"TIME: {symbol} - 1 Saac dhammaaday. Qiimaha: {curr_price} | P&L: ${pnl:.3f}")
                break
        except Exception as e:
            print(f"CILAD ILAALINTA ({symbol}): {e}")
            time.sleep(10)
    with lock:
        if symbol in active_trades:
            del active_trades[symbol]
    print(f"LOG: {symbol} trade-ka waa dhammaaday. Slot waa xor.")

def engine():
    print("--- BOT $10 SINGLE TRADE MODE ---")
    print(f"LOG: Trade Size: ${TRADE_SIZE} | Max Slots: {MAX_SLOTS}")
    while True:
        try:
            with lock:
                current_slots = len(active_trades)
            if current_slots < MAX_SLOTS:
                print(f"LOG: Searching... (Active trades: {current_slots}/{MAX_SLOTS})")
                resp = session.get_tickers(category="spot")
                tickers = resp['result']['list']
                candidates = []
                for t in tickers:
                    symbol = t['symbol']
                    if not symbol.endswith('USDT'):
                        continue
                    try:
                        vol = float(t['volume24h']) * float(t['lastPrice'])
                        change = float(t['price24hPcnt']) * 100
                        price = float(t['lastPrice'])
                        if 40000 < vol < 500000 and change > 5 and price > 0.00001:
                            candidates.append((symbol, change, vol, price))
                    except:
                        continue
                candidates.sort(key=lambda x: x[1], reverse=True)
                for symbol, change, vol, price in candidates:
                    with lock:
                        if symbol in active_trades or len(active_trades) >= MAX_SLOTS:
                            break
                    rsi = get_rsi(symbol)
                    print(f"LOG: Checking {symbol} | Change: {change:.1f}% | Vol: ${vol:,.0f} | RSI: {rsi:.1f}")
                    if rsi < 70:
                        try:
                            instr = session.get_instruments_info(category="spot", symbol=symbol)
                            lot_filter = instr['result']['list'][0]['lotSizeFilter']
                            step = float(lot_filter['qtyStep'])
                            min_qty = float(lot_filter['minOrderQty'])
                            decimals = get_qty_precision(step)
                            raw_qty = (TRADE_SIZE / price) / step
                            qty = round(math.floor(raw_qty) * step, decimals)
                            if qty < min_qty:
                                print(f"SKIP: {symbol} - Qty ({qty}) waa ka yar min ({min_qty})")
                                continue
                            order = session.place_order(category="spot", symbol=symbol, side="Buy", orderType="Market", qty=str(qty))
                            if order.get('retCode') == 0:
                                print(f"BUY: {symbol} | Qiimaha: {price} | Qty: {qty} | ~${qty*price:.2f}")
                                with lock:
                                    active_trades[symbol] = True
                                th = Thread(target=monitor_trade, args=(symbol, price, qty))
                                th.daemon = True
                                th.start()
                                break
                            else:
                                print(f"FAIL: {symbol} - {order.get('retMsg')}")
                        except Exception as e:
                            print(f"LOG: Instrument error ({symbol}): {e}")
                            continue
            else:
                print(f"LOG: Trade socda... ({current_slots}/{MAX_SLOTS} slots)")
            time.sleep(10)
        except Exception as e:
            print(f"CILAD ENGINE: {e}")
            time.sleep(30)

if __name__ == "__main__":
    engine()
