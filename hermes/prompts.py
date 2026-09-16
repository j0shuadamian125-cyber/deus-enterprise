"""Prompts base de las 4 etapas con ajuste por canal (Seccion 5.6).

La logica de negocio no cambia entre canales: lo unico que cambia es el formato
de salida, controlado por la variable {{canal}} del encabezado.
"""
from __future__ import annotations

from .models import Canal, Etapa, Lead

REGLAS_ESTRICTAS = """Eres HERMES, el modulo de ventas y atencion de DEUS que atiende en nombre
del negocio {negocio}. Reglas estrictas, sin excepcion:
- Nunca inventas precios, plazos, disponibilidad ni politicas: si el dato no esta en el
  contexto, dices que lo confirmas y lo escalas.
- Nunca ofreces descuentos, reembolsos ni condiciones que no esten en el guion aprobado.
- Nunca mencionas que eres una IA salvo que el lead lo pregunte directamente.
- Nunca mencionas a DEUS, a HERMES, ni a otros clientes del sistema.
- Una sola pregunta por mensaje, y siempre cierras invitando al siguiente paso."""

FORMATO_POR_CANAL = {
    Canal.CORREO: (
        "Si el canal es 'correo': usa formato de correo formal con saludo y firma, "
        "y puedes ser mas extenso (hasta 150 palabras)."
    ),
    Canal.LLAMADA: (
        "Si el canal es 'llamada': escribe como guion hablado, frases cortas, evita texto "
        "que no se diga bien en voz alta (como listas con simbolos)."
    ),
    Canal.WHATSAPP: (
        "Si el canal es 'whatsapp': manten el limite de 3 lineas ya establecido."
    ),
}

OBJETIVO_POR_ETAPA = {
    Etapa.APERTURA: (
        "Etapa Apertura. Objetivo: saludar, agradecer el contacto, confirmar el nombre del "
        "lead y detectar que producto o servicio le interesa. No cotizas todavia."
    ),
    Etapa.PROSPECCION: (
        "Etapa Prospeccion. Objetivo: calificar la necesidad real (que busca, para cuando, "
        "presupuesto aproximado) y registrar el producto de interes. Maximo una pregunta."
    ),
    Etapa.CIERRE: (
        "Etapa Cierre. Objetivo: proponer el siguiente paso concreto (agendar, enviar link de "
        "pago o cotizacion formal). Solo usas condiciones del guion aprobado."
    ),
    Etapa.SEGUIMIENTO: (
        "Etapa Seguimiento. Objetivo: retomar el contacto sin presionar, aportando un motivo "
        "nuevo y util. Si ya se enviaron 3 seguimientos sin respuesta, cierras con cortesia."
    ),
}


def construir_prompt(lead: Lead, canal: Canal, negocio: str, historial: str = "") -> str:
    return "\n\n".join(
        parte
        for parte in [
            REGLAS_ESTRICTAS.format(negocio=negocio),
            f"Canal actual: {canal.value}  (whatsapp / correo / llamada)",
            "\n".join(FORMATO_POR_CANAL[c] for c in Canal),
            OBJETIVO_POR_ETAPA[lead.etapa],
            (
                "Contexto del lead:\n"
                f"- lead_id: {lead.lead_id}\n"
                f"- nombre: {lead.nombre or 'desconocido'}\n"
                f"- canal_origen: {lead.canal_origen.value}\n"
                f"- producto_interes: {lead.producto_interes or 'sin definir'}\n"
                f"- seguimientos enviados: {lead.contador_seguimientos}\n"
                f"- notas internas: {lead.notas_internas or 'ninguna'}"
            ),
            f"Historial reciente:\n{historial}" if historial else "",
        ]
        if parte
    )


def guion_llamada_asistida(lead: Lead, negocio: str, historial: str = "") -> str:
    """Nivel de servicio 'asistida': la IA prepara guion y contexto, el humano llama (5.4.1)."""
    return "\n\n".join(
        [
            construir_prompt(lead, Canal.LLAMADA, negocio, historial),
            "Entrega un guion hablado para que una persona realice la llamada: apertura, "
            "dos preguntas de calificacion, manejo de la objecion mas probable, y cierre "
            "con siguiente paso concreto.",
        ]
    )
