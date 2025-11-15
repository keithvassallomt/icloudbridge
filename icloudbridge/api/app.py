"""FastAPI application for iCloudBridge Web UI.

This module provides the main FastAPI application with:
- CORS middleware for frontend access
- Request logging
- Exception handlers
- API routes for all services (notes, reminders, passwords)
- WebSocket support for real-time updates
- Background scheduler for automated syncs
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from icloudbridge.api.exceptions import (
    ICBException,
    icb_exception_handler,
    validation_exception_handler,
)
from icloudbridge.core.config import load_config
from icloudbridge.utils.db import SettingsDB
from icloudbridge.utils.logging import (
    attach_websocket_log_handler,
    set_logging_level,
    setup_logging,
)

logger = logging.getLogger(__name__)

# Scheduler will be initialized in lifespan
scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager.

    Handles startup and shutdown events:
    - Startup: Initialize scheduler, load config
    - Shutdown: Stop scheduler, cleanup resources
    """
    logger.info("iCloudBridge API starting up...")

    # Load configuration
    config = load_config()
    setup_logging(config)

    settings_db = SettingsDB(config.general.data_dir / "settings.db")
    await settings_db.initialize()
    stored_level = await settings_db.get_setting("log_level")
    if stored_level:
        set_logging_level(stored_level)

    attach_websocket_log_handler(asyncio.get_running_loop(), config)
    logger.info(f"Configuration loaded from {config.general.config_file or 'defaults'}")

    # Ensure data directory exists
    config.ensure_data_dir()
    logger.info(f"Data directory: {config.general.data_dir}")

    # Initialize scheduler
    from icloudbridge.api.scheduler import SchedulerManager
    global scheduler
    scheduler = SchedulerManager(config)
    await scheduler.start()
    logger.info("Scheduler initialized and started")

    yield

    # Shutdown
    logger.info("iCloudBridge API shutting down...")
    if scheduler:
        await scheduler.stop()
        logger.info("Scheduler stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(
        title="iCloudBridge API",
        description="REST API for iCloudBridge - Sync Apple Notes, Reminders, and Passwords",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS
    # In production, this should be restricted to specific origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Make this configurable
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all incoming requests."""
        logger.debug(f"{request.method} {request.url.path}")
        response = await call_next(request)
        logger.debug(
            f"{request.method} {request.url.path} - {response.status_code}"
        )
        return response

    # Exception handlers
    app.add_exception_handler(ICBException, icb_exception_handler)
    app.add_exception_handler(Exception, validation_exception_handler)

    # Register API routes
    from icloudbridge.api.routes import (
        config,
        health,
        notes,
        passwords,
        photos,
        reminders,
        schedules,
        settings,
        system,
    )
    app.include_router(health.router, prefix="/api", tags=["Health"])
    app.include_router(config.router, prefix="/api/config", tags=["Configuration"])
    app.include_router(notes.router, prefix="/api/notes", tags=["Notes"])
    app.include_router(reminders.router, prefix="/api/reminders", tags=["Reminders"])
    app.include_router(passwords.router, prefix="/api/passwords", tags=["Passwords"])
    app.include_router(photos.router, prefix="/api/photos", tags=["Photos"])
    app.include_router(schedules.router, prefix="/api/schedules", tags=["Schedules"])
    app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
    app.include_router(system.router, prefix="/api/system", tags=["System"])

    # WebSocket endpoint
    from icloudbridge.api.websocket import websocket_endpoint
    app.add_api_websocket_route("/api/ws", websocket_endpoint)

    # Serve static files (frontend build) - will be added in Phase 2
    # frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    # if frontend_dist.exists():
    #     app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    #     logger.info(f"Serving frontend from {frontend_dist}")

    # Root endpoint
    @app.get("/")
    async def root():
        """Root endpoint - returns API information."""
        return {
            "name": "iCloudBridge API",
            "version": "0.1.0",
            "docs": "/api/docs",
            "health": "/api/health",
        }

    logger.info("FastAPI application created")
    return app


# Create the application instance
app = create_app()
