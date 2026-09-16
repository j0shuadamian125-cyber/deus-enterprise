"""Motor canal-agnostico de HERMES: pipeline de 4 etapas (Seccion 5.5).

Un mismo lead avanza Apertura -> Prospeccion -> Cierre -> Seguimiento sin
importar por que canal entro ni por cual esta siendo atendido hoy. Lo unico
que cambia entre canales es la capa de entrada/salida (Seccion 5.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .governance import Gobernanza
from .llm import GeneradorRespuestas
from .models import (
    Canal,
    Cliente,
    Decision,
    Estatus,
    Etapa,
    Lead,
    Llamada,
    Mensaje,
    NivelDecision,
    NivelServicioLlamada,
    ResultadoLlamada,
    ahora,
)
from .prompts import construir_prompt, guion_llamada_asistida
from .storage import Almacen

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


class ClienteNoRegistradoError(RuntimeError):
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

        lead = self._lead_para(evento)
        etapa_anterior = lead.etapa

        self.almacen.guardar_mensaje(
            Mensaje(
                mensaje_id=self.almacen.siguiente_folio("mensaje", "MSG", 6),
                cliente_id=lead.cliente_id,
                lead_id=lead.lead_id,
                canal=evento.canal,
                direccion="entrante",
                texto=evento.texto,
                etapa=lead.etapa,
            )
        )
        lead.ultima_respuesta_lead = evento.texto
        lead.ultimo_contacto = ahora()
        lead.estatus = Estatus.EN_PROCESO if lead.estatus == Estatus.NUEVO else lead.estatus

        if self._es_perdida(evento.texto):
            lead.estatus = Estatus.PERDIDO
            self.almacen.guardar_lead(lead)
            decision = self.gobernanza.registrar(
                cliente_id=lead.cliente_id,
                accion="registrar_lead",
                descripcion=f"Lead {lead.lead_id} marcado como Perdido por respuesta del lead",
                payload={"lead_id": lead.lead_id, "canal": evento.canal.value},
            )
            return ResultadoAtencion(
                lead=lead,
                decision=decision,
                respuesta=None,
                enviada=False,
                etapa_anterior=etapa_anterior,
                motivo="lead_no_interesado",
            )

        lead.etapa = self._siguiente_etapa(lead, evento.texto)
        if lead.etapa == Etapa.PROSPECCION and not lead.producto_interes:
            lead.producto_interes = evento.texto[:120]

        historial = self._historial_texto(lead)
        prompt = construir_prompt(lead, evento.canal, cliente.nombre_negocio, historial)
        generada = self.generador.responder(
            lead, evento.canal, cliente.nombre_negocio, prompt, evento.texto
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
            },
        )
        lead.nivel_decision = decision.nivel
        self.almacen.guardar_lead(lead)

        enviada = self.gobernanza.puede_ejecutar(decision)
        if enviada:
            self.almacen.guardar_mensaje(
                Mensaje(
                    mensaje_id=self.almacen.siguiente_folio("mensaje", "MSG", 6),
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

        return ResultadoAtencion(
            lead=lead,
            decision=decision,
            respuesta=generada.texto,
            enviada=enviada,
            etapa_anterior=etapa_anterior,
            motivo="" if enviada else "pendiente_de_aprobacion",
        )

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
        prompt = guion_llamada_asistida(lead, cliente.nombre_negocio, self._historial_texto(lead))
        generada = self.generador.responder(
            lead, Canal.LLAMADA, cliente.nombre_negocio, prompt, lead.ultima_respuesta_lead or ""
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

        if lead.contador_seguimientos > MAX_SEGUIMIENTOS:
            lead.estatus = Estatus.PERDIDO
            self.almacen.guardar_lead(lead)
            decision = self.gobernanza.registrar(
                cliente_id=cliente_id,
                accion="programar_seguimiento",
                descripcion=f"Lead {lead_id} cerrado tras {MAX_SEGUIMIENTOS} seguimientos sin respuesta",
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

        prompt = construir_prompt(lead, canal, cliente.nombre_negocio, self._historial_texto(lead))
        generada = self.generador.responder(
            lead, canal, cliente.nombre_negocio, prompt, lead.ultima_respuesta_lead or ""
        )
        decision = self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="programar_seguimiento",
            descripcion=f"Seguimiento {lead.contador_seguimientos} a {lead_id} por {canal.value}",
            payload={"lead_id": lead_id, "canal": canal.value, "respuesta": generada.texto},
        )
        self.almacen.guardar_lead(lead)
        self.almacen.guardar_mensaje(
            Mensaje(
                mensaje_id=self.almacen.siguiente_folio("mensaje", "MSG", 6),
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
        )

    def cerrar_lead(self, cliente_id: str, lead_id: str, resultado: str = "ganado") -> Lead:
        lead = self.almacen.obtener_lead(cliente_id, lead_id)
        if lead is None:
            raise ValueError(f"Lead inexistente para {cliente_id}: {lead_id}")
        lead.estatus = Estatus.CERRADO if resultado == "ganado" else Estatus.PERDIDO
        self.almacen.guardar_lead(lead)
        self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="registrar_lead",
            descripcion=f"Lead {lead_id} cerrado como {lead.estatus.value}",
            payload={"lead_id": lead_id},
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
        }

    # --- internos ------------------------------------------------------------
    def _verificar_canal_habilitado(self, cliente: Cliente, canal: Canal) -> None:
        if canal == Canal.CORREO and not cliente.atencion_correo:
            raise ClienteNoRegistradoError(
                f"{cliente.cliente_id} no tiene habilitada la atencion por correo (Seccion 5.7)"
            )
        if canal == Canal.LLAMADA and not cliente.atencion_llamada:
            raise ClienteNoRegistradoError(
                f"{cliente.cliente_id} no tiene habilitada la atencion por llamada (Seccion 5.7)"
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
