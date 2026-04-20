import os
import time
import math
from pybit.unified_trading import HTTP
from threading import Thread, Lock
from flask import Flask
from datetime import date

# ========= FURAHA API (waxaa laga qaadaa Environment Variables Render) =========
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Haddii aad rabto inaad si toos ah u qorto (kaliya tijaabo), kana saar # hoose:
# BYBIT_API_KEY = "qh1ujOchsuqW8xHo9x"
# BYBIT_API_SECRET = "OWUpnNVtGUoHPjYoKBcsWRVv6FulhE0HUkXH"

session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    recv_window=10000
)

# ========= QEEMO =========
MAX_SLOTS = 2
TAKE_PROFIT_PCT = 1.015
TRAIL_SL_PCT = 0.994
MIN_MOMENTUM = 0.05
MIN_VOLUME_USD = 10000
MAX_VOLUME_USD = 1000000
MAX_HOLD_SEC = 900
BTC_MIN_CHANGE = -3.0
DAILY_LOSS_LIMIT = -0.5
PAUSE_HOURS = 2
RESERVE_PCT = 0.03
MIN_TRADE_USD = 10

SKIP_LARGE_CAPS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
                   'ADAUSDT', 'DOTUSDT', 'LINKUSDT', 'MATICUSDT', 'DOGEUSDT']

# ========= XOGTA GLOBAL =========
active_trades = {}
daily_pnl = 0.0
trade_date = str(date.today())
is_running = True
pause_until = 0
last_known_balance = 0.0
lock = Lock()

# ========= HAWL FARSAMO =========
def get_wallet_balance():
    global last_known_balance
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        if resp and resp.get('retCode') == 0:
            for account in resp['result']['list']:
                total_eq = account.get('totalEquity')
                if total_eq is not None:
                    bal = float(total_eq)
                    if bal > 0:
                        last_known_balance = bal
                        return bal
                for coin in account.get('coin', []):
                    if coin['coin'] == 'USDT':
                        wb = coin.get('walletBalance')
                        if wb is not None:
                            bal = float(wb)
                            if bal > 0:
                                last_known_balance = bal
                                return bal
    except Exception as e:
        print(f"Balance error: {e}")
    return last_known_balance

def get_btc_change():
    try:
        ticker = session.get_tickers(category="spot", symbol="BTCUSDT")
        return float(ticker['result']['list'][0]['price24hPcnt']) * 100
    except:
        return 0.0

def get_5m_momentum(symbol):
    try:
        klines = session.get_kline(category="spot", symbol=symbol, interval="5", limit=2)
        data = klines['result']['list']
        if len(data) < 2:
            return 0.0
        now = float(data[0][4])
        prev = float(data[1][4])
        return ((now - prev) / prev) * 100
    except:
        return 0.0

def get_24h_volume_usd(symbol):
    try:
        ticker = session.get_tickers(category="spot", symbol=symbol)
        vol = float(ticker['result']['list'][0]['volume24h'])
        price = float(ticker['result']['list'][0]['lastPrice'])
        return vol * price
    except:
        return 0.0

def get_rsi(symbol, period=14):
    try:
        klines = session.get_kline(category="spot", symbol=symbol, interval="5", limit=period+1)
        closes = [float(k[4]) for k in klines['result']['list']]
        closes.reverse()
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 50.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except:
        return 50.0

def get_precision_qty(symbol, amount_usd):
    try:
        instr = session.get_instruments_info(category="spot", symbol=symbol)
        step = float(instr['result']['list'][0]['lotSizeFilter']['qtyStep'])
        ticker = session.get_tickers(category="spot", symbol=symbol)
        price = float(ticker['result']['list'][0]['lastPrice'])
        if price <= 0:
            return None, None
        raw_qty = amount_usd / price
        qty = math.floor(raw_qty / step) * step
        qty = round(qty, 8)
        if qty <= 0:
            return None, None
        min_notional = 10.0
        if qty * price < min_notional:
            return None, None
        return qty, price
    except Exception as e:
        print(f"Precision error {symbol}: {e}")
        return None, None

def buy(symbol, amount_usd):
    if amount_usd < MIN_TRADE_USD:
        print(f"Skip {symbol}: amount ${amount_usd:.2f} < ${MIN_TRADE_USD}")
        return False, None, None
    try:
        qty, price = get_precision_qty(symbol, amount_usd)
        if not qty:
            return False, None, None
        order = session.place_order(category="spot", symbol=symbol,
                                    side="Buy", orderType="Market", qty=str(qty))
        if order.get('retCode') == 0:
            return True, price, qty
        else:
            print(f"Buy failed {symbol}: {order}")
    except Exception as e:
        print(f"Buy error {symbol}: {e}")
    return False, None, None

def sell(symbol, qty_to_sell):
    try:
        qty_str = str(round(qty_to_sell, 8)).rstrip('0').rstrip('.')
        order = session.place_order(category="spot", symbol=symbol,
                                    side="Sell", orderType="Market", qty=qty_str)
        return order.get('retCode') == 0
    except Exception as e:
        print(f"Sell error {symbol}: {e}")
        return False

def sell_full_position(symbol, qty, entry_price):
    global daily_pnl
    for attempt in range(3):
        if sell(symbol, qty):
            try:
                ticker = session.get_tickers(category="spot", symbol=symbol)
                curr_price = float(ticker['result']['list'][0]['lastPrice'])
                pnl = (curr_price - entry_price) * qty
                with lock:
                    daily_pnl += pnl
                    if symbol in active_trades:
                        del active_trades[symbol]
                print(f"SOLD {symbol} PnL: ${pnl:.3f}")
                return True
            except:
                pass
        time.sleep(1)
    print(f"Failed to sell {symbol} after 3 attempts")
    return False

def monitor_trade(symbol):
    with lock:
        if symbol not in active_trades:
            return
        trade = active_trades[symbol].copy()
    entry = trade['entry']
    qty = trade['qty']
    start_time = trade['time']
    highest = entry
    while True:
        time.sleep(5)
        with lock:
            if symbol not in active_trades:
                break
        try:
            ticker = session.get_tickers(category="spot", symbol=symbol)
            curr_price = float(ticker['result']['list'][0]['lastPrice'])
            change = (curr_price / entry) - 1
            if change >= (TAKE_PROFIT_PCT - 1):
                sell_full_position(symbol, qty, entry)
                break
            if curr_price > highest:
                highest = curr_price
            trail_sl = highest * TRAIL_SL_PCT
            if curr_price <= trail_sl:
                sell_full_position(symbol, qty, entry)
                break
            if time.time() - start_time > MAX_HOLD_SEC:
                sell_full_position(symbol, qty, entry)
                break
        except:
            pass
        time.sleep(10)

def get_top_candidates():
    candidates = []
    try:
        tickers = session.get_tickers(category="spot")['result']['list']
        temp = []
        for t in tickers:
            sym = t['symbol']
            if not sym.endswith('USDT'):
                continue
            if sym in SKIP_LARGE_CAPS:
                continue
            if 'UP' in sym or 'DOWN' in sym:
                continue
            vol = get_24h_volume_usd(sym)
            if vol < MIN_VOLUME_USD or vol > MAX_VOLUME_USD:
                continue
            temp.append((sym, vol))
        temp.sort(key=lambda x: x[1], reverse=True)
        temp = temp[:20]
        for sym, vol in temp:
            mom = get_5m_momentum(sym)
            if mom < MIN_MOMENTUM:
                continue
            rsi = get_rsi(sym)
            if rsi < 20 or rsi > 95:
                continue
            candidates.append((sym, mom, vol))
    except Exception as e:
        print(f"Candidates error: {e}")
        return []
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:15]

def engine():
    global is_running, pause_until, daily_pnl, trade_date
    while is_running:
        time.sleep(15)
        today = str(date.today())
        if today != trade_date:
            daily_pnl = 0.0
            trade_date = today
        with lock:
            if pause_until > 0 and time.time() < pause_until:
                continue
            elif pause_until > 0 and time.time() >= pause_until:
                pause_until = 0
            if daily_pnl <= DAILY_LOSS_LIMIT and pause_until == 0:
                pause_until = time.time() + (PAUSE_HOURS * 3600)
                print(f"Daily loss limit reached. Pausing for {PAUSE_HOURS} hours.")
                continue
            if len(active_trades) >= MAX_SLOTS:
                continue
        if get_btc_change() < BTC_MIN_CHANGE:
            continue
        candidates = get_top_candidates()
        if not candidates:
            continue
        balance = get_wallet_balance()
        if balance < 4.0:
            print("Balance too low, stopping.")
            break
        usable = balance * (1 - RESERVE_PCT)
        for sym, mom, vol in candidates:
            with lock:
                if len(active_trades) >= MAX_SLOTS:
                    break
                if sym in active_trades:
                    continue
                slots_left = MAX_SLOTS - len(active_trades)
                trade_size = usable / slots_left if slots_left > 0 else usable / MAX_SLOTS
            if trade_size < MIN_TRADE_USD:
                continue
            success, price, qty = buy(sym, trade_size)
            if success:
                with lock:
                    active_trades[sym] = {'entry': price, 'qty': qty, 'invested': trade_size, 'time': time.time()}
                print(f"BOUGHT {sym} at ${price} qty {qty}")
                Thread(target=monitor_trade, args=(sym,), daemon=True).start()
            time.sleep(2)

app = Flask('')

@app.route('/')
def home():
    bal = get_wallet_balance()
    trades = len(active_trades)
    return f"<h1>Bot Running</h1><p>Balance: ${bal:.2f}</p><p>Active trades: {trades}</p>"

if __name__ == "__main__":
    Thread(target=engine, daemon=True).start()
    app.run(host='0.0.0.0', port=7860)
