"""Lectura comercial del mensaje: intencion, objeciones, senales y escalamiento.

Es una capa determinista y auditable que corre siempre, tambien cuando el LLM
esta disponible: el modelo redacta, pero la clasificacion que dispara decisiones
(avance de etapa, escalamiento a humano) no depende de texto generado.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .models import Cliente, Etapa, Intencion, Lead, MotivoEscalamiento

SENALES_COMPRA = (
    "quiero comprar",
    "lo quiero",
    "como pago",
    "donde pago",
    "acepto",
    "confirmo",
    "de acuerdo",
    "adelante",
    "reservar",
    "apartar",
    "agendar",
    "firmar",
)

OBJECIONES = {
    "precio": ("caro", "costoso", "muy alto", "no me alcanza", "descuento", "rebaja", "barato"),
    "tiempo": ("despues", "mas adelante", "ahora no", "lo pienso", "no tengo tiempo"),
    "confianza": ("es seguro", "garantia", "estafa", "referencias", "opiniones"),
    "competencia": ("otra empresa", "mas barato en", "competencia", "otra opcion"),
    "necesidad": ("no lo necesito", "no me sirve", "no aplica"),
}

PALABRAS_INTENCION: tuple[tuple[Intencion, tuple[str, ...]], ...] = (
    (
        Intencion.PIDE_HUMANO,
        ("hablar con una persona", "un humano", "un asesor", "un agente", "una persona real"),
    ),
    (Intencion.QUEJA, ("queja", "reclamo", "pesimo", "malisimo", "demanda", "estoy molesto")),
    (
        Intencion.COMPRA,
        ("comprar", "pagar", "pago", "contratar", "reservar", "apartar", "confirmo", "acepto"),
    ),
    (Intencion.PRECIO, ("precio", "costo", "cuanto cuesta", "cotizacion", "cotiza", "tarifa")),
    (Intencion.DISPONIBILIDAD, ("disponible", "disponibilidad", "tienen", "hay ", "stock")),
    (Intencion.SOPORTE, ("no funciona", "problema con", "soporte", "ayuda con mi")),
    (Intencion.DESINTERES, ("no me interesa", "no gracias", "ya compre", "ya no", "baja")),
    (
        Intencion.INFORMACION,
        ("informacion", "informes", "me interesa", "quiero saber", "como funciona"),
    ),
    (
        Intencion.SALUDO,
        ("hola", "buenos dias", "buenas tardes", "buenas noches", "que tal", "buen dia"),
    ),
)

URGENCIA = ("hoy", "urgente", "ahora mismo", "de inmediato", "para ya", "cuanto antes")

TEMAS_LEGALES = ("abogado", "demanda", "contrato legal", "denuncia", "juridico", "fraude")
TEMAS_FINANCIEROS = ("reembolso", "devolucion del dinero", "cargo duplicado", "factura incorrecta")
PIDE_DESCUENTO = ("descuento", "rebaja", "promocion especial", "precio especial", "me lo dejas en")


def normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto.lower())
    return "".join(caracter for caracter in plano if not unicodedata.combining(caracter))


@dataclass
class AnalisisMensaje:
    intencion: Intencion = Intencion.DESCONOCIDA
    confianza: float = 0.0
    urgencia: bool = False
    objeciones: list[str] = field(default_factory=list)
    senales_compra: list[str] = field(default_factory=list)
    pide_descuento: bool = False
    tema_legal: bool = False
    tema_financiero: bool = False
    texto_vacio: bool = False

    def resumen(self) -> str:
        partes = [f"intencion={self.intencion.value}", f"confianza={self.confianza:.2f}"]
        if self.urgencia:
            partes.append("urgencia")
        if self.objeciones:
            partes.append("objeciones=" + ",".join(self.objeciones))
        if self.senales_compra:
            partes.append("senales_compra=" + ",".join(self.senales_compra))
        return " ".join(partes)


def analizar(texto: str) -> AnalisisMensaje:
    plano = normalizar(texto or "")
    analisis = AnalisisMensaje(texto_vacio=not plano.strip())
    if analisis.texto_vacio:
        return analisis

    coincidencias = 0
    for intencion, palabras in PALABRAS_INTENCION:
        encontradas = [palabra for palabra in palabras if palabra in plano]
        if encontradas:
            coincidencias += len(encontradas)
            if analisis.intencion == Intencion.DESCONOCIDA:
                analisis.intencion = intencion

    analisis.senales_compra = [senal for senal in SENALES_COMPRA if senal in plano]
    for categoria, palabras in OBJECIONES.items():
        if any(palabra in plano for palabra in palabras):
            analisis.objeciones.append(categoria)
    if analisis.objeciones and analisis.intencion in (
        Intencion.DESCONOCIDA,
        Intencion.INFORMACION,
    ):
        analisis.intencion = Intencion.OBJECION

    analisis.urgencia = any(palabra in plano for palabra in URGENCIA)
    analisis.pide_descuento = any(palabra in plano for palabra in PIDE_DESCUENTO)
    analisis.tema_legal = any(palabra in plano for palabra in TEMAS_LEGALES)
    analisis.tema_financiero = any(palabra in plano for palabra in TEMAS_FINANCIEROS)

    base = 0.0 if analisis.intencion == Intencion.DESCONOCIDA else 0.5
    base += min(0.1 * coincidencias, 0.3)
    base += 0.1 if analisis.senales_compra else 0.0
    if len(plano.split()) < 3 and analisis.intencion == Intencion.DESCONOCIDA:
        base = 0.1
    analisis.confianza = round(min(base, 0.95), 2)
    return analisis


def evaluar_escalamiento(
    analisis: AnalisisMensaje, cliente: Cliente, lead: Lead
) -> MotivoEscalamiento | None:
    """Decide si HERMES debe dejar de responder solo (Seccion 17)."""
    reglas = cliente.reglas_escalamiento
    plano = normalizar(lead.ultima_respuesta_lead or "")

    if reglas.escalar_solicitud_humano and analisis.intencion == Intencion.PIDE_HUMANO:
        return MotivoEscalamiento.SOLICITA_HUMANO
    if reglas.escalar_temas_legales and analisis.tema_legal:
        return MotivoEscalamiento.LEGAL
    if analisis.tema_financiero:
        return MotivoEscalamiento.FINANCIERO_SENSIBLE
    if reglas.escalar_quejas and analisis.intencion == Intencion.QUEJA:
        return MotivoEscalamiento.QUEJA_COMPLEJA
    if (
        reglas.escalar_descuentos
        and analisis.pide_descuento
        and reglas.descuento_maximo_por_ciento <= 0
    ):
        return MotivoEscalamiento.FUERA_DE_LIMITES
    if any(normalizar(clave) in plano for clave in reglas.palabras_clave_extra):
        return MotivoEscalamiento.REGLA_DEL_CLIENTE
    if analisis.texto_vacio:
        return MotivoEscalamiento.INFORMACION_INSUFICIENTE
    # En Apertura el objetivo es justamente averiguar que necesita el lead: no
    # se escala por no entenderlo todavia, se pregunta.
    if lead.etapa != Etapa.APERTURA and analisis.confianza < reglas.confianza_minima:
        return MotivoEscalamiento.BAJA_CONFIANZA
    return None


def objetivo_de_etapa(lead: Lead) -> str:
    objetivos = {
        "Apertura": "Identificar la necesidad y el producto de interes",
        "Prospeccion": "Calificar necesidad, urgencia y presupuesto",
        "Cierre": "Obtener la confirmacion de compra o la cita",
        "Seguimiento": "Recuperar al prospecto sin presionar",
    }
    return objetivos[lead.etapa.value]
