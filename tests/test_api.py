"""API HTTP: webhooks, panel y aprobaciones (Secciones 5.5 y 5.7)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes.api import crear_app
from hermes.config import Servicio
from hermes.models import Etapa

CABECERAS = {"X-Panel-Token": "token-de-prueba"}


@pytest.fixture()
def cliente_http(servicio: Servicio) -> TestClient:
    return TestClient(crear_app(servicio))


def _alta(cliente_http: TestClient, cliente_id: str = "cli_001") -> dict:
    respuesta = cliente_http.post(
        "/v1/onboarding",
        headers=CABECERAS,
        json={
            "cliente_id": cliente_id,
            "nombre_negocio": "Estudio Aurora",
            "plan": "piloto",
            "productos": ["Sesion fotografica"],
            "zona_horaria": "America/Mexico_City",
            "telefono_whatsapp": "+521000000000",
            "quiere_correo": True,
            "correo_conectado": "ventas@aurora.com",
            "quiere_llamadas": True,
            "nivel_servicio_llamada": "voz_ia",
            "telefono_llamadas": "+521999999999",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_health(cliente_http: TestClient):
    assert cliente_http.get("/health").json()["modulo"] == "HERMES"


def test_el_panel_exige_token(cliente_http: TestClient):
    assert cliente_http.get("/v1/clientes").status_code == 401


def test_onboarding_incompleto_es_rechazado(cliente_http: TestClient):
    respuesta = cliente_http.post(
        "/v1/onboarding",
        headers=CABECERAS,
        json={"cliente_id": "cli_x", "nombre_negocio": "X", "quiere_correo": True},
    )
    assert respuesta.status_code == 422


def test_grabacion_sin_jurisdiccion_es_rechazada(cliente_http: TestClient):
    respuesta = cliente_http.post(
        "/v1/onboarding",
        headers=CABECERAS,
        json={
            "cliente_id": "cli_y",
            "nombre_negocio": "Y",
            "plan": "plan_3",
            "quiere_llamadas": True,
            "nivel_servicio_llamada": "asistida",
            "telefono_llamadas": "+52100",
            "autoriza_grabacion": True,
        },
    )
    assert respuesta.status_code == 422


def test_onboarding_con_grabacion_advierte_del_requisito_legal(cliente_http: TestClient):
    respuesta = cliente_http.post(
        "/v1/onboarding",
        headers=CABECERAS,
        json={
            "cliente_id": "cli_z",
            "nombre_negocio": "Z",
            "plan": "plan_3",
            "quiere_llamadas": True,
            "nivel_servicio_llamada": "voz_ia",
            "telefono_llamadas": "+52100",
            "autoriza_grabacion": True,
            "jurisdiccion": "MX",
        },
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["advertencias"]


def test_webhook_whatsapp_crea_lead_y_responde(cliente_http: TestClient):
    _alta(cliente_http)
    respuesta = cliente_http.post(
        "/v1/webhooks/whatsapp/cli_001",
        data={"From": "whatsapp:+5215550001", "Body": "Hola, me interesa"},
    )
    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert cuerpo["etapa"] == Etapa.PROSPECCION.value
    assert cuerpo["enviada"] is True


def test_webhook_de_cliente_inexistente_devuelve_404(cliente_http: TestClient):
    respuesta = cliente_http.post(
        "/v1/webhooks/whatsapp/cli_fantasma", data={"From": "whatsapp:+52155", "Body": "hola"}
    )
    assert respuesta.status_code == 404


def test_webhook_correo_y_whatsapp_comparten_el_lead(cliente_http: TestClient):
    _alta(cliente_http)
    correo = cliente_http.post(
        "/v1/webhooks/correo/cli_001",
        json={"from": "ana@correo.com", "subject": "Precio", "body": "me interesa"},
    ).json()
    seguimiento = cliente_http.post(
        "/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:ana@correo.com", "Body": "cuando?"}
    ).json()
    assert seguimiento["lead_id"] == correo["lead_id"]


def test_webhook_llamada_guarda_transcripcion(cliente_http: TestClient):
    _alta(cliente_http)
    respuesta = cliente_http.post(
        "/v1/webhooks/llamada/cli_001",
        json={
            "customer_number": "+5215550002",
            "transcript": "Quiero cotizacion",
            "duration_seconds": 51,
            "recording_url": "https://grabaciones/1.mp3",
        },
    )
    cuerpo = respuesta.json()
    assert cuerpo["llamada"]["transcripcion_texto"] == "Quiero cotizacion"
    assert cuerpo["llamada"]["url_grabacion"] is None  # sin autorizacion de grabacion
    assert cuerpo["llamada"]["lead_id"] == cuerpo["atencion"]["lead_id"]


def test_el_panel_no_cruza_datos_entre_clientes(cliente_http: TestClient):
    _alta(cliente_http, "cli_a")
    _alta(cliente_http, "cli_b")
    cliente_http.post("/v1/webhooks/whatsapp/cli_a", data={"From": "whatsapp:+521", "Body": "hola"})

    leads_a = cliente_http.get("/v1/clientes/cli_a/leads", headers=CABECERAS).json()
    leads_b = cliente_http.get("/v1/clientes/cli_b/leads", headers=CABECERAS).json()
    assert len(leads_a) == 1
    assert leads_b == []
    ajeno = cliente_http.get(
        f"/v1/clientes/cli_b/leads/{leads_a[0]['lead_id']}", headers=CABECERAS
    )
    assert ajeno.status_code == 404


def test_respuesta_fuera_de_guion_espera_aprobacion_y_luego_se_envia(
    cliente_http: TestClient, servicio: Servicio
):
    _alta(cliente_http)
    cliente = servicio.almacen.obtener_cliente("cli_001")
    cliente.guion_aprobado = False
    servicio.almacen.guardar_cliente(cliente)

    class MotorLibre:
        nombre = "falso"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            return "Respuesta redactada por el modelo"

    servicio.hermes.generador.motor = MotorLibre()

    atencion = cliente_http.post(
        "/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:+5215550001", "Body": "hola"}
    ).json()
    assert atencion["nivel"] == 2
    assert atencion["enviada"] is False

    pendientes = cliente_http.get(
        "/v1/decisiones", headers=CABECERAS, params={"cliente_id": "cli_001", "estado": "pendiente"}
    ).json()
    assert pendientes

    aprobada = cliente_http.post(
        f"/v1/decisiones/{atencion['decision_id']}/aprobar",
        headers=CABECERAS,
        json={"aprobado_por": "joshua"},
    ).json()
    assert aprobada["estado"] == "aprobada"

    detalle = cliente_http.get(
        f"/v1/clientes/cli_001/leads/{atencion['lead_id']}", headers=CABECERAS
    ).json()
    salientes = [m for m in detalle["historial"] if m["direccion"] == "saliente"]
    assert salientes and salientes[-1]["texto"] == "Respuesta redactada por el modelo"


def test_reporte_y_hoja_de_leads(cliente_http: TestClient):
    _alta(cliente_http)
    cliente_http.post("/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:+521", "Body": "hola"})
    reporte = cliente_http.get("/v1/clientes/cli_001/reporte", headers=CABECERAS).json()
    assert reporte["total_leads"] == 1
    hoja = cliente_http.get("/v1/clientes/cli_001/hoja-leads", headers=CABECERAS).json()
    assert hoja[0]["etapa"] == Etapa.APERTURA.value


def test_guion_de_llamada_asistida(cliente_http: TestClient):
    _alta(cliente_http)
    atencion = cliente_http.post(
        "/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:+521", "Body": "hola"}
    ).json()
    respuesta = cliente_http.post(
        f"/v1/clientes/cli_001/leads/{atencion['lead_id']}/guion-llamada", headers=CABECERAS
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["guion"]
