#!/usr/bin/env python3
"""
HyperLiquid Momentum Breakout Engine — WebSocket Edition
Real-time signal detection + comprehensive QA tracking.

Architecture:
- Single WebSocket connection to HyperLiquid
- Subscribed to allMids (live prices) + 15m candles (BTC, ETH, SOL, HYPE)
- Breakout detection: 20-period high/low, volume > 2x average, confidence scoring
- QA engine: grades every signal, tracks outcomes over 24h, computes metrics
- Telegram notifications for live signals
- FastAPI /api/qa endpoint served from shared JSON DB
"""

import os
import json
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from collections import defaultdict
import websockets

# ─── Config ─────────────────────────────────────────────────────────────────
WS_URL = "wss://api.hyperliquid.xyz/ws"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1948260663")
SIGNAL_COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "1800"))   # 30 min
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", "2.0"))
LOOKBACK_PERIODS = int(os.getenv("LOOKBACK_PERIODS", "20"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "65.0"))
MAX_FUNDING_EXTREME = float(os.getenv("MAX_FUNDING_EXTREME", "0.01"))

SIGNALS_FILE = "/opt/hyperliquid-mcp/signals/recent_signals.json"
QA_DB_FILE = "/opt/hyperliquid-mcp/signals/signal_db.json"

WATCH_COINS = ["BTC", "ETH", "SOL", "HYPE"]

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/hyperliquid-signals.log")
    ]
)
logger = logging.getLogger("momentum_engine")

# ─── State ──────────────────────────────────────────────────────────────────
last_signal_time: Dict[str, float] = {}
price_history: Dict[str, float] = {}          # coin -> latest mid price
candle_history: Dict[str, List[Dict]] = defaultdict(list)  # coin -> [candle, ...]
signal_db_lock = asyncio.Lock()


@dataclass
class Signal:
    coin: str
    direction: str           # LONG or SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    volume_ratio: float
    funding_rate: float
    timestamp: str
    reason: str
    grade: str = ""          # A/B/C/D
    outcome: str = "pending" # win / loss / expired
    outcome_price: float = 0.0
    outcome_time: str = ""
    r_value: float = 0.0     # how many R's the signal made


# ─── QA Helpers ───────────────────────────────────────────────────────────────

def compute_grade(confidence: float, volume_ratio: float) -> str:
    if confidence > 80 and volume_ratio > 3.0:
        return "A"
    if confidence >= 70 and volume_ratio >= 2.5:
        return "B"
    if confidence >= 65 and volume_ratio >= 2.0:
        return "C"
    return "D"


def load_qa_db() -> List[Dict[str, Any]]:
    if not os.path.exists(QA_DB_FILE):
        return []
    try:
        with open(QA_DB_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load QA DB: {e}")
        return []


async def save_qa_db(db: List[Dict[str, Any]]):
    async with signal_db_lock:
        try:
            with open(QA_DB_FILE, "w") as f:
                json.dump(db, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write QA DB: {e}")


async def append_signal(signal: Signal):
    db = load_qa_db()
    record = asdict(signal)
    db.append(record)
    await save_qa_db(db)
    # Also update recent_signals.json (last 100)
    recent = [r for r in db if r.get("outcome") == "pending"][-50:] + \
             [r for r in db if r.get("outcome") != "pending"][-50:]
    try:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(recent[-100:], f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write recent_signals.json: {e}")


async def update_signal_outcome(coin: str, timestamp: str, outcome: str, outcome_price: float, r_value: float):
    db = load_qa_db()
    for record in db:
        if record.get("coin") == coin and record.get("timestamp") == timestamp:
            record["outcome"] = outcome
            record["outcome_price"] = round(outcome_price, 4)
            record["outcome_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            record["r_value"] = round(r_value, 2)
            break
    await save_qa_db(db)
    # Refresh recent_signals.json
    recent = [r for r in db if r.get("outcome") == "pending"][-50:] + \
             [r for r in db if r.get("outcome") != "pending"][-50:]
    try:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(recent[-100:], f, indent=2)
    except Exception as e:
        logger.error(f"Failed to update recent_signals.json: {e}")


# ─── Signal Monitor (24h outcome tracking) ─────────────────────────────────────

async def monitor_signal_outcome(signal: Signal):
    """Watch price for 24h after signal. Determine win/loss/expired."""
    start_time = time.time()
    end_time = start_time + 24 * 3600  # 24 hours
    tp = signal.take_profit
    sl = signal.stop_loss
    direction = signal.direction
    entry = signal.entry_price

    # Calculate R (risk amount in price terms)
    risk = abs(entry - sl)
    if risk == 0:
        risk = entry * 0.01  # fallback 1%

    while time.time() < end_time:
        await asyncio.sleep(10)  # check every 10s
        current_price = price_history.get(signal.coin, 0)
        if current_price == 0:
            continue

        if direction == "LONG":
            if current_price >= tp:
                r_value = (current_price - entry) / risk
                await update_signal_outcome(signal.coin, signal.timestamp, "win", current_price, r_value)
                logger.info(f"✅ WIN: {signal.coin} LONG hit TP @ ${current_price:,.2f} (+{r_value:.1f}R)")
                return
            if current_price <= sl:
                r_value = (current_price - entry) / risk
                await update_signal_outcome(signal.coin, signal.timestamp, "loss", current_price, r_value)
                logger.info(f"❌ LOSS: {signal.coin} LONG hit SL @ ${current_price:,.2f} ({r_value:.1f}R)")
                return
        else:  # SHORT
            if current_price <= tp:
                r_value = (entry - current_price) / risk
                await update_signal_outcome(signal.coin, signal.timestamp, "win", current_price, r_value)
                logger.info(f"✅ WIN: {signal.coin} SHORT hit TP @ ${current_price:,.2f} (+{r_value:.1f}R)")
                return
            if current_price >= sl:
                r_value = (entry - current_price) / risk
                await update_signal_outcome(signal.coin, signal.timestamp, "loss", current_price, r_value)
                logger.info(f"❌ LOSS: {signal.coin} SHORT hit SL @ ${current_price:,.2f} ({r_value:.1f}R)")
                return

    # Expired — neither TP nor SL hit in 24h
    current_price = price_history.get(signal.coin, entry)
    if direction == "LONG":
        r_value = (current_price - entry) / risk
    else:
        r_value = (entry - current_price) / risk
    await update_signal_outcome(signal.coin, signal.timestamp, "expired", current_price, r_value)
    logger.info(f"⏱ EXPIRED: {signal.coin} {direction} after 24h @ ${current_price:,.2f} ({r_value:.1f}R)")


# ─── Breakout Detection ───────────────────────────────────────────────────────

def calculate_levels(candles: List[Dict]) -> Optional[Dict]:
    if len(candles) < LOOKBACK_PERIODS + 1:
        return None
    recent = candles[-(LOOKBACK_PERIODS + 1):-1]
    current = candles[-1]
    highs = [float(c["h"]) for c in recent]
    lows = [float(c["l"]) for c in recent]
    volumes = [float(c["v"]) for c in recent]
    return {
        "high_20": max(highs),
        "low_20": min(lows),
        "avg_volume": sum(volumes) / len(volumes),
        "current_high": float(current["h"]),
        "current_low": float(current["l"]),
        "current_close": float(current["c"]),
        "current_volume": float(current["v"]),
        "current_open": float(current["o"]),
    }


def detect_signal(coin: str, candles: List[Dict], funding: float) -> Optional[Signal]:
    levels = calculate_levels(candles)
    if not levels:
        return None

    now = time.time()
    if coin in last_signal_time and (now - last_signal_time[coin]) < SIGNAL_COOLDOWN:
        return None

    volume_ratio = levels["current_volume"] / levels["avg_volume"] if levels["avg_volume"] > 0 else 0
    if volume_ratio < VOLUME_MULTIPLIER:
        return None

    if abs(funding) > MAX_FUNDING_EXTREME:
        return None

    current = levels["current_close"]
    high_20 = levels["high_20"]
    low_20 = levels["low_20"]
    direction = None
    reason = ""

    if current > high_20:
        direction = "LONG"
        stop_loss = low_20 * 0.998
        take_profit = current + (current - stop_loss) * 2.5
        confidence = min(95, 60 + volume_ratio * 10 + (current - high_20) / high_20 * 1000)
        reason = f"Broke above 20-period high (${high_20:,.2f}) on {volume_ratio:.1f}x volume"
    elif current < low_20:
        direction = "SHORT"
        stop_loss = high_20 * 1.002
        take_profit = current - (stop_loss - current) * 2.5
        confidence = min(95, 60 + volume_ratio * 10 + (low_20 - current) / low_20 * 1000)
        reason = f"Broke below 20-period low (${low_20:,.2f}) on {volume_ratio:.1f}x volume"

    if direction and confidence >= MIN_CONFIDENCE:
        last_signal_time[coin] = now
        grade = compute_grade(confidence, volume_ratio)
        return Signal(
            coin=coin,
            direction=direction,
            entry_price=round(current, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            confidence=round(confidence, 1),
            volume_ratio=round(volume_ratio, 1),
            funding_rate=round(funding * 100, 4),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            reason=reason,
            grade=grade,
        )
    return None


# ─── Telegram ────────────────────────────────────────────────────────────────

def format_telegram_message(signal: Signal) -> str:
    emoji = "🟢" if signal.direction == "LONG" else "🔴"
    funding_emoji = "⚠️" if abs(signal.funding_rate) > 0.05 else "✅"
    rr = abs((signal.take_profit - signal.entry_price) / (signal.entry_price - signal.stop_loss))
    return f"""{emoji} <b>MOMENTUM BREAKOUT — {signal.direction}</b> {emoji}

<b>Coin:</b> <code>{signal.coin}</code>
<b>Time:</b> {signal.timestamp}
<b>Grade:</b> {signal.grade}

📊 <b>Signal Details</b>
├ <b>Entry:</b> ${signal.entry_price:,.2f}
├ <b>Stop Loss:</b> ${signal.stop_loss:,.2f}
├ <b>Take Profit:</b> ${signal.take_profit:,.2f}
├ <b>Risk:Reward:</b> 1:{rr:.1f}
└ <b>Confidence:</b> {signal.confidence}%

📈 <b>Context</b>
├ <b>Volume:</b> {signal.volume_ratio}x average
├ <b>Funding:</b> {signal.funding_rate}% {funding_emoji}
└ <b>Reason:</b> {signal.reason}

💡 <b>Action</b>
Consider opening a <b>{signal.direction}</b> position at market or limit near entry.
Set stop at ${signal.stop_loss:,.2f} and take profit at ${signal.take_profit:,.2f}.

⚠️ <b>Risk Warning</b>
Only risk 1-2% of portfolio per trade. This is a signal, not financial advice.
"""


async def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No TELEGRAM_BOT_TOKEN configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False


# ─── WebSocket Engine ─────────────────────────────────────────────────────────

class WebSocketEngine:
    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.reconnect_delay = 5
        self.funding_rates: Dict[str, float] = {}
        self.running = True
        self.ping_task: Optional[asyncio.Task] = None
        self.funding_task: Optional[asyncio.Task] = None

    async def subscribe(self):
        """Send all subscription messages."""
        if not self.ws:
            return
        # allMids — live prices for all coins
        await self.ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
        # Candle feeds for watched coins
        for coin in WATCH_COINS:
            await self.ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "candle", "coin": coin, "interval": "15m"}
            }))
            await asyncio.sleep(0.1)
        logger.info(f"Subscribed to allMids + candles for {', '.join(WATCH_COINS)}")

    async def fetch_funding_rates(self):
        """Background task: fetch funding rates every 5 minutes via REST."""
        import httpx
        while self.running:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    for coin in WATCH_COINS:
                        try:
                            resp = await client.post(
                                "https://api.hyperliquid.xyz/info",
                                json={
                                    "type": "fundingHistory",
                                    "coin": coin,
                                    "startTime": int((time.time() - 3600) * 1000)
                                }
                            )
                            resp.raise_for_status()
                            data = resp.json()
                            if data:
                                self.funding_rates[coin] = float(data[-1].get("fundingRate", 0))
                        except Exception as e:
                            logger.debug(f"Funding fetch error for {coin}: {e}")
                await asyncio.sleep(300)  # 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Funding loop error: {e}")
                await asyncio.sleep(60)

    async def handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        channel = msg.get("channel", "")
        data = msg.get("data", {})

        if channel == "allMids":
            # data: {"mids": {"BTC": "123.45", ...}}
            mids = data.get("mids", {})
            for coin, price in mids.items():
                try:
                    price_history[coin] = float(price)
                except (ValueError, TypeError):
                    continue

        elif channel == "candle":
            # data: {"s": "BTC", "i": "15m", "o": "123", "h": "124", "l": "122", "c": "123.5", "v": "100", "t": 1234567890000}
            if not isinstance(data, dict):
                return
            coin = data.get("s", "")
            if not coin:
                return
            try:
                candle = {
                    "o": float(data["o"]),
                    "h": float(data["h"]),
                    "l": float(data["l"]),
                    "c": float(data["c"]),
                    "v": float(data["v"]),
                    "t": int(data["t"]),
                }
            except (KeyError, ValueError, TypeError):
                return
            # Append, keep max 50 candles
            candle_history[coin].append(candle)
            if len(candle_history[coin]) > 50:
                candle_history[coin] = candle_history[coin][-50:]
            # Try to detect signal on this coin
            await self.try_detect(coin)

    async def try_detect(self, coin: str):
        candles = candle_history.get(coin, [])
        if len(candles) < LOOKBACK_PERIODS + 1:
            return
        funding = self.funding_rates.get(coin, 0.0)
        signal = detect_signal(coin, candles, funding)
        if signal:
            msg = format_telegram_message(signal)
            success = await send_telegram(msg)
            if success:
                logger.info(f"📤 SIGNAL: {signal.direction} {signal.coin} @ ${signal.entry_price:,.2f} ({signal.confidence}% confidence, grade {signal.grade})")
            else:
                logger.warning(f"Signal detected but Telegram failed: {signal.coin}")
            await append_signal(signal)
            # Spawn 24h outcome monitor
            asyncio.create_task(monitor_signal_outcome(signal))

    async def ping_loop(self):
        """Send periodic ping to keep connection alive."""
        while self.running and self.ws:
            try:
                await asyncio.sleep(30)
                if self.ws and self.ws.open:
                    await self.ws.send(json.dumps({"method": "ping"}))
            except Exception as e:
                logger.debug(f"Ping error: {e}")
                break

    async def run(self):
        logger.info("🚀 WebSocket Momentum Breakout Engine starting")
        logger.info(f"Config: {LOOKBACK_PERIODS}-period lookback, {VOLUME_MULTIPLIER}x volume, {MIN_CONFIDENCE}% min confidence")
        logger.info(f"Watching: {', '.join(WATCH_COINS)}")

        while self.running:
            try:
                logger.info(f"Connecting to {WS_URL}...")
                async with websockets.connect(WS_URL, ping_interval=None, ping_timeout=None) as ws:
                    self.ws = ws
                    self.reconnect_delay = 5
                    await self.subscribe()
                    self.ping_task = asyncio.create_task(self.ping_loop())
                    self.funding_task = asyncio.create_task(self.fetch_funding_rates())

                    async for raw in ws:
                        if not self.running:
                            break
                        await self.handle_message(raw)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                for task in (self.ping_task, self.funding_task):
                    if task:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                self.ws = None

            if self.running:
                logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)


# ─── Entrypoint ───────────────────────────────────────────────────────────────

async def main():
    engine = WebSocketEngine()
    try:
        await engine.run()
    except asyncio.CancelledError:
        engine.running = False
        logger.info("Engine shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
