"""Canal llamada telefonica (Seccion 5.4).

Normaliza el webhook de una plataforma de voz conversacional (Vapi o Retell AI
son las recomendadas en 5.8) al mismo contrato que los demas canales. La
grabacion solo se conserva si el cliente la autorizo y su jurisdiccion lo
permite: esa validacion vive en `Hermes.registrar_llamada`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import Canal, NivelServicioLlamada, ResultadoLlamada
from ..pipeline import ContactoEntrante


def _primero(carga: Mapping[str, Any], *claves: str) -> Any | None:
    for clave in claves:
        valor = carga.get(clave)
        if valor not in (None, ""):
            return valor
    return None


class AdaptadorLlamada:
    canal = Canal.LLAMADA

    def normalizar(self, cliente_id: str, carga: Mapping[str, Any]) -> ContactoEntrante:
        numero = _primero(carga, "customer_number", "from", "caller", "telefono")
        if not numero:
            raise ValueError("El webhook de voz no trae el numero del lead")
        transcripcion = str(
            _primero(carga, "transcript", "transcripcion", "transcripcion_texto") or ""
        ).strip()
        return ContactoEntrante(
            cliente_id=cliente_id,
            canal=Canal.LLAMADA,
            contacto=str(numero).strip(),
            texto=transcripcion,
            nombre=_primero(carga, "customer_name", "nombre"),
            fuente=str(_primero(carga, "fuente") or "Llamada"),
            metadatos={
                "duracion_segundos": int(_primero(carga, "duration_seconds", "duracion_segundos") or 0),
                "url_grabacion": _primero(carga, "recording_url", "url_grabacion"),
                "nivel_servicio": self.nivel_servicio(carga).value,
                "call_id": _primero(carga, "call_id", "llamada_id"),
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
