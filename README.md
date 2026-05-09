# HyperLiquid-Claw

A real-time momentum breakout signal engine for HyperLiquid perpetual futures, built for manual trading with automated signal detection and comprehensive quality tracking.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![HyperLiquid](https://img.shields.io/badge/HyperLiquid-API-green.svg)](https://hyperliquid.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🔥 What It Does

Scans **561+ perpetual markets** on HyperLiquid in **real-time via WebSocket** and detects momentum breakout signals:

- **Price breaks** above 20-period high → **LONG signal**
- **Price breaks** below 20-period low → **SHORT signal**
- **Volume confirms** the breakout (>2x average)
- **Funding rate filter** avoids crowded/extreme positions
- **Confidence scoring** with A/B/C/D grading
- **24-hour outcome tracking** — records win/loss/expired with R-value

Signals are delivered as rich Telegram notifications with entry, stop-loss, take-profit, and risk:reward ratios.

---

## 📊 Signal Example

```
🟢 MOMENTUM BREAKOUT — LONG

Coin: BTC
Time: 2026-05-09 22:15 UTC
Grade: B

📊 Signal Details
├ Entry: $97,450.00
├ Stop Loss: $96,820.00
├ Take Profit: $99,025.00
├ Risk:Reward: 1:2.5
└ Confidence: 78%

📈 Context
├ Volume: 3.2x average
├ Funding: 0.003% ✅
└ Reason: Broke above 20-period high ($97,200) on 3.2x volume

💡 Action
Consider opening a LONG position at market or limit near entry.
Set stop at $96,820 and take profit at $99,025.

⚠️ Risk Warning
Only risk 1-2% of portfolio per trade. This is a signal, not financial advice.
```

---

## 🛠️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  HyperLiquid WebSocket (wss://api.hyperliquid.xyz/ws)      │
│  ├── allMids        → live prices for all 561 markets       │
│  └── candle 15m     → OHLCV for BTC, ETH, SOL, HYPE         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Signal Engine (momentum_engine.py)                         │
│  ├── WebSocket receiver                                     │
│  ├── Breakout detection (20-period high/low)                │
│  ├── Volume + funding + confidence scoring                  │
│  ├── Grade assignment (A/B/C/D)                           │
│  ├── 24h outcome monitor (async per signal)               │
│  └── Telegram notification sender                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  QA Database (signal_db.json)                               │
│  ├── Every signal: metadata + outcome tracking              │
│  └── Metrics: win rate, profit factor, expectancy, R        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  FastAPI MCP Server (app.py)                                │
│  ├── /health                → server status                 │
│  ├── /api/signals           → recent signals                │
│  ├── /api/qa                → quality dashboard             │
│  ├── /api/prices            → live prices                 │
│  ├── /api/portfolio         → wallet positions              │
│  └── /clawith/sse           → MCP SSE stream                │
└─────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Installation

### Prerequisites

- Ubuntu 22.04 (or similar)
- Python 3.10+
- nginx (for reverse proxy + SSL)
- supervisord (for process management)

### 1. Clone & Setup

```bash
git clone https://github.com/patrickabedin/HyperLiquid-Claw.git
cd HyperLiquid-Claw
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
nano .env
```

**Required variables:**

```env
# HyperLiquid
HYPERLIQUID_ADDRESS=0xYourWalletAddressHere
HYPERLIQUID_TESTNET=1

# MCP / API Security
CLAWITH_API_KEY=your_random_api_key_here

# Telegram Notifications (optional but recommended)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz
TELEGRAM_CHAT_ID=1948260663

# Signal Engine Settings
POLL_INTERVAL=300
SIGNAL_COOLDOWN=1800
VOLUME_MULTIPLIER=2.0
LOOKBACK_PERIODS=20
MIN_CONFIDENCE=65.0
MAX_FUNDING_EXTREME=0.01
```

> **Security note:** Never commit `.env` to GitHub. The `.env` file is already in `.gitignore`.

### 3. Start Services

```bash
# MCP Server
supervisorctl reread
supervisorctl update
supervisorctl start hyperliquid-mcp

# Signal Engine
supervisorctl start hyperliquid-signals

# Check status
supervisorctl status
```

---

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server health check |
| `/api/prices` | GET | Live prices (BTC, ETH, SOL, HYPE) |
| `/api/portfolio?address=0x...` | GET | Portfolio data for a wallet |
| `/api/signals` | GET | Recent breakout signals (last 50) |
| `/api/qa` | GET | Quality dashboard with metrics |
| `/clawith/sse` | GET | MCP SSE stream (requires Bearer token) |

### Example: Quality Dashboard

```bash
curl -s https://hype.hellenicai.com/api/qa | python3 -m json.tool
```

Returns:
```json
{
  "metrics": {
    "total_signals": 45,
    "win_rate": 58.3,
    "profit_factor": 1.42,
    "average_r": 0.85,
    "expectancy": 0.34,
    "grade_distribution": {"A": 8, "B": 15, "C": 18, "D": 4}
  },
  "recent_resolved": [...],
  "recent_pending": [...]
}
```

---

## 🎯 Signal Logic

### Breakout Detection

1. **20-period high/low lookback** (15m candles = 5 hours of data)
2. **Current candle closes** above high or below low
3. **Volume** on breakout candle > 2x average of last 20 candles
4. **Funding rate** not extreme (>1% absolute = skip)
5. **Confidence score** computed from volume ratio + breakout magnitude

### Grade Assignment

| Grade | Confidence | Volume | Quality |
|-------|-----------|--------|---------|
| **A** | >80% | >3x | Best setups |
| **B** | 70-80% | 2.5-3x | Strong |
| **C** | 65-70% | 2-2.5x | Acceptable |
| **D** | <65% or <2x | Skipped/weak |

### Risk Management (per signal)

- **Stop loss:** Just inside the 20-period range (0.2% buffer)
- **Take profit:** 2.5R from entry (2.5x the risk amount)
- **Risk:Reward:** Fixed at ~1:2.5
- **Cooldown:** 30 minutes per coin (no spam)

---

## 📊 QA Tracking

Every signal is monitored for **24 hours** after it fires:

- **WIN** → Price hits TP first (+R recorded)
- **LOSS** → Price hits SL first (-R recorded)
- **EXPIRED** → Neither TP nor SL hit in 24h (final R at expiry)

**Computed metrics:**
- Win rate (%)
- Profit factor (gross profit / gross loss)
- Average R per signal
- Expectancy (EV per trade)
- Grade distribution
- Best/worst performing coins

Use these metrics to decide when to enable auto-trading.

---

## 🔄 WebSocket vs REST

This engine uses **WebSocket** for real-time data:

| Feature | WebSocket (this engine) | REST (legacy) |
|---------|------------------------|---------------|
| Latency | Sub-second | ~100-300ms per request |
| Data flow | Push (events as they happen) | Pull (poll every 5 min) |
| API load | 1 persistent connection | ~112 requests/min |
| Reconnection | Auto with exponential backoff | N/A |
| Subscription cap | 1,000 per IP | N/A |

The MCP server still exposes **SSE (Server-Sent Events)** at `/clawith/sse` for compatibility with MCP clients.

---

## 🔐 Security

- **API key** stored only on server `.env` (chmod 600)
- **Private key** never leaves the server — SSH in to edit `.env`
- **Dedicated wallet** recommended — fund with only what you're willing to lose
- **Funds stay in your wallet** — bot only executes if `HYPERLIQUID_PRIVATE_KEY` is set

---

## 📝 Usage Flow

### Phase 1: Signal Validation (Weeks 1-2)

1. Engine runs in **read-only mode** (no private key)
2. You receive Telegram alerts for every signal
3. **Manually log** each signal: did you take it? What happened?
4. Track your own win rate vs. the engine's reported win rate

### Phase 2: Enable Trading (When Ready)

Target metrics before enabling auto-trading:
- **Win rate > 55%**
- **Profit factor > 1.3**
- **At least 50 signals** logged

To enable:
```bash
ssh root@your-droplet-ip
cd /opt/hyperliquid-mcp
nano .env
# Add: HYPERLIQUID_PRIVATE_KEY=0xYourPrivateKeyHere
supervisorctl restart hyperliquid-mcp
```

### Phase 3: Expand

- Add more coins (XRP, DOGE, etc.)
- Add more engines (funding rate scanner, mean reversion)
- Run multiple strategies simultaneously

---

## 🧰 Files

| File | Purpose |
|------|---------|
| `momentum_engine.py` | WebSocket signal engine + QA |
| `app.py` | FastAPI MCP server + API endpoints |
| `dashboard.html` | Web dashboard (optional) |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment template |
| `nginx.conf` | Reverse proxy config |
| `supervisord.conf` | Process manager config |
| `hyperliquid-mcp.service` | systemd service file |

---

## 🌐 Deployment

### Recommended Stack

- **DigitalOcean droplet** ($6/mo, Ubuntu 22.04, Frankfurt)
- **Domain:** `hype.hellenicai.com` → A record to droplet IP
- **SSL:** Let's Encrypt via Certbot
- **Reverse proxy:** nginx
- **Process manager:** supervisord

### DNS Setup

```
Type: A
Name: hype
Target: 161.35.18.201   # your droplet IP
TTL: Auto
Proxy: OFF (required for SSL origin)
```

### SSL (Certbot)

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d hype.hellenicai.com
```

---

## 🐛 Logs & Debugging

```bash
# Signal engine logs
tail -f /var/log/hyperliquid-signals.out.log
tail -f /var/log/hyperliquid-signals.err.log

# MCP server logs
tail -f /var/log/hyperliquid-mcp.out.log
tail -f /var/log/hyperliquid-mcp.err.log

# QA database
cat /opt/hyperliquid-mcp/signals/signal_db.json | python3 -m json.tool

# Recent signals
cat /opt/hyperliquid-mcp/signals/recent_signals.json | python3 -m json.tool
```

---

## 🚀 Roadmap

- [x] WebSocket real-time scanning
- [x] Momentum breakout detection
- [x] QA tracking with 24h outcome monitoring
- [x] Telegram notifications
- [x] FastAPI MCP server
- [ ] Auto-trading mode (when private key configured)
- [ ] Additional engines (funding rate arb, mean reversion)
- [ ] Web dashboard at `/dashboard`
- [ ] Multi-coin watchlist expansion
- [ ] Backtesting framework

---

## ⚠️ Disclaimer

This software is for **educational and research purposes**. Cryptocurrency trading carries significant risk. Past signal performance does not guarantee future results. Only trade with capital you can afford to lose. This is not financial advice.

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

## 🙏 Credits

- Built by [Hellenic Technologies](https://hellenictechnologies.com)
- Powered by [HyperLiquid](https://hyperliquid.xyz) API
- MCP protocol by [Model Context Protocol](https://modelcontextprotocol.io)

---

**Questions?** Message me in Telegram. 🔥
