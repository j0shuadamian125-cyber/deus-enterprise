"""Roles operador / cliente / empleado: un token de tenant solo alcanza su propio tenant."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hermes.api import crear_app
from hermes.config import Configuracion, Servicio
from hermes.models import Etapa
from hermes.storage import Almacen

OPERADOR = {"X-Panel-Token": "token-de-prueba"}

PERMITIDAS_CLIENTE = {
    ("GET", "/health"),
    ("GET", "/v1/sesion"),
    ("GET", "/v1/clientes/{cliente_id}"),
    ("GET", "/v1/clientes/{cliente_id}/leads"),
    ("GET", "/v1/clientes/{cliente_id}/leads/{lead_id}"),
    ("GET", "/v1/clientes/{cliente_id}/hoja-leads"),
    ("GET", "/v1/clientes/{cliente_id}/llamadas"),
    ("GET", "/v1/clientes/{cliente_id}/reporte"),
    ("GET", "/v1/clientes/{cliente_id}/reporte.pdf"),
    ("GET", "/v1/clientes/{cliente_id}/resultados"),
    ("GET", "/v1/clientes/{cliente_id}/escalamientos"),
    ("GET", "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}"),
    ("POST", "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}/intervenir"),
    ("GET", "/v1/clientes/{cliente_id}/estrategias"),
    ("POST", "/v1/clientes/{cliente_id}/estrategias/{estrategia_id}/activar"),
    ("GET", "/v1/clientes/{cliente_id}/patrones"),
    ("GET", "/v1/clientes/{cliente_id}/decisiones"),
    ("POST", "/v1/clientes/{cliente_id}/decisiones/{decision_id}/aprobar"),
    ("POST", "/v1/clientes/{cliente_id}/decisiones/{decision_id}/rechazar"),
}

PERMITIDAS_EMPLEADO = {
    ("GET", "/health"),
    ("GET", "/v1/sesion"),
    ("GET", "/v1/clientes/{cliente_id}/escalamientos"),
    ("GET", "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}"),
    ("POST", "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}/intervenir"),
}


def _todas_las_rutas() -> list[tuple[str, str]]:
    app = crear_app(config=Configuracion(ruta_base_datos=":memory:", token_panel="x"))
    return sorted(
        (metodo, ruta.path)
        for ruta in app.routes
        if isinstance(ruta, APIRoute)
        for metodo in ruta.methods
    )


RUTAS = _todas_las_rutas()


def _concretar(plantilla: str, cliente_id: str) -> str:
    return re.sub(
        r"\{(\w+)\}",
        lambda m: cliente_id if m.group(1) == "cliente_id" else "X-00001",
        plantilla,
    )


def _alta(http: TestClient, cliente_id: str) -> None:
    respuesta = http.post(
        "/v1/onboarding",
        headers=OPERADOR,
        json={
            "cliente_id": cliente_id,
            "nombre_negocio": f"Tienda {cliente_id}",
            "plan": "piloto",
            "productos": ["Producto"],
            "zona_horaria": "America/Mexico_City",
            "telefono_whatsapp": "+521000000000",
        },
    )
    assert respuesta.status_code == 200, respuesta.text


def _proponer_estrategia(http: TestClient, cliente_id: str) -> tuple[str, str]:
    creada = http.post(
        f"/v1/clientes/{cliente_id}/estrategias",
        headers=OPERADOR,
        json={
            "nombre": "Respuesta rapida",
            "objetivo": "cerrar mas",
            "etapa": Etapa.APERTURA.value,
            "plantilla": "Hola {nombre}",
        },
    ).json()
    return creada["estrategia"]["estrategia_id"], creada["decision_id"]


@pytest.fixture()
def http(servicio: Servicio) -> TestClient:
    cliente = TestClient(crear_app(servicio))
    _alta(cliente, "cli_001")
    _alta(cliente, "cli_002")
    return cliente


@pytest.fixture()
def token_cliente(http: TestClient) -> str:
    respuesta = http.post("/v1/clientes/cli_001/accesos", headers=OPERADOR)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["token"]


@pytest.fixture()
def token_empleado(http: TestClient) -> str:
    respuesta = http.post(
        "/v1/clientes/cli_001/accesos", headers=OPERADOR, json={"rol": "empleado"}
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["rol"] == "empleado"
    return respuesta.json()["token"]


def _escalar(http: TestClient, cliente_id: str) -> str:
    http.post(
        f"/v1/webhooks/whatsapp/{cliente_id}",
        data={"From": "whatsapp:+521", "Body": "quiero hablar con una persona"},
    )
    pendientes = http.get(
        f"/v1/clientes/{cliente_id}/escalamientos?estado=pendiente", headers=OPERADOR
    ).json()
    assert pendientes
    return pendientes[0]["escalamiento_id"]


@pytest.mark.parametrize(("metodo", "plantilla"), RUTAS, ids=[f"{m} {p}" for m, p in RUTAS])
def test_matriz_de_rutas_del_rol_operador(http: TestClient, metodo: str, plantilla: str):
    respuesta = http.request(
        metodo, _concretar(plantilla, "cli_001"), headers=OPERADOR, json={}
    )
    assert respuesta.status_code not in (401, 403), respuesta.text


@pytest.mark.parametrize(("metodo", "plantilla"), RUTAS, ids=[f"{m} {p}" for m, p in RUTAS])
def test_matriz_de_rutas_del_rol_empleado(
    http: TestClient, token_empleado: str, metodo: str, plantilla: str
):
    cabeceras = {"X-Panel-Token": token_empleado}
    propio = http.request(metodo, _concretar(plantilla, "cli_001"), headers=cabeceras, json={})
    ajeno = http.request(metodo, _concretar(plantilla, "cli_002"), headers=cabeceras, json={})
    if (metodo, plantilla) in PERMITIDAS_EMPLEADO:
        assert propio.status_code not in (401, 403), propio.text
        if "{cliente_id}" in plantilla:
            assert ajeno.status_code == 403, ajeno.text
    else:
        assert propio.status_code == 403, propio.text
        assert ajeno.status_code == 403, ajeno.text


def test_el_empleado_tiene_un_subconjunto_estricto_de_lo_del_cliente():
    assert PERMITIDAS_EMPLEADO < PERMITIDAS_CLIENTE
    for ruta in PERMITIDAS_EMPLEADO - {("GET", "/health"), ("GET", "/v1/sesion")}:
        assert "/escalamientos" in ruta[1]


@pytest.mark.parametrize(("metodo", "plantilla"), RUTAS, ids=[f"{m} {p}" for m, p in RUTAS])
def test_matriz_de_rutas_del_rol_cliente(
    http: TestClient, token_cliente: str, metodo: str, plantilla: str
):
    cabeceras = {"X-Panel-Token": token_cliente}
    propio = http.request(metodo, _concretar(plantilla, "cli_001"), headers=cabeceras, json={})
    ajeno = http.request(metodo, _concretar(plantilla, "cli_002"), headers=cabeceras, json={})
    if (metodo, plantilla) in PERMITIDAS_CLIENTE:
        assert propio.status_code not in (401, 403), propio.text
        if "{cliente_id}" in plantilla:
            assert ajeno.status_code == 403, ajeno.text
    else:
        assert propio.status_code == 403, propio.text
        assert ajeno.status_code == 403, ajeno.text


def test_el_operador_conserva_el_acceso_completo(http: TestClient):
    for ruta in ("/v1/clientes", "/v1/decisiones", "/v1/memoria", "/v1/estado"):
        assert http.get(ruta, headers=OPERADOR).status_code == 200, ruta
    assert http.get("/v1/clientes/cli_002/leads", headers=OPERADOR).status_code == 200
    assert http.get("/v1/sesion", headers=OPERADOR).json() == {
        "rol": "operador",
        "cliente_id": None,
        "nombre_negocio": None,
    }


def test_la_sesion_identifica_al_cliente(http: TestClient, token_cliente: str):
    sesion = http.get("/v1/sesion", headers={"X-Panel-Token": token_cliente}).json()
    assert sesion == {
        "rol": "cliente",
        "cliente_id": "cli_001",
        "nombre_negocio": "Tienda cli_001",
    }


def test_token_inventado_o_revocado_no_entra(
    http: TestClient, servicio: Servicio, token_cliente: str
):
    cabeceras = {"X-Panel-Token": token_cliente}
    assert http.get("/v1/clientes/cli_001/leads", headers=cabeceras).status_code == 200
    assert (
        http.get("/v1/clientes/cli_001/leads", headers={"X-Panel-Token": "inventado"}).status_code
        == 401
    )
    acceso_id = servicio.almacen.listar_accesos("cli_001")[0]["acceso_id"]
    revocado = http.post(f"/v1/clientes/cli_001/accesos/{acceso_id}/revocar", headers=OPERADOR)
    assert revocado.status_code == 200
    assert http.get("/v1/clientes/cli_001/leads", headers=cabeceras).status_code == 401


def test_el_token_solo_se_guarda_como_hash(servicio: Servicio, token_cliente: str):
    assert servicio.almacen.cliente_de_acceso(token_cliente) is None
    huella = hashlib.sha256(token_cliente.encode()).hexdigest()
    assert servicio.almacen.cliente_de_acceso(huella) == "cli_001"
    assert all("token" not in acceso for acceso in servicio.almacen.listar_accesos("cli_001"))


def test_el_cliente_aprueba_y_activa_una_estrategia_de_su_tenant(
    http: TestClient, token_cliente: str
):
    cabeceras = {"X-Panel-Token": token_cliente}
    estrategia_id, decision_id = _proponer_estrategia(http, "cli_001")
    visibles = http.get("/v1/clientes/cli_001/decisiones", headers=cabeceras).json()
    assert [d["decision_id"] for d in visibles] == [decision_id]

    aprobada = http.post(
        f"/v1/clientes/cli_001/decisiones/{decision_id}/aprobar",
        headers=cabeceras,
        json={"aprobado_por": "dueña"},
    )
    assert aprobada.status_code == 200, aprobada.text
    assert aprobada.json()["aprobado_por"] == "cliente:cli_001/dueña"

    activada = http.post(
        f"/v1/clientes/cli_001/estrategias/{estrategia_id}/activar",
        headers=cabeceras,
        json={"decision_id": decision_id},
    )
    assert activada.status_code == 200, activada.text
    assert activada.json()["estado"] == "activa"


def test_el_cliente_rechaza_una_estrategia_de_su_tenant(http: TestClient, token_cliente: str):
    _, decision_id = _proponer_estrategia(http, "cli_001")
    rechazada = http.post(
        f"/v1/clientes/cli_001/decisiones/{decision_id}/rechazar",
        headers={"X-Panel-Token": token_cliente},
        json={"rechazado_por": "dueña", "motivo": "no me convence"},
    )
    assert rechazada.status_code == 200, rechazada.text
    assert rechazada.json()["estado"] == "rechazada"


def test_el_cliente_no_resuelve_decisiones_que_no_son_de_estrategia(
    http: TestClient, servicio: Servicio, token_cliente: str
):
    cabeceras = {"X-Panel-Token": token_cliente}
    otra = servicio.gobernanza.registrar("cli_001", accion="accion_nueva", descripcion="x")
    assert otra.estado.value == "pendiente"
    visibles = http.get("/v1/clientes/cli_001/decisiones", headers=cabeceras).json()
    assert otra.decision_id not in [d["decision_id"] for d in visibles]
    respuesta = http.post(
        f"/v1/clientes/cli_001/decisiones/{otra.decision_id}/aprobar",
        headers=cabeceras,
        json={"aprobado_por": "dueña"},
    )
    assert respuesta.status_code == 403
    del_operador = http.get("/v1/clientes/cli_001/decisiones", headers=OPERADOR).json()
    assert otra.decision_id in [d["decision_id"] for d in del_operador]


def test_el_cliente_no_alcanza_decisiones_de_otro_tenant_por_su_ruta(
    http: TestClient, token_cliente: str
):
    _, decision_ajena = _proponer_estrategia(http, "cli_002")
    respuesta = http.post(
        f"/v1/clientes/cli_001/decisiones/{decision_ajena}/aprobar",
        headers={"X-Panel-Token": token_cliente},
        json={"aprobado_por": "dueña"},
    )
    assert respuesta.status_code == 404


def test_la_intervencion_del_cliente_queda_firmada_con_su_rol(
    http: TestClient, token_cliente: str
):
    cabeceras = {"X-Panel-Token": token_cliente}
    http.post(
        "/v1/webhooks/whatsapp/cli_001",
        data={"From": "whatsapp:+521", "Body": "quiero hablar con una persona"},
    )
    pendientes = http.get(
        "/v1/clientes/cli_001/escalamientos?estado=pendiente", headers=cabeceras
    ).json()
    assert pendientes
    respuesta = http.post(
        f"/v1/clientes/cli_001/escalamientos/{pendientes[0]['escalamiento_id']}/intervenir",
        headers=cabeceras,
        json={"atendido_por": "dueña", "accion": "tomar"},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["escalamiento"]["atendido_por"] == "cliente:cli_001/dueña"


def test_sin_token_de_operador_configurado_tampoco_entra_el_cliente(
    http: TestClient, servicio: Servicio, token_cliente: str
):
    servicio.config.token_panel = None
    servicio.config.permitir_sin_token = False
    respuesta = http.get("/v1/clientes/cli_001/leads", headers={"X-Panel-Token": token_cliente})
    assert respuesta.status_code == 503


def test_la_sesion_identifica_al_empleado(http: TestClient, token_empleado: str):
    sesion = http.get("/v1/sesion", headers={"X-Panel-Token": token_empleado}).json()
    assert sesion == {
        "rol": "empleado",
        "cliente_id": "cli_001",
        "nombre_negocio": "Tienda cli_001",
    }


def test_el_empleado_atiende_la_cola_y_queda_firmado(http: TestClient, token_empleado: str):
    cabeceras = {"X-Panel-Token": token_empleado}
    escalamiento_id = _escalar(http, "cli_001")
    cola = http.get("/v1/clientes/cli_001/escalamientos?estado=pendiente", headers=cabeceras)
    assert [e["escalamiento_id"] for e in cola.json()] == [escalamiento_id]
    caso = http.get(f"/v1/clientes/cli_001/escalamientos/{escalamiento_id}", headers=cabeceras)
    assert caso.status_code == 200
    assert {"escalamiento", "lead", "historial"} <= caso.json().keys()
    assert "recomendacion" in caso.json()["escalamiento"]

    respuesta = http.post(
        f"/v1/clientes/cli_001/escalamientos/{escalamiento_id}/intervenir",
        headers=cabeceras,
        json={
            "atendido_por": "Luis",
            "accion": "editar",
            "respuesta": "Con gusto le ayudo",
            "resultado": "atendido",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["escalamiento"]["atendido_por"] == "empleado:cli_001/Luis"


def test_el_empleado_no_alcanza_la_cola_de_otro_tenant(http: TestClient, token_empleado: str):
    ajeno = _escalar(http, "cli_002")
    respuesta = http.post(
        f"/v1/clientes/cli_002/escalamientos/{ajeno}/intervenir",
        headers={"X-Panel-Token": token_empleado},
        json={"atendido_por": "Luis", "accion": "tomar"},
    )
    assert respuesta.status_code == 403
    assert http.get(
        f"/v1/clientes/cli_001/escalamientos/{ajeno}",
        headers={"X-Panel-Token": token_empleado},
    ).status_code == 404


def test_token_de_empleado_revocado_no_entra(
    http: TestClient, servicio: Servicio, token_empleado: str
):
    cabeceras = {"X-Panel-Token": token_empleado}
    assert http.get("/v1/clientes/cli_001/escalamientos", headers=cabeceras).status_code == 200
    acceso = servicio.almacen.listar_accesos("cli_001")[0]
    assert acceso["rol"] == "empleado"
    revocado = http.post(
        f"/v1/clientes/cli_001/accesos/{acceso['acceso_id']}/revocar", headers=OPERADOR
    )
    assert revocado.status_code == 200
    assert http.get("/v1/clientes/cli_001/escalamientos", headers=cabeceras).status_code == 401


def test_el_rol_del_acceso_es_cerrado(http: TestClient):
    respuesta = http.post(
        "/v1/clientes/cli_001/accesos", headers=OPERADOR, json={"rol": "operador"}
    )
    assert respuesta.status_code == 422
    sin_cuerpo = http.post("/v1/clientes/cli_001/accesos", headers=OPERADOR)
    assert sin_cuerpo.json()["rol"] == "cliente"


def test_una_base_anterior_migra_sus_accesos_como_cliente(tmp_path: Path):
    ruta = str(tmp_path / "hermes.db")
    conexion = sqlite3.connect(ruta)
    conexion.execute(
        "CREATE TABLE accesos_cliente (acceso_id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL, "
        "hash_token TEXT NOT NULL UNIQUE, creado_en TEXT NOT NULL, revocado_en TEXT)"
    )
    conexion.execute(
        "INSERT INTO accesos_cliente VALUES ('ACC-00001', 'cli_001', 'huella', '2026-01-01', NULL)"
    )
    conexion.commit()
    conexion.close()
    almacen = Almacen(ruta)
    try:
        assert almacen.acceso_de_hash("huella") == ("cli_001", "cliente")
        assert almacen.listar_accesos("cli_001")[0]["rol"] == "cliente"
    finally:
        almacen.cerrar()
