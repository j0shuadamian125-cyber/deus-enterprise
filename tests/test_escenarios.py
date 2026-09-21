"""Los 14 escenarios minimos de la directiva, extremo a extremo sobre el nucleo real.

Cada prueba entra por donde entraria un cliente real (webhook o pipeline) y
comprueba el estado que queda en el CRM, no solo que el codigo no reviente.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes.api import crear_app
from hermes.channels.whatsapp import AdaptadorWhatsApp, FirmaInvalidaError, firmar
from hermes.config import Configuracion, Servicio
from hermes.models import Canal, Estatus, Etapa, MotivoEscalamiento, Plan
from hermes.pipeline import ContactoEntrante, Hermes
from hermes.resilience import ReintentosAgotados, reintentar
from hermes.storage import AislamientoError, Almacen

from .conftest import dar_de_alta

CABECERAS = {"X-Panel-Token": "token-de-prueba"}


def entrante(cliente_id: str, texto: str, contacto: str = "+5215550001") -> ContactoEntrante:
    return ContactoEntrante(
        cliente_id=cliente_id, canal=Canal.WHATSAPP, contacto=contacto, texto=texto
    )


@pytest.fixture()
def cliente_http(servicio: Servicio) -> TestClient:
    return TestClient(crear_app(servicio))


# 1 -------------------------------------------------------------------------
def test_prospecto_interesado_avanza_hasta_cierre(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "Hola, me interesa su servicio"))
    hermes.atender(entrante("cli_001", "Cuanto cuesta?"))
    final = hermes.atender(entrante("cli_001", "Quiero comprar, como pago?"))
    assert final.lead.etapa == Etapa.CIERRE
    assert final.lead.senales_compra
    assert final.respuesta


# 2 -------------------------------------------------------------------------
def test_prospecto_indeciso_registra_objecion_de_tiempo(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "Quiero saber como funciona"))
    resultado = hermes.atender(entrante("cli_001", "Lo pienso y le aviso mas adelante"))
    assert "tiempo" in resultado.lead.objeciones
    assert resultado.lead.estatus == Estatus.EN_PROCESO


# 3 -------------------------------------------------------------------------
def test_objecion_de_precio_se_detecta_y_se_responde(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "Me interesa"))
    resultado = hermes.atender(entrante("cli_001", "Esta muy caro, no me alcanza"))
    assert "precio" in resultado.lead.objeciones
    assert resultado.respuesta or resultado.escalamiento


# 4 -------------------------------------------------------------------------
def test_peticion_fuera_de_limites_escala(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    resultado = hermes.atender(entrante("cli_001", "Me hacen un descuento especial?"))
    assert resultado.escalamiento is not None
    assert resultado.escalamiento.motivo == MotivoEscalamiento.FUERA_DE_LIMITES
    assert resultado.enviada is False
    assert resultado.lead.requiere_humano is True


# 5 -------------------------------------------------------------------------
def test_peticion_de_humano_escala_y_se_resuelve_con_intervencion(
    hermes: Hermes, almacen: Almacen
):
    dar_de_alta(almacen)
    resultado = hermes.atender(entrante("cli_001", "Quiero hablar con una persona real"))
    assert resultado.escalamiento is not None
    assert resultado.escalamiento.motivo == MotivoEscalamiento.SOLICITA_HUMANO

    escalamiento, texto = hermes.resolver_escalamiento(
        cliente_id="cli_001",
        escalamiento_id=resultado.escalamiento.escalamiento_id,
        atendido_por="joshua",
        accion="editar",
        respuesta="Le llamo en 10 minutos, soy Joshua.",
        resultado="atendido",
    )
    assert escalamiento.segundos_hasta_resultado is not None
    assert texto == "Le llamo en 10 minutos, soy Joshua."
    lead = almacen.obtener_lead("cli_001", resultado.lead.lead_id)
    assert lead is not None and lead.requiere_humano is False


# 6 -------------------------------------------------------------------------
def test_cierre_registra_resultado_con_impacto(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    resultado = hermes.atender(entrante("cli_001", "Acepto, confirmo la contratacion"))
    lead = hermes.cerrar_lead("cli_001", resultado.lead.lead_id, "ganado")
    assert lead.estatus == Estatus.CERRADO
    registros = almacen.listar_resultados("cli_001", lead.lead_id)
    assert any(r.impacto == "venta generada" for r in registros)


# 7 -------------------------------------------------------------------------
def test_seguimiento_conserva_contexto(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    primero = hermes.atender(entrante("cli_001", "Me interesa"))
    seguimiento = hermes.programar_seguimiento("cli_001", primero.lead.lead_id)
    assert seguimiento.lead.etapa == Etapa.SEGUIMIENTO
    assert seguimiento.lead.contador_seguimientos == 1
    assert seguimiento.respuesta


# 8 -------------------------------------------------------------------------
def test_recuperacion_reactiva_al_lead(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    primero = hermes.atender(entrante("cli_001", "Me interesa"))
    hermes.programar_seguimiento("cli_001", primero.lead.lead_id)
    vuelta = hermes.atender(entrante("cli_001", "Cambie de opinion, me interesa de nuevo"))
    assert vuelta.lead.etapa == Etapa.PROSPECCION
    assert vuelta.lead.estatus == Estatus.EN_PROCESO


# 9 -------------------------------------------------------------------------
def test_error_del_proveedor_no_pierde_la_conversacion(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)

    class MotorCaido:
        nombre = "claude"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            raise RuntimeError("503 del proveedor")

    hermes.generador.motor = MotorCaido()
    resultado = hermes.atender(entrante("cli_001", "Me interesa"))
    assert resultado.respuesta  # el guion aprobado sostiene la conversacion
    assert resultado.motor == "guion_aprobado"
    assert resultado.error_motor is not None  # y queda constancia de que el LLM fallo


# 10 ------------------------------------------------------------------------
def test_conversaciones_simultaneas_no_se_mezclan(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    uno = hermes.atender(entrante("cli_001", "Me interesa el precio", contacto="+521111"))
    dos = hermes.atender(entrante("cli_001", "Quiero hablar con una persona", contacto="+521222"))
    tres = hermes.atender(entrante("cli_001", "Cuanto cuesta?", contacto="+521333"))
    assert len({uno.lead.lead_id, dos.lead.lead_id, tres.lead.lead_id}) == 3
    assert dos.lead.requiere_humano is True
    assert uno.lead.requiere_humano is False
    assert almacen.historial("cli_001", uno.lead.lead_id) != almacen.historial(
        "cli_001", tres.lead.lead_id
    )


# 11 ------------------------------------------------------------------------
def test_aislamiento_entre_tenants(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_a")
    dar_de_alta(almacen, cliente_id="cli_b")
    de_a = hermes.atender(entrante("cli_a", "hola", contacto="+521111")).lead
    hermes.atender(entrante("cli_b", "hola", contacto="+522222"))

    # el folio se cuenta por tenant: el mismo LEAD-00001 existe en ambos y
    # jamas devuelve los datos del otro
    homonimo = almacen.obtener_lead("cli_b", de_a.lead_id)
    assert homonimo is not None
    assert homonimo.cliente_id == "cli_b"
    assert homonimo.contacto != de_a.contacto
    assert almacen.buscar_lead_por_contacto("cli_b", de_a.contacto) is None
    assert [lead.cliente_id for lead in almacen.listar_leads("cli_b")] == ["cli_b"]
    with pytest.raises(AislamientoError):
        almacen.listar_leads("")


# 12 ------------------------------------------------------------------------
def test_webhook_rechaza_firma_invalida_antes_de_tocar_el_crm(
    servicio: Servicio, almacen: Almacen
):
    dar_de_alta(almacen)
    servicio.whatsapp = AdaptadorWhatsApp(auth_token="token-secreto", validar_firmas=True)
    servicio.config = Configuracion(
        ruta_base_datos=":memory:",
        token_panel="token-de-prueba",
        url_publica="https://hermes.ejemplo.com",
    )
    http = TestClient(crear_app(servicio))
    carga = {"From": "whatsapp:+5215550001", "Body": "hola", "MessageSid": "SM-firma"}

    invalida = http.post(
        "/v1/webhooks/whatsapp/cli_001", data=carga, headers={"X-Twilio-Signature": "no"}
    )
    assert invalida.status_code == 403
    assert almacen.listar_leads("cli_001") == []

    firma = firmar(
        "token-secreto", "https://hermes.ejemplo.com/v1/webhooks/whatsapp/cli_001", carga
    )
    valida = http.post(
        "/v1/webhooks/whatsapp/cli_001", data=carga, headers={"X-Twilio-Signature": firma}
    )
    assert valida.status_code == 200
    assert len(almacen.listar_leads("cli_001")) == 1


def test_firma_activa_sin_token_configurado_falla_cerrado():
    with pytest.raises(FirmaInvalidaError):
        AdaptadorWhatsApp(auth_token=None, validar_firmas=True).verificar_firma("u", {}, "x")


# 13 ------------------------------------------------------------------------
def test_reintentos_con_backoff_y_error_final_sin_secretos():
    intentos = {"n": 0}

    def inestable() -> str:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("timeout")
        return "ok"

    assert reintentar("prueba", inestable, dormir=lambda _: None) == "ok"
    assert intentos["n"] == 3

    def siempre_falla() -> str:
        raise RuntimeError("auth_token=abc123 invalido")

    with pytest.raises(ReintentosAgotados) as error:
        reintentar("prueba", siempre_falla, intentos=2, dormir=lambda _: None)
    assert "abc123" not in str(error.value)


# 14 ------------------------------------------------------------------------
def test_webhook_repetido_no_duplica_el_lead(cliente_http: TestClient, almacen: Almacen):
    dar_de_alta(almacen)
    carga = {"From": "whatsapp:+5215550001", "Body": "Hola", "MessageSid": "SM-repetido"}
    primera = cliente_http.post("/v1/webhooks/whatsapp/cli_001", data=carga).json()
    segunda = cliente_http.post("/v1/webhooks/whatsapp/cli_001", data=carga).json()

    assert primera == segunda
    assert len(almacen.listar_leads("cli_001")) == 1
    entrantes = [
        m
        for m in almacen.historial("cli_001", primera["lead_id"])
        if m.direccion == "entrante"
    ]
    assert len(entrantes) == 1


# --- sandbox y planes -------------------------------------------------------
def test_sandbox_usa_el_nucleo_y_exige_tenant_de_pruebas(cliente_http: TestClient, almacen: Almacen):
    from hermes.models import HorarioComercial
    from hermes.onboarding import RespuestasOnboarding, alta_cliente

    alta_cliente(
        almacen,
        RespuestasOnboarding(
            cliente_id="cli_lab",
            nombre_negocio="Laboratorio",
            plan=Plan.PILOTO,
            productos=["Servicio"],
            telefono_whatsapp="+521000000000",
            horario_comercial=HorarioComercial(),
            sandbox=True,
        ),
    )
    respuesta = cliente_http.post(
        "/v1/clientes/cli_lab/sandbox",
        headers=CABECERAS,
        json={"escenario": "pide_humano"},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["escalo"] is True

    dar_de_alta(almacen, cliente_id="cli_produccion")
    negado = cliente_http.post(
        "/v1/clientes/cli_produccion/sandbox",
        headers=CABECERAS,
        json={"escenario": "pide_humano"},
    )
    assert negado.status_code == 422


def test_el_plan_limita_los_canales(cliente_http: TestClient, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_wa", correo=False, llamadas=False, plan=Plan.PLAN_1)
    respuesta = cliente_http.post(
        "/v1/webhooks/correo/cli_wa", json={"from": "a@b.com", "subject": "x", "body": "hola"}
    )
    assert respuesta.status_code == 409
