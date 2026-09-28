"""HERMES: modulo de ventas y atencion multicanal de DEUS.

WhatsApp, correo y llamada entran por adaptadores distintos y desembocan en el
mismo CRM y el mismo pipeline de 4 etapas. Todo registro esta aislado por
cliente_id y toda accion queda en la Bitacora de Decisiones.
"""
from .config import Configuracion, Servicio, construir_servicio
from .governance import Gobernanza, GobernanzaError, nivel_de
from .llm import GeneradorRespuestas, MotorClaude, MotorGuion
from .models import (
    Canal,
    CanalConfirmacion,
    Cliente,
    Decision,
    EstadoDecision,
    Estatus,
    Etapa,
    Lead,
    Llamada,
    MemoriaResumen,
    Mensaje,
    NivelDecision,
    NivelServicioLlamada,
    ResultadoLlamada,
)
from .onboarding import RespuestasOnboarding, alta_cliente
from .pipeline import ContactoEntrante, Hermes, ResultadoAtencion
from .storage import AislamientoError, Almacen

__all__ = [
    "AislamientoError",
    "Almacen",
    "Canal",
    "CanalConfirmacion",
    "Cliente",
    "Configuracion",
    "ContactoEntrante",
    "Decision",
    "EstadoDecision",
    "Estatus",
    "Etapa",
    "GeneradorRespuestas",
    "Gobernanza",
    "GobernanzaError",
    "Hermes",
    "Lead",
    "Llamada",
    "MemoriaResumen",
    "Mensaje",
    "MotorClaude",
    "MotorGuion",
    "NivelDecision",
    "NivelServicioLlamada",
    "RespuestasOnboarding",
    "ResultadoAtencion",
    "ResultadoLlamada",
    "Servicio",
    "alta_cliente",
    "construir_servicio",
    "nivel_de",
]
