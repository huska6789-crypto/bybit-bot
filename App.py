import os
from pybit.unified_trading import HTTP
from flask import Flask

session = HTTP(
    testnet=False,
    api_key=os.environ.get('BYBIT_KEY'),
    api_secret=os.environ.get('BYBIT_SECRET'),
)

def get_balance():
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        if resp['retCode'] == 0:
            for account in resp['result']['list']:
                total_eq = account.get('totalEquity', 0.0)
                if total_eq > 0:
                    return f"${total_eq:.2f}"
                for coin in account.get('coin', []):
                    if coin['coin'] == 'USDT':
                        bal = coin.get('walletBalance', 0.0)
                        if bal > 0:
                            return f"${bal:.2f}"
        return "Error: No balance"
    except Exception as e:
        return f"Error: {str(e)}"

app = Flask('')
@app.route('/')
def home():
    bal = get_balance()
    return f"<h1>Bot Running</h1><p>Balance: {bal}</p>"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=7860)
