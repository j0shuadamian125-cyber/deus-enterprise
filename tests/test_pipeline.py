"""Pipeline de 4 etapas, continuidad entre canales y aislamiento (Secciones 2.3 y 5.5)."""
from __future__ import annotations

import pytest

from hermes.models import Canal, Estatus, Etapa
from hermes.pipeline import ClienteNoRegistradoError, ContactoEntrante, Hermes
from hermes.storage import AislamientoError, Almacen

from .conftest import dar_de_alta


def entrante(cliente_id: str, texto: str, canal: Canal = Canal.WHATSAPP, contacto: str = "+5215550001"):
    return ContactoEntrante(cliente_id=cliente_id, canal=canal, contacto=contacto, texto=texto)


def test_cliente_no_registrado_no_es_atendido(hermes: Hermes):
    with pytest.raises(ClienteNoRegistradoError):
        hermes.atender(entrante("cli_fantasma", "hola"))


def test_lead_nuevo_entra_en_apertura(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    resultado = hermes.atender(entrante("cli_001", "Hola, buenas tardes"))
    assert resultado.lead.etapa == Etapa.APERTURA
    assert resultado.lead.estatus == Estatus.EN_PROCESO
    assert resultado.enviada is True
    assert resultado.respuesta


def test_pipeline_avanza_las_cuatro_etapas(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "Hola"))
    assert hermes.atender(entrante("cli_001", "me interesa el precio")).lead.etapa == Etapa.PROSPECCION
    assert hermes.atender(entrante("cli_001", "lo quiero para manana")).lead.etapa == Etapa.CIERRE
    cierre = hermes.atender(entrante("cli_001", "acepto, confirmo"))
    assert cierre.lead.etapa == Etapa.SEGUIMIENTO


def test_lead_sigue_siendo_el_mismo_al_cambiar_de_canal(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    primero = hermes.atender(
        entrante("cli_001", "me interesa", contacto="lead@correo.com", canal=Canal.CORREO)
    )
    segundo = hermes.atender(entrante("cli_001", "cuando puedo agendar", contacto="lead@correo.com"))
    assert segundo.lead.lead_id == primero.lead.lead_id
    assert segundo.lead.canal_origen == Canal.CORREO
    assert segundo.lead.etapa == Etapa.CIERRE


def test_lead_no_interesado_se_marca_perdido(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    resultado = hermes.atender(entrante("cli_001", "no me interesa, gracias"))
    assert resultado.lead.estatus == Estatus.PERDIDO
    assert resultado.respuesta is None


def test_canal_deshabilitado_se_rechaza(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_sin_correo", correo=False, llamadas=False)
    with pytest.raises(ClienteNoRegistradoError):
        hermes.atender(entrante("cli_sin_correo", "hola", canal=Canal.CORREO, contacto="x@y.com"))


def test_cada_cliente_solo_ve_sus_propios_leads(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_a")
    dar_de_alta(almacen, cliente_id="cli_b")
    hermes.atender(entrante("cli_a", "hola", contacto="+521111"))
    segundo_de_a = hermes.atender(entrante("cli_a", "hola", contacto="+521112")).lead
    hermes.atender(entrante("cli_b", "hola", contacto="+522222"))

    assert len(almacen.listar_leads("cli_a")) == 2
    assert len(almacen.listar_leads("cli_b")) == 1
    assert almacen.obtener_lead("cli_b", segundo_de_a.lead_id) is None
    assert almacen.buscar_lead_por_contacto("cli_b", "+521111") is None


def test_consulta_sin_cliente_id_es_error(almacen: Almacen):
    with pytest.raises(AislamientoError):
        almacen.listar_leads("")


def test_seguimiento_se_detiene_en_el_tercero(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    lead_id = hermes.atender(entrante("cli_001", "hola")).lead.lead_id
    for _ in range(3):
        hermes.programar_seguimiento("cli_001", lead_id)
    ultimo = hermes.programar_seguimiento("cli_001", lead_id)
    assert ultimo.motivo == "limite_de_seguimientos"
    assert ultimo.lead.estatus == Estatus.PERDIDO


def test_reporte_solo_cuenta_al_tenant(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen, cliente_id="cli_a")
    dar_de_alta(almacen, cliente_id="cli_b")
    hermes.atender(entrante("cli_a", "hola", contacto="+521111"))
    hermes.atender(entrante("cli_b", "hola", contacto="+522222"))
    reporte = hermes.reporte("cli_a")
    assert reporte["total_leads"] == 1
    assert reporte["por_etapa"][Etapa.APERTURA.value] == 1


def test_hoja_leads_tiene_las_columnas_de_la_especificacion(hermes: Hermes, almacen: Almacen):
    dar_de_alta(almacen)
    hermes.atender(entrante("cli_001", "hola"))
    fila = almacen.exportar_hoja_leads("cli_001")[0]
    assert set(fila) == {
        "lead_id",
        "nombre",
        "contacto",
        "canal_origen",
        "fuente",
        "mensaje_inicial",
        "fecha_entrada",
        "estatus",
        "etapa",
        "ultima_respuesta_lead",
        "ultimo_contacto",
        "contador_seguimientos",
        "producto_interes",
        "notas_internas",
        "nivel_decision",
        "canal_preferido_lead",
    }
