"""Reporte ejecutivo en PDF y worker de correo entrante por IMAP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hermes.api import crear_app
from hermes.config import Servicio
from hermes.models import Canal, EstadoEntrega, Etapa
from hermes.pipeline import ContactoEntrante
from hermes.reportes import generar_reporte_pdf
from hermes.storage import Almacen
from hermes.workers import procesar_correo_entrante, programar_seguimientos_vencidos

from .conftest import dar_de_alta


def entrante(cliente_id: str, texto: str) -> ContactoEntrante:
    return ContactoEntrante(
        cliente_id=cliente_id, canal=Canal.WHATSAPP, contacto="+5215550001", texto=texto
    )


@pytest.fixture()
def cliente_http(servicio: Servicio) -> TestClient:
    return TestClient(crear_app(servicio), headers={"X-Panel-Token": "token-de-prueba"})


def test_reporte_pdf_usa_datos_reales_del_tenant(hermes, almacen: Almacen):
    cliente = dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "Hola, me interesa"))

    pdf = generar_reporte_pdf(almacen, cliente, hermes.reporte("cli_001"))

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_endpoint_pdf_exige_token(
    cliente_http: TestClient, servicio: Servicio, almacen: Almacen
):
    dar_de_alta(almacen)
    anonimo = TestClient(crear_app(servicio))
    assert anonimo.get("/v1/clientes/cli_001/reporte.pdf").status_code == 401

    respuesta = cliente_http.get("/v1/clientes/cli_001/reporte.pdf")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "application/pdf"
    assert respuesta.content.startswith(b"%PDF")


class _BuzonFalso:
    """Reemplaza solo la capa IMAP/SMTP; el pipeline es el de produccion."""

    def __init__(self, cargas):
        self.cargas = cargas
        self.enviados: list[tuple[str, str]] = []

    def leer_no_leidos(self, limite: int = 20):
        return self.cargas[:limite]

    def enviar(self, destino: str, texto: str, asunto: str = "") -> None:
        self.enviados.append((destino, texto))


@pytest.fixture()
def servicio_correo(servicio: Servicio, almacen: Almacen) -> Servicio:
    dar_de_alta(almacen)
    servicio.config.envio_real = True
    return servicio


def test_worker_correo_procesa_y_responde(servicio_correo: Servicio, monkeypatch):
    buzon = _BuzonFalso(
        [{"from": "Ana <ana@ejemplo.com>", "subject": "Precio", "body": "Cuanto cuesta?",
          "message_id": "<m-1@ejemplo.com>"}]
    )
    monkeypatch.setattr(servicio_correo.correo, "leer_no_leidos", buzon.leer_no_leidos)
    monkeypatch.setattr(servicio_correo.correo, "enviar", buzon.enviar)

    resumen = procesar_correo_entrante(servicio_correo, "cli_001")

    assert resumen.procesados == 1
    leads = servicio_correo.almacen.listar_leads("cli_001")
    assert [lead.contacto for lead in leads] == ["ana@ejemplo.com"]
    assert buzon.enviados and buzon.enviados[0][0] == "ana@ejemplo.com"
    mensajes = servicio_correo.almacen.historial("cli_001", leads[0].lead_id)
    salientes = [m for m in mensajes if m.direccion == "saliente"]
    assert salientes[-1].estado_entrega == EstadoEntrega.ENVIADA


def test_worker_correo_es_idempotente(servicio_correo: Servicio, monkeypatch):
    carga = {"from": "ana@ejemplo.com", "subject": "Hola", "body": "Info",
             "message_id": "<m-2@ejemplo.com>"}
    buzon = _BuzonFalso([carga])
    monkeypatch.setattr(servicio_correo.correo, "leer_no_leidos", buzon.leer_no_leidos)
    monkeypatch.setattr(servicio_correo.correo, "enviar", buzon.enviar)

    procesar_correo_entrante(servicio_correo, "cli_001")
    segundo = procesar_correo_entrante(servicio_correo, "cli_001")

    assert segundo.repetidos == 1
    assert segundo.procesados == 0
    assert len(servicio_correo.almacen.listar_leads("cli_001")) == 1


def _lunes_a_mediodia() -> datetime:
    """Lunes 12:00 en America/Mexico_City, dentro del horario comercial por defecto."""
    return datetime(2025, 6, 2, 18, 0, tzinfo=timezone.utc)


def test_seguimiento_automatico_solo_toca_leads_en_silencio(
    servicio: Servicio, almacen: Almacen
):
    dar_de_alta(almacen)
    servicio.hermes.atender(entrante("cli_001", "Hola, me interesa"))
    reciente = almacen.listar_leads("cli_001")[0]

    momento = _lunes_a_mediodia()
    assert programar_seguimientos_vencidos(servicio, "cli_001", momento=momento) == []

    reciente.ultimo_contacto = momento - timedelta(days=5)
    almacen.guardar_lead(reciente)
    tocados = programar_seguimientos_vencidos(servicio, "cli_001", momento=momento)

    assert tocados == [reciente.lead_id]
    assert almacen.obtener_lead("cli_001", reciente.lead_id).etapa == Etapa.SEGUIMIENTO


def test_seguimiento_automatico_respeta_el_horario_del_tenant(
    servicio: Servicio, almacen: Almacen
):
    dar_de_alta(almacen)
    servicio.hermes.atender(entrante("cli_001", "Hola"))
    lead = almacen.listar_leads("cli_001")[0]
    madrugada = _lunes_a_mediodia() - timedelta(hours=9)
    lead.ultimo_contacto = madrugada - timedelta(days=5)
    almacen.guardar_lead(lead)

    assert programar_seguimientos_vencidos(servicio, "cli_001", momento=madrugada) == []


def test_worker_correo_no_se_detiene_por_un_correo_invalido(
    servicio_correo: Servicio, monkeypatch
):
    buzon = _BuzonFalso(
        [
            {"from": "sin-direccion", "subject": "", "body": "x", "message_id": "<m-3>"},
            {"from": "ok@ejemplo.com", "subject": "Hola", "body": "Info",
             "message_id": "<m-4>"},
        ]
    )
    monkeypatch.setattr(servicio_correo.correo, "leer_no_leidos", buzon.leer_no_leidos)
    monkeypatch.setattr(servicio_correo.correo, "enviar", buzon.enviar)

    resumen = procesar_correo_entrante(servicio_correo, "cli_001")

    assert resumen.fallidos == 1
    assert resumen.procesados == 1
