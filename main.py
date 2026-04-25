import time
import sys
import os
import json
from decimal import Decimal
from binance.client import Client
from binance.exceptions import BinanceAPIException
from threading import Thread, Lock

# ==============================================
# API KEYS (HALKAN KU QOR)
# ==============================================
API_KEY = "halkan_ku_qor_binance_api_key"
API_SECRET = "halkan_ku_qor_binance_secret_key"
# ==============================================

if not API_KEY or not API_SECRET:
    print("CILAD: API Keys-ka ma jiraan!")
    sys.exit()

try:
    client = Client(API_KEY, API_SECRET)
    client.ping()
    print("LOG: Xidhidhka Binance waa guul.")
except Exception as e:
    print(f"CILAD BILOWGA AH: {e}")
    sys.exit()

# ==============================================
# SETTINGS
# ==============================================
MAX_SLOTS         = 1
TRADE_SIZE        = 15.0                # 15 USDT (3 qaybood oo 5 usdt ah)
STOP_LOSS_PCT     = Decimal('0.985')    # 1.5% stop loss
TRAILING_START    = Decimal('1.02')     # 2% trailing trigger
TRAILING_DROP     = Decimal('0.003')    # 0.3% drop
BREAKEVEN_TRIGGER = Decimal('1.005')    # 0.5% breakeven
TIME_LIMIT_SEC    = 0
FEE_RATE          = Decimal('0.001')
MIN_BALANCE       = 12.0
MIN_VOLUME        = 50000
DAILY_LOSS_LIMIT  = -0.6

# Dynamic RSI Strategy
RSI_ENTRY_MIN     = 45
RSI_ENTRY_MAX     = 55
RSI_PARTIAL_SELL  = 65
RSI_FULL_EXIT     = 72
RSI_PARTIAL_PCT   = Decimal('0.50')

# Shaandhooyin 1-saac iyo 24h
MIN_CHANGE_1H     = 0.5
MAX_CHANGE_1H     = 4.0
MIN_CHANGE_24H    = -10.0
MAX_CHANGE_24H    = 8.0
MIN_VOLUME_RATIO  = 0.5

# DCA
DCA_STEPS         = 3
DCA_DROP_TRIGGER  = Decimal('0.99')

# Bounce
BOUNCE_ZONE       = Decimal('0.99')
BOUNCE_TRIGGER    = Decimal('1.004')
BOUNCE_EXIT_PCT   = Decimal('0.30')
BOUNCE_WAIT_SEC   = 120
BOUNCE_MAX_TRIES  = 3

# Price-based partial sells
PARTIAL_SELLS = [
    (Decimal('1.015'), Decimal('0.20')),
    (Decimal('1.03'),  Decimal('0.25')),
    (Decimal('1.05'),  Decimal('0.25')),
    (Decimal('1.08'),  Decimal('0.20')),
]

BLACKLIST_FILE = "blacklist.json"
DAILY_PNL_FILE = "daily_pnl.json"

active_trades = {}
lock = Lock()

# ==============================================
# CACHE (kaydi 60 seconds)
# ==============================================
cache = {
    'rsi': {},
    'ma99': {},
    'volume_ma5': {},
    '1h_change': {},
    'min_notional': {}
}
CACHE_TTL = 60

def get_cached(key, symbol, fetch_func):
    now = time.time()
    if symbol in cache[key] and now - cache[key][symbol]['ts'] < CACHE_TTL:
        return cache[key][symbol]['value']
    value = fetch_func(symbol)
    cache[key][symbol] = {'value': value, 'ts': now}
    return value

def fetch_min_notional(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for f in info['filters']:
            if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                return float(f.get('minNotional', f.get('notional', 10.0)))
        return 10.0
    except:
        return 10.0

def get_min_notional(symbol):
    return get_cached('min_notional', symbol, fetch_min_notional)

def fetch_rsi(symbol):
    try:
        klines = client.get_klines(symbol, Client.KLINE_INTERVAL_5MINUTE, limit=30)
        if len(klines) < 14:
            return 50
        closes = [float(k[4]) for k in klines]
        diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d for d in diffs if d > 0]
        losses = [-d for d in diffs if d < 0]
        avg_gain = sum(gains) / len(diffs) if gains else 0
        avg_loss = sum(losses) / len(diffs) if losses else 0
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except:
        return 50

def get_rsi(symbol):
    return get_cached('rsi', symbol, fetch_rsi)

def fetch_1h_change(symbol):
    try:
        klines = client.get_klines(symbol, Client.KLINE_INTERVAL_1HOUR, limit=2)
        if len(klines) < 2:
            return 0.0
        old = float(klines[0][4])
        new = float(klines[1][4])
        return ((new - old) / old) * 100
    except:
        return 0.0

def get_1h_change(symbol):
    return get_cached('1h_change', symbol, fetch_1h_change)

def fetch_ma99(symbol):
    try:
        klines = client.get_klines(symbol, Client.KLINE_INTERVAL_1HOUR, limit=100)
        if len(klines) < 99:
            return None
        closes = [float(k[4]) for k in klines[-99:]]
        return sum(closes) / 99
    except:
        return None

def get_ma99(symbol):
    return get_cached('ma99', symbol, fetch_ma99)

def fetch_volume_ma5(symbol):
    try:
        klines = client.get_klines(symbol, Client.KLINE_INTERVAL_1HOUR, limit=5)
        if len(klines) < 5:
            return None
        volumes = [float(k[5]) for k in klines]
        return sum(volumes) / 5
    except:
        return None

def get_volume_ma5(symbol):
    return get_cached('volume_ma5', symbol, fetch_volume_ma5)

# ==============================================
# BALANCE & HELPERS
# ==============================================
def get_usdt_balance():
    try:
        bal = client.get_asset_balance(asset='USDT')
        return float(bal['free'])
    except:
        return 0.0

def has_sufficient_balance(required_usdt):
    bal = get_usdt_balance()
    return bal >= required_usdt * 1.05

def get_slot_size():
    return TRADE_SIZE / DCA_STEPS

def has_open_sell_orders():
    try:
        orders = client.get_open_orders()
        return any(o['side'] == 'SELL' for o in orders)
    except:
        return True

# ==============================================
# BLACKLIST
# ==============================================
def load_blacklist():
    if not os.path.exists(BLACKLIST_FILE):
        return {}
    with open(BLACKLIST_FILE, 'r') as f:
        data = json.load(f)
    now = time.time()
    return {k: v for k, v in data.items() if now - v < 86400}

def add_to_blacklist(symbol):
    bl = load_blacklist()
    bl[symbol] = time.time()
    with open(BLACKLIST_FILE, 'w') as f:
        json.dump(bl, f)
    print(f"LOG: {symbol} blacklist (24 saac).")

# ==============================================
# DAILY PNL
# ==============================================
def load_daily_pnl():
    today = time.strftime("%Y-%m-%d")
    if os.path.exists(DAILY_PNL_FILE):
        with open(DAILY_PNL_FILE, 'r') as f:
            data = json.load(f)
        if data.get('date') == today:
            return data.get('pnl', 0.0)
    return 0.0

def update_daily_pnl(amount):
    today = time.strftime("%Y-%m-%d")
    pnl = load_daily_pnl() + amount
    with open(DAILY_PNL_FILE, 'w') as f:
        json.dump({'date': today, 'pnl': round(pnl, 6)}, f)
    print(f"LOG: Daily PNL: ${pnl:.4f}")
    if pnl <= DAILY_LOSS_LIMIT:
        print(f"!!! DAILY LOSS LIMIT (${pnl:.2f}) - jooji maalinta !!!")
        return True
    return False

# ==============================================
# SELL MARKET
# ==============================================
def sell_market(symbol, qty):
    try:
        qty_str = f"{float(qty):.6f}".rstrip('0').rstrip('.')
        order = client.order_market_sell(symbol=symbol, quantity=qty_str)
        print(f"SELL: {symbol} | Qty: {qty_str}")
        return order
    except Exception as e:
        print(f"CILAD IIBINTA {symbol}: {e}")
        return None

# ==============================================
# BOUNCE DETECTOR
# ==============================================
def wait_for_bounce(symbol, entry_price, current_price, remaining_qty):
    bounce_low = current_price
    bounce_tries = 0
    bounce_sold = False
    print(f"BOUNCE WATCH: {symbol} | Price: {current_price:.8f}")
    while bounce_tries < BOUNCE_MAX_TRIES:
        time.sleep(10)
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            now_price = Decimal(ticker['price'])
            if now_price < bounce_low:
                bounce_low = now_price
            if now_price >= bounce_low * BOUNCE_TRIGGER and not bounce_sold:
                sell_qty = remaining_qty * BOUNCE_EXIT_PCT
                if sell_qty > 0:
                    order = sell_market(symbol, sell_qty)
                    if order:
                        loss = float((now_price - entry_price) * sell_qty)
                        update_daily_pnl(loss)
                        print(f"BOUNCE EXIT: {symbol} | 30% iib | PNL: ${loss:.4f}")
                        bounce_sold = True
                        return sell_qty, now_price, True
        except Exception as e:
            print(f"BOUNCE ERROR {symbol}: {e}")
        bounce_tries += 1
        if bounce_tries < BOUNCE_MAX_TRIES:
            print(f"BOUNCE: {symbol} | {bounce_tries}/{BOUNCE_MAX_TRIES} try | sug...")
    return Decimal('0'), current_price, False

# ==============================================
# MONITOR
# ==============================================
def monitor_trade(symbol, buy_list, slot_usdt):
    total_qty = sum(Decimal(str(qty)) for _, qty in buy_list)
    total_cost = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty in buy_list)
    entry_price = total_cost / total_qty if total_qty > 0 else Decimal('0')
    entry_price = entry_price * (1 + FEE_RATE)

    remaining_qty = total_qty
    highest_price = entry_price
    start_time = time.time()
    sl_price = entry_price * STOP_LOSS_PCT
    bounce_zone_price = entry_price * BOUNCE_ZONE
    trailing_activated = False
    breakeven_activated = False
    partial_sold_levels = []
    in_bounce_check = False
    dca_done = len(buy_list) >= DCA_STEPS
    dca_last_buy_price = Decimal(str(buy_list[-1][0])) if buy_list else entry_price
    rsi_partial_sold = False

    print(f"MONITOR: {symbol} | Entry(+fee): {entry_price:.8f} | Qty: {total_qty:.6f}")
    print(f"  SL: {sl_price:.8f} | Bounce: {bounce_zone_price:.8f} | DCA: {len(buy_list)}/{DCA_STEPS}")

    while True:
        time.sleep(5)
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            curr_price = Decimal(ticker['price'])
            if curr_price > highest_price:
                highest_price = curr_price

            # DCA
            if not dca_done:
                if curr_price <= dca_last_buy_price * DCA_DROP_TRIGGER:
                    min_notional = get_min_notional(symbol)
                    if slot_usdt >= min_notional:
                        try:
                            order = client.order_market_buy(symbol=symbol, quoteOrderQty=round(slot_usdt, 2))
                            if order:
                                exec_qty = float(order['executedQty'])
                                exec_price = float(order['fills'][0]['price'])
                                print(f"DCA BUY: {symbol} step {len(buy_list)+1} | {exec_price} | {exec_qty}")
                                buy_list.append((Decimal(str(exec_price)), Decimal(str(exec_qty))))
                                dca_last_buy_price = Decimal(str(exec_price))
                                total_qty = sum(Decimal(str(q)) for _, q in buy_list)
                                total_cost = sum(Decimal(str(p)) * Decimal(str(q)) for p, q in buy_list)
                                entry_price = total_cost / total_qty * (1 + FEE_RATE)
                                remaining_qty = total_qty
                                sl_price = entry_price * STOP_LOSS_PCT
                                bounce_zone_price = entry_price * BOUNCE_ZONE
                                print(f"DCA UPDATED: Entry {entry_price:.8f} | Qty {remaining_qty:.6f}")
                                if len(buy_list) >= DCA_STEPS:
                                    dca_done = True
                        except Exception as e:
                            print(f"DCA ERROR {symbol}: {e}")

            # Breakeven
            if not breakeven_activated and curr_price >= entry_price * BREAKEVEN_TRIGGER:
                sl_price = entry_price
                breakeven_activated = True
                print(f"BREAKEVEN: {symbol} | sl = {sl_price:.8f}")

            # Price-based partial sells (only if RSI partial not sold)
            if not rsi_partial_sold:
                for level, pct in PARTIAL_SELLS:
                    if level not in partial_sold_levels and curr_price >= entry_price * level:
                        sell_qty = remaining_qty * pct
                        if sell_qty > 0:
                            order = sell_market(symbol, sell_qty)
                            if order:
                                profit = float((curr_price - entry_price) * sell_qty)
                                update_daily_pnl(profit)
                                remaining_qty -= sell_qty
                                partial_sold_levels.append(level)
                                print(f"PRICE PARTIAL: {symbol} {float(pct)*100:.0f}% | +${profit:.4f}")

            # Dynamic RSI exits
            current_rsi = get_rsi(symbol)
            if current_rsi >= RSI_FULL_EXIT and remaining_qty > 0:
                order = sell_market(symbol, remaining_qty)
                if order:
                    profit = float((curr_price - entry_price) * remaining_qty)
                    update_daily_pnl(profit)
                    print(f"RSI FULL EXIT: {symbol} RSI={current_rsi:.1f} | +${profit:.4f}")
                break
            elif current_rsi >= RSI_PARTIAL_SELL and not rsi_partial_sold and remaining_qty > 0:
                sell_qty = remaining_qty * RSI_PARTIAL_PCT
                if sell_qty > 0:
                    order = sell_market(symbol, sell_qty)
                    if order:
                        profit = float((curr_price - entry_price) * sell_qty)
                        update_daily_pnl(profit)
                        remaining_qty -= sell_qty
                        rsi_partial_sold = True
                        print(f"RSI PARTIAL: {symbol} RSI={current_rsi:.1f} | 50% | +${profit:.4f}")

            # Trailing stop
            if not trailing_activated and highest_price >= entry_price * TRAILING_START:
                trailing_activated = True
                print(f"TRAILING ON: {symbol} | High: {highest_price:.8f}")
            if trailing_activated and remaining_qty > 0:
                if curr_price <= highest_price * (1 - TRAILING_DROP):
                    order = sell_market(symbol, remaining_qty)
                    if order:
                        profit = float((curr_price - entry_price) * remaining_qty)
                        update_daily_pnl(profit)
                        print(f"TRAILING EXIT: {symbol} | +${profit:.4f}")
                    break

            # Bounce zone
            if remaining_qty > 0 and not in_bounce_check:
                if sl_price < curr_price <= bounce_zone_price:
                    print(f"BOUNCE ZONE: {symbol}")
                    in_bounce_check = True
                    qty_sold, _, bounced = wait_for_bounce(symbol, entry_price, curr_price, remaining_qty)
                    if bounced:
                        remaining_qty -= qty_sold
                        in_bounce_check = False
                        continue
                    else:
                        if remaining_qty > 0:
                            ticker2 = client.get_symbol_ticker(symbol=symbol)
                            now_p = Decimal(ticker2['price'])
                            order = sell_market(symbol, remaining_qty)
                            if order:
                                loss = float((now_p - entry_price) * remaining_qty)
                                update_daily_pnl(loss)
                                add_to_blacklist(symbol)
                                print(f"STOP LOSS (bounce fail): {symbol} | ${loss:.4f}")
                        break
                elif curr_price <= sl_price:
                    order = sell_market(symbol, remaining_qty)
                    if order:
                        loss = float((curr_price - entry_price) * remaining_qty)
                        update_daily_pnl(loss)
                        add_to_blacklist(symbol)
                        print(f"STOP LOSS EXIT: {symbol} | ${loss:.4f}")
                    break

            # Time limit
            if TIME_LIMIT_SEC > 0 and (time.time() - start_time) > TIME_LIMIT_SEC:
                if remaining_qty > 0:
                    order = sell_market(symbol, remaining_qty)
                    if order:
                        pnl = float((curr_price - entry_price) * remaining_qty)
                        update_daily_pnl(pnl)
                        print(f"TIME LIMIT: {symbol} | ${pnl:.4f}")
                break

        except Exception as e:
            print(f"MONITOR ERROR {symbol}: {e}")
            time.sleep(10)

    with lock:
        if symbol in active_trades:
            del active_trades[symbol]
    print(f"MONITOR: {symbol} dhammaaday.")

# ==============================================
# ENGINE
# ==============================================
def engine():
    print("=" * 60)
    print("  BINANCE BOT 100/100 — Dynamic RSI | DCA | Bounce")
    print("  RSI Entry: 45-55 | Partial: 65 | Full Exit: 72+")
    print("  Trade: $15 (3x$5) | SL: 1.5% | Daily Loss: $0.60")
    print("=" * 60)

    while True:
        try:
            if load_daily_pnl() <= DAILY_LOSS_LIMIT:
                print("Daily loss limit. Seexo 1 saac...")
                time.sleep(3600)
                continue

            with lock:
                if len(active_trades) >= MAX_SLOTS:
                    time.sleep(5)
                    continue

            balance = get_usdt_balance()
            if balance < MIN_BALANCE:
                print(f"LOG: Balance ${balance:.2f} < ${MIN_BALANCE}")
                time.sleep(60)
                continue

            if has_open_sell_orders():
                time.sleep(30)
                continue

            slot_size = get_slot_size()
            if slot_size < 5.0:
                time.sleep(60)
                continue

            if not has_sufficient_balance(slot_size):
                time.sleep(60)
                continue

            blacklist = load_blacklist()

            # HAL API CALL — dhammaan tickers
            all_tickers = client.get_ticker()
            candidates = []
            for t in all_tickers:
                if not t['symbol'].endswith('USDT'):
                    continue
                try:
                    vol      = float(t['quoteVolume'])
                    price    = float(t['lastPrice'])
                    change24 = float(t['priceChangePercent'])
                    if vol > MIN_VOLUME and price > 0:
                        candidates.append((t['symbol'], vol, price, change24))
                except:
                    continue

            top_candidates = []
            for symbol, vol, price, change24 in candidates:
                if symbol in blacklist or symbol in active_trades:
                    continue
                # 24h filter
                if not (MIN_CHANGE_24H <= change24 <= MAX_CHANGE_24H):
                    continue
                # 1h filter
                ch1h = get_1h_change(symbol)
                if not (MIN_CHANGE_1H <= ch1h <= MAX_CHANGE_1H):
                    continue
                # MA99
                ma99 = get_ma99(symbol)
                if ma99 is not None and price <= ma99:
                    continue
                # Volume ratio
                vol_ma5 = get_volume_ma5(symbol)
                if vol_ma5 is not None and vol < vol_ma5 * MIN_VOLUME_RATIO:
                    continue
                # Dynamic RSI entry
                rsi = get_rsi(symbol)
                if not (RSI_ENTRY_MIN <= rsi <= RSI_ENTRY_MAX):
                    print(f"SKIP: {symbol} RSI={rsi:.1f} (45-55 ma ahan)")
                    continue
                # Min notional
                min_notional = get_min_notional(symbol)
                if slot_size < min_notional:
                    continue
                top_candidates.append((symbol, ch1h, rsi, vol, price))

            top_candidates.sort(key=lambda x: x[1], reverse=True)

            for symbol, ch1h, rsi, vol, price in top_candidates[:20]:
                print(f"\nTARGET: {symbol} | 1h:{ch1h:.2f}% | RSI:{rsi:.1f}")
                try:
                    order = client.order_market_buy(
                        symbol=symbol,
                        quoteOrderQty=round(slot_size, 2)
                    )
                    if order:
                        exec_qty   = float(order['executedQty'])
                        exec_price = float(order['fills'][0]['price']) if order['fills'] else price
                        print(f"BUY: {symbol} | {exec_price} | {exec_qty} | ${slot_size:.2f}")
                        buy_list = [(Decimal(str(exec_price)), Decimal(str(exec_qty)))]
                        with lock:
                            active_trades[symbol] = True
                        Thread(
                            target=monitor_trade,
                            args=(symbol, buy_list, slot_size),
                            daemon=True
                        ).start()
                        break
                except BinanceAPIException as e:
                    print(f"ORDER ERROR {symbol}: {e}")
                except Exception as e:
                    print(f"ORDER ERROR {symbol}: {e}")

            time.sleep(30)

        except Exception as e:
            print(f"ENGINE FATAL: {e}")
            time.sleep(10)

if __name__ == "__main__":
    engine()
