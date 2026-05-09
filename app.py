import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CLAWITH_API_KEY = os.getenv("CLAWITH_API_KEY", "")
HYPERLIQUID_ADDRESS = os.getenv("HYPERLIQUID_ADDRESS", "")
HYPERLIQUID_PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
HYPERLIQUID_TESTNET = os.getenv("HYPERLIQUID_TESTNET", "1").lower() in ("1", "true", "yes")

if not CLAWITH_API_KEY:
    logger.warning("CLAWITH_API_KEY not set - SSE endpoint will be unprotected!")

# Initialize Hyperliquid Info client (read-only)
base_url = constants.TESTNET_API_URL if HYPERLIQUID_TESTNET else constants.MAINNET_API_URL
logger.info(f"Hyperliquid base URL: {base_url}")
info_client = Info(base_url, skip_ws=True)

# MCP Server state
class MCPSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.message_queue = asyncio.Queue()
        self.initialized = False
        self.tools = []

sessions: dict[str, MCPSession] = {}

# Bearer token middleware
class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for health check
        if request.url.path == "/health":
            return await call_next(request)
        
        # Skip auth if no key configured
        if not CLAWITH_API_KEY:
            return await call_next(request)
        
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        
        token = auth_header[7:]
        if token != CLAWITH_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid Bearer token")
        
        return await call_next(request)

# MCP Tools definition
TOOLS = [
    {
        "name": "hyperliquid_get_account_info",
        "description": "Get user's perpetual account summary including positions and margin. Requires HYPERLIQUID_ADDRESS env var or userAddress parameter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "userAddress": {
                    "type": "string",
                    "description": "User wallet address (optional, defaults to HYPERLIQUID_ADDRESS env var)"
                }
            }
        }
    },
    {
        "name": "hyperliquid_get_positions",
        "description": "Get user's open positions with margin summary",
        "inputSchema": {
            "type": "object",
            "properties": {
                "userAddress": {
                    "type": "string",
                    "description": "User wallet address (optional)"
                }
            }
        }
    },
    {
        "name": "hyperliquid_get_balance",
        "description": "Get user's account balance and withdrawable amount",
        "inputSchema": {
            "type": "object",
            "properties": {
                "userAddress": {
                    "type": "string",
                    "description": "User wallet address (optional)"
                }
            }
        }
    },
    {
        "name": "hyperliquid_get_meta",
        "description": "Get exchange metadata including all available trading assets with their indices, names, max leverage, and trading parameters",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "hyperliquid_get_all_mids",
        "description": "Get current mid prices for all assets",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "hyperliquid_get_order_book",
        "description": "Get order book (market depth) for a specific asset",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Asset symbol (e.g., 'BTC', 'ETH', 'SOL')"
                }
            },
            "required": ["coin"]
        }
    },
    {
        "name": "hyperliquid_get_recent_trades",
        "description": "Get recent trades for a specific asset",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Asset symbol (e.g., 'BTC', 'ETH', 'SOL')"
                }
            },
            "required": ["coin"]
        }
    },
    {
        "name": "hyperliquid_get_candles",
        "description": "Get historical candle/OHLCV data for an asset",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Asset symbol (e.g., 'BTC', 'ETH', 'SOL')"
                },
                "interval": {
                    "type": "string",
                    "description": "Candle interval",
                    "enum": ["1m", "5m", "15m", "1h", "4h", "1d"]
                },
                "startTime": {
                    "type": "integer",
                    "description": "Start time in milliseconds"
                },
                "endTime": {
                    "type": "integer",
                    "description": "End time in milliseconds (optional, defaults to current time)"
                }
            },
            "required": ["coin", "interval", "startTime"]
        }
    },
    {
        "name": "hyperliquid_get_open_orders",
        "description": "Get user's currently open orders",
        "inputSchema": {
            "type": "object",
            "properties": {
                "userAddress": {
                    "type": "string",
                    "description": "User wallet address (optional)"
                }
            }
        }
    },
    {
        "name": "hyperliquid_get_user_fills",
        "description": "Get user's historical trade fills",
        "inputSchema": {
            "type": "object",
            "properties": {
                "userAddress": {
                    "type": "string",
                    "description": "User wallet address (optional)"
                },
                "startTime": {
                    "type": "integer",
                    "description": "Start time in milliseconds (required)"
                },
                "endTime": {
                    "type": "integer",
                    "description": "End time in milliseconds (optional)"
                }
            },
            "required": ["startTime"]
        }
    },
    {
        "name": "hyperliquid_get_historical_funding",
        "description": "Get historical funding rates for an asset",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Asset symbol (e.g., 'BTC', 'ETH', 'SOL')"
                },
                "startTime": {
                    "type": "integer",
                    "description": "Start time in milliseconds"
                },
                "endTime": {
                    "type": "integer",
                    "description": "End time in milliseconds (optional)"
                }
            },
            "required": ["coin", "startTime"]
        }
    },
]

async def handle_tool_call(name: str, arguments: dict) -> dict:
    """Execute a Hyperliquid tool call."""
    user_address = arguments.get("userAddress", HYPERLIQUID_ADDRESS)
    
    if name == "hyperliquid_get_account_info":
        if not user_address:
            return {"error": "userAddress required (or set HYPERLIQUID_ADDRESS env var)"}
        result = info_client.user_state(user_address)
        return {
            "message": "Account information retrieved successfully",
            "data": result,
            "summary": {
                "accountValue": result["marginSummary"]["accountValue"],
                "totalMarginUsed": result["marginSummary"]["totalMarginUsed"],
                "withdrawable": result["withdrawable"],
                "numberOfPositions": len(result["assetPositions"])
            }
        }
    
    elif name == "hyperliquid_get_positions":
        if not user_address:
            return {"error": "userAddress required (or set HYPERLIQUID_ADDRESS env var)"}
        result = info_client.user_state(user_address)
        return {
            "message": "Positions retrieved successfully",
            "data": {
                "assetPositions": result["assetPositions"],
                "marginSummary": result["marginSummary"],
                "withdrawable": result["withdrawable"]
            },
            "summary": {
                "numberOfPositions": len(result["assetPositions"]),
                "accountValue": result["marginSummary"]["accountValue"],
                "totalMarginUsed": result["marginSummary"]["totalMarginUsed"]
            }
        }
    
    elif name == "hyperliquid_get_balance":
        if not user_address:
            return {"error": "userAddress required (or set HYPERLIQUID_ADDRESS env var)"}
        result = info_client.user_state(user_address)
        margin_summary = result["marginSummary"]
        return {
            "message": "Balance retrieved successfully",
            "data": {
                "accountValue": margin_summary["accountValue"],
                "totalMarginUsed": margin_summary["totalMarginUsed"],
                "totalNtlPos": margin_summary["totalNtlPos"],
                "totalRawUsd": margin_summary["totalRawUsd"],
                "withdrawable": result["withdrawable"]
            }
        }
    
    elif name == "hyperliquid_get_meta":
        result = info_client.meta()
        return {
            "message": "Exchange metadata retrieved",
            "data": result,
            "assets": [
                {"index": i, "name": asset["name"], "maxLeverage": asset.get("maxLeverage", 0)}
                for i, asset in enumerate(result.get("universe", []))
            ]
        }
    
    elif name == "hyperliquid_get_all_mids":
        result = info_client.all_mids()
        return {
            "message": "Mid prices retrieved",
            "data": result,
            "count": len(result)
        }
    
    elif name == "hyperliquid_get_order_book":
        coin = arguments["coin"]
        result = info_client.l2_snapshot(coin)
        return {
            "message": f"Order book for {coin} retrieved",
            "data": result,
            "coin": coin
        }
    
    elif name == "hyperliquid_get_recent_trades":
        coin = arguments["coin"]
        result = info_client.recent_trades(coin)
        return {
            "message": f"Recent trades for {coin} retrieved",
            "data": result,
            "coin": coin,
            "count": len(result)
        }
    
    elif name == "hyperliquid_get_candles":
        coin = arguments["coin"]
        interval = arguments["interval"]
        start_time = int(arguments["startTime"])
        end_time = int(arguments.get("endTime", 0)) or None
        result = info_client.candles_snapshot(coin, interval, start_time, end_time)
        return {
            "message": f"Candles for {coin} ({interval}) retrieved",
            "data": result,
            "coin": coin,
            "interval": interval,
            "count": len(result)
        }
    
    elif name == "hyperliquid_get_open_orders":
        if not user_address:
            return {"error": "userAddress required (or set HYPERLIQUID_ADDRESS env var)"}
        result = info_client.open_orders(user_address)
        return {
            "message": "Open orders retrieved",
            "data": result,
            "count": len(result)
        }
    
    elif name == "hyperliquid_get_user_fills":
        if not user_address:
            return {"error": "userAddress required (or set HYPERLIQUID_ADDRESS env var)"}
        start_time = int(arguments["startTime"])
        end_time = int(arguments.get("endTime", 0)) or None
        result = info_client.user_fills_by_time(user_address, start_time, end_time)
        return {
            "message": "User fills retrieved",
            "data": result,
            "count": len(result)
        }
    
    elif name == "hyperliquid_get_historical_funding":
        coin = arguments["coin"]
        start_time = int(arguments["startTime"])
        end_time = int(arguments.get("endTime", 0)) or None
        result = info_client.funding_history(coin, start_time, end_time)
        return {
            "message": f"Funding history for {coin} retrieved",
            "data": result,
            "coin": coin,
            "count": len(result)
        }
    
    else:
        return {"error": f"Unknown tool: {name}"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("HyperLiquid SSE MCP Server starting...")
    logger.info(f"Network: {'testnet' if HYPERLIQUID_TESTNET else 'mainnet'}")
    logger.info(f"Default address: {HYPERLIQUID_ADDRESS or 'not set'}")
    yield
    logger.info("HyperLiquid SSE MCP Server shutting down...")

app = FastAPI(title="HyperLiquid MCP SSE Server", lifespan=lifespan)
app.add_middleware(BearerAuthMiddleware)

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "network": "testnet" if HYPERLIQUID_TESTNET else "mainnet",
        "read_only": not bool(HYPERLIQUID_PRIVATE_KEY)
    }

@app.get("/clawith/sse")
async def sse_endpoint(request: Request):
    """SSE endpoint for MCP communication."""
    session_id = f"session_{id(request)}_{asyncio.get_event_loop().time()}"
    session = MCPSession(session_id)
    sessions[session_id] = session
    
    logger.info(f"SSE session started: {session_id}")
    
    async def event_generator():
        try:
            while True:
                msg = await asyncio.wait_for(session.message_queue.get(), timeout=30)
                yield f"event: message\ndata: {json.dumps(msg)}\n\n"
        except asyncio.TimeoutError:
            # Send keepalive
            yield f"event: ping\ndata: {{}}\n\n"
        except Exception as e:
            logger.error(f"SSE error: {e}")
        finally:
            sessions.pop(session_id, None)
            logger.info(f"SSE session ended: {session_id}")
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": session_id,
        }
    )

@app.post("/clawith/messages")
async def post_message(request: Request):
    """Receive messages from MCP client."""
    body = await request.json()
    session_id = body.get("session_id", "")
    
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    await session.message_queue.put(body)
    return {"status": "ok"}

@app.post("/mcp/message")
async def mcp_message(request: Request):
    """Direct MCP message endpoint (non-SSE fallback)."""
    body = await request.json()
    
    method = body.get("method", "")
    id = body.get("id", 0)
    params = body.get("params", {})
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "hyperliquid-mcp-sse",
                    "version": "1.0.0"
                }
            }
        }
    
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {"tools": TOOLS}
        }
    
    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = await handle_tool_call(name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            }
        }
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
