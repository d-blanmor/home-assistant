#!/usr/bin/env python
"""Home Assistant Custom Component – JobHunter App.
This integration exposes both lightweight services that operate on the same SQLite database
used by the original FastAPI application and a pair of controls that start/stop an HTTP server
running the FastAPI app. The server runs in a background asyncio task so Home Assistant’s event loop is not blocked.
"""
from __future__ import annotations

import logging
import sys
import pathlib
import asyncio

import voluptuous as vol
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant import config_entries

# ---------------------------------------------------------------------------
# Make the top‑level project modules discoverable.
# The integration lives in <repo>/custom_components/ha_app – we insert the repo root.
project_root = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

try:
    from database import get_session  # type: ignore
    from config import read_config      # type: ignore
except Exception as exc:  # pragma: no cover – defensive
    raise RuntimeError("Failed to import project modules: %s" % exc)

# ---------------------------------------------------------------------------
_LOGGER = logging.getLogger(__name__)
DOMAIN = "ha_app"
SERVER_TASK_KEY = "api_server_task"
SERVICE_LIST_ALL = "list_all"
SERVICE_CREATE_ROLE = "create_role"

SERVICE_SCHEMA = vol.Schema({vol.Optional(CONF_API_KEY): str})

# ---------------------------------------------------------------------------
async def start_http_service(
    hass: HomeAssistant,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Runs uvicorn in a background asyncio task.
    The FastAPI instance is created from the original application’s create_app() helper.
    A daemon‑style cancellation is used so HA stays responsive.
    """
    import uvicorn
    from fastapi import FastAPI

    # Import that creates the same FastAPI object your API exposes.
    from ..main import create_app  # type: ignore

    app: FastAPI = create_app()

    loop = asyncio.get_event_loop()
    task = loop.create_task(
        loop.run_in_executor(None, uvicorn.run, app, host=host, port=port)
    )
    hass.data[DOMAIN][SERVER_TASK_KEY] = task
    _LOGGER.info("Started FastAPI server on %s:%d", host, port)

async def stop_http_service(hass: HomeAssistant) -> None:
    """Cancels the background server task if it exists."""
    task = hass.data[DOMAIN].get(SERVER_TASK_KEY)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except Exception:  # pragma: no cover – cancellation error
            pass
        _LOGGER.info("Stopped FastAPI server")

# ---------------------------------------------------------------------------
async def handle_list_all_service(call: ServiceCall) -> None:
    """Return all role records as JSON."""
    session = get_session()
    try:
        rows = session.query(roles.Role).all()  # type: ignore[name-match]
    except Exception as exc:  # pragma: no cover – defensive
        _LOGGER.error("DB query failed: %s", exc)
        raise
    result = [{"id": r.id, "name": r.name} for r in rows]
    await call.async_create_task(call.set_result({"role_list": result}))

async def handle_create_role_service(call: ServiceCall) -> None:
    """Create a role record from payload."""
    data = call.data.get("role")
    if not data:
        raise RuntimeError("Missing 'role' key in service payload")
    session = get_session()
    new_role = roles.Role(**data)  # type: ignore[name-match]
    session.add(new_role)
    session.commit()
    await call.async_create_task(call.set_result({"id": new_role.id}))

# ---------------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Register start/stop services for the HTTP server.
    hass.services.async_register(
        DOMAIN,
        "start_api",
        lambda call: asyncio.create_task(start_http_service(hass)),
    )
    hass.services.async_register(
        DOMAIN,
        "stop_api",
        lambda call: asyncio.create_task(stop_http_service(hass)),
    )

    # Register services that interact with the DB.
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_ALL, handle_list_all_service, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_ROLE,
        handle_create_role_service,
        schema=SERVICE_SCHEMA,
    )

    # Load app configuration (optional). If you want the user to edit via HA UI use a config_flow.
    cfg_file = config.get("file", project_root / "config.ini")
    try:
        hass.data[DOMAIN]["config"] = read_config(str(cfg_file))
    except Exception as exc:  # pragma: no cover
        _LOGGER.error("Failed to load app config: %s", exc)

    _LOGGER.info("JobHunter App integration ready")
    return True

# Optional entry setup (for YAML installs)
async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    return True
