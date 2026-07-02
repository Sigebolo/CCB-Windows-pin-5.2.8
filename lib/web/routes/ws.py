"""
WebSocket routes for real-time status updates.
"""

import asyncio
import json
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Connected clients
connected_clients: Set[WebSocket] = set()


async def broadcast_status(data: dict):
    """Broadcast status update to all connected clients."""
    if not connected_clients:
        return

    message = json.dumps(data)
    disconnected = set()

    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.add(client)

    # Remove disconnected clients
    for client in disconnected:
        connected_clients.discard(client)


async def get_current_status() -> dict:
    """Get current system status."""
    from web.routes.daemons import get_askd_status, get_maild_status

    return {
        "type": "status",
        "daemons": {
            "askd": get_askd_status().model_dump(),
            "maild": get_maild_status().model_dump(),
        },
    }


@router.websocket("/status")
async def websocket_status(websocket: WebSocket):
    """WebSocket endpoint for real-time status updates."""
    await websocket.accept()
    connected_clients.add(websocket)

    try:
        # Send initial status
        status = await get_current_status()
        await websocket.send_text(json.dumps(status))

        # Keep connection alive and send periodic updates
        while True:
            try:
                # Wait for client message or timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )

                # Handle client requests
                try:
                    request = json.loads(data)
                    if request.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                    elif request.get("type") == "refresh":
                        status = await get_current_status()
                        await websocket.send_text(json.dumps(status))
                except json.JSONDecodeError:
                    pass

            except asyncio.TimeoutError:
                # Send periodic status update
                status = await get_current_status()
                await websocket.send_text(json.dumps(status))

    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(websocket)
