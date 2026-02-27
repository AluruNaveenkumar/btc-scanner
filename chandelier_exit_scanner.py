"""
Chandelier Exit Scanner — BTCUSD.P
Timeframe : 30m | ATR Period=1 | ATR Mult=2.0
Data      : Kraken public API
Alerts    : Telegram
"""

import os
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# ── Config from GitHub Secrets ──────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

SYMBOL        = "XBTUSD"
DISPLAY_NAME  = "BTCUSD.P"
TIMEFRAME_MIN = 30
TIMEFRAME_STR = "30m"
ATR_PERIOD    = 1
ATR_MULT      = 2.0
USE_CLOSE     = True
AWAIT_CONFIRM = True

# ── ATR ─────────────────────────────────────────
def compute_atr(df, period):
    close_prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - close_prev).abs(),
        (df['Low']  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

# ── Chandelier Exit ──────────────────────────────
def chandelier_exit(df):
    atr     = ATR_MULT * compute_atr(df, ATR_PERIOD)
    highest = df['Close'].rolling(ATR_PERIOD).max() if USE_CLOSE else df['High'].rolling(ATR_PERIOD).max()
    lowest  = df['Close'].rolling(ATR_PERIOD).min() if USE_CLOSE else df['Low'].rolling(ATR_PERIOD).min()

    n          = len(df)
    long_stop  = np.full(n, np.nan)
    short_stop = np.full(n, np.nan)
    direction  = np.ones(n, dtype=int)

    for i in range(ATR_PERIOD, n):
        ls      = highest.iloc[i] - atr.iloc[i]
        ls_prev = long_stop[i-1]  if not np.isnan(long_stop[i-1])  else ls
        long_stop[i] = max(ls, ls_prev) if df['Close'].iloc[i-1] > ls_prev else ls

        ss      = lowest.iloc[i] + atr.iloc[i]
        ss_prev = short_stop[i-1] if not np.isnan(short_stop[i-1]) else ss
        short_stop[i] = min(ss, ss_prev) if df['Close'].iloc[i-1] < ss_prev else ss

        ss2 = short_stop[i-1] if not np.isnan(short_stop[i-1]) else ss
        ls2 = long_stop[i-1]  if not np.isnan(long_stop[i-1])  else ls
        if   df['Close'].iloc[i] > ss2: direction[i] = 1
        elif df['Close'].iloc[i] < ls2: direction[i] = -1
        else:                           direction[i] = direction[i-1]

    df = df.copy()
    df['longStop']  = long_stop
    df['shortStop'] = short_stop
    df['dir']       = direction
    df['dir_prev']  = pd.Series(direction).shift(1).fillna(1).astype(int).values
    df['buySignal']  = (df['dir'] == 1)  & (df['dir_prev'] == -1)
    df['sellSignal'] = (df['dir'] == -1) & (df['dir_prev'] == 1)
    return df

# ── Kraken Data ──────────────────────────────────
def fetch_data():
    try:
        resp = requests.get("https://api.kraken.com/0/public/OHLC",
                            params={"pair": SYMBOL, "interval": TIMEFRAME_MIN},
                            timeout=15)
        data = resp.json()
        if data.get("error"):
            print(f"Kraken error: {data['error']}")
            return None
        key = [k for k in data["result"] if k != "last"][0]
        raw = data["result"][key]
        df  = pd.DataFrame(raw, columns=["Time","Open","High","Low","Close","VWAP","Volume","Count"])
        df["Time"] = pd.to_datetime(df["Time"].astype(int), unit="s")
        df.set_index("Time", inplace=True)
        for col in ["Open","High","Low","Close","Volume"]:
            df[col] = df[col].astype(float)
        return df[["Open","High","Low","Close","Volume"]]
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

# ── Telegram ─────────────────────────────────────
def send_telegram(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code == 200:
            print("  📱 Telegram sent!")
        else:
            print(f"  ❌ Telegram error: {r.text}")
    except Exception as e:
        print(f"  ❌ Telegram exception: {e}")

# ── Main ─────────────────────────────────────────
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now} UTC] Scanning {DISPLAY_NAME} on {TIMEFRAME_STR}...")

    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_TOKEN or CHAT_ID missing!")
        return

    df = fetch_data()
    if df is None:
        send_telegram("⚠️ <b>Scanner Error</b>\nFailed to fetch data from Kraken.")
        return

    result   = chandelier_exit(df)
    idx      = -2 if AWAIT_CONFIRM else -1
    row      = result.iloc[idx]
    bar_time = result.index[idx]
    price    = round(row['Close'], 2)
    trend    = "📈 Bullish" if row['dir'] == 1 else "📉 Bearish"

    print(f"  Price: ${price:,.2f}  |  Trend: {trend}")
    print(f"  BUY: {row['buySignal']}  |  SELL: {row['sellSignal']}")

    if row['buySignal']:
        stop = round(row['longStop'], 2)
        send_telegram(
            f"🟢 <b>BUY Signal — Chandelier Exit</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📌 Symbol    : <b>{DISPLAY_NAME}</b>\n"
            f"⏱ Timeframe : <b>{TIMEFRAME_STR}</b>\n"
            f"⚙️ ATR        : Period={ATR_PERIOD} × Mult={ATR_MULT}\n"
            f"💰 Price     : <b>${price:,.2f}</b>\n"
            f"🛡 Long Stop : <b>${stop:,.2f}</b>\n"
            f"🕐 Bar Close : {bar_time} UTC\n"
            f"━━━━━━━━━━━━━━━━━"
        )

    elif row['sellSignal']:
        stop = round(row['shortStop'], 2)
        send_telegram(
            f"🔴 <b>SELL Signal — Chandelier Exit</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📌 Symbol    : <b>{DISPLAY_NAME}</b>\n"
            f"⏱ Timeframe : <b>{TIMEFRAME_STR}</b>\n"
            f"⚙️ ATR        : Period={ATR_PERIOD} × Mult={ATR_MULT}\n"
            f"💰 Price     : <b>${price:,.2f}</b>\n"
            f"🛡 Short Stop: <b>${stop:,.2f}</b>\n"
            f"🕐 Bar Close : {bar_time} UTC\n"
            f"━━━━━━━━━━━━━━━━━"
        )

    else:
        # TEMPORARY TEST MESSAGE — confirms Telegram is working
        # Remove the send_telegram() call below once you confirm alerts work
        send_telegram(
            f"🔍 <b>Scanner Active — No Signal</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📌 Symbol    : <b>{DISPLAY_NAME}</b>\n"
            f"⏱ Timeframe : <b>{TIMEFRAME_STR}</b>\n"
            f"📊 Trend     : {trend}\n"
            f"💰 Price     : <b>${price:,.2f}</b>\n"
            f"🕐 Checked   : {bar_time} UTC\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"✅ Alerts are working! You will be\n"
            f"notified on BUY/SELL signals only."
        )
        print("  No signal — test Telegram sent.")

    print("✅ Done.")

if __name__ == "__main__":
    main()
