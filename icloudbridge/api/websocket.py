"""WebSocket manager for real-time updates.

This module provides WebSocket support for real-time communication between
the API and frontend clients. It enables:
- Real-time sync progress updates
- Log streaming during operations
- Schedule execution notifications
- Error alerts
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting.

    Handles multiple concurrent client connections and provides methods
    for broadcasting messages to all connected clients or specific subsets.
    """

    def __init__(self):
        """Initialize the connection manager."""
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection.

        Args:
            websocket: WebSocket connection to register
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from active connections.

        Args:
            websocket: WebSocket connection to remove
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket) -> None:
        """Send a message to a specific client.

        Args:
            message: Message dictionary to send
            websocket: Target WebSocket connection
        """
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send personal message: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all connected clients.

        Args:
            message: Message dictionary to broadcast
        """
        disconnected = []

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Failed to broadcast to client: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_to_service(self, service: str, message: Dict[str, Any]) -> None:
        """Broadcast a message to clients subscribed to a specific service.

        Args:
            service: Service name (notes, reminders, passwords)
            message: Message dictionary to broadcast

        Note:
            Currently broadcasts to all clients. Future enhancement could
            implement per-service subscriptions.
        """
        # For now, broadcast to all clients
        # Future: Implement service-specific subscriptions
        await self.broadcast(message)


# Global connection manager instance
manager = ConnectionManager()


def create_message(
    message_type: str,
    service: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a standardized WebSocket message.

    Args:
        message_type: Type of message (sync_progress, log_entry, schedule_run, error, status_update)
        service: Service name (notes, reminders, photos, passwords)
        data: Message payload

    Returns:
        Formatted message dictionary
    """
    return {
        "type": message_type,
        "service": service,
        "data": data,
        "timestamp": datetime.now().isoformat(),
    }


async def send_sync_progress(
    service: str,
    status: str,
    progress: int,
    message: str,
    stats: Dict[str, Any] | None = None,
) -> None:
    """Send sync progress update to all clients.

    Args:
        service: Service name (notes, reminders, photos, passwords)
        status: Sync status (running, success, error)
        progress: Progress percentage (0-100)
        message: Progress message
        stats: Optional sync statistics
    """
    msg = create_message(
        message_type="sync_progress",
        service=service,
        data={
            "status": status,
            "progress": progress,
            "message": message,
            "stats": stats or {},
        },
    )
    await manager.broadcast_to_service(service, msg)


async def send_log_entry(
    service: str,
    level: str,
    message: str,
) -> None:
    """Send a log entry to all clients.

    Args:
        service: Service name (notes, reminders, photos, passwords)
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        message: Log message
    """
    msg = create_message(
        message_type="log_entry",
        service=service,
        data={
            "level": level,
            "message": message,
        },
    )
    await manager.broadcast_to_service(service, msg)


async def send_schedule_run(
    service: str,
    schedule_id: int,
    schedule_name: str,
    status: str,
) -> None:
    """Send schedule execution notification to all clients.

    Args:
        service: Service name (notes, reminders, passwords)
        schedule_id: Schedule ID
        schedule_name: Schedule name
        status: Execution status (started, completed, failed)
    """
    msg = create_message(
        message_type="schedule_run",
        service=service,
        data={
            "schedule_id": schedule_id,
            "schedule_name": schedule_name,
            "status": status,
        },
    )
    await manager.broadcast_to_service(service, msg)


async def send_error(
    service: str,
    error_message: str,
    error_type: str = "error",
) -> None:
    """Send error notification to all clients.

    Args:
        service: Service name (notes, reminders, passwords)
        error_message: Error message
        error_type: Error type (error, warning)
    """
    msg = create_message(
        message_type="error",
        service=service,
        data={
            "error_message": error_message,
            "error_type": error_type,
        },
    )
    await manager.broadcast_to_service(service, msg)


async def send_status_update(
    service: str,
    status: Dict[str, Any],
) -> None:
    """Send status update to all clients.

    Args:
        service: Service name (notes, reminders, passwords)
        status: Status information dictionary
    """
    msg = create_message(
        message_type="status_update",
        service=service,
        data=status,
    )
    await manager.broadcast_to_service(service, msg)


async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint handler.

    Manages WebSocket connections and handles incoming messages.

    Args:
        websocket: WebSocket connection
    """
    await manager.connect(websocket)

    try:
        # Send welcome message
        await manager.send_personal_message(
            {
                "type": "connection",
                "status": "connected",
                "message": "Connected to iCloudBridge API",
                "timestamp": datetime.now().isoformat(),
            },
            websocket,
        )

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()

                # Parse message
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    await manager.send_personal_message(
                        {
                            "type": "error",
                            "message": "Invalid JSON format",
                            "timestamp": datetime.now().isoformat(),
                        },
                        websocket,
                    )
                    continue

                # Handle different message types
                message_type = message.get("type")

                if message_type == "ping":
                    # Respond to ping with pong
                    await manager.send_personal_message(
                        {
                            "type": "pong",
                            "timestamp": datetime.now().isoformat(),
                        },
                        websocket,
                    )

                elif message_type == "subscribe":
                    # Subscribe to specific service updates
                    # Future enhancement: implement service-specific subscriptions
                    service = message.get("service")
                    await manager.send_personal_message(
                        {
                            "type": "subscribed",
                            "service": service,
                            "message": f"Subscribed to {service} updates",
                            "timestamp": datetime.now().isoformat(),
                        },
                        websocket,
                    )

                elif message_type == "unsubscribe":
                    # Unsubscribe from service updates
                    # Future enhancement: implement service-specific subscriptions
                    service = message.get("service")
                    await manager.send_personal_message(
                        {
                            "type": "unsubscribed",
                            "service": service,
                            "message": f"Unsubscribed from {service} updates",
                            "timestamp": datetime.now().isoformat(),
                        },
                        websocket,
                    )

                else:
                    # Unknown message type
                    await manager.send_personal_message(
                        {
                            "type": "error",
                            "message": f"Unknown message type: {message_type}",
                            "timestamp": datetime.now().isoformat(),
                        },
                        websocket,
                    )

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                await manager.send_personal_message(
                    {
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().isoformat(),
                    },
                    websocket,
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
