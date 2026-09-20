"""Contexto temporal por tenant.

La zona horaria nunca se toma del servidor: cada tenant declara la suya en el
onboarding y HERMES saluda y decide el horario comercial con ella.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Cliente, ahora

DIAS = (
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
)


@dataclass
class ContextoTemporal:
    fecha_hora_local: datetime
    zona_horaria: str
    dia_semana: str
    saludo: str
    dentro_de_horario: bool

    def como_texto(self) -> str:
        return (
            f"Fecha y hora local del negocio: {self.fecha_hora_local:%Y-%m-%d %H:%M} "
            f"({self.zona_horaria}), {self.dia_semana}. "
            f"Saludo correcto para esta hora: '{self.saludo}'. "
            f"{'Dentro' if self.dentro_de_horario else 'Fuera'} del horario comercial."
        )


def zona_de(cliente: Cliente) -> ZoneInfo:
    try:
        return ZoneInfo(cliente.zona_horaria)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def saludo_para(hora: int) -> str:
    if hora < 12:
        return "Buenos dias"
    if hora < 19:
        return "Buenas tardes"
    return "Buenas noches"


def contexto_temporal(cliente: Cliente, momento: datetime | None = None) -> ContextoTemporal:
    local = (momento or ahora()).astimezone(zona_de(cliente))
    horario = cliente.horario_comercial
    dentro = (
        local.weekday() in horario.dias and horario.hora_inicio <= local.hour < horario.hora_fin
    )
    return ContextoTemporal(
        fecha_hora_local=local,
        zona_horaria=cliente.zona_horaria,
        dia_semana=DIAS[local.weekday()],
        saludo=saludo_para(local.hour),
        dentro_de_horario=dentro,
    )
