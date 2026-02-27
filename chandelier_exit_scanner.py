"""
============================================================
  Chandelier Exit Scanner — Telegram Alert Bot
  Replicates Pine Script v6 Chandelier Exit logic exactly
  Deployed on Render.com (free, 24/7)
============================================================
"""

import os
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# ─────────────────────────────────────────────
#   CONFIG — reads from Render Environment Variables
#   (so your token is never hardcoded in the file)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

SYMBOL        = "XBTUSD"     # Kraken symbol for BTC/USD
DISPLAY_NAME  = "BTCUSD.P"
TIMEFRAME_MIN = 30           # 30-minute candles
TIMEFRAME_STR = "30m"

ATR_PERIOD        = 1
ATR_MULT          = 2.0
USE_CLOSE         = True
AWAIT_BAR_CONFIRM = True
CHECK_INTERVAL    = 1800     # 30 minutes in seconds


# ─────────────────────────────────────────────
#   ATR CALCULATION
# ─────────────────────────────────────────────
def compute_atr(df, period):
    high       = df['High']
    low        = df['Low']
    close_prev = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


# ─────────────────────────────────────────────
#   CHANDELIER EXIT (exact Pine Script logic)
# ─────────────────────────────────────────────
def chandelier_exit(df):
    atr = ATR_MULT * compute_atr(df, ATR_PERIOD)

    if USE_CLOSE:
        highest = df['Close'].rolling(ATR_PERIOD).max()
        lowest  = df['Close'].rolling(ATR_PERIOD).min()
    else:
        highest = df['High'].rolling(ATR_PERIOD).max()
        lowest  = df['Low'].rolling(ATR_PERIOD).min()

    n          = len(df)
    long_stop  = np.full(n, np.nan)
    short_stop = np.full(n, np.nan)
    direction  = np.ones(n, dtype=int)

    for i in range(ATR_PERIOD, n):
        ls      = highest.iloc[i] - atr.iloc[i]
        ls_prev = long_stop[i-1] if not np.isnan(long_stop[i-1]) else ls
        long_stop[i] = max(ls, ls_prev) if df['Close'].iloc[i-1] > ls_prev else ls

        ss      = lowest.iloc[i] + atr.iloc[i]
        ss_prev = short_stop[i-1] if not np.isnan(short_stop[i-1]) else ss
        short_stop[i] = min(ss, ss_prev) if df['Close'].iloc[i-1] < ss_prev else ss

        ss_prev2 = short_stop[i-1] if not np.isnan(short_stop[i-1]) else ss
        ls_prev2 = long_stop[i-1]  if not np.isnan(long_stop[i-1])  else ls
        if   df['Close'].iloc[i] > ss_prev2: direction[i] = 1
        elif df['Close'].iloc[i] < ls_prev2: direction[i] = -1
        else:                                direction[i] = direction[i-1]

    result              = df.copy()
    result['longStop']  = long_stop
    result['shortStop'] = short_stop
    result['dir']       = direction
    result['dir_prev']  = pd.Series(direction).shift(1).fillna(1).astype(int).values
    result['buySignal']  = (result['dir'] == 1)  & (result['dir_prev'] == -1)
    result['sellSignal'] = (result['dir'] == -1) & (result['dir_prev'] == 1)
    return result


# ─────────────────────────────────────────────
#   DATA FETCH — Kraken public API
# ─────────────────────────────────────────────
def fetch_data():
    url    = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": SYMBOL, "interval": TIMEFRAME_MIN}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            print(f"[Kraken Error] {data['error']}")
            return None
        key = [k for k in data["result"] if k != "last"][0]
        raw = data["result"][key]
        if len(raw) < ATR_PERIOD + 5:
            print(f"Not enough candles: {len(raw)}")
            return None
        df = pd.DataFrame(raw, columns=[
            "Time","Open","High","Low","Close","VWAP","Volume","Count"
        ])
        df["Time"] = pd.to_datetime(df["Time"].astype(int), unit="s")
        df.set_index("Time", inplace=True)
        for col in ["Open","High","Low","Close","Volume"]:
            df[col] = df[col].astype(float)
        return df[["Open","High","Low","Close","Volume"]]
    except Exception as e:
        print(f"[Kraken fetch error] {e}")
        return None


# ─────────────────────────────────────────────
#   TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code != 200:
            print(f"[Telegram Error] {resp.text}")
        else:
            print("  📱 Telegram alert sent!")
    except Exception as e:
        print(f"[Telegram Exception] {e}")


# ─────────────────────────────────────────────
#   SIGNAL CHECK
# ─────────────────────────────────────────────
def check_signals():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n[{now} UTC] Scanning {DISPLAY_NAME} on {TIMEFRAME_STR}...")

    df = fetch_data()
    if df is None:
        print("  ⚠️  Data fetch failed. Retrying next cycle.")
        return

    result   = chandelier_exit(df)
    idx      = -2 if AWAIT_BAR_CONFIRM else -1
    row      = result.iloc[idx]
    bar_time = result.index[idx]
    price    = round(row['Close'], 2)

    if row['buySignal']:
        stop = round(row['longStop'], 2)
        msg  = (
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
        print(f"  ✅ BUY  @ ${price:,.2f}  | LongStop: ${stop:,.2f}")
        send_telegram(msg)

    elif row['sellSignal']:
        stop = round(row['shortStop'], 2)
        msg  = (
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
        print(f"  🔴 SELL @ ${price:,.2f}  | ShortStop: ${stop:,.2f}")
        send_telegram(msg)

    else:
        trend = "📈 Bullish" if row['dir'] == 1 else "📉 Bearish"
        print(f"  — No signal  ({trend})  Price: ${price:,.2f}")


# ─────────────────────────────────────────────
#   MAIN LOOP — runs forever on Render.com
# ─────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Chandelier Exit Scanner — BTCUSD.P")
    print(f"  Symbol    : {DISPLAY_NAME}")
    print(f"  Timeframe : {TIMEFRAME_STR}")
    print(f"  ATR       : Period={ATR_PERIOD}, Mult={ATR_MULT}")
    print(f"  Source    : Kraken (public API)")
    print("=" * 50)

    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("\n⚠️  TELEGRAM_TOKEN or CHAT_ID not set in environment variables!\n")
        return

    # Single run — GitHub Actions calls this every 30 min via cron schedule
    check_signals()
    print("\n✅ Scan complete.")


if __name__ == "__main__":
    main()
