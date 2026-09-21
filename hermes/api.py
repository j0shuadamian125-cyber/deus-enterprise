"""API HTTP de HERMES.

Cada canal entra por su propio webhook y todos escriben al mismo CRM. El panel
expone lectura de leads, llamadas, bitacora, escalamientos, estrategias,
patrones y aprobaciones.

Autenticacion:
- panel y operaciones: cabecera `X-Panel-Token` (HERMES_PANEL_TOKEN).
- webhook de WhatsApp: firma `X-Twilio-Signature` (HERMES_URL_PUBLICA + Auth
  Token de Twilio); si la validacion esta desactivada se acepta el token de
  webhook como alternativa.
- webhooks de correo y voz: cabecera `X-Webhook-Token` (HERMES_WEBHOOK_TOKEN),
  porque esos proveedores no firman como Twilio.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .channels.whatsapp import FirmaInvalidaError
from .config import Configuracion, Servicio, construir_servicio
from .governance import GobernanzaError
from .learning import Aprendizaje
from .models import (
    Canal,
    CanalConfirmacion,
    EstadoEntrega,
    Estatus,
    Etapa,
    Mensaje,
    NivelServicioLlamada,
)
from .onboarding import RespuestasOnboarding, alta_cliente
from .pipeline import (
    CanalNoHabilitadoError,
    ClienteNoRegistradoError,
    ContactoEntrante,
    ResultadoAtencion,
)
from .reportes import generar_reporte_pdf
from .sandbox import ESCENARIOS, SandboxError, ejecutar_escenario, ejecutar_todos
from .storage import AislamientoError
from .strategies import EstrategiaError


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
    requiere_humano: bool = False
    escalamiento_id: str | None = None
    motor: str = ""
    error_motor: str | None = None


class AprobacionPeticion(BaseModel):
    aprobado_por: str
    canal_confirmacion: CanalConfirmacion = CanalConfirmacion.CHAT


class RechazoPeticion(BaseModel):
    rechazado_por: str
    motivo: str = ""


class IntervencionPeticion(BaseModel):
    atendido_por: str
    accion: str
    respuesta: str | None = None
    resultado: str | None = None


class EstrategiaPeticion(BaseModel):
    nombre: str
    objetivo: str
    etapa: Etapa
    plantilla: str
    condiciones: list[str] = Field(default_factory=list)
    evidencia: str = ""


class ActivacionPeticion(BaseModel):
    decision_id: str


class SandboxPeticion(BaseModel):
    escenario: str
    canal: Canal = Canal.WHATSAPP
    contacto: str | None = None


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
        requiere_humano=resultado.lead.requiere_humano,
        escalamiento_id=(
            resultado.escalamiento.escalamiento_id if resultado.escalamiento else None
        ),
        motor=resultado.motor,
        error_motor=resultado.error_motor,
    )


def crear_app(servicio: Servicio | None = None, config: Configuracion | None = None) -> FastAPI:
    servicio = servicio or construir_servicio(config)
    app = FastAPI(
        title="HERMES",
        version="4.0",
        description="Modulo de ventas y atencion multicanal de DEUS",
    )
    app.state.servicio = servicio
    if servicio.config.origenes_panel:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(servicio.config.origenes_panel),
            allow_methods=["GET", "POST"],
            allow_headers=["X-Panel-Token", "Content-Type"],
        )

    def autorizar(x_panel_token: str | None = Header(default=None)) -> None:
        esperado = servicio.config.token_panel
        if esperado and x_panel_token != esperado:
            raise HTTPException(status_code=401, detail="Token de panel invalido")

    def autorizar_webhook(x_webhook_token: str | None = Header(default=None)) -> None:
        """Correo y voz: los proveedores no firman, se exige token compartido."""
        esperado = servicio.config.token_webhook
        if esperado and x_webhook_token != esperado:
            raise HTTPException(status_code=401, detail="Token de webhook invalido")

    def exigir_cliente(cliente_id: str) -> None:
        """404 explicito: un tenant inexistente no debe verse como uno vacio."""
        if servicio.almacen.obtener_cliente(cliente_id) is None:
            raise HTTPException(status_code=404, detail="Cliente inexistente")

    @app.exception_handler(ClienteNoRegistradoError)
    async def _cliente_no_registrado(_: Request, exc: ClienteNoRegistradoError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(CanalNoHabilitadoError)
    async def _canal_no_habilitado(_: Request, exc: CanalNoHabilitadoError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AislamientoError)
    async def _aislamiento(_: Request, exc: AislamientoError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FirmaInvalidaError)
    async def _firma(_: Request, exc: FirmaInvalidaError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    # --- salud ---------------------------------------------------------
    @app.get("/health")
    def salud() -> dict:
        return {"modulo": "HERMES", "estado": "operativo", "version": "4.0"}

    @app.get("/v1/estado", dependencies=[Depends(autorizar)])
    def estado() -> dict:
        """Que integraciones estan realmente configuradas en este despliegue."""
        return {
            "llm_real": servicio.hermes.generador.motor is not None,
            "whatsapp_configurado": servicio.whatsapp.configurado,
            "correo_configurado": servicio.correo.configurado,
            "correo_recepcion_configurada": servicio.correo.recepcion_configurada,
            "voz_configurada": servicio.llamada.configurado,
            "envio_real": servicio.config.envio_real,
            "validacion_firma_whatsapp": servicio.whatsapp.validar_firmas,
            "token_webhook_configurado": bool(servicio.config.token_webhook),
        }

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
        """Vista global: exclusiva del operador de DEUS."""
        return [cliente.model_dump(mode="json") for cliente in servicio.almacen.listar_clientes()]

    @app.get("/v1/clientes/{cliente_id}", dependencies=[Depends(autorizar)])
    def cliente(cliente_id: str) -> dict:
        registro = servicio.almacen.obtener_cliente(cliente_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="Cliente inexistente")
        return registro.model_dump(mode="json")

    # --- webhooks por canal ----------------------------------------------
    @app.post("/v1/webhooks/whatsapp/{cliente_id}", response_model=RespuestaAtencion)
    async def webhook_whatsapp(
        cliente_id: str,
        peticion: Request,
        x_twilio_signature: str | None = Header(default=None),
        x_webhook_token: str | None = Header(default=None),
    ) -> RespuestaAtencion:
        carga = await _carga(peticion)
        if servicio.whatsapp.validar_firmas:
            servicio.whatsapp.verificar_firma(
                _url_canonica(servicio, peticion), carga, x_twilio_signature
            )
        elif servicio.config.token_webhook and x_webhook_token != servicio.config.token_webhook:
            raise HTTPException(status_code=401, detail="Token de webhook invalido")

        evento_id = servicio.whatsapp.identificador_evento(carga)
        repetido = _respuesta_repetida(servicio, cliente_id, Canal.WHATSAPP, evento_id)
        if repetido is not None:
            return repetido

        evento = _normalizar(servicio.whatsapp, cliente_id, carga)
        resultado = servicio.hermes.atender(evento)
        _entregar(servicio, resultado, Canal.WHATSAPP)
        respuesta = _a_respuesta(resultado, Canal.WHATSAPP)
        _marcar_evento(servicio, cliente_id, Canal.WHATSAPP, evento_id, respuesta)
        return respuesta

    @app.post(
        "/v1/webhooks/correo/{cliente_id}",
        response_model=RespuestaAtencion,
        dependencies=[Depends(autorizar_webhook)],
    )
    async def webhook_correo(cliente_id: str, peticion: Request) -> RespuestaAtencion:
        carga = await _carga(peticion)
        evento_id = servicio.correo.identificador_evento(carga)
        repetido = _respuesta_repetida(servicio, cliente_id, Canal.CORREO, evento_id)
        if repetido is not None:
            return repetido

        evento = _normalizar(servicio.correo, cliente_id, carga)
        resultado = servicio.hermes.atender(evento)
        _entregar(servicio, resultado, Canal.CORREO)
        respuesta = _a_respuesta(resultado, Canal.CORREO)
        _marcar_evento(servicio, cliente_id, Canal.CORREO, evento_id, respuesta)
        return respuesta

    @app.post("/v1/webhooks/llamada/{cliente_id}", dependencies=[Depends(autorizar_webhook)])
    async def webhook_llamada(
        cliente_id: str,
        peticion: Request,
        x_twilio_signature: str | None = Header(default=None),
    ) -> dict:
        carga = await _carga(peticion)
        servicio.llamada.verificar_firma(
            _url_canonica(servicio, peticion), carga, x_twilio_signature
        )
        evento_id = servicio.llamada.identificador_evento(carga)
        if evento_id:
            previa = servicio.almacen.evento_ya_procesado(cliente_id, Canal.LLAMADA.value, evento_id)
            if previa is not None:
                return {"repetido": True, "atencion": _json(previa)}

        evento = _normalizar(servicio.llamada, cliente_id, carga)
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
            proveedor=evento.metadatos.get("proveedor"),
            call_sid=evento_id,
        )
        cuerpo = {
            "llamada": llamada.model_dump(mode="json"),
            "atencion": _a_respuesta(atencion, Canal.LLAMADA).model_dump(mode="json"),
        }
        if evento_id:
            servicio.almacen.marcar_evento(
                cliente_id, Canal.LLAMADA.value, evento_id, _texto(cuerpo["atencion"])
            )
        return cuerpo

    # --- CRM -------------------------------------------------------------
    @app.get(
        "/v1/clientes/{cliente_id}/leads",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
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

    @app.get(
        "/v1/clientes/{cliente_id}/leads/{lead_id}",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def lead(cliente_id: str, lead_id: str) -> dict:
        registro = servicio.almacen.obtener_lead(cliente_id, lead_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="Lead inexistente para ese cliente_id")
        historial = servicio.almacen.historial(cliente_id, lead_id)
        return {
            "lead": registro.model_dump(mode="json"),
            "historial": [m.model_dump(mode="json") for m in historial],
        }

    @app.get(
        "/v1/clientes/{cliente_id}/hoja-leads",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def hoja_leads(cliente_id: str) -> list[dict]:
        """Exporta el CRM con las columnas de la hoja Leads_[cliente_id]."""
        return servicio.almacen.exportar_hoja_leads(cliente_id)

    @app.post(
        "/v1/clientes/{cliente_id}/leads/{lead_id}/seguimiento",
        response_model=RespuestaAtencion,
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def seguimiento(cliente_id: str, lead_id: str) -> RespuestaAtencion:
        try:
            resultado = servicio.hermes.programar_seguimiento(cliente_id, lead_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        canal = resultado.lead.canal_preferido_lead or resultado.lead.canal_origen
        _entregar(servicio, resultado, canal)
        return _a_respuesta(resultado, canal)

    @app.post(
        "/v1/clientes/{cliente_id}/leads/{lead_id}/cierre",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def cierre(cliente_id: str, lead_id: str, resultado: str = "ganado") -> dict:
        try:
            lead = servicio.hermes.cerrar_lead(cliente_id, lead_id, resultado)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return lead.model_dump(mode="json")

    @app.post(
        "/v1/clientes/{cliente_id}/leads/{lead_id}/guion-llamada",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def guion_llamada(cliente_id: str, lead_id: str) -> dict:
        try:
            guion = servicio.hermes.preparar_guion_asistido(cliente_id, lead_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "lead_id": lead_id,
            "nivel_servicio": NivelServicioLlamada.ASISTIDA.value,
            "guion": guion,
        }

    @app.get(
        "/v1/clientes/{cliente_id}/llamadas",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def llamadas(cliente_id: str, lead_id: str | None = None) -> list[dict]:
        return [
            llamada.model_dump(mode="json")
            for llamada in servicio.almacen.listar_llamadas(cliente_id, lead_id)
        ]

    @app.get(
        "/v1/clientes/{cliente_id}/reporte",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def reporte(cliente_id: str) -> dict:
        return servicio.hermes.reporte(cliente_id)

    @app.get(
        "/v1/clientes/{cliente_id}/reporte.pdf",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def reporte_pdf(cliente_id: str) -> Response:
        cliente = servicio.almacen.obtener_cliente(cliente_id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no registrado")
        pdf = generar_reporte_pdf(
            servicio.almacen, cliente, servicio.hermes.reporte(cliente_id)
        )
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="hermes-{cliente_id}.pdf"',
            },
        )

    @app.get(
        "/v1/clientes/{cliente_id}/resultados",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def resultados(cliente_id: str, lead_id: str | None = None) -> list[dict]:
        return [
            registro.model_dump(mode="json")
            for registro in servicio.almacen.listar_resultados(cliente_id, lead_id)
        ]

    # --- intervencion humana ------------------------------------------------
    @app.get(
        "/v1/clientes/{cliente_id}/escalamientos",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def escalamientos(
        cliente_id: str, estado: str | None = None, lead_id: str | None = None
    ) -> list[dict]:
        return [
            escalamiento.model_dump(mode="json")
            for escalamiento in servicio.almacen.listar_escalamientos(cliente_id, estado, lead_id)
        ]

    @app.get(
        "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def escalamiento(cliente_id: str, escalamiento_id: str) -> dict:
        registro = servicio.almacen.obtener_escalamiento(cliente_id, escalamiento_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="Escalamiento inexistente")
        historial = servicio.almacen.historial(cliente_id, registro.lead_id)
        lead = servicio.almacen.obtener_lead(cliente_id, registro.lead_id)
        return {
            "escalamiento": registro.model_dump(mode="json"),
            "lead": lead.model_dump(mode="json") if lead else None,
            "historial": [m.model_dump(mode="json") for m in historial],
        }

    @app.post(
        "/v1/clientes/{cliente_id}/escalamientos/{escalamiento_id}/intervenir",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def intervenir(cliente_id: str, escalamiento_id: str, peticion: IntervencionPeticion) -> dict:
        try:
            registro, texto = servicio.hermes.resolver_escalamiento(
                cliente_id=cliente_id,
                escalamiento_id=escalamiento_id,
                atendido_por=peticion.atendido_por,
                accion=peticion.accion,
                respuesta=peticion.respuesta,
                resultado=peticion.resultado,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if texto:
            _entregar_texto(servicio, cliente_id, registro.lead_id, registro.canal, texto)
        return {"escalamiento": registro.model_dump(mode="json"), "respuesta_enviada": texto}

    # --- estrategias ---------------------------------------------------------
    @app.get(
        "/v1/clientes/{cliente_id}/estrategias",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def estrategias(
        cliente_id: str, etapa: Etapa | None = None, estado: str | None = None
    ) -> list[dict]:
        medicion = servicio.hermes.estrategias.medir(cliente_id)
        salida = []
        for estrategia in servicio.almacen.listar_estrategias(cliente_id, etapa, estado):
            datos = estrategia.model_dump(mode="json")
            datos["medicion"] = medicion.get(estrategia.estrategia_id, {"usos": 0, "exitos": 0})
            salida.append(datos)
        return salida

    @app.post(
        "/v1/clientes/{cliente_id}/estrategias",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def crear_estrategia(cliente_id: str, peticion: EstrategiaPeticion) -> dict:
        estrategia = servicio.hermes.estrategias.crear(
            cliente_id=cliente_id,
            nombre=peticion.nombre,
            objetivo=peticion.objetivo,
            etapa=peticion.etapa,
            plantilla=peticion.plantilla,
            condiciones=peticion.condiciones,
            evidencia=peticion.evidencia,
        )
        decision = servicio.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="activar_estrategia",
            descripcion=(
                f"Alta de la estrategia {estrategia.nombre} v{estrategia.version} "
                f"({estrategia.etapa.value}); requiere aprobacion para activarse"
            ),
            payload={"estrategia_id": estrategia.estrategia_id},
        )
        return {
            "estrategia": estrategia.model_dump(mode="json"),
            "decision_id": decision.decision_id,
        }

    @app.post(
        "/v1/clientes/{cliente_id}/estrategias/{estrategia_id}/activar",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def activar_estrategia(
        cliente_id: str, estrategia_id: str, peticion: ActivacionPeticion
    ) -> dict:
        decision = servicio.almacen.obtener_decision(peticion.decision_id)
        if decision is None or decision.cliente_id != cliente_id:
            raise HTTPException(status_code=404, detail="Decision inexistente para ese cliente")
        if not servicio.gobernanza.puede_ejecutar(decision):
            raise HTTPException(
                status_code=409,
                detail="La estrategia solo se activa con una decision aprobada",
            )
        try:
            estrategia = servicio.hermes.estrategias.activar(
                cliente_id, estrategia_id, peticion.decision_id
            )
        except EstrategiaError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return estrategia.model_dump(mode="json")

    # --- aprendizaje ----------------------------------------------------------
    @app.get(
        "/v1/clientes/{cliente_id}/patrones",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def patrones(cliente_id: str) -> list[dict]:
        return [p.model_dump(mode="json") for p in servicio.almacen.listar_patrones(cliente_id)]

    @app.post(
        "/v1/clientes/{cliente_id}/patrones/detectar",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def detectar_patrones(cliente_id: str) -> list[dict]:
        aprendizaje: Aprendizaje = servicio.aprendizaje
        return [p.model_dump(mode="json") for p in aprendizaje.detectar_patrones(cliente_id)]

    @app.post(
        "/v1/clientes/{cliente_id}/patrones/{patron_id}/proponer",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def proponer_cambio(cliente_id: str, patron_id: str) -> dict:
        try:
            patron = servicio.aprendizaje.proponer_cambio(cliente_id, patron_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return patron.model_dump(mode="json")

    # --- sandbox ---------------------------------------------------------------
    @app.get("/v1/sandbox/escenarios", dependencies=[Depends(autorizar)])
    def escenarios() -> list[dict]:
        return [
            {
                "clave": escenario.clave,
                "descripcion": escenario.descripcion,
                "mensajes": list(escenario.mensajes),
                "espera_escalamiento": escenario.espera_escalamiento,
            }
            for escenario in ESCENARIOS
        ]

    @app.post(
        "/v1/clientes/{cliente_id}/sandbox",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def sandbox(cliente_id: str, peticion: SandboxPeticion) -> dict:
        try:
            resultado = ejecutar_escenario(
                servicio.hermes, cliente_id, peticion.escenario, peticion.canal, peticion.contacto
            )
        except SandboxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "escenario": resultado.escenario,
            "descripcion": resultado.descripcion,
            "escalo": resultado.escalo,
            "coincide_con_lo_esperado": resultado.coincide_con_lo_esperado,
            "turnos": resultado.turnos,
        }

    @app.post(
        "/v1/clientes/{cliente_id}/sandbox/todos",
        dependencies=[Depends(autorizar), Depends(exigir_cliente)],
    )
    def sandbox_completo(cliente_id: str, canal: Canal = Canal.WHATSAPP) -> list[dict]:
        try:
            return ejecutar_todos(servicio.hermes, cliente_id, canal)
        except SandboxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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


class AdaptadorEntrante(Protocol):
    def normalizar(self, cliente_id: str, carga: Mapping[str, str]) -> ContactoEntrante: ...


async def _carga(peticion: Request) -> dict[str, str]:
    """Acepta JSON o formulario (Twilio envia form-urlencoded)."""
    tipo = peticion.headers.get("content-type", "")
    try:
        if tipo.startswith("application/json"):
            cuerpo = await peticion.json()
            if not isinstance(cuerpo, dict):
                raise HTTPException(status_code=422, detail="El webhook espera un objeto JSON")
            return cuerpo
        formulario = await peticion.form()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Carga ilegible: {exc}") from exc
    return {clave: str(valor) for clave, valor in formulario.items()}


def _normalizar(
    adaptador: AdaptadorEntrante, cliente_id: str, carga: Mapping[str, str]
) -> ContactoEntrante:
    try:
        return adaptador.normalizar(cliente_id, carga)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _url_canonica(servicio: Servicio, peticion: Request) -> str:
    """Twilio firma sobre la URL publica, no sobre la que ve el proceso tras un proxy."""
    base = servicio.config.url_publica
    if not base:
        return str(peticion.url)
    return base.rstrip("/") + peticion.url.path


def _respuesta_repetida(
    servicio: Servicio, cliente_id: str, canal: Canal, evento_id: str | None
) -> RespuestaAtencion | None:
    if not evento_id:
        return None
    previa = servicio.almacen.evento_ya_procesado(cliente_id, canal.value, evento_id)
    if previa is None:
        return None
    return RespuestaAtencion.model_validate_json(previa)


def _marcar_evento(
    servicio: Servicio,
    cliente_id: str,
    canal: Canal,
    evento_id: str | None,
    respuesta: RespuestaAtencion,
) -> None:
    if evento_id:
        servicio.almacen.marcar_evento(
            cliente_id, canal.value, evento_id, respuesta.model_dump_json()
        )


def _json(texto: str) -> object:
    try:
        return json.loads(texto)
    except ValueError:
        return texto


def _texto(cuerpo: dict) -> str:
    import json

    return json.dumps(cuerpo, default=str)


def _entregar(servicio: Servicio, resultado: ResultadoAtencion, canal: Canal) -> None:
    """Envio real por el canal correspondiente; en Nivel 2+ no hay nada que enviar aun."""
    if not resultado.enviada or not resultado.respuesta:
        return
    if not servicio.config.envio_real:
        return
    error = _enviar_por_canal(servicio, canal, resultado.lead.contacto, resultado.respuesta)
    if resultado.mensaje_id:
        servicio.almacen.actualizar_entrega(
            resultado.lead.cliente_id,
            resultado.mensaje_id,
            EstadoEntrega.FALLIDA if error else EstadoEntrega.ENVIADA,
            error,
        )
    if error is None:
        return
    servicio.hermes.gobernanza.registrar_resultado(
        resultado.decision.decision_id, f"fallo_envio: {error}"
    )
    if servicio.nexus is not None:
        try:
            servicio.nexus.reportar_error(
                mensaje=f"Fallo de envio por {canal.value}: {error}",
                correlation_key=resultado.decision.decision_id,
            )
        except Exception:
            pass


def _entregar_texto(
    servicio: Servicio, cliente_id: str, lead_id: str, canal: Canal, texto: str
) -> None:
    if not servicio.config.envio_real:
        return
    lead = servicio.almacen.obtener_lead(cliente_id, lead_id)
    if lead is None:
        return
    _enviar_por_canal(servicio, canal, lead.contacto, texto)


def _enviar_por_canal(servicio: Servicio, canal: Canal, destino: str, texto: str) -> str | None:
    """Devuelve el error como texto; nunca propaga: un fallo de canal no tumba el webhook."""
    try:
        if canal == Canal.WHATSAPP and servicio.whatsapp.configurado:
            servicio.whatsapp.enviar(destino, texto)
        elif canal == Canal.CORREO and servicio.correo.configurado:
            servicio.correo.enviar(destino, texto)
        else:
            return None
    except Exception as exc:
        return str(exc)
    return None


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
    mensaje_id = servicio.almacen.siguiente_folio("mensaje", "MSG", 6)
    servicio.almacen.guardar_mensaje(
        Mensaje(
            mensaje_id=mensaje_id,
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
    error = _enviar_por_canal(servicio, canal, lead.contacto, respuesta)
    servicio.almacen.actualizar_entrega(
        decision.cliente_id,
        mensaje_id,
        EstadoEntrega.FALLIDA if error else EstadoEntrega.ENVIADA,
        error,
    )
    if error:
        servicio.gobernanza.registrar_resultado(decision_id, f"fallo_envio: {error}")
