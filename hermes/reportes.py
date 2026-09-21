"""Reporte ejecutivo del cliente (Seccion 27).

Solo imprime lo que el CRM midio. No hay estimaciones ni proyecciones: cuando
falta informacion el reporte lo dice en lugar de rellenar el hueco. Las
oportunidades potenciales se marcan como tales y se derivan de conteos reales.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Cliente, EstadoEscalamiento, Estatus
from .storage import Almacen

_ESTILOS = getSampleStyleSheet()
_TITULO = ParagraphStyle("titulo", parent=_ESTILOS["Title"], fontSize=20, spaceAfter=4)
_SECCION = ParagraphStyle("seccion", parent=_ESTILOS["Heading2"], fontSize=13, spaceBefore=14)
_TEXTO = ParagraphStyle("texto", parent=_ESTILOS["BodyText"], fontSize=10, leading=14)
_TENUE = ParagraphStyle("tenue", parent=_TEXTO, textColor=colors.HexColor("#5b6770"))

_ESTILO_TABLA = TableStyle(
    [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0b3d3b")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#0b3d3b")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f6")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
)


def _tabla(encabezados: list[str], filas: list[list[str]]) -> Table:
    tabla = Table([encabezados, *filas], hAlign="LEFT", colWidths=[9 * cm, 6 * cm])
    tabla.setStyle(_ESTILO_TABLA)
    return tabla


def _conclusion(metricas: dict, escalamientos_pendientes: int) -> str:
    total = metricas["total_leads"]
    if total == 0:
        return (
            "Todavia no hay conversaciones registradas en el periodo, asi que no es posible "
            "evaluar resultados comerciales. En cuanto entren los primeros mensajes este "
            "reporte mostrara cifras reales."
        )
    cerrados = metricas["cerrados"]
    partes = [
        f"Se atendieron {total} prospectos y se cerraron {cerrados} "
        f"({cerrados * 100 // total}% de los atendidos)."
    ]
    if escalamientos_pendientes:
        partes.append(
            f"Hay {escalamientos_pendientes} conversaciones esperando respuesta de una persona: "
            "son la accion mas rentable a corto plazo porque ya mostraron interes."
        )
    seguimiento = metricas["por_etapa"].get("seguimiento", 0)
    if seguimiento:
        partes.append(
            f"{seguimiento} prospectos estan en seguimiento y siguen siendo recuperables."
        )
    return " ".join(partes)


def generar_reporte_pdf(almacen: Almacen, cliente: Cliente, metricas: dict) -> bytes:
    """Devuelve el PDF del reporte ejecutivo con los datos reales del tenant."""
    leads = almacen.listar_leads(cliente.cliente_id)
    escalamientos = almacen.listar_escalamientos(cliente.cliente_id)
    pendientes = sum(1 for e in escalamientos if e.estado == EstadoEscalamiento.PENDIENTE)
    resultados = almacen.listar_resultados(cliente.cliente_id)
    estrategias = almacen.listar_estrategias(cliente.cliente_id)

    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        title=f"HERMES - Reporte ejecutivo - {cliente.nombre_negocio}",
        author="HERMES",
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    partes: list = [
        Paragraph("Reporte ejecutivo", _TITULO),
        Paragraph(
            f"{cliente.nombre_negocio} &middot; plan {cliente.plan.value} &middot; "
            f"generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} "
            f"({cliente.zona_horaria})",
            _TENUE,
        ),
        Paragraph(
            "Todas las cifras de este reporte son datos reales tomados del sistema. "
            "No contiene estimaciones; las oportunidades potenciales aparecen en su "
            "propia seccion y estan marcadas como tales.",
            _TENUE,
        ),
        Paragraph("Resumen", _SECCION),
        _tabla(
            ["Indicador", "Valor (dato real)"],
            [
                ["Prospectos atendidos", str(metricas["total_leads"])],
                ["Cerrados", str(metricas["cerrados"])],
                ["Perdidos", str(metricas["perdidos"])],
                ["Conversaciones con llamada", str(metricas["llamadas"])],
                ["Escalamientos a una persona", str(metricas["escalamientos"])],
                ["Escalamientos sin atender", str(pendientes)],
            ],
        ),
        Paragraph("Prospectos por etapa", _SECCION),
        _tabla(
            ["Etapa", "Prospectos"],
            [[etapa, str(valor)] for etapa, valor in metricas["por_etapa"].items()],
        ),
        Paragraph("Prospectos por canal de entrada", _SECCION),
        _tabla(
            ["Canal", "Prospectos"],
            [[canal, str(valor)] for canal, valor in metricas["por_canal_origen"].items()],
        ),
    ]

    partes.append(Paragraph("Resultados registrados", _SECCION))
    if resultados:
        filas = [
            [f"{r.objetivo} &rarr; {r.accion}", f"{r.resultado} ({r.impacto})"]
            for r in resultados[:15]
        ]
        partes.append(_tabla(["Objetivo y accion", "Resultado"], filas))
    else:
        partes.append(
            Paragraph(
                "Sin resultados registrados todavia. El sistema los va anotando conforme "
                "las conversaciones avanzan.",
                _TENUE,
            )
        )

    partes.append(Paragraph("Estrategias en uso", _SECCION))
    if estrategias:
        partes.append(
            _tabla(
                ["Estrategia", "Estado y uso"],
                [
                    [
                        f"{e.nombre} (v{e.version}, {e.etapa.value})",
                        f"{e.estado.value} &middot; {e.resultados.get('usos', 0)} usos",
                    ]
                    for e in estrategias
                ],
            )
        )
    else:
        partes.append(
            Paragraph("Todavia no hay estrategias propias; se usa el guion base.", _TENUE)
        )

    activos = [
        lead for lead in leads if lead.estatus in (Estatus.NUEVO, Estatus.EN_PROCESO)
    ]
    partes.append(
        KeepTogether(
            [
                Paragraph("Oportunidades potenciales (no son ventas)", _SECCION),
                Paragraph(
                    f"{len(activos)} prospectos siguen activos y {pendientes} esperan "
                    "respuesta humana. Son oportunidades, no ingresos comprometidos: "
                    "el sistema no estima su valor porque no dispone de precios cerrados.",
                    _TEXTO,
                ),
            ]
        )
    )

    partes.append(Paragraph("Conclusion", _SECCION))
    partes.append(Paragraph(_conclusion(metricas, pendientes), _TEXTO))
    partes.append(Spacer(1, 10))

    documento.build(partes)
    return buffer.getvalue()
