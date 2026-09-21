"""Procesos de fondo de HERMES.

El correo no tiene webhook propio salvo que el cliente contrate un proveedor
que lo ofrezca: el camino que siempre funciona es leer el buzon por IMAP. Este
worker cierra esa ruta usando el mismo nucleo que los webhooks, con la misma
idempotencia (Message-ID) y el mismo registro de entrega.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Servicio
from .models import Canal, EstadoEntrega, Estatus
from .resilience import logger
from .tiempo import contexto_temporal


@dataclass
class ResumenCiclo:
    leidos: int = 0
    procesados: int = 0
    repetidos: int = 0
    fallidos: int = 0


def procesar_correo_entrante(servicio: Servicio, cliente_id: str, limite: int = 20) -> ResumenCiclo:
    """Un ciclo de lectura IMAP -> pipeline -> respuesta por SMTP."""
    resumen = ResumenCiclo()
    for carga in servicio.correo.leer_no_leidos(limite):
        resumen.leidos += 1
        evento_id = servicio.correo.identificador_evento(carga)
        if evento_id and servicio.almacen.evento_ya_procesado(
            cliente_id, Canal.CORREO.value, evento_id
        ):
            resumen.repetidos += 1
            continue
        try:
            entrante = servicio.correo.normalizar(cliente_id, carga)
            resultado = servicio.hermes.atender(entrante)
        except Exception as exc:  # noqa: BLE001 - un correo malo no detiene el buzon
            resumen.fallidos += 1
            logger.warning("correo entrante %s no procesado: %s", evento_id, exc)
            continue

        if resultado.enviada and resultado.respuesta and servicio.config.envio_real:
            error: str | None = None
            try:
                servicio.correo.enviar(resultado.lead.contacto, resultado.respuesta)
            except Exception as exc:  # noqa: BLE001 - se registra y se sigue
                error = str(exc)
                logger.warning("no se pudo responder a %s: %s", resultado.lead.lead_id, exc)
            if resultado.mensaje_id:
                servicio.almacen.actualizar_entrega(
                    cliente_id,
                    resultado.mensaje_id,
                    EstadoEntrega.FALLIDA if error else EstadoEntrega.ENVIADA,
                    error,
                )
        if evento_id:
            servicio.almacen.marcar_evento(
                cliente_id, Canal.CORREO.value, evento_id, resultado.lead.lead_id
            )
        resumen.procesados += 1
    return resumen


def programar_seguimientos_vencidos(
    servicio: Servicio,
    cliente_id: str,
    horas_inactividad: int = 48,
    momento: datetime | None = None,
) -> list[str]:
    """Reactiva leads en silencio, dentro del horario comercial del tenant.

    Devuelve los lead_id tocados. Sale por el mismo `programar_seguimiento` que
    usa el panel, asi que la gobernanza y el limite de seguimientos aplican
    igual; aqui solo se decide *a quien* toca.
    """
    cliente = servicio.almacen.obtener_cliente(cliente_id)
    if cliente is None:
        raise ValueError(f"Cliente no registrado: {cliente_id}")
    contexto = contexto_temporal(cliente, momento)
    if not contexto.dentro_de_horario:
        return []

    ahora_local = momento or datetime.now(timezone.utc)
    limite = ahora_local - timedelta(hours=horas_inactividad)
    tocados: list[str] = []
    for lead in servicio.almacen.listar_leads(cliente_id):
        if lead.estatus in (Estatus.CERRADO, Estatus.PERDIDO) or lead.requiere_humano:
            continue
        referencia = lead.ultimo_contacto or lead.fecha_entrada
        if referencia.tzinfo is None:
            referencia = referencia.replace(tzinfo=timezone.utc)
        if referencia > limite:
            continue
        try:
            servicio.hermes.programar_seguimiento(cliente_id, lead.lead_id)
        except Exception as exc:  # noqa: BLE001 - un lead no bloquea al resto
            logger.warning("seguimiento de %s fallido: %s", lead.lead_id, exc)
            continue
        tocados.append(lead.lead_id)
    return tocados


def bucle_correo(
    servicio: Servicio,
    cliente_id: str,
    segundos: int = 60,
    ciclos: int | None = None,
    dormir: Callable[[float], None] = time.sleep,
) -> list[ResumenCiclo]:
    """Sondeo periodico; `ciclos=None` corre indefinidamente."""
    resumenes: list[ResumenCiclo] = []
    ejecutados = 0
    while ciclos is None or ejecutados < ciclos:
        try:
            resumenes.append(procesar_correo_entrante(servicio, cliente_id))
        except Exception as exc:  # noqa: BLE001 - IMAP caido no debe matar el worker
            logger.warning("ciclo de correo fallido: %s", exc)
            resumenes.append(ResumenCiclo(fallidos=1))
        ejecutados += 1
        if ciclos is None or ejecutados < ciclos:
            dormir(segundos)
    return resumenes
