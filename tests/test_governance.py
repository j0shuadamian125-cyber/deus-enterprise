"""Niveles de autonomia, segunda confirmacion y bitacora (Secciones 3.8 y 4)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from hermes.governance import (
    APROBACIONES_PARA_BAJAR_A_NIVEL_1,
    Gobernanza,
    GobernanzaError,
    nivel_de,
)
from hermes.models import CanalConfirmacion, EstadoDecision, NivelDecision, ahora
from hermes.storage import Almacen


@pytest.fixture()
def gobernanza(almacen: Almacen) -> Gobernanza:
    return Gobernanza(almacen)


def test_accion_desconocida_nace_en_nivel_2():
    assert nivel_de("accion_que_nadie_clasifico") == NivelDecision.APROBACION_PREVIA


def test_nivel_1_se_ejecuta_sin_aprobacion(gobernanza: Gobernanza):
    decision = gobernanza.registrar("cli_001", "responder_con_guion_aprobado", "responder")
    assert decision.nivel == NivelDecision.REVERSIBLE
    assert decision.estado == EstadoDecision.APROBADA
    assert gobernanza.puede_ejecutar(decision) is True


def test_nivel_2_queda_pendiente_hasta_aprobacion(gobernanza: Gobernanza):
    decision = gobernanza.registrar("cli_001", "responder_fuera_de_guion", "salir del guion")
    assert decision.estado == EstadoDecision.PENDIENTE
    assert gobernanza.puede_ejecutar(decision) is False

    aprobada = gobernanza.aprobar(decision.decision_id, "joshua")
    assert aprobada.estado == EstadoDecision.APROBADA
    assert gobernanza.puede_ejecutar(aprobada) is True


def test_nivel_4_no_se_ejecuta_solo_con_el_chat(gobernanza: Gobernanza):
    decision = gobernanza.registrar("cli_001", "eliminar_datos_cliente", "borrar tenant")
    assert decision.nivel == NivelDecision.IRREVERSIBLE

    tras_chat = gobernanza.aprobar(decision.decision_id, "joshua", CanalConfirmacion.CHAT)
    assert tras_chat.estado == EstadoDecision.ESPERA_SEGUNDA_CONFIRMACION
    assert gobernanza.puede_ejecutar(tras_chat) is False

    confirmada = gobernanza.aprobar(decision.decision_id, "joshua", CanalConfirmacion.WHATSAPP)
    assert confirmada.estado == EstadoDecision.APROBADA
    assert gobernanza.puede_ejecutar(confirmada) is True


def test_nivel_4_exige_pasar_primero_por_el_chat(gobernanza: Gobernanza):
    decision = gobernanza.registrar("cli_001", "cancelar_cuenta_cliente", "cancelar")
    with pytest.raises(GobernanzaError):
        gobernanza.aprobar(decision.decision_id, "joshua", CanalConfirmacion.WHATSAPP)


def test_segunda_confirmacion_caduca_y_el_silencio_no_aprueba(gobernanza: Gobernanza, almacen: Almacen):
    decision = gobernanza.registrar("cli_001", "eliminar_datos_cliente", "borrar tenant")
    gobernanza.aprobar(decision.decision_id, "joshua", CanalConfirmacion.CHAT)

    vencida = almacen.obtener_decision(decision.decision_id)
    vencida.payload["vence_segunda_confirmacion"] = (ahora() - timedelta(hours=1)).isoformat()
    almacen.guardar_decision(vencida)

    caducadas = gobernanza.caducar_pendientes_nivel_4()
    assert [d.decision_id for d in caducadas] == [decision.decision_id]
    final = almacen.obtener_decision(decision.decision_id)
    assert final.estado == EstadoDecision.PENDIENTE
    assert gobernanza.puede_ejecutar(final) is False


def test_una_decision_resuelta_no_se_reaprueba(gobernanza: Gobernanza):
    decision = gobernanza.registrar("cli_001", "modificar_prompt", "cambiar prompt")
    gobernanza.rechazar(decision.decision_id, "joshua", "no por ahora")
    with pytest.raises(GobernanzaError):
        gobernanza.aprobar(decision.decision_id, "joshua")


def test_bajar_a_nivel_1_requiere_15_aprobaciones_y_es_nivel_3(gobernanza: Gobernanza):
    for _ in range(APROBACIONES_PARA_BAJAR_A_NIVEL_1 - 1):
        decision = gobernanza.registrar("cli_001", "modificar_prompt", "cambio")
        gobernanza.aprobar(decision.decision_id, "joshua")
    assert gobernanza.elegible_para_nivel_1("cli_001", "modificar_prompt") is False

    ultima = gobernanza.registrar("cli_001", "modificar_prompt", "cambio")
    gobernanza.aprobar(ultima.decision_id, "joshua")
    assert gobernanza.elegible_para_nivel_1("cli_001", "modificar_prompt") is True

    propuesta = gobernanza.proponer_baja_a_nivel_1("cli_001", "modificar_prompt")
    assert propuesta.nivel == NivelDecision.CAMBIO_ESTRUCTURAL
    assert propuesta.estado == EstadoDecision.PENDIENTE


def test_un_rechazo_rompe_la_racha(gobernanza: Gobernanza):
    for _ in range(APROBACIONES_PARA_BAJAR_A_NIVEL_1):
        decision = gobernanza.registrar("cli_001", "modificar_prompt", "cambio")
        gobernanza.aprobar(decision.decision_id, "joshua")
    rechazada = gobernanza.registrar("cli_001", "modificar_prompt", "cambio")
    gobernanza.rechazar(rechazada.decision_id, "joshua")
    assert gobernanza.elegible_para_nivel_1("cli_001", "modificar_prompt") is False


def test_cada_decision_deja_memoria_del_tenant(gobernanza: Gobernanza, almacen: Almacen):
    gobernanza.registrar("cli_a", "registrar_lead", "alta de lead")
    gobernanza.registrar("cli_b", "registrar_lead", "alta de lead")
    assert len(almacen.listar_memoria("cli_a")) == 1
    assert len(almacen.listar_memoria()) == 2
