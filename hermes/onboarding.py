"""Cuestionario de onboarding de HERMES (Seccion 5.7).

Convierte las respuestas del cuestionario en un `Cliente` valido y devuelve los
recordatorios legales que la especificacion exige (grabacion de llamadas).
"""
from __future__ import annotations

from pydantic import BaseModel

from .models import Cliente, NivelServicioLlamada
from .storage import Almacen

RECORDATORIO_GRABACION = (
    "Confirmar los requisitos legales de la jurisdiccion del cliente antes de activar "
    "la grabacion de llamadas (Seccion 5.4.2)."
)


class RespuestasOnboarding(BaseModel):
    cliente_id: str
    nombre_negocio: str
    plan: str = "basico"
    telefono_whatsapp: str | None = None
    quiere_correo: bool = False
    correo_conectado: str | None = None
    quiere_llamadas: bool = False
    nivel_servicio_llamada: NivelServicioLlamada | None = None
    telefono_llamadas: str | None = None
    autoriza_grabacion: bool = False
    jurisdiccion: str | None = None
    guion_aprobado: bool = True


class ResultadoOnboarding(BaseModel):
    cliente: Cliente
    advertencias: list[str] = []


def alta_cliente(almacen: Almacen, respuestas: RespuestasOnboarding) -> ResultadoOnboarding:
    advertencias: list[str] = []

    if respuestas.quiere_correo and not respuestas.correo_conectado:
        raise ValueError("Si el cliente quiere atencion por correo, debe indicar que direccion se conecta")
    if respuestas.quiere_llamadas:
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

    cliente = Cliente(
        cliente_id=respuestas.cliente_id,
        nombre_negocio=respuestas.nombre_negocio,
        plan=respuestas.plan,
        correo_conectado=respuestas.correo_conectado,
        telefono_whatsapp=respuestas.telefono_whatsapp,
        telefono_llamadas=respuestas.telefono_llamadas,
        atencion_correo=respuestas.quiere_correo,
        atencion_llamada=respuestas.quiere_llamadas,
        nivel_servicio_llamada=respuestas.nivel_servicio_llamada,
        grabacion_autorizada=respuestas.quiere_llamadas and respuestas.autoriza_grabacion,
        jurisdiccion=respuestas.jurisdiccion,
        guion_aprobado=respuestas.guion_aprobado,
    )
    almacen.guardar_cliente(cliente)
    return ResultadoOnboarding(cliente=cliente, advertencias=advertencias)
