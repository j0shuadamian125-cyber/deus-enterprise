"""API HTTP de HERMES.

Cada canal entra por su propio webhook (equivalente a los tres disparadores de
Make.com de la Seccion 5.5) y todos escriben al mismo CRM. El panel expone
lectura de leads, llamadas, bitacora y aprobaciones para el chat de DEUS.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Configuracion, Servicio, construir_servicio
from .governance import GobernanzaError
from .models import (
    Canal,
    CanalConfirmacion,
    Estatus,
    Etapa,
    Mensaje,
    NivelServicioLlamada,
)
from .onboarding import RespuestasOnboarding, alta_cliente
from .pipeline import ClienteNoRegistradoError, ResultadoAtencion
from .storage import AislamientoError


class RespuestaAtencion(BaseModel):
    lead_id: str
    cliente_id: str
    canal: Canal
    etapa_anterior: Etapa
    etapa: Etapa
    estatus: Estatus
    decision_id: str
    nivel: int
    estado_decision: str
    respuesta: str | None
    enviada: bool
    motivo: str = ""


class AprobacionPeticion(BaseModel):
    aprobado_por: str
    canal_confirmacion: CanalConfirmacion = CanalConfirmacion.CHAT


class RechazoPeticion(BaseModel):
    rechazado_por: str
    motivo: str = ""


def _a_respuesta(resultado: ResultadoAtencion, canal: Canal) -> RespuestaAtencion:
    return RespuestaAtencion(
        lead_id=resultado.lead.lead_id,
        cliente_id=resultado.lead.cliente_id,
        canal=canal,
        etapa_anterior=resultado.etapa_anterior,
        etapa=resultado.lead.etapa,
        estatus=resultado.lead.estatus,
        decision_id=resultado.decision.decision_id,
        nivel=int(resultado.decision.nivel),
        estado_decision=resultado.decision.estado.value,
        respuesta=resultado.respuesta,
        enviada=resultado.enviada,
        motivo=resultado.motivo,
    )


def crear_app(servicio: Servicio | None = None, config: Configuracion | None = None) -> FastAPI:
    servicio = servicio or construir_servicio(config)
    app = FastAPI(
        title="HERMES",
        version="4.0",
        description="Modulo de ventas y atencion multicanal de DEUS (Seccion 5 de la spec v4.0)",
    )
    app.state.servicio = servicio

    def autorizar(x_panel_token: str | None = Header(default=None)) -> None:
        esperado = servicio.config.token_panel
        if esperado and x_panel_token != esperado:
            raise HTTPException(status_code=401, detail="Token de panel invalido")

    @app.exception_handler(ClienteNoRegistradoError)
    async def _cliente_no_registrado(_: Request, exc: ClienteNoRegistradoError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(AislamientoError)
    async def _aislamiento(_: Request, exc: AislamientoError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # --- salud ---------------------------------------------------------
    @app.get("/health")
    def salud() -> dict:
        return {"modulo": "HERMES", "estado": "operativo", "version": "4.0"}

    # --- onboarding ----------------------------------------------------
    @app.post("/v1/onboarding", dependencies=[Depends(autorizar)])
    def onboarding(respuestas: RespuestasOnboarding) -> dict:
        try:
            resultado = alta_cliente(servicio.almacen, respuestas)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return resultado.model_dump(mode="json")

    @app.get("/v1/clientes", dependencies=[Depends(autorizar)])
    def clientes() -> list[dict]:
        """Vista global: exclusiva del operador de DEUS (Seccion 1.4)."""
        return [cliente.model_dump(mode="json") for cliente in servicio.almacen.listar_clientes()]

    # --- webhooks por canal ----------------------------------------------
    @app.post("/v1/webhooks/whatsapp/{cliente_id}", response_model=RespuestaAtencion)
    async def webhook_whatsapp(cliente_id: str, peticion: Request) -> RespuestaAtencion:
        carga = await _carga(peticion)
        evento = servicio.whatsapp.normalizar(cliente_id, carga)
        resultado = servicio.hermes.atender(evento)
        _entregar(servicio, resultado, Canal.WHATSAPP)
        return _a_respuesta(resultado, Canal.WHATSAPP)

    @app.post("/v1/webhooks/correo/{cliente_id}", response_model=RespuestaAtencion)
    async def webhook_correo(cliente_id: str, peticion: Request) -> RespuestaAtencion:
        carga = await _carga(peticion)
        evento = servicio.correo.normalizar(cliente_id, carga)
        resultado = servicio.hermes.atender(evento)
        _entregar(servicio, resultado, Canal.CORREO)
        return _a_respuesta(resultado, Canal.CORREO)

    @app.post("/v1/webhooks/llamada/{cliente_id}")
    async def webhook_llamada(cliente_id: str, peticion: Request) -> dict:
        carga = await _carga(peticion)
        evento = servicio.llamada.normalizar(cliente_id, carga)
        llamada, atencion = servicio.hermes.registrar_llamada(
            cliente_id=cliente_id,
            contacto=evento.contacto,
            nivel_servicio=servicio.llamada.nivel_servicio(carga),
            transcripcion=evento.texto,
            duracion_segundos=evento.metadatos.get("duracion_segundos", 0),
            url_grabacion=evento.metadatos.get("url_grabacion"),
            resultado=servicio.llamada.resultado(carga),
            nombre=evento.nombre,
            fuente=evento.fuente,
        )
        return {
            "llamada": llamada.model_dump(mode="json"),
            "atencion": _a_respuesta(atencion, Canal.LLAMADA).model_dump(mode="json"),
        }

    # --- CRM -------------------------------------------------------------
    @app.get("/v1/clientes/{cliente_id}/leads", dependencies=[Depends(autorizar)])
    def leads(
        cliente_id: str,
        etapa: Etapa | None = None,
        estatus: Estatus | None = None,
        canal_origen: Canal | None = None,
    ) -> list[dict]:
        return [
            lead.model_dump(mode="json")
            for lead in servicio.almacen.listar_leads(cliente_id, etapa, estatus, canal_origen)
        ]

    @app.get("/v1/clientes/{cliente_id}/leads/{lead_id}", dependencies=[Depends(autorizar)])
    def lead(cliente_id: str, lead_id: str) -> dict:
        registro = servicio.almacen.obtener_lead(cliente_id, lead_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="Lead inexistente para ese cliente_id")
        historial = servicio.almacen.historial(cliente_id, lead_id)
        return {
            "lead": registro.model_dump(mode="json"),
            "historial": [m.model_dump(mode="json") for m in historial],
        }

    @app.get("/v1/clientes/{cliente_id}/hoja-leads", dependencies=[Depends(autorizar)])
    def hoja_leads(cliente_id: str) -> list[dict]:
        """Exporta el CRM con las columnas de la hoja Leads_[cliente_id] (5.2.1)."""
        return servicio.almacen.exportar_hoja_leads(cliente_id)

    @app.post(
        "/v1/clientes/{cliente_id}/leads/{lead_id}/seguimiento",
        response_model=RespuestaAtencion,
        dependencies=[Depends(autorizar)],
    )
    def seguimiento(cliente_id: str, lead_id: str) -> RespuestaAtencion:
        try:
            resultado = servicio.hermes.programar_seguimiento(cliente_id, lead_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        canal = resultado.lead.canal_preferido_lead or resultado.lead.canal_origen
        _entregar(servicio, resultado, canal)
        return _a_respuesta(resultado, canal)

    @app.post("/v1/clientes/{cliente_id}/leads/{lead_id}/guion-llamada", dependencies=[Depends(autorizar)])
    def guion_llamada(cliente_id: str, lead_id: str) -> dict:
        try:
            guion = servicio.hermes.preparar_guion_asistido(cliente_id, lead_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"lead_id": lead_id, "nivel_servicio": NivelServicioLlamada.ASISTIDA.value, "guion": guion}

    @app.get("/v1/clientes/{cliente_id}/llamadas", dependencies=[Depends(autorizar)])
    def llamadas(cliente_id: str, lead_id: str | None = None) -> list[dict]:
        return [
            llamada.model_dump(mode="json")
            for llamada in servicio.almacen.listar_llamadas(cliente_id, lead_id)
        ]

    @app.get("/v1/clientes/{cliente_id}/reporte", dependencies=[Depends(autorizar)])
    def reporte(cliente_id: str) -> dict:
        return servicio.hermes.reporte(cliente_id)

    # --- gobernanza --------------------------------------------------------
    @app.get("/v1/decisiones", dependencies=[Depends(autorizar)])
    def decisiones(cliente_id: str | None = None, estado: str | None = None) -> list[dict]:
        return [
            decision.model_dump(mode="json")
            for decision in servicio.almacen.listar_decisiones(cliente_id=cliente_id, estado=estado)
        ]

    @app.post("/v1/decisiones/{decision_id}/aprobar", dependencies=[Depends(autorizar)])
    def aprobar(decision_id: str, peticion: AprobacionPeticion) -> dict:
        try:
            decision = servicio.gobernanza.aprobar(
                decision_id, peticion.aprobado_por, peticion.canal_confirmacion
            )
        except GobernanzaError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _ejecutar_pendiente(servicio, decision_id)
        return decision.model_dump(mode="json")

    @app.post("/v1/decisiones/{decision_id}/rechazar", dependencies=[Depends(autorizar)])
    def rechazar(decision_id: str, peticion: RechazoPeticion) -> dict:
        try:
            decision = servicio.gobernanza.rechazar(
                decision_id, peticion.rechazado_por, peticion.motivo
            )
        except GobernanzaError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return decision.model_dump(mode="json")

    @app.post("/v1/decisiones/caducar-nivel-4", dependencies=[Depends(autorizar)])
    def caducar() -> list[dict]:
        return [d.model_dump(mode="json") for d in servicio.gobernanza.caducar_pendientes_nivel_4()]

    @app.get("/v1/memoria", dependencies=[Depends(autorizar)])
    def memoria(cliente_id: str | None = None, limite: int = 60) -> list[dict]:
        return [
            entrada.model_dump(mode="json")
            for entrada in servicio.almacen.listar_memoria(cliente_id, limite)
        ]

    return app


async def _carga(peticion: Request) -> dict[str, Any]:
    """Acepta JSON o formulario (Twilio envia form-urlencoded)."""
    tipo = peticion.headers.get("content-type", "")
    if tipo.startswith("application/json"):
        return await peticion.json()
    formulario = await peticion.form()
    return {clave: str(valor) for clave, valor in formulario.items()}


def _entregar(servicio: Servicio, resultado: ResultadoAtencion, canal: Canal) -> None:
    """Envio real por el canal correspondiente; en Nivel 2+ no hay nada que enviar aun."""
    if not resultado.enviada or not resultado.respuesta or not servicio.config.envio_real:
        return
    try:
        if canal == Canal.WHATSAPP and servicio.whatsapp.configurado:
            servicio.whatsapp.enviar(resultado.lead.contacto, resultado.respuesta)
        elif canal == Canal.CORREO and servicio.correo.configurado:
            servicio.correo.enviar(resultado.lead.contacto, resultado.respuesta)
    except Exception as exc:  # el fallo de entrega se reporta a NEXUS, no tumba el webhook
        servicio.hermes.gobernanza.registrar_resultado(
            resultado.decision.decision_id, f"fallo_envio: {exc}"
        )
        if servicio.nexus is not None:
            try:
                servicio.nexus.reportar_error(
                    mensaje=f"Fallo de envio por {canal.value}: {exc}",
                    correlation_key=resultado.decision.decision_id,
                )
            except Exception:
                pass


def _ejecutar_pendiente(servicio: Servicio, decision_id: str) -> None:
    """Tras aprobar en el chat, envia la respuesta que quedo retenida."""
    decision = servicio.almacen.obtener_decision(decision_id)
    if decision is None or not servicio.gobernanza.puede_ejecutar(decision):
        return
    respuesta = decision.payload.get("respuesta")
    canal_crudo = decision.payload.get("canal")
    lead_id = decision.payload.get("lead_id")
    if not (respuesta and canal_crudo and lead_id):
        return
    canal = Canal(canal_crudo)
    lead = servicio.almacen.obtener_lead(decision.cliente_id, lead_id)
    if lead is None:
        return
    servicio.almacen.guardar_mensaje(
        Mensaje(
            mensaje_id=servicio.almacen.siguiente_folio("mensaje", "MSG", 6),
            cliente_id=decision.cliente_id,
            lead_id=lead_id,
            canal=canal,
            direccion="saliente",
            texto=respuesta,
            etapa=lead.etapa,
            decision_id=decision_id,
        )
    )
    servicio.gobernanza.registrar_resultado(decision_id, "respuesta_enviada_tras_aprobacion")
    if not servicio.config.envio_real:
        return
    try:
        if canal == Canal.WHATSAPP and servicio.whatsapp.configurado:
            servicio.whatsapp.enviar(lead.contacto, respuesta)
        elif canal == Canal.CORREO and servicio.correo.configurado:
            servicio.correo.enviar(lead.contacto, respuesta)
    except Exception as exc:
        servicio.gobernanza.registrar_resultado(decision_id, f"fallo_envio: {exc}")
