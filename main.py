import os
import time
import math
import requests
from pybit.unified_trading import HTTP
from threading import Thread, Lock
from flask import Flask
from datetime import date

# ========= FURAHA API =========
BYBIT_API_KEY    = os.environ.get("BYBIT_API_KEY", "YOUR_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "YOUR_API_SECRET")

BYBIT_PUBLIC = "https://api.bybit.com"

def public_get(path, params={}):
    try:
        r = requests.get(BYBIT_PUBLIC + path, params=params, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Public API error: {e}")
        return None

session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    recv_window=10000
)

# ========= QEEMO =========
MAX_SLOTS        = 2
TAKE_PROFIT_PCT  = 1.015
TRAIL_SL_PCT     = 0.994
MIN_MOMENTUM     = 0.05
MIN_VOLUME_USD   = 10000
MAX_VOLUME_USD   = 1000000
MAX_HOLD_SEC     = 900
BTC_MIN_CHANGE   = -3.0
DAILY_LOSS_LIMIT = -0.5
PAUSE_HOURS      = 2
RESERVE_PCT      = 0.03
MIN_TRADE_USD    = 10

SKIP_LARGE_CAPS = [
    'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT',
    'ADAUSDT','DOTUSDT','LINKUSDT','MATICUSDT','DOGEUSDT'
]

# ========= XOGTA GLOBAL =========
active_trades      = {}
daily_pnl          = 0.0
trade_date         = str(date.today())
is_running         = True
pause_until        = 0
last_known_balance = 0.0
lock               = Lock()

# ========= BALANCE =========
def get_wallet_balance():
    global last_known_balance
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        if resp and resp.get('retCode') == 0:
            for account in resp['result']['list']:
                total_eq = account.get('totalEquity')
                if total_eq:
                    bal = float(total_eq)
                    if bal > 0:
                        last_known_balance = bal
                        return bal
                for coin in account.get('coin', []):
                    if coin['coin'] == 'USDT':
                        wb = coin.get('walletBalance')
                        if wb:
                            bal = float(wb)
                            if bal > 0:
                                last_known_balance = bal
                                return bal
    except Exception as e:
        print(f"Balance error: {e}")
    return last_known_balance

# ========= MARKET DATA (Public REST) =========
def get_ticker(symbol):
    data = public_get("/v5/market/tickers", {"category": "spot", "symbol": symbol})
    if data and data.get('retCode') == 0:
        return data['result']['list'][0]
    return None

def get_all_tickers():
    data = public_get("/v5/market/tickers", {"category": "spot"})
    if data and data.get('retCode') == 0:
        return data['result']['list']
    return []

def get_btc_change():
    try:
        t = get_ticker("BTCUSDT")
        if t:
            return float(t['price24hPcnt']) * 100
    except:
        pass
    return 0.0

def get_5m_momentum(symbol):
    try:
        data = public_get("/v5/market/kline", {
            "category": "spot", "symbol": symbol, "interval": "5", "limit": 2
        })
        if data and data.get('retCode') == 0:
            lst = data['result']['list']
            if len(lst) < 2:
                return 0.0
            return ((float(lst[0][4]) - float(lst[1][4])) / float(lst[1][4])) * 100
    except:
        pass
    return 0.0

def get_rsi(symbol, period=14):
    try:
        data = public_get("/v5/market/kline", {
            "category": "spot", "symbol": symbol, "interval": "5", "limit": period + 1
        })
        if data and data.get('retCode') == 0:
            closes = [float(k[4]) for k in data['result']['list']]
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
        pass
    return 50.0

# ========= ORDER HELPERS =========
def get_precision_qty(symbol, amount_usd):
    try:
        instr = session.get_instruments_info(category="spot", symbol=symbol)
        step  = float(instr['result']['list'][0]['lotSizeFilter']['qtyStep'])
        t     = get_ticker(symbol)
        if not t:
            return None, None
        price = float(t['lastPrice'])
        if price <= 0:
            return None, None
        qty = math.floor((amount_usd / price) / step) * step
        qty = round(qty, 8)
        if qty <= 0 or qty * price < 10.0:
            return None, None
        return qty, price
    except Exception as e:
        print(f"Precision error {symbol}: {e}")
        return None, None

def buy(symbol, amount_usd):
    if amount_usd < MIN_TRADE_USD:
        return False, None, None
    try:
        qty, price = get_precision_qty(symbol, amount_usd)
        if not qty:
            return False, None, None
        order = session.place_order(
            category="spot", symbol=symbol,
            side="Buy", orderType="Market", qty=str(qty)
        )
        if order.get('retCode') == 0:
            return True, price, qty
        print(f"Buy failed {symbol}: {order}")
    except Exception as e:
        print(f"Buy error {symbol}: {e}")
    return False, None, None

def sell(symbol, qty_to_sell):
    try:
        qty_str = str(round(qty_to_sell, 8)).rstrip('0').rstrip('.')
        order = session.place_order(
            category="spot", symbol=symbol,
            side="Sell", orderType="Market", qty=qty_str
        )
        return order.get('retCode') == 0
    except Exception as e:
        print(f"Sell error {symbol}: {e}")
        return False

def sell_full_position(symbol, qty, entry_price):
    global daily_pnl
    for _ in range(3):
        if sell(symbol, qty):
            try:
                t = get_ticker(symbol)
                curr_price = float(t['lastPrice']) if t else entry_price
                pnl = (curr_price - entry_price) * qty
                with lock:
                    daily_pnl += pnl
                    active_trades.pop(symbol, None)
                print(f"SOLD {symbol} PnL: ${pnl:.3f} | Daily: ${daily_pnl:.3f}")
                return True
            except:
                pass
        time.sleep(1)
    print(f"Failed to sell {symbol}")
    return False

# ========= TRADE MONITOR =========
def monitor_trade(symbol):
    with lock:
        if symbol not in active_trades:
            return
        trade = active_trades[symbol].copy()
    entry      = trade['entry']
    qty        = trade['qty']
    start_time = trade['time']
    highest    = entry

    while True:
        time.sleep(5)
        with lock:
            if symbol not in active_trades:
                break
        try:
            t = get_ticker(symbol)
            if not t:
                continue
            curr = float(t['lastPrice'])
            if (curr / entry) - 1 >= (TAKE_PROFIT_PCT - 1):
                print(f"[TP] {symbol} @ ${curr:.6f}")
                sell_full_position(symbol, qty, entry)
                break
            if curr > highest:
                highest = curr
            if curr <= highest * TRAIL_SL_PCT:
                print(f"[TSL] {symbol} @ ${curr:.6f}")
                sell_full_position(symbol, qty, entry)
                break
            if time.time() - start_time > MAX_HOLD_SEC:
                print(f"[TIMEOUT] {symbol}")
                sell_full_position(symbol, qty, entry)
                break
        except Exception as e:
            print(f"Monitor error {symbol}: {e}")
        time.sleep(10)

# ========= CANDIDATE FINDER =========
def get_top_candidates():
    candidates = []
    try:
        tickers = get_all_tickers()
        temp = []
        for t in tickers:
            sym = t['symbol']
            if not sym.endswith('USDT'):
                continue
            if sym in SKIP_LARGE_CAPS:
                continue
            if 'UP' in sym or 'DOWN' in sym:
                continue
            try:
                vol = float(t['volume24h']) * float(t['lastPrice'])
            except:
                continue
            if vol < MIN_VOLUME_USD or vol > MAX_VOLUME_USD:
                continue
            temp.append((sym, vol))

        temp.sort(key=lambda x: x[1], reverse=True)
        for sym, vol in temp[:20]:
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

# ========= MAIN ENGINE =========
def engine():
    global is_running, pause_until, daily_pnl, trade_date
    print("[BOT] Engine started!")

    while is_running:
        time.sleep(15)

        today = str(date.today())
        if today != trade_date:
            print(f"[BOT] New day - PnL reset (was ${daily_pnl:.2f})")
            daily_pnl  = 0.0
            trade_date = today

        with lock:
            if pause_until > 0 and time.time() < pause_until:
                continue
            if pause_until > 0 and time.time() >= pause_until:
                pause_until = 0
                print("[BOT] Pause lifted.")
            if daily_pnl <= DAILY_LOSS_LIMIT and pause_until == 0:
                pause_until = time.time() + PAUSE_HOURS * 3600
                print(f"[BOT] Daily loss limit. Pausing {PAUSE_HOURS}h.")
                continue
            if len(active_trades) >= MAX_SLOTS:
                continue

        if get_btc_change() < BTC_MIN_CHANGE:
            print("[BOT] BTC bearish, skipping.")
            continue

        candidates = get_top_candidates()
        if not candidates:
            continue

        balance = get_wallet_balance()
        if balance < 4.0:
            print("[BOT] Balance too low, stopping.")
            break

        usable = balance * (1 - RESERVE_PCT)

        for sym, mom, vol in candidates:
            with lock:
                if len(active_trades) >= MAX_SLOTS:
                    break
                if sym in active_trades:
                    continue
                slots_left = MAX_SLOTS - len(active_trades)
                trade_size = usable / max(slots_left, 1)

            if trade_size < MIN_TRADE_USD:
                continue

            success, price, qty = buy(sym, trade_size)
            if success:
                with lock:
                    active_trades[sym] = {
                        'entry': price, 'qty': qty,
                        'invested': trade_size, 'time': time.time()
                    }
                print(f"[BUY] {sym} @ ${price} qty={qty} size=${trade_size:.2f}")
                Thread(target=monitor_trade, args=(sym,), daemon=True).start()
            time.sleep(2)

# ========= FLASK =========
app = Flask(__name__)

@app.route('/')
def home():
    bal  = get_wallet_balance()
    rows = ""
    with lock:
        for sym, t in active_trades.items():
            elapsed = int(time.time() - t['time'])
            rows += f"<tr><td>{sym}</td><td>${t['entry']:.6f}</td><td>{t['qty']}</td><td>{elapsed}s</td></tr>"
    return f"""<html><head><title>Bot</title>
    <meta http-equiv='refresh' content='15'>
    <style>body{{background:#0d0d0d;color:#e0e0e0;font-family:monospace;padding:20px}}
    h1{{color:#00ff88}}table{{border-collapse:collapse;width:100%}}
    th,td{{border:1px solid #333;padding:8px}}th{{background:#1a1a1a}}</style></head>
    <body><h1>🤖 Bybit Bot</h1>
    <p>💰 Balance: <b>${bal:.2f}</b></p>
    <p>📊 Trades: <b>{len(active_trades)}/{MAX_SLOTS}</b></p>
    <p>📅 Daily PnL: <b>${daily_pnl:.3f}</b></p>
    <table><tr><th>Symbol</th><th>Entry</th><th>Qty</th><th>Time</th></tr>
    {rows or '<tr><td colspan=4>No active trades</td></tr>'}
    </table></body></html>"""

if __name__ == "__main__":
    Thread(target=engine, daemon=True).start()
    app.run(host='0.0.0.0', port=7860)
