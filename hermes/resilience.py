"""Reintentos, timeouts, rate limiting y logs sin secretos.

Un fallo aislado (LLM, Twilio, SMTP) no debe tumbar HERMES ni perder la
conversacion: se registra, se reintenta con backoff y se deja rastro auditable.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_PATRONES_SECRETO = (
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|password|secret|bearer)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AC[0-9a-fA-F]{30,}"),
)


def redactar(texto: str) -> str:
    limpio = texto
    for patron in _PATRONES_SECRETO:
        limpio = patron.sub("[REDACTADO]", limpio)
    return limpio


class FiltroSecretos(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redactar(str(record.msg))
        return True


def obtener_logger(nombre: str = "hermes") -> logging.Logger:
    logger = logging.getLogger(nombre)
    if not any(isinstance(f, FiltroSecretos) for f in logger.filters):
        logger.addFilter(FiltroSecretos())
    return logger


logger = obtener_logger()


class ReintentosAgotados(RuntimeError):
    def __init__(self, operacion: str, ultimo_error: Exception) -> None:
        super().__init__(f"{operacion} fallo tras varios intentos: {redactar(str(ultimo_error))}")
        self.operacion = operacion
        self.ultimo_error = ultimo_error


def reintentar(
    operacion: str,
    funcion: Callable[[], T],
    intentos: int = 3,
    espera_inicial: float = 0.5,
    factor: float = 2.0,
    dormir: Callable[[float], None] = time.sleep,
) -> T:
    """Backoff exponencial; la excepcion final nunca incluye credenciales."""
    espera = espera_inicial
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            return funcion()
        except Exception as exc:  # noqa: BLE001 - se reintenta cualquier fallo de red
            ultimo = exc
            logger.warning("%s: intento %s/%s fallido: %s", operacion, intento, intentos, exc)
            if intento < intentos:
                dormir(espera)
                espera *= factor
    assert ultimo is not None
    raise ReintentosAgotados(operacion, ultimo)


class LimitadorTasa:
    """Ventana deslizante por clave (tenant o webhook)."""

    def __init__(self, maximo: int = 60, ventana_segundos: float = 60.0) -> None:
        self.maximo = maximo
        self.ventana = ventana_segundos
        self._eventos: dict[str, deque[float]] = defaultdict(deque)

    def permitir(self, clave: str, momento: float | None = None) -> bool:
        ahora_s = momento if momento is not None else time.monotonic()
        eventos = self._eventos[clave]
        while eventos and ahora_s - eventos[0] > self.ventana:
            eventos.popleft()
        if len(eventos) >= self.maximo:
            return False
        eventos.append(ahora_s)
        return True
