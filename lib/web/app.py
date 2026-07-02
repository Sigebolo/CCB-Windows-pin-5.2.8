"""
CCB Web Controller - FastAPI Application.

Provides a web-based interface for managing CCB services.
"""

import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web.auth import get_current_user, verify_local_access
from web.routes import daemons, providers, mail, ws

# Application info
APP_NAME = "CCB Web Controller"
APP_VERSION = "1.0.0"

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(
    local_only: bool = True,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        docs_url="/api/docs" if not local_only else None,
        redoc_url=None,
    )

    # Store config in app state
    app.state.local_only = local_only
    app.state.auth_token = auth_token

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Setup templates
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    app.state.templates = templates

    # Include routers
    app.include_router(daemons.router, prefix="/api/daemons", tags=["daemons"])
    app.include_router(providers.router, prefix="/api/providers", tags=["providers"])
    app.include_router(mail.router, prefix="/api/mail", tags=["mail"])
    app.include_router(ws.router, prefix="/ws", tags=["websocket"])

    # Root route - Dashboard
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, "title": "Dashboard"},
        )

    # Mail configuration page
    @app.get("/mail", response_class=HTMLResponse)
    async def mail_page(request: Request):
        return templates.TemplateResponse(
            "mail.html",
            {"request": request, "title": "Mail Configuration"},
        )

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": APP_VERSION}

    return app


def generate_token() -> str:
    """Generate a secure access token."""
    return secrets.token_urlsafe(32)
