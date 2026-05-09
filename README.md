# HyperLiquid-Claw SSE MCP Server

A Model Context Protocol (MCP) server for Hyperliquid perpetual trading, exposed over SSE (Server-Sent Events) for use with **clawith.ai** and other MCP clients.

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- A Hyperliquid wallet address (for read-only mode)

### Installation

```bash
git clone https://github.com/patrickabedin/HyperLiquid-Claw.git
cd HyperLiquid-Claw
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Key variables:
- `CLAWITH_API_KEY` — Bearer token for SSE auth (generate a secure random string)
- `HYPERLIQUID_ADDRESS` — Your wallet address (read-only mode)
- `HYPERLIQUID_PRIVATE_KEY` — Optional, only needed for trading
- `HYPERLIQUID_TESTNET` — Set to `1` for testnet, remove for mainnet

### Run

```bash
source venv/bin/activate
python app.py
```

Or with uvicorn directly:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 📡 clawith.ai Configuration

Add this to your `clawith.ai` MCP servers config:

```json
{
  "mcpServers": {
    "hyperliquid": {
      "url": "https://hype.hellenicai.com/clawith/sse",
      "headers": {
        "Authorization": "Bearer YOUR_CLAWITH_API_KEY"
      },
      "env": {
        "HYPERLIQUID_ADDRESS": "0xYourWalletAddress",
        "HYPERLIQUID_TESTNET": "1"
      }
    }
  }
}
```

## 🔧 Available Tools

| Tool | Description |
|------|-------------|
| `hyperliquid_get_account_info` | Account summary with positions and margin |
| `hyperliquid_get_positions` | Open positions |
| `hyperliquid_get_balance` | Account balance |
| `hyperliquid_get_meta` | Exchange metadata (all assets) |
| `hyperliquid_get_all_mids` | Current mid prices for all assets |
| `hyperliquid_get_order_book` | Order book for a specific coin |
| `hyperliquid_get_recent_trades` | Recent trades for a coin |
| `hyperliquid_get_candles` | Historical OHLCV data |
| `hyperliquid_get_open_orders` | User's open orders |
| `hyperliquid_get_user_fills` | Historical fills |
| `hyperliquid_get_historical_funding` | Funding rate history |

## 🛡️ Read-Only Mode

By default, the server runs in **read-only mode** — no private key is required. Only monitoring and market data tools are available.

To enable trading, set `HYPERLIQUID_PRIVATE_KEY` in your `.env` file.

## 🌐 Deployment

### Nginx + SSL (Let's Encrypt)

```bash
sudo cp nginx.conf /etc/nginx/sites-available/hyperliquid-mcp
sudo ln -s /etc/nginx/sites-available/hyperliquid-mcp /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d hype.hellenicai.com
```

### Supervisor (Auto-start)

```bash
sudo cp supervisord.conf /etc/supervisor/conf.d/hyperliquid-mcp.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start hyperliquid-mcp
```

### Systemd (Alternative)

```bash
sudo cp hyperliquid-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hyperliquid-mcp
sudo systemctl start hyperliquid-mcp
```

## 📋 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CLAWITH_API_KEY` | Yes | Bearer token for SSE auth |
| `HYPERLIQUID_ADDRESS` | Yes* | Wallet address for read-only queries |
| `HYPERLIQUID_PRIVATE_KEY` | No | Private key for trading (optional) |
| `HYPERLIQUID_TESTNET` | No | Set to `1` for testnet |
| `PORT` | No | Server port (default: 8000) |

*Required unless you always pass `userAddress` in tool calls.

## 🔗 Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /clawith/sse` | MCP SSE stream |
| `POST /clawith/messages` | Send messages to SSE session |
| `POST /mcp/message` | Direct MCP JSON-RPC endpoint |

## 📄 License

MIT
