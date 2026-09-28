"""Cuestionario de onboarding de HERMES (Seccion 5.7).

Convierte las respuestas del cuestionario en un `Cliente` valido y devuelve los
recordatorios legales que la especificacion exige (grabacion de llamadas).

El plan no crea un HERMES distinto: solo habilita canales. Pedir un canal que el
plan no incluye es un error explicito, no un upgrade silencioso.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .models import (
    CANALES_POR_PLAN,
    Canal,
    Cliente,
    HorarioComercial,
    NivelServicioLlamada,
    Plan,
    ReglasEscalamiento,
)
from .storage import Almacen

RECORDATORIO_GRABACION = (
    "Confirmar los requisitos legales de la jurisdiccion del cliente antes de activar "
    "la grabacion de llamadas (Seccion 5.4.2)."
)


class RespuestasOnboarding(BaseModel):
    cliente_id: str
    nombre_negocio: str
    plan: Plan = Plan.PLAN_1
    representante: str | None = None
    correo_representante: str | None = None
    industria: str | None = None
    descripcion_negocio: str | None = None
    productos: list[str] = Field(default_factory=list)
    objetivos: list[str] = Field(default_factory=list)
    zona_horaria: str = "UTC"
    horario_comercial: HorarioComercial = Field(default_factory=HorarioComercial)
    reglas_escalamiento: ReglasEscalamiento = Field(default_factory=ReglasEscalamiento)
    telefono_whatsapp: str | None = None
    quiere_correo: bool = False
    correo_conectado: str | None = None
    quiere_llamadas: bool = False
    nivel_servicio_llamada: NivelServicioLlamada | None = None
    telefono_llamadas: str | None = None
    autoriza_grabacion: bool = False
    jurisdiccion: str | None = None
    guion_aprobado: bool = True
    sandbox: bool = False


class ResultadoOnboarding(BaseModel):
    cliente: Cliente
    advertencias: list[str] = []


def alta_cliente(almacen: Almacen, respuestas: RespuestasOnboarding) -> ResultadoOnboarding:
    advertencias: list[str] = []
    permitidos = CANALES_POR_PLAN[respuestas.plan]

    try:
        ZoneInfo(respuestas.zona_horaria)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"Zona horaria desconocida: {respuestas.zona_horaria} (use formato IANA, "
            "por ejemplo America/Mexico_City)"
        ) from None

    if not respuestas.telefono_whatsapp:
        advertencias.append(
            "Sin numero de WhatsApp registrado: el canal principal quedara sin numero de salida."
        )
    if respuestas.quiere_correo:
        if Canal.CORREO not in permitidos:
            raise ValueError(
                f"El plan {respuestas.plan.value} no incluye correo; contrate plan_2 o superior"
            )
        if not respuestas.correo_conectado:
            raise ValueError(
                "Si el cliente quiere atencion por correo, debe indicar que direccion se conecta"
            )
    if respuestas.quiere_llamadas:
        if Canal.LLAMADA not in permitidos:
            raise ValueError(
                f"El plan {respuestas.plan.value} no incluye llamadas; contrate plan_3 o piloto"
            )
        if respuestas.nivel_servicio_llamada is None:
            raise ValueError("Indique el nivel de servicio de llamadas: voz_ia o asistida")
        if not respuestas.telefono_llamadas:
            raise ValueError("Indique el numero telefonico para el canal de llamadas")
        if respuestas.autoriza_grabacion:
            if not respuestas.jurisdiccion:
                raise ValueError(
                    "Para autorizar grabacion debe registrarse la jurisdiccion del cliente"
                )
            advertencias.append(RECORDATORIO_GRABACION)
    if not respuestas.productos:
        advertencias.append(
            "Sin productos/servicios cargados: HERMES no podra presentar catalogo y "
            "preguntara en su lugar."
        )

    cliente = Cliente(
        cliente_id=respuestas.cliente_id,
        nombre_negocio=respuestas.nombre_negocio,
        plan=respuestas.plan,
        representante=respuestas.representante,
        correo_representante=respuestas.correo_representante,
        industria=respuestas.industria,
        descripcion_negocio=respuestas.descripcion_negocio,
        productos=respuestas.productos,
        objetivos=respuestas.objetivos,
        zona_horaria=respuestas.zona_horaria,
        horario_comercial=respuestas.horario_comercial,
        reglas_escalamiento=respuestas.reglas_escalamiento,
        correo_conectado=respuestas.correo_conectado,
        telefono_whatsapp=respuestas.telefono_whatsapp,
        telefono_llamadas=respuestas.telefono_llamadas,
        atencion_correo=respuestas.quiere_correo,
        atencion_llamada=respuestas.quiere_llamadas,
        nivel_servicio_llamada=respuestas.nivel_servicio_llamada,
        grabacion_autorizada=respuestas.quiere_llamadas and respuestas.autoriza_grabacion,
        jurisdiccion=respuestas.jurisdiccion,
        guion_aprobado=respuestas.guion_aprobado,
        sandbox=respuestas.sandbox,
    )
    almacen.guardar_cliente(cliente)
    return ResultadoOnboarding(cliente=cliente, advertencias=advertencias)
