"""Puente con NEXUS (Seccion 10), siguiendo el contrato de `deus_adapter.py`.

HERMES solo publica hechos: se registra como modulo y reporta errores. Nunca
recibe ni aplica cambios desde NEXUS — cualquier orden de la outbox la ejecuta
el orquestador de DEUS con sus propios controles.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class NexusApiError(RuntimeError):
    pass


class ClienteNexus:
    def __init__(
        self,
        base_url: str,
        token: str,
        actor: str = "HERMES",
        timeout_seconds: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.actor = actor
        self.timeout_seconds = timeout_seconds

    @classmethod
    def desde_entorno(cls) -> ClienteNexus | None:
        base_url = os.environ.get("NEXUS_URL")
        token = os.environ.get("NEXUS_INTERNAL_API_TOKEN")
        if not base_url or not token:
            return None
        return cls(base_url=base_url, token=token)

    def _peticion(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> Any:
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        peticion = Request(
            f"{self.base_url}{ruta}",
            method=metodo,
            data=datos,
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Token": self.token,
                "X-Deus-Actor": self.actor,
            },
        )
        try:
            with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
                return json.loads(respuesta.read().decode("utf-8"))
        except URLError as exc:
            raise NexusApiError(f"NEXUS no respondio: {exc}") from exc

    def registrar_modulo(self) -> Any:
        return self._peticion(
            "POST",
            "/v1/modules",
            {
                "module_id": "hermes",
                "display_name": "HERMES",
                "description": "Ventas y atencion multicanal (WhatsApp, correo, llamada)",
                "owner": "DEUS",
            },
        )

    def reportar_error(
        self, mensaje: str, correlation_key: str, severity: str = "high", source: str = "hermes"
    ) -> Any:
        return self._peticion(
            "POST",
            "/v1/observations",
            {
                "module_id": "hermes",
                "source": source,
                "kind": "error",
                "severity": severity,
                "message": mensaje,
                "correlation_key": correlation_key,
            },
        )
