"""Configuracion y ensamblado de HERMES desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .channels import AdaptadorCorreo, AdaptadorLlamada, AdaptadorWhatsApp
from .governance import Gobernanza
from .learning import Aprendizaje
from .llm import GeneradorRespuestas, MotorClaude
from .nexus import ClienteNexus
from .pipeline import Hermes
from .storage import Almacen


@dataclass
class Configuracion:
    ruta_base_datos: str = os.environ.get("HERMES_DB", "hermes.db")
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
    modelo: str = os.environ.get("HERMES_MODELO", "claude-sonnet-4-20250514")
    token_panel: str | None = os.environ.get("HERMES_PANEL_TOKEN")
    token_webhook: str | None = os.environ.get("HERMES_WEBHOOK_TOKEN")
    url_publica: str | None = os.environ.get("HERMES_URL_PUBLICA")
    envio_real: bool = os.environ.get("HERMES_ENVIO_REAL", "false").lower() == "true"
    origenes_panel: tuple[str, ...] = tuple(
        origen.strip()
        for origen in os.environ.get("HERMES_ORIGENES_PANEL", "").split(",")
        if origen.strip()
    )


@dataclass
class Servicio:
    """Todas las piezas de HERMES ya cableadas entre si."""

    config: Configuracion
    almacen: Almacen
    hermes: Hermes
    whatsapp: AdaptadorWhatsApp
    correo: AdaptadorCorreo
    llamada: AdaptadorLlamada
    nexus: ClienteNexus | None

    @property
    def gobernanza(self) -> Gobernanza:
        return self.hermes.gobernanza

    @property
    def aprendizaje(self) -> Aprendizaje:
        return Aprendizaje(self.almacen, self.gobernanza)


def construir_servicio(config: Configuracion | None = None) -> Servicio:
    config = config or Configuracion()
    almacen = Almacen(config.ruta_base_datos)
    motor = (
        MotorClaude(api_key=config.anthropic_api_key, modelo=config.modelo)
        if config.anthropic_api_key
        else None
    )
    return Servicio(
        config=config,
        almacen=almacen,
        hermes=Hermes(almacen, GeneradorRespuestas(motor)),
        whatsapp=AdaptadorWhatsApp(),
        correo=AdaptadorCorreo(),
        llamada=AdaptadorLlamada(),
        nexus=ClienteNexus.desde_entorno(),
    )
