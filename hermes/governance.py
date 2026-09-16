"""Niveles de autonomia y Bitacora de Decisiones (Seccion 4).

Reglas que este modulo hace cumplir:
- Toda accion nueva nace en Nivel 2 (4.2). Solo las excepciones de bajo riesgo
  de 4.4 son Nivel 1 y se ejecutan sin aprobacion previa.
- Una accion baja a Nivel 1 solo tras 15 aprobaciones seguidas sin rechazo, y
  ese cambio de nivel es en si una decision de Nivel 3.
- Nivel 4 exige segunda confirmacion por un canal distinto al chat (3.8), con
  caducidad: el silencio nunca se interpreta como aprobacion.
"""
from __future__ import annotations

from datetime import timedelta

from .models import (
    CanalConfirmacion,
    Decision,
    EstadoDecision,
    MemoriaResumen,
    NivelDecision,
    ahora,
)
from .storage import Almacen

#: Acciones de HERMES cubiertas por la excepcion de bajo riesgo de la Seccion 4.4.
ACCIONES_NIVEL_1 = frozenset(
    {
        "responder_con_guion_aprobado",
        "registrar_lead",
        "registrar_transcripcion",
        "avanzar_etapa",
        "programar_seguimiento",
        "generar_guion_llamada_asistida",
    }
)

#: Acciones que cambian configuracion o prompts: Nivel 2 o mas (tabla 4.1).
NIVELES_EXPLICITOS: dict[str, NivelDecision] = {
    "modificar_prompt": NivelDecision.APROBACION_PREVIA,
    "cambiar_plan_cliente": NivelDecision.APROBACION_PREVIA,
    "responder_fuera_de_guion": NivelDecision.APROBACION_PREVIA,
    "conectar_canal_nuevo": NivelDecision.CAMBIO_ESTRUCTURAL,
    "activar_grabacion_llamadas": NivelDecision.CAMBIO_ESTRUCTURAL,
    "eliminar_datos_cliente": NivelDecision.IRREVERSIBLE,
    "cancelar_cuenta_cliente": NivelDecision.IRREVERSIBLE,
}

APROBACIONES_PARA_BAJAR_A_NIVEL_1 = 15
PLAZO_SEGUNDA_CONFIRMACION = timedelta(hours=2)


class GobernanzaError(RuntimeError):
    pass


def nivel_de(accion: str) -> NivelDecision:
    """Nivel que corresponde a una accion; Nivel 2 por defecto (Seccion 4.2)."""
    if accion in NIVELES_EXPLICITOS:
        return NIVELES_EXPLICITOS[accion]
    if accion in ACCIONES_NIVEL_1:
        return NivelDecision.REVERSIBLE
    return NivelDecision.APROBACION_PREVIA


class Gobernanza:
    def __init__(self, almacen: Almacen) -> None:
        self.almacen = almacen

    # --- registro ------------------------------------------------------
    def registrar(
        self,
        cliente_id: str,
        accion: str,
        descripcion: str,
        payload: dict | None = None,
        nivel: NivelDecision | None = None,
    ) -> Decision:
        nivel_efectivo = nivel if nivel is not None else nivel_de(accion)
        decision = Decision(
            decision_id=self.almacen.siguiente_folio("decision", "DEC"),
            cliente_id=cliente_id,
            nivel=nivel_efectivo,
            descripcion=descripcion,
            payload={"accion": accion, **(payload or {})},
        )
        if nivel_efectivo <= NivelDecision.REVERSIBLE:
            decision.estado = EstadoDecision.APROBADA
            decision.aprobado_por = "auto-nivel-1"
            decision.fecha_resolucion = ahora()
        self.almacen.guardar_decision(decision)
        self.anotar_memoria(decision)
        return decision

    def puede_ejecutar(self, decision: Decision) -> bool:
        return (
            decision.estado == EstadoDecision.APROBADA
            and (
                decision.nivel < NivelDecision.IRREVERSIBLE
                or decision.canal_confirmacion
                in (CanalConfirmacion.WHATSAPP, CanalConfirmacion.TELEGRAM)
            )
        )

    # --- resolucion ----------------------------------------------------
    def aprobar(
        self,
        decision_id: str,
        aprobado_por: str,
        canal: CanalConfirmacion = CanalConfirmacion.CHAT,
    ) -> Decision:
        decision = self._cargar(decision_id)
        self._exigir_abierta(decision)
        if decision.nivel == NivelDecision.IRREVERSIBLE and canal == CanalConfirmacion.CHAT:
            # 3.8: la aprobacion en el chat no basta para Nivel 4.
            decision.estado = EstadoDecision.ESPERA_SEGUNDA_CONFIRMACION
            decision.aprobado_por = aprobado_por
            decision.canal_confirmacion = CanalConfirmacion.CHAT
            decision.payload["vence_segunda_confirmacion"] = (
                ahora() + PLAZO_SEGUNDA_CONFIRMACION
            ).isoformat()
            self.almacen.guardar_decision(decision)
            return decision

        if decision.nivel == NivelDecision.IRREVERSIBLE:
            if decision.estado != EstadoDecision.ESPERA_SEGUNDA_CONFIRMACION:
                raise GobernanzaError(
                    "Nivel 4 requiere primero la aprobacion en el chat y luego la confirmacion "
                    "por un canal distinto (Seccion 3.8)"
                )
            if canal not in (CanalConfirmacion.WHATSAPP, CanalConfirmacion.TELEGRAM):
                raise GobernanzaError(
                    "La segunda confirmacion de Nivel 4 debe llegar por WhatsApp o Telegram"
                )

        decision.estado = EstadoDecision.APROBADA
        decision.aprobado_por = aprobado_por
        decision.canal_confirmacion = canal
        decision.fecha_resolucion = ahora()
        self.almacen.guardar_decision(decision)
        self.anotar_memoria(decision)
        return decision

    def rechazar(self, decision_id: str, rechazado_por: str, motivo: str = "") -> Decision:
        decision = self._cargar(decision_id)
        self._exigir_abierta(decision)
        decision.estado = EstadoDecision.RECHAZADA
        decision.aprobado_por = rechazado_por
        decision.fecha_resolucion = ahora()
        decision.resultado_observado = motivo
        self.almacen.guardar_decision(decision)
        self.anotar_memoria(decision)
        return decision

    def caducar_pendientes_nivel_4(self) -> list[Decision]:
        """Sin confirmacion en el plazo, la propuesta vuelve a 'pendiente' (3.8, paso 4)."""
        caducadas = []
        for decision in self.almacen.listar_decisiones(
            estado=EstadoDecision.ESPERA_SEGUNDA_CONFIRMACION.value
        ):
            limite = decision.payload.get("vence_segunda_confirmacion")
            if limite and ahora().isoformat() > limite:
                decision.estado = EstadoDecision.PENDIENTE
                decision.aprobado_por = None
                decision.canal_confirmacion = CanalConfirmacion.NO_APLICA
                decision.payload.pop("vence_segunda_confirmacion", None)
                decision.resultado_observado = "Sin segunda confirmacion en el plazo"
                self.almacen.guardar_decision(decision)
                caducadas.append(decision)
        return caducadas

    def registrar_resultado(self, decision_id: str, resultado: str) -> Decision:
        decision = self._cargar(decision_id)
        decision.resultado_observado = resultado
        self.almacen.guardar_decision(decision)
        return decision

    # --- degradacion de nivel -------------------------------------------
    def elegible_para_nivel_1(self, cliente_id: str, accion: str) -> bool:
        """15 aprobaciones seguidas sin rechazo (Seccion 4.2)."""
        historial = [
            decision
            for decision in self.almacen.listar_decisiones(cliente_id=cliente_id)
            if decision.payload.get("accion") == accion
            and decision.estado
            in (EstadoDecision.APROBADA, EstadoDecision.RECHAZADA)
        ]
        racha = 0
        for decision in reversed(historial):
            if decision.estado == EstadoDecision.RECHAZADA:
                break
            racha += 1
        return racha >= APROBACIONES_PARA_BAJAR_A_NIVEL_1

    def proponer_baja_a_nivel_1(self, cliente_id: str, accion: str) -> Decision:
        """El cambio de nivel es, en si, una decision de Nivel 3 (Seccion 4.2)."""
        if not self.elegible_para_nivel_1(cliente_id, accion):
            raise GobernanzaError(
                f"'{accion}' aun no acumula {APROBACIONES_PARA_BAJAR_A_NIVEL_1} aprobaciones seguidas"
            )
        return self.registrar(
            cliente_id=cliente_id,
            accion="cambio_de_nivel",
            descripcion=f"Bajar la accion '{accion}' de Nivel 2 a Nivel 1",
            payload={"accion_objetivo": accion, "nivel_destino": 1},
            nivel=NivelDecision.CAMBIO_ESTRUCTURAL,
        )

    # --- memoria viva ----------------------------------------------------
    def anotar_memoria(self, decision: Decision) -> MemoriaResumen:
        entrada = MemoriaResumen(
            cliente_id=decision.cliente_id,
            resumen_evento=f"[{decision.estado.value}] {decision.descripcion}",
            decision_id_referencia=decision.decision_id,
        )
        return self.almacen.guardar_memoria(entrada)

    @staticmethod
    def _exigir_abierta(decision: Decision) -> None:
        if decision.estado in (EstadoDecision.APROBADA, EstadoDecision.RECHAZADA):
            raise GobernanzaError(
                f"La decision {decision.decision_id} ya esta {decision.estado.value}"
            )

    def _cargar(self, decision_id: str) -> Decision:
        decision = self.almacen.obtener_decision(decision_id)
        if decision is None:
            raise GobernanzaError(f"Decision inexistente: {decision_id}")
        return decision
