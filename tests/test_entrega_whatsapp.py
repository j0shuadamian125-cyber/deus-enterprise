"""Entrega de WhatsApp fuera del webhook, reintentos solo transitorios y estado_entrega real."""

from __future__ import annotations

import functools
import io
import threading
import time
from collections.abc import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
import uvicorn
from fastapi.testclient import TestClient

from hermes import resilience
from hermes.api import TWIML_VACIO, crear_app
from hermes.channels import llamada as modulo_llamada
from hermes.channels import whatsapp as modulo_whatsapp
from hermes.channels.llamada import AdaptadorLlamada, LlamadaError
from hermes.channels.whatsapp import AdaptadorWhatsApp, EnvioError
from hermes.config import Servicio
from hermes.models import EstadoEntrega, Mensaje
from hermes.resilience import ReintentosAgotados

from .conftest import dar_de_alta

CABECERAS = {"X-Panel-Token": "token-de-prueba"}


@pytest.fixture()
def servicio_real(servicio: Servicio) -> Servicio:
    dar_de_alta(servicio.almacen)
    servicio.config.envio_real = True
    servicio.whatsapp = AdaptadorWhatsApp(
        account_sid="AC-prueba",
        auth_token="token-prueba",
        numero_remitente="+14155238886",
        validar_firmas=False,
    )
    return servicio


@pytest.fixture()
def url_base(servicio_real: Servicio) -> Iterator[str]:
    """Servidor uvicorn real: TestClient espera a las tareas en segundo plano antes de devolver."""
    servidor = uvicorn.Server(
        uvicorn.Config(crear_app(servicio_real), host="127.0.0.1", port=0, log_level="warning")
    )
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + 10
    while not servidor.started:
        assert time.monotonic() < limite, "uvicorn no arranco"
        time.sleep(0.01)
    puerto = servidor.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{puerto}"
    servidor.should_exit = True
    hilo.join(timeout=10)


def _post_webhook(url_base: str, cuerpo: str, sid: str) -> tuple[int, str, str, float]:
    datos = urlencode({"From": "whatsapp:+5215550001", "Body": cuerpo, "MessageSid": sid})
    peticion = Request(
        f"{url_base}/v1/webhooks/whatsapp/cli_001", data=datos.encode(), method="POST"
    )
    inicio = time.monotonic()
    with urlopen(peticion, timeout=10) as respuesta:
        texto = respuesta.read().decode()
        return respuesta.status, respuesta.headers["Content-Type"], texto, time.monotonic() - inicio


def _ultima_saliente(servicio: Servicio) -> Mensaje:
    [lead] = servicio.almacen.listar_leads("cli_001")
    salientes = [
        m for m in servicio.almacen.historial("cli_001", lead.lead_id) if m.direccion == "saliente"
    ]
    return salientes[-1]


def _esperar_estado(servicio: Servicio, estado: EstadoEntrega) -> Mensaje:
    limite = time.monotonic() + 10
    while True:
        mensaje = _ultima_saliente(servicio)
        if mensaje.estado_entrega == estado:
            return mensaje
        assert time.monotonic() < limite, f"la entrega sigue en {mensaje.estado_entrega}"
        time.sleep(0.05)


# --- 2: la entrega no retrasa la respuesta del webhook ----------------------
def test_webhook_responde_mientras_la_entrega_sigue_bloqueada(
    servicio_real: Servicio, url_base: str, monkeypatch
):
    liberar = threading.Event()
    enviados: list[tuple[str, str]] = []

    def enviar_lento(destino: str, texto: str) -> dict:
        assert liberar.wait(timeout=30)
        enviados.append((destino, texto))
        return {"sid": "SM-falso"}

    monkeypatch.setattr(servicio_real.whatsapp, "enviar", enviar_lento)

    estado, tipo, cuerpo, segundos = _post_webhook(url_base, "Hola, me interesa", "SM-lento")

    assert estado == 200
    assert tipo.startswith("application/xml")
    assert cuerpo == TWIML_VACIO
    assert segundos < 2
    assert _ultima_saliente(servicio_real).estado_entrega == EstadoEntrega.PENDIENTE
    assert enviados == []

    liberar.set()
    _esperar_estado(servicio_real, EstadoEntrega.ENVIADA)
    assert [destino for destino, _ in enviados] == ["+5215550001"]


def test_un_fallo_de_entrega_en_segundo_plano_queda_fallida(
    servicio_real: Servicio, url_base: str, monkeypatch
):
    def enviar_tras_timeout(destino: str, texto: str) -> dict:
        time.sleep(3)
        raise EnvioError("Fallo de red hacia Twilio: timed out")

    monkeypatch.setattr(servicio_real.whatsapp, "enviar", enviar_tras_timeout)

    estado, _, cuerpo, segundos = _post_webhook(url_base, "Hola, me interesa", "SM-timeout")

    assert (estado, cuerpo) == (200, TWIML_VACIO)
    assert segundos < 2
    fallida = _esperar_estado(servicio_real, EstadoEntrega.FALLIDA)
    assert "timed out" in (fallida.error_entrega or "")


def test_webhook_repetido_no_programa_otra_entrega(servicio_real: Servicio, monkeypatch):
    enviados: list[str] = []
    monkeypatch.setattr(
        servicio_real.whatsapp, "enviar", lambda destino, texto: enviados.append(destino) or {}
    )
    http = TestClient(crear_app(servicio_real))
    carga = {"From": "whatsapp:+5215550001", "Body": "Hola", "MessageSid": "SM-dup"}

    for _ in range(3):
        assert http.post("/v1/webhooks/whatsapp/cli_001", data=carga).text == TWIML_VACIO

    assert enviados == ["+5215550001"]


# --- 3: solo se reintentan red y 5xx ----------------------------------------
@pytest.fixture()
def sin_esperas(monkeypatch) -> None:
    rapido = functools.partial(resilience.reintentar, dormir=lambda _segundos: None)
    monkeypatch.setattr(modulo_whatsapp, "reintentar", rapido)
    monkeypatch.setattr(modulo_llamada, "reintentar", rapido)


def _error_http(codigo: int, cuerpo: str) -> HTTPError:
    return HTTPError("https://api.twilio.com", codigo, "error", {}, io.BytesIO(cuerpo.encode()))


def _contar_urlopen(monkeypatch, modulo, error: Exception) -> list[int]:
    llamadas: list[int] = []

    def falla(*_args, **_kwargs):
        llamadas.append(1)
        raise error

    monkeypatch.setattr(modulo, "urlopen", falla)
    return llamadas


def _whatsapp() -> AdaptadorWhatsApp:
    return AdaptadorWhatsApp(
        account_sid="AC-prueba", auth_token="token-prueba", numero_remitente="+10000000000"
    )


def test_un_4xx_de_twilio_no_se_reintenta(monkeypatch, sin_esperas):
    error = _error_http(400, '{"code": 21212, "message": "invalid From"}')
    llamadas = _contar_urlopen(monkeypatch, modulo_whatsapp, error)

    with pytest.raises(EnvioError) as fallo:
        _whatsapp().enviar("+5215550001", "hola")

    assert len(llamadas) == 1
    assert "21212" in str(fallo.value)


@pytest.mark.parametrize(
    "error",
    [_error_http(503, "Service Unavailable"), URLError("timed out")],
    ids=["5xx", "red"],
)
def test_red_y_5xx_si_se_reintentan(monkeypatch, sin_esperas, error: Exception):
    llamadas = _contar_urlopen(monkeypatch, modulo_whatsapp, error)

    with pytest.raises(ReintentosAgotados):
        _whatsapp().enviar("+5215550001", "hola")

    assert len(llamadas) == 3


def test_error_code_en_respuesta_200_no_se_reintenta(monkeypatch, sin_esperas):
    llamadas: list[int] = []

    class Respuesta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def responde_con_error(*_args, **_kwargs):
        llamadas.append(1)
        return Respuesta(b'{"error_code": 63016, "error_message": "fuera de ventana"}')

    monkeypatch.setattr(modulo_whatsapp, "urlopen", responde_con_error)

    with pytest.raises(EnvioError):
        _whatsapp().enviar("+5215550001", "hola")
    assert len(llamadas) == 1


def test_un_4xx_de_twilio_voice_tampoco_se_reintenta(monkeypatch, sin_esperas):
    llamadas = _contar_urlopen(monkeypatch, modulo_llamada, _error_http(400, "bad To"))
    adaptador = AdaptadorLlamada(
        account_sid="AC-prueba",
        auth_token="token-prueba",
        numero_saliente="+10000000000",
        url_twiml="https://hermes.ejemplo.com/twiml",
    )

    with pytest.raises(LlamadaError):
        adaptador.llamar("+5215550001")
    assert len(llamadas) == 1


# --- 4: estado_entrega refleja la entrega real ------------------------------
def _seguimiento(servicio: Servicio) -> dict:
    http = TestClient(crear_app(servicio))
    servicio.config.envio_real = False
    http.post("/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:+5215550001", "Body": "hola"})
    servicio.config.envio_real = True
    [lead] = servicio.almacen.listar_leads("cli_001")
    respuesta = http.post(
        f"/v1/clientes/cli_001/leads/{lead.lead_id}/seguimiento", headers=CABECERAS
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_estado_entrega_es_fallida_si_twilio_falla(servicio_real: Servicio, monkeypatch):
    def falla(destino: str, texto: str) -> dict:
        raise EnvioError("Twilio respondio 400: code 21212", reintentable=False)

    monkeypatch.setattr(servicio_real.whatsapp, "enviar", falla)

    cuerpo = _seguimiento(servicio_real)

    assert "enviada" not in cuerpo
    assert cuerpo["estado_entrega"] == "fallida"
    assert _ultima_saliente(servicio_real).estado_entrega == EstadoEntrega.FALLIDA


def test_estado_entrega_es_enviada_solo_si_twilio_acepta(servicio_real: Servicio, monkeypatch):
    monkeypatch.setattr(servicio_real.whatsapp, "enviar", lambda destino, texto: {"sid": "SM"})

    cuerpo = _seguimiento(servicio_real)

    assert cuerpo["estado_entrega"] == "enviada"
    assert _ultima_saliente(servicio_real).estado_entrega == EstadoEntrega.ENVIADA


def test_sin_credenciales_del_canal_no_se_marca_como_enviada(servicio_real: Servicio):
    servicio_real.whatsapp = AdaptadorWhatsApp(validar_firmas=False)

    cuerpo = _seguimiento(servicio_real)

    assert cuerpo["estado_entrega"] == "fallida"
    mensaje = _ultima_saliente(servicio_real)
    assert mensaje.estado_entrega == EstadoEntrega.FALLIDA
    assert "credenciales" in (mensaje.error_entrega or "")


def test_dry_run_deja_la_entrega_pendiente(servicio_real: Servicio):
    servicio_real.config.envio_real = False
    http = TestClient(crear_app(servicio_real))
    http.post("/v1/webhooks/whatsapp/cli_001", data={"From": "whatsapp:+5215550001", "Body": "hola"})
    [lead] = servicio_real.almacen.listar_leads("cli_001")

    cuerpo = http.post(
        f"/v1/clientes/cli_001/leads/{lead.lead_id}/seguimiento", headers=CABECERAS
    ).json()

    assert cuerpo["estado_entrega"] == "pendiente"


def test_webhook_llamada_informa_la_respuesta_pendiente(servicio_real: Servicio):
    http = TestClient(crear_app(servicio_real))

    cuerpo = http.post(
        "/v1/webhooks/llamada/cli_001",
        json={"customer_number": "+5215550002", "transcript": "Quiero cotizacion"},
    ).json()

    [lead] = servicio_real.almacen.listar_leads("cli_001")
    salientes = [
        m
        for m in servicio_real.almacen.historial("cli_001", lead.lead_id)
        if m.direccion == "saliente"
    ]
    assert salientes[-1].estado_entrega == EstadoEntrega.PENDIENTE
    assert cuerpo["atencion"]["estado_entrega"] == "pendiente"
