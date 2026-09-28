"""Configuracion y ensamblado de HERMES desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .channels import AdaptadorCorreo, AdaptadorLlamada, AdaptadorWhatsApp
from .governance import Gobernanza
from .learning import Aprendizaje
from .llm import GeneradorRespuestas, MotorClaude
from .nexus import ClienteNexus
from .pipeline import Hermes
from .storage import Almacen


def _entorno(nombre: str, respaldo: str = "") -> str:
    return os.environ.get(nombre, respaldo)


def _bandera(nombre: str, respaldo: bool = False) -> bool:
    return os.environ.get(nombre, str(respaldo)).strip().lower() == "true"


def _origenes() -> tuple[str, ...]:
    return tuple(
        origen.strip()
        for origen in os.environ.get("HERMES_ORIGENES_PANEL", "").split(",")
        if origen.strip()
    )


@dataclass
class Configuracion:
    """Se lee del entorno en cada instancia, no al importar el modulo."""

    ruta_base_datos: str = field(default_factory=lambda: _entorno("HERMES_DB", "hermes.db"))
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY")
    )
    modelo: str = field(
        default_factory=lambda: _entorno("HERMES_MODELO", "claude-sonnet-4-20250514")
    )
    token_panel: str | None = field(default_factory=lambda: os.environ.get("HERMES_PANEL_TOKEN"))
    token_webhook: str | None = field(
        default_factory=lambda: os.environ.get("HERMES_WEBHOOK_TOKEN")
    )
    url_publica: str | None = field(default_factory=lambda: os.environ.get("HERMES_URL_PUBLICA"))
    envio_real: bool = field(default_factory=lambda: _bandera("HERMES_ENVIO_REAL"))
    origenes_panel: tuple[str, ...] = field(default_factory=_origenes)
    # Sin token configurado la API queda abierta; solo se permite si se pide
    # explicitamente (desarrollo local), nunca por omision.
    permitir_sin_token: bool = field(default_factory=lambda: _bandera("HERMES_PERMITIR_SIN_TOKEN"))
    limite_webhook_por_minuto: int = field(
        default_factory=lambda: int(_entorno("HERMES_LIMITE_WEBHOOK", "120") or 120)
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
