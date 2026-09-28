"""Canal llamada telefonica (Seccion 5.4).

Entrada: webhook de la plataforma de voz (Twilio Voice, o una conversacional
como Vapi/Retell) normalizado al mismo contrato que los demas canales. Salida:
creacion de llamada saliente via la API de Twilio Voice, que requiere una URL
TwiML publica del propio cliente (dependencia externa documentada en el README).

La grabacion solo se conserva si el cliente la autorizo y su jurisdiccion lo
permite: esa validacion vive en `Hermes.registrar_llamada`.
"""
from __future__ import annotations

import hmac
import json
import os
from base64 import b64encode
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import Canal, NivelServicioLlamada, ResultadoLlamada
from ..pipeline import ContactoEntrante
from ..resilience import redactar, reintentar
from .whatsapp import FirmaInvalidaError, firmar


class LlamadaError(RuntimeError):
    pass


def _primero(carga: Mapping[str, Any], *claves: str) -> Any | None:
    for clave in claves:
        valor = carga.get(clave)
        if valor not in (None, ""):
            return valor
    return None


class AdaptadorLlamada:
    canal = Canal.LLAMADA

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        numero_saliente: str | None = None,
        url_twiml: str | None = None,
        timeout_seconds: int = 10,
        validar_firmas: bool | None = None,
    ) -> None:
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.numero_saliente = numero_saliente or os.environ.get("TWILIO_VOICE_FROM")
        self.url_twiml = url_twiml or os.environ.get("TWILIO_VOICE_TWIML_URL")
        self.timeout_seconds = timeout_seconds
        if validar_firmas is None:
            validar_firmas = os.environ.get("TWILIO_VALIDAR_FIRMA", "true").lower() == "true"
        self.validar_firmas = validar_firmas

    @property
    def configurado(self) -> bool:
        """Llamada saliente real: requiere numero de voz y URL TwiML publica."""
        return bool(
            self.account_sid and self.auth_token and self.numero_saliente and self.url_twiml
        )

    # --- entrada -----------------------------------------------------------
    def verificar_firma(self, url: str, parametros: Mapping[str, str], firma: str | None) -> None:
        """Solo aplica a proveedores que firman como Twilio; otros usan token de panel."""
        if not self.validar_firmas or not self.auth_token:
            return
        if not firma:
            raise FirmaInvalidaError("Falta la cabecera X-Twilio-Signature")
        if not hmac.compare_digest(firmar(self.auth_token, url, parametros), firma):
            raise FirmaInvalidaError("La firma X-Twilio-Signature no coincide")

    @staticmethod
    def identificador_evento(carga: Mapping[str, Any]) -> str | None:
        valor = _primero(carga, "CallSid", "call_id", "llamada_id")
        return str(valor) if valor else None

    def normalizar(self, cliente_id: str, carga: Mapping[str, Any]) -> ContactoEntrante:
        numero = _primero(carga, "customer_number", "from", "From", "caller", "telefono")
        if not numero:
            raise ValueError("El webhook de voz no trae el numero del lead")
        transcripcion = str(
            _primero(carga, "transcript", "transcripcion", "transcripcion_texto", "TranscriptionText")
            or ""
        ).strip()
        return ContactoEntrante(
            cliente_id=cliente_id,
            canal=Canal.LLAMADA,
            contacto=str(numero).strip(),
            texto=transcripcion,
            nombre=_primero(carga, "customer_name", "nombre"),
            fuente=str(_primero(carga, "fuente") or "Llamada"),
            metadatos={
                "duracion_segundos": int(
                    _primero(carga, "duration_seconds", "duracion_segundos", "CallDuration") or 0
                ),
                "url_grabacion": _primero(carga, "recording_url", "url_grabacion", "RecordingUrl"),
                "nivel_servicio": self.nivel_servicio(carga).value,
                "call_id": self.identificador_evento(carga),
                "proveedor": str(_primero(carga, "proveedor", "provider") or "desconocido"),
            },
        )

    @staticmethod
    def nivel_servicio(carga: Mapping[str, Any]) -> NivelServicioLlamada:
        crudo = str(_primero(carga, "nivel_servicio", "service_level") or "voz_ia").lower()
        return (
            NivelServicioLlamada.ASISTIDA
            if crudo in ("asistida", "assisted", "humano")
            else NivelServicioLlamada.VOZ_IA
        )

    @staticmethod
    def resultado(carga: Mapping[str, Any]) -> ResultadoLlamada | None:
        crudo = _primero(carga, "resultado", "outcome")
        if crudo is None:
            return None
        try:
            return ResultadoLlamada(str(crudo))
        except ValueError:
            return None

    # --- salida --------------------------------------------------------------
    def llamar(self, destino: str, url_twiml: str | None = None) -> dict:
        """Crea una llamada saliente real (POST /Calls.json de Twilio Voice)."""
        if not self.configurado:
            raise LlamadaError(
                "Faltan credenciales o URL TwiML de voz "
                "(TWILIO_ACCOUNT_SID/AUTH_TOKEN/TWILIO_VOICE_FROM/TWILIO_VOICE_TWIML_URL)"
            )
        return reintentar("twilio.calls", lambda: self._llamar_una_vez(destino, url_twiml))

    def _llamar_una_vez(self, destino: str, url_twiml: str | None) -> dict:
        datos = urlencode(
            {"From": self.numero_saliente, "To": destino, "Url": url_twiml or self.url_twiml}
        ).encode("utf-8")
        credenciales = b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        peticion = Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls.json",
            method="POST",
            data=datos,
            headers={
                "Authorization": f"Basic {credenciales}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
                return json.loads(respuesta.read().decode("utf-8"))
        except HTTPError as exc:
            detalle = exc.read().decode("utf-8", "ignore")[:300]
            raise LlamadaError(f"Twilio Voice respondio {exc.code}: {redactar(detalle)}") from None
        except Exception as exc:  # noqa: BLE001 - red, DNS, timeout
            raise LlamadaError(f"Fallo de red hacia Twilio Voice: {redactar(str(exc))}") from None
