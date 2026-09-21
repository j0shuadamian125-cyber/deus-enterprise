"""Motor canal-agnostico de HERMES: pipeline de 4 etapas (Seccion 5.5).

Un mismo lead avanza Apertura -> Prospeccion -> Cierre -> Seguimiento sin
importar por que canal entro ni por cual esta siendo atendido hoy. Lo unico
que cambia entre canales es la capa de entrada/salida (Seccion 5.2).

Sobre ese pipeline corren tres capas mas: lectura comercial del mensaje
(`intelligence`), contexto temporal del tenant (`tiempo`) y estrategias
versionadas (`strategies`). Cuando la lectura detecta que HERMES no debe seguir
solo, la conversacion pasa a la bandeja de intervencion humana en lugar de
responder.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .governance import Gobernanza
from .intelligence import AnalisisMensaje, analizar, evaluar_escalamiento, objetivo_de_etapa
from .llm import GeneradorRespuestas
from .models import (
    Canal,
    Cliente,
    Decision,
    Escalamiento,
    EstadoEscalamiento,
    Estatus,
    Etapa,
    Intencion,
    Lead,
    Llamada,
    Mensaje,
    MotivoEscalamiento,
    NivelDecision,
    NivelServicioLlamada,
    RegistroResultado,
    ResultadoLlamada,
    ahora,
)
from .prompts import construir_prompt, guion_llamada_asistida
from .storage import Almacen
from .strategies import Estrategias, renderizar, variables_de_contexto
from .tiempo import ContextoTemporal, contexto_temporal

ORDEN_ETAPAS = [Etapa.APERTURA, Etapa.PROSPECCION, Etapa.CIERRE, Etapa.SEGUIMIENTO]
MAX_SEGUIMIENTOS = 3

#: Senales textuales que califican a un lead para avanzar de etapa.
SENALES_AVANCE = {
    Etapa.APERTURA: (
        "quiero",
        "me interesa",
        "precio",
        "costo",
        "cotiza",
        "informacion",
        "información",
        "disponible",
    ),
    Etapa.PROSPECCION: (
        "cuando",
        "cuándo",
        "agendar",
        "comprar",
        "presupuesto",
        "hoy",
        "manana",
        "mañana",
        "listo",
    ),
    Etapa.CIERRE: ("pagar", "pago", "confirmo", "acepto", "de acuerdo", "adelante"),
}

SENALES_PERDIDA = ("no me interesa", "no gracias", "ya compre", "ya no", "baja")

RECOMENDACION_POR_MOTIVO = {
    MotivoEscalamiento.SOLICITA_HUMANO: "El lead pidio hablar con una persona: tomar la conversacion",
    MotivoEscalamiento.LEGAL: "Tema legal: responder solo con el area responsable",
    MotivoEscalamiento.FINANCIERO_SENSIBLE: "Movimiento de dinero: confirmar antes de comprometer",
    MotivoEscalamiento.QUEJA_COMPLEJA: "Queja: atender con una persona y ofrecer solucion concreta",
    MotivoEscalamiento.FUERA_DE_LIMITES: "Pide condiciones fuera del guion aprobado (descuento)",
    MotivoEscalamiento.BAJA_CONFIANZA: "No se entendio la intencion con confianza suficiente",
    MotivoEscalamiento.INFORMACION_INSUFICIENTE: "Mensaje sin contenido util: pedir detalle",
    MotivoEscalamiento.REGLA_DEL_CLIENTE: "Coincide con una palabra clave que el cliente marco",
    MotivoEscalamiento.RIESGO_OPERATIVO: "Riesgo operativo detectado",
}


@dataclass
class ContactoEntrante:
    """Evento normalizado que produce cualquier adaptador de canal."""

    cliente_id: str
    canal: Canal
    contacto: str
    texto: str
    nombre: str | None = None
    fuente: str | None = None
    metadatos: dict = field(default_factory=dict)


@dataclass
class ResultadoAtencion:
    lead: Lead
    decision: Decision
    respuesta: str | None
    enviada: bool
    etapa_anterior: Etapa
    motivo: str = ""
    analisis: AnalisisMensaje | None = None
    escalamiento: Escalamiento | None = None
    estrategia_id: str | None = None
    mensaje_id: str | None = None
    motor: str = ""
    error_motor: str | None = None


class ClienteNoRegistradoError(RuntimeError):
    pass


class CanalNoHabilitadoError(RuntimeError):
    pass


class Hermes:
    def __init__(
        self,
        almacen: Almacen,
        generador: GeneradorRespuestas | None = None,
        gobernanza: Gobernanza | None = None,
    ) -> None:
        self.almacen = almacen
        self.generador = generador or GeneradorRespuestas()
        self.gobernanza = gobernanza or Gobernanza(almacen)
        self.estrategias = Estrategias(almacen)

    # --- clientes ------------------------------------------------------
    def cliente(self, cliente_id: str) -> Cliente:
        cliente = self.almacen.obtener_cliente(cliente_id)
        if cliente is None:
            raise ClienteNoRegistradoError(
                f"{cliente_id} no esta dado de alta; ejecute el onboarding (Seccion 5.7)"
            )
        return cliente

    # --- entrada de cualquier canal -------------------------------------
    def atender(self, evento: ContactoEntrante) -> ResultadoAtencion:
        cliente = self.cliente(evento.cliente_id)
        self._verificar_canal_habilitado(cliente, evento.canal)
        contexto = contexto_temporal(cliente)

        lead = self._lead_para(evento)
        etapa_anterior = lead.etapa

        self.almacen.guardar_mensaje(
            Mensaje(
                mensaje_id=self.almacen.siguiente_folio("mensaje", "MSG", 6),
                cliente_id=lead.cliente_id,
                lead_id=lead.lead_id,
                canal=evento.canal,
                direccion="entrante",
                autor="lead",
                texto=evento.texto,
                etapa=lead.etapa,
            )
        )
        lead.ultima_respuesta_lead = evento.texto
        lead.ultimo_contacto = ahora()
        lead.canal_preferido_lead = evento.canal
        lead.estatus = Estatus.EN_PROCESO if lead.estatus == Estatus.NUEVO else lead.estatus

        analisis = analizar(evento.texto)
        lead.intencion = analisis.intencion
        lead.confianza = analisis.confianza
        lead.urgencia = analisis.urgencia
        lead.objeciones = analisis.objeciones
        lead.senales_compra = analisis.senales_compra

        if analisis.intencion == Intencion.DESINTERES or self._es_perdida(evento.texto):
            lead.estatus = Estatus.PERDIDO
            self.almacen.guardar_lead(lead)
            decision = self.gobernanza.registrar(
                cliente_id=lead.cliente_id,
                accion="registrar_lead",
                descripcion=f"Lead {lead.lead_id} marcado como Perdido por respuesta del lead",
                payload={"lead_id": lead.lead_id, "canal": evento.canal.value},
            )
            self._registrar_resultado(
                lead, objetivo_de_etapa(lead), "cerrar sin insistir", "perdido", decision.decision_id
            )
            return ResultadoAtencion(
                lead=lead,
                decision=decision,
                respuesta=None,
                enviada=False,
                etapa_anterior=etapa_anterior,
                motivo="lead_no_interesado",
                analisis=analisis,
            )

        lead.etapa = self._siguiente_etapa(lead, evento.texto)
        lead.objetivo_actual = objetivo_de_etapa(lead)
        if lead.etapa == Etapa.PROSPECCION and not lead.producto_interes:
            lead.producto_interes = evento.texto[:120]

        estrategia = self.estrategias.activa_para(lead.cliente_id, lead.etapa, evento.texto)
        lead.estrategia_aplicada = estrategia.estrategia_id if estrategia else None
        sugerida = (
            renderizar(estrategia.plantilla, variables_de_contexto(lead, cliente, contexto))
            if estrategia
            else ""
        )

        motivo_escalamiento = evaluar_escalamiento(analisis, cliente, lead)
        if motivo_escalamiento is not None:
            return self._escalar(
                lead, cliente, evento.canal, analisis, motivo_escalamiento, sugerida, etapa_anterior
            )

        lead.requiere_humano = False
        lead.motivo_escalamiento = None
        historial = self._historial_texto(lead)
        prompt = construir_prompt(
            lead,
            evento.canal,
            cliente,
            historial,
            contexto=contexto,
            analisis=analisis,
            estrategia=sugerida,
        )
        generada = self.generador.responder(
            lead, evento.canal, cliente.nombre_negocio, prompt, evento.texto, contexto.saludo
        )

        # Seccion 4.4: responder con guion aprobado es Nivel 1; salir del guion,
        # Nivel 2 con aprobacion previa en el chat de DEUS.
        con_guion = generada.con_guion_aprobado or cliente.guion_aprobado
        accion = "responder_con_guion_aprobado" if con_guion else "responder_fuera_de_guion"
        decision = self.gobernanza.registrar(
            cliente_id=lead.cliente_id,
            accion=accion,
            descripcion=(
                f"Responder a {lead.lead_id} por {evento.canal.value} en etapa {lead.etapa.value}"
            ),
            payload={
                "lead_id": lead.lead_id,
                "canal": evento.canal.value,
                "etapa": lead.etapa.value,
                "respuesta": generada.texto,
                "motor": generada.motor,
                "estrategia_id": lead.estrategia_aplicada,
                "analisis": analisis.resumen(),
            },
        )
        lead.nivel_decision = decision.nivel
        self.almacen.guardar_lead(lead)

        enviada = self.gobernanza.puede_ejecutar(decision)
        mensaje_id: str | None = None
        if enviada:
            mensaje_id = self.almacen.siguiente_folio("mensaje", "MSG", 6)
            self.almacen.guardar_mensaje(
                Mensaje(
                    mensaje_id=mensaje_id,
                    cliente_id=lead.cliente_id,
                    lead_id=lead.lead_id,
                    canal=evento.canal,
                    direccion="saliente",
                    texto=generada.texto,
                    etapa=lead.etapa,
                    decision_id=decision.decision_id,
                )
            )
            self.gobernanza.registrar_resultado(decision.decision_id, "respuesta_enviada")

        self._registrar_resultado(
            lead,
            lead.objetivo_actual or objetivo_de_etapa(lead),
            f"responder en {lead.etapa.value} con motor {generada.motor}",
            "respuesta_enviada" if enviada else "pendiente_de_aprobacion",
            decision.decision_id,
            estrategia_id=lead.estrategia_aplicada,
        )

        return ResultadoAtencion(
            lead=lead,
            decision=decision,
            respuesta=generada.texto,
            enviada=enviada,
            etapa_anterior=etapa_anterior,
            motivo="" if enviada else "pendiente_de_aprobacion",
            analisis=analisis,
            estrategia_id=lead.estrategia_aplicada,
            mensaje_id=mensaje_id,
            motor=generada.motor,
            error_motor=generada.error,
        )

    # --- intervencion humana ----------------------------------------------
    def _escalar(
        self,
        lead: Lead,
        cliente: Cliente,
        canal: Canal,
        analisis: AnalisisMensaje,
        motivo: MotivoEscalamiento,
        sugerida: str,
        etapa_anterior: Etapa,
    ) -> ResultadoAtencion:
        lead.requiere_humano = True
        lead.motivo_escalamiento = motivo
        self.almacen.guardar_lead(lead)
        decision = self.gobernanza.registrar(
            cliente_id=lead.cliente_id,
            accion="escalar_a_humano",
            descripcion=f"Lead {lead.lead_id} escalado por {motivo.value}",
            payload={"lead_id": lead.lead_id, "canal": canal.value, "motivo": motivo.value},
        )
        escalamiento = self.almacen.guardar_escalamiento(
            Escalamiento(
                escalamiento_id=self.almacen.siguiente_folio(
                    f"escalamiento:{lead.cliente_id}", "ESC"
                ),
                cliente_id=lead.cliente_id,
                lead_id=lead.lead_id,
                canal=canal,
                motivo=motivo,
                contexto=self._historial_texto(lead),
                objetivo=lead.objetivo_actual or objetivo_de_etapa(lead),
                recomendacion=RECOMENDACION_POR_MOTIVO[motivo],
                respuesta_sugerida=sugerida,
                decision_id=decision.decision_id,
            )
        )
        self.gobernanza.registrar_resultado(decision.decision_id, "escalado_a_humano")
        return ResultadoAtencion(
            lead=lead,
            decision=decision,
            respuesta=None,
            enviada=False,
            etapa_anterior=etapa_anterior,
            motivo="requiere_intervencion_humana",
            analisis=analisis,
            escalamiento=escalamiento,
        )

    def resolver_escalamiento(
        self,
        cliente_id: str,
        escalamiento_id: str,
        atendido_por: str,
        accion: str,
        respuesta: str | None = None,
        resultado: str | None = None,
    ) -> tuple[Escalamiento, str | None]:
        """Aprobar / editar / tomar la conversacion desde el panel humano."""
        escalamiento = self.almacen.obtener_escalamiento(cliente_id, escalamiento_id)
        if escalamiento is None:
            raise ValueError(f"Escalamiento inexistente para {cliente_id}: {escalamiento_id}")
        lead = self.almacen.obtener_lead(cliente_id, escalamiento.lead_id)
        if lead is None:
            raise ValueError(f"Lead inexistente para {cliente_id}: {escalamiento.lead_id}")

        texto: str | None = None
        if accion == "aprobar":
            if not (escalamiento.respuesta_sugerida or "").strip():
                raise ValueError(
                    "No hay respuesta sugerida que aprobar: edite la respuesta antes de enviarla"
                )
            texto = escalamiento.respuesta_sugerida
        elif accion == "editar":
            if not respuesta:
                raise ValueError("Para editar debe enviarse la respuesta final")
            texto = respuesta
        elif accion == "tomar":
            escalamiento.estado = EstadoEscalamiento.TOMADO
        elif accion == "posponer":
            escalamiento.estado = EstadoEscalamiento.POSPUESTO
        elif accion == "rechazar":
            escalamiento.estado = EstadoEscalamiento.RECHAZADO
        elif accion == "resolver":
            escalamiento.estado = EstadoEscalamiento.RESUELTO
        else:
            raise ValueError(f"Accion de intervencion desconocida: {accion}")

        if texto:
            if not texto.strip():
                raise ValueError("No hay respuesta que enviar: edite la respuesta antes de aprobar")
            escalamiento.estado = EstadoEscalamiento.RESUELTO
            self.almacen.guardar_mensaje(
                Mensaje(
                    mensaje_id=self.almacen.siguiente_folio("mensaje", "MSG", 6),
                    cliente_id=cliente_id,
                    lead_id=lead.lead_id,
                    canal=escalamiento.canal,
                    direccion="saliente",
                    autor="humano",
                    texto=texto,
                    etapa=lead.etapa,
                    decision_id=escalamiento.decision_id,
                )
            )
            lead.requiere_humano = False
            lead.motivo_escalamiento = None
            self.almacen.guardar_lead(lead)

        escalamiento.atendido_por = atendido_por
        escalamiento.accion = accion
        escalamiento.respuesta_final = texto
        escalamiento.resultado = resultado
        if escalamiento.estado in (EstadoEscalamiento.RESUELTO, EstadoEscalamiento.RECHAZADO):
            escalamiento.fecha_resolucion = ahora()
            escalamiento.segundos_hasta_resultado = int(
                (escalamiento.fecha_resolucion - escalamiento.fecha_creacion).total_seconds()
            )
        self.almacen.guardar_escalamiento(escalamiento)

        decision = self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="intervencion_humana",
            descripcion=f"{atendido_por} ejecuto '{accion}' sobre {escalamiento_id}",
            payload={
                "escalamiento_id": escalamiento_id,
                "lead_id": lead.lead_id,
                "accion": accion,
                "motivo": escalamiento.motivo.value,
            },
        )
        self._registrar_resultado(
            lead,
            escalamiento.objetivo,
            escalamiento.recomendacion,
            resultado or accion,
            decision.decision_id,
            segundos=escalamiento.segundos_hasta_resultado,
        )
        return escalamiento, texto

    # --- llamadas --------------------------------------------------------
    def registrar_llamada(
        self,
        cliente_id: str,
        contacto: str,
        nivel_servicio: NivelServicioLlamada,
        transcripcion: str,
        duracion_segundos: int = 0,
        url_grabacion: str | None = None,
        resultado: ResultadoLlamada | None = None,
        nombre: str | None = None,
        fuente: str | None = None,
        proveedor: str | None = None,
        call_sid: str | None = None,
    ) -> tuple[Llamada, ResultadoAtencion]:
        """Webhook de la plataforma de voz: la transcripcion entra al mismo CRM (5.4.3)."""
        cliente = self.cliente(cliente_id)
        if url_grabacion and not cliente.grabacion_autorizada:
            # 5.4.2: grabar requiere autorizacion y validacion legal por jurisdiccion.
            url_grabacion = None

        atencion = self.atender(
            ContactoEntrante(
                cliente_id=cliente_id,
                canal=Canal.LLAMADA,
                contacto=contacto,
                texto=transcripcion,
                nombre=nombre,
                fuente=fuente,
            )
        )
        llamada = Llamada(
            llamada_id=self.almacen.siguiente_folio("llamada", "CALL"),
            cliente_id=cliente_id,
            lead_id=atencion.lead.lead_id,
            nivel_servicio=nivel_servicio,
            duracion_segundos=duracion_segundos,
            url_grabacion=url_grabacion,
            transcripcion_texto=transcripcion,
            resultado=resultado
            or (
                ResultadoLlamada.AVANZO_ETAPA
                if atencion.lead.etapa != atencion.etapa_anterior
                else ResultadoLlamada.SIN_RESPUESTA
            ),
            etapa_resultante=atencion.lead.etapa,
            proveedor=proveedor,
            call_sid=call_sid,
        )
        self.almacen.guardar_llamada(llamada)
        self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="registrar_transcripcion",
            descripcion=f"Transcripcion de {llamada.llamada_id} asociada a {llamada.lead_id}",
            payload={"llamada_id": llamada.llamada_id, "lead_id": llamada.lead_id},
        )
        return llamada, atencion

    def preparar_guion_asistido(self, cliente_id: str, lead_id: str) -> str:
        """Llamada asistida: la IA prepara el guion, la persona hace la llamada (5.4.1)."""
        cliente = self.cliente(cliente_id)
        lead = self.almacen.obtener_lead(cliente_id, lead_id)
        if lead is None:
            raise ValueError(f"Lead inexistente para {cliente_id}: {lead_id}")
        contexto = contexto_temporal(cliente)
        prompt = guion_llamada_asistida(lead, cliente, self._historial_texto(lead), contexto)
        generada = self.generador.responder(
            lead,
            Canal.LLAMADA,
            cliente.nombre_negocio,
            prompt,
            lead.ultima_respuesta_lead or "",
            contexto.saludo,
        )
        self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="generar_guion_llamada_asistida",
            descripcion=f"Guion de llamada asistida para {lead_id}",
            payload={"lead_id": lead_id},
        )
        return generada.texto

    # --- seguimiento ------------------------------------------------------
    def programar_seguimiento(self, cliente_id: str, lead_id: str) -> ResultadoAtencion:
        cliente = self.cliente(cliente_id)
        lead = self.almacen.obtener_lead(cliente_id, lead_id)
        if lead is None:
            raise ValueError(f"Lead inexistente para {cliente_id}: {lead_id}")

        canal = lead.canal_preferido_lead or lead.canal_origen
        etapa_anterior = lead.etapa
        lead.etapa = Etapa.SEGUIMIENTO
        lead.contador_seguimientos += 1
        contexto = contexto_temporal(cliente)

        if lead.contador_seguimientos > MAX_SEGUIMIENTOS:
            lead.estatus = Estatus.PERDIDO
            self.almacen.guardar_lead(lead)
            decision = self.gobernanza.registrar(
                cliente_id=cliente_id,
                accion="programar_seguimiento",
                descripcion=(
                    f"Lead {lead_id} cerrado tras {MAX_SEGUIMIENTOS} seguimientos sin respuesta"
                ),
                payload={"lead_id": lead_id},
            )
            return ResultadoAtencion(
                lead=lead,
                decision=decision,
                respuesta=None,
                enviada=False,
                etapa_anterior=etapa_anterior,
                motivo="limite_de_seguimientos",
            )

        estrategia = self.estrategias.activa_para(cliente_id, Etapa.SEGUIMIENTO)
        sugerida = (
            renderizar(estrategia.plantilla, variables_de_contexto(lead, cliente, contexto))
            if estrategia
            else ""
        )
        prompt = construir_prompt(
            lead, canal, cliente, self._historial_texto(lead), contexto=contexto, estrategia=sugerida
        )
        generada = self.generador.responder(
            lead,
            canal,
            cliente.nombre_negocio,
            prompt,
            lead.ultima_respuesta_lead or "",
            contexto.saludo,
        )
        decision = self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="programar_seguimiento",
            descripcion=f"Seguimiento {lead.contador_seguimientos} a {lead_id} por {canal.value}",
            payload={"lead_id": lead_id, "canal": canal.value, "respuesta": generada.texto},
        )
        lead.estrategia_aplicada = estrategia.estrategia_id if estrategia else None
        self.almacen.guardar_lead(lead)
        mensaje_id = self.almacen.siguiente_folio("mensaje", "MSG", 6)
        self.almacen.guardar_mensaje(
            Mensaje(
                mensaje_id=mensaje_id,
                cliente_id=cliente_id,
                lead_id=lead_id,
                canal=canal,
                direccion="saliente",
                texto=generada.texto,
                etapa=lead.etapa,
                decision_id=decision.decision_id,
            )
        )
        return ResultadoAtencion(
            lead=lead,
            decision=decision,
            respuesta=generada.texto,
            enviada=True,
            etapa_anterior=etapa_anterior,
            estrategia_id=lead.estrategia_aplicada,
            mensaje_id=mensaje_id,
            motor=generada.motor,
            error_motor=generada.error,
        )

    def cerrar_lead(self, cliente_id: str, lead_id: str, resultado: str = "ganado") -> Lead:
        lead = self.almacen.obtener_lead(cliente_id, lead_id)
        if lead is None:
            raise ValueError(f"Lead inexistente para {cliente_id}: {lead_id}")
        lead.estatus = Estatus.CERRADO if resultado == "ganado" else Estatus.PERDIDO
        self.almacen.guardar_lead(lead)
        decision = self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="registrar_lead",
            descripcion=f"Lead {lead_id} cerrado como {lead.estatus.value}",
            payload={"lead_id": lead_id},
        )
        segundos = (
            int((ahora() - lead.fecha_entrada).total_seconds()) if lead.fecha_entrada else None
        )
        self._registrar_resultado(
            lead,
            "cerrar venta",
            "cierre registrado por el operador",
            resultado,
            decision.decision_id,
            segundos=segundos,
            impacto="venta generada" if resultado == "ganado" else "sin venta",
        )
        return lead

    # --- reportes ----------------------------------------------------------
    def reporte(self, cliente_id: str) -> dict:
        """Nivel 0, solo lectura (tabla 4.1)."""
        leads = self.almacen.listar_leads(cliente_id)
        por_etapa = {etapa.value: 0 for etapa in ORDEN_ETAPAS}
        por_canal = {canal.value: 0 for canal in Canal}
        for lead in leads:
            por_etapa[lead.etapa.value] += 1
            por_canal[lead.canal_origen.value] += 1
        escalamientos = self.almacen.listar_escalamientos(cliente_id)
        return {
            "cliente_id": cliente_id,
            "total_leads": len(leads),
            "por_etapa": por_etapa,
            "por_canal_origen": por_canal,
            "cerrados": sum(1 for lead in leads if lead.estatus == Estatus.CERRADO),
            "perdidos": sum(1 for lead in leads if lead.estatus == Estatus.PERDIDO),
            "pendientes_de_aprobacion": len(
                self.almacen.listar_decisiones(cliente_id=cliente_id, estado="pendiente")
            ),
            "llamadas": len(self.almacen.listar_llamadas(cliente_id)),
            "escalamientos": len(escalamientos),
            "escalamientos_pendientes": sum(
                1 for e in escalamientos if e.estado == EstadoEscalamiento.PENDIENTE
            ),
        }

    # --- internos ------------------------------------------------------------
    def _registrar_resultado(
        self,
        lead: Lead,
        objetivo: str,
        recomendacion: str,
        resultado: str,
        decision_id: str | None = None,
        estrategia_id: str | None = None,
        segundos: int | None = None,
        impacto: str = "",
    ) -> RegistroResultado:
        return self.almacen.guardar_resultado(
            RegistroResultado(
                resultado_id=self.almacen.siguiente_folio(f"resultado:{lead.cliente_id}", "RES", 6),
                cliente_id=lead.cliente_id,
                lead_id=lead.lead_id,
                objetivo=objetivo,
                recomendacion=recomendacion,
                accion=f"etapa={lead.etapa.value}",
                resultado=resultado,
                impacto=impacto,
                estrategia_id=estrategia_id or lead.estrategia_aplicada,
                decision_id=decision_id,
                segundos_hasta_resultado=segundos,
            )
        )

    def _verificar_canal_habilitado(self, cliente: Cliente, canal: Canal) -> None:
        if canal in cliente.canales_habilitados:
            return
        raise CanalNoHabilitadoError(
            f"{cliente.cliente_id} no tiene habilitado el canal {canal.value} "
            f"con el plan {cliente.plan.value} (Seccion 5.7)"
        )

    def _lead_para(self, evento: ContactoEntrante) -> Lead:
        lead = self.almacen.buscar_lead_por_contacto(evento.cliente_id, evento.contacto)
        if lead is not None:
            if evento.nombre and not lead.nombre:
                lead.nombre = evento.nombre
            return lead
        return self.almacen.guardar_lead(
            Lead(
                lead_id=self.almacen.siguiente_folio(f"lead:{evento.cliente_id}", "LEAD"),
                cliente_id=evento.cliente_id,
                nombre=evento.nombre,
                contacto=evento.contacto,
                canal_origen=evento.canal,
                fuente=evento.fuente,
                mensaje_inicial=evento.texto,
                nivel_decision=NivelDecision.REVERSIBLE,
            )
        )

    def _historial_texto(self, lead: Lead, limite: int = 10) -> str:
        mensajes = self.almacen.historial(lead.cliente_id, lead.lead_id, limite)
        return "\n".join(
            f"{'Lead' if m.direccion == 'entrante' else 'HERMES'}: {m.texto}" for m in mensajes
        )

    @staticmethod
    def _es_perdida(texto: str) -> bool:
        minusculas = texto.lower()
        return any(senal in minusculas for senal in SENALES_PERDIDA)

    @staticmethod
    def _siguiente_etapa(lead: Lead, texto: str) -> Etapa:
        if lead.etapa == Etapa.SEGUIMIENTO:
            return Etapa.PROSPECCION if texto.strip() else Etapa.SEGUIMIENTO
        minusculas = texto.lower()
        senales = SENALES_AVANCE.get(lead.etapa, ())
        if any(senal in minusculas for senal in senales):
            return ORDEN_ETAPAS[min(ORDEN_ETAPAS.index(lead.etapa) + 1, len(ORDEN_ETAPAS) - 1)]
        return lead.etapa


__all__ = [
    "CanalNoHabilitadoError",
    "ClienteNoRegistradoError",
    "ContactoEntrante",
    "ContextoTemporal",
    "Hermes",
    "MAX_SEGUIMIENTOS",
    "ResultadoAtencion",
]
