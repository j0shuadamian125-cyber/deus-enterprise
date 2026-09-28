"""Punto de entrada ASGI: `uvicorn hermes.main:app --reload`."""
from __future__ import annotations

from .api import crear_app

app = crear_app()
