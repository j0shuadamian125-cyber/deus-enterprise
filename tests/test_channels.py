"""Adaptadores de canal, formato de salida y fallback del LLM (Secciones 5.2 a 5.6)."""
from __future__ import annotations

import pytest

from hermes.channels import AdaptadorCorreo, AdaptadorLlamada, AdaptadorWhatsApp
from hermes.llm import GeneradorRespuestas
from hermes.models import Canal, Lead, NivelServicioLlamada, ResultadoLlamada
from hermes.pipeline import Hermes
from hermes.prompts import construir_prompt
from hermes.storage import Almacen

from .conftest import dar_de_alta


def test_whatsapp_normaliza_el_formulario_de_twilio():
    evento = AdaptadorWhatsApp().normalizar(
        "cli_001",
        {"From": "whatsapp:+5215550001", "Body": " Hola ", "ProfileName": "Ana", "MessageSid": "SM1"},
    )
    assert evento.contacto == "+5215550001"
    assert evento.texto == "Hola"
    assert evento.nombre == "Ana"
    assert evento.canal == Canal.WHATSAPP


def test_whatsapp_sin_remitente_es_error():
    with pytest.raises(ValueError):
        AdaptadorWhatsApp().normalizar("cli_001", {"Body": "hola"})


def test_whatsapp_sin_credenciales_no_envia():
    adaptador = AdaptadorWhatsApp(account_sid=None, auth_token=None, numero_remitente=None)
    assert adaptador.configurado is False


def test_correo_extrae_la_direccion_y_une_asunto_y_cuerpo():
    evento = AdaptadorCorreo().normalizar(
        "cli_001", {"from": "Ana Perez <Ana@Correo.com>", "subject": "Cotizacion", "body": "Necesito precios"}
    )
    assert evento.contacto == "ana@correo.com"
    assert evento.texto == "Cotizacion\n\nNecesito precios"


def test_correo_sin_direccion_valida_es_error():
    with pytest.raises(ValueError):
        AdaptadorCorreo().normalizar("cli_001", {"from": "sin direccion"})


def test_llamada_normaliza_transcripcion_y_nivel_de_servicio():
    carga = {
        "customer_number": "+5215550002",
        "transcript": "Quiero informacion",
        "duration_seconds": 42,
        "nivel_servicio": "asistida",
        "outcome": "avanzo_etapa",
    }
    adaptador = AdaptadorLlamada()
    evento = adaptador.normalizar("cli_001", carga)
    assert evento.contacto == "+5215550002"
    assert evento.metadatos["duracion_segundos"] == 42
    assert adaptador.nivel_servicio(carga) == NivelServicioLlamada.ASISTIDA
    assert adaptador.resultado(carga) == ResultadoLlamada.AVANZO_ETAPA


def test_la_transcripcion_queda_asociada_al_lead(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    llamada, atencion = hermes.registrar_llamada(
        cliente_id="cli_001",
        contacto="+5215550002",
        nivel_servicio=NivelServicioLlamada.VOZ_IA,
        transcripcion="Me interesa el precio",
        duracion_segundos=30,
    )
    assert llamada.lead_id == atencion.lead.lead_id
    assert almacen.listar_llamadas("cli_001", llamada.lead_id) == [llamada]


def test_la_grabacion_se_descarta_sin_autorizacion(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_sin_grabacion", grabacion=False)
    llamada, _ = hermes.registrar_llamada(
        cliente_id="cli_sin_grabacion",
        contacto="+5215550003",
        nivel_servicio=NivelServicioLlamada.VOZ_IA,
        transcripcion="hola",
        url_grabacion="https://grabaciones/ejemplo.mp3",
    )
    assert llamada.url_grabacion is None


def test_la_grabacion_se_conserva_con_autorizacion(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_con_grabacion", grabacion=True)
    llamada, _ = hermes.registrar_llamada(
        cliente_id="cli_con_grabacion",
        contacto="+5215550004",
        nivel_servicio=NivelServicioLlamada.VOZ_IA,
        transcripcion="hola",
        url_grabacion="https://grabaciones/ejemplo.mp3",
    )
    assert llamada.url_grabacion == "https://grabaciones/ejemplo.mp3"


def _lead() -> Lead:
    return Lead(lead_id="LEAD-1", cliente_id="cli_001", contacto="+521", canal_origen=Canal.WHATSAPP)


def test_whatsapp_responde_en_tres_lineas_como_maximo():
    class MotorLargo:
        nombre = "falso"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            return "uno\ndos\ntres\ncuatro\ncinco"

    respuesta = GeneradorRespuestas(MotorLargo()).responder(
        _lead(), Canal.WHATSAPP, "Negocio", "prompt", "hola"
    )
    assert len(respuesta.texto.split("\n")) == 3


def test_correo_lleva_saludo_y_firma():
    class MotorSimple:
        nombre = "falso"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            return "Le comparto la informacion solicitada."

    respuesta = GeneradorRespuestas(MotorSimple()).responder(
        _lead(), Canal.CORREO, "Negocio", "prompt", "hola"
    )
    assert respuesta.texto.startswith("Buen dia,")
    assert respuesta.texto.endswith("Negocio")


def test_llamada_devuelve_texto_hablado_sin_saltos():
    class MotorConSaltos:
        nombre = "falso"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            return "Buenas tardes.\nLe llamo del negocio."

    respuesta = GeneradorRespuestas(MotorConSaltos()).responder(
        _lead(), Canal.LLAMADA, "Negocio", "prompt", "hola"
    )
    assert "\n" not in respuesta.texto


def test_si_el_llm_falla_se_responde_con_guion_aprobado():
    class MotorCaido:
        nombre = "falso"

        def generar(self, prompt: str, mensaje_lead: str) -> str:
            raise RuntimeError("timeout de la API")

    respuesta = GeneradorRespuestas(MotorCaido()).responder(
        _lead(), Canal.WHATSAPP, "Negocio", "prompt", "hola"
    )
    assert respuesta.con_guion_aprobado is True
    assert respuesta.motor == "guion_aprobado"
    assert respuesta.texto


def test_sin_credenciales_el_generador_usa_el_guion():
    respuesta = GeneradorRespuestas().responder(_lead(), Canal.WHATSAPP, "Negocio", "prompt", "hola")
    assert respuesta.con_guion_aprobado is True


def test_el_prompt_declara_el_canal_y_prohibe_inventar():
    prompt = construir_prompt(_lead(), Canal.CORREO, "Negocio")
    assert "Canal actual: correo" in prompt
    assert "Nunca inventas precios" in prompt
    assert "Nunca mencionas a DEUS" in prompt
