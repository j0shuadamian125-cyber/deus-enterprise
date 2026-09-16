"""Modelos de datos de HERMES (Seccion 5 de la Especificacion Tecnica v4.0).

Todo registro lleva cliente_id: es la regla de multi-tenancy de la Seccion 2.3.2.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class Canal(str, Enum):
    WHATSAPP = "whatsapp"
    CORREO = "correo"
    LLAMADA = "llamada"


class Etapa(str, Enum):
    APERTURA = "Apertura"
    PROSPECCION = "Prospeccion"
    CIERRE = "Cierre"
    SEGUIMIENTO = "Seguimiento"


class Estatus(str, Enum):
    NUEVO = "Nuevo"
    EN_PROCESO = "En_proceso"
    CERRADO = "Cerrado"
    PERDIDO = "Perdido"


class NivelServicioLlamada(str, Enum):
    VOZ_IA = "voz_ia"
    ASISTIDA = "asistida"


class ResultadoLlamada(str, Enum):
    AVANZO_ETAPA = "avanzo_etapa"
    SIN_RESPUESTA = "sin_respuesta"
    NO_INTERESADO = "no_interesado"


class NivelDecision(int, Enum):
    SOLO_LECTURA = 0
    REVERSIBLE = 1
    APROBACION_PREVIA = 2
    CAMBIO_ESTRUCTURAL = 3
    IRREVERSIBLE = 4


class EstadoDecision(str, Enum):
    PENDIENTE = "pendiente"
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"
    REVERTIDA = "revertida"
    ESPERA_SEGUNDA_CONFIRMACION = "espera_segunda_confirmacion"


class CanalConfirmacion(str, Enum):
    CHAT = "chat"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    NO_APLICA = "N-A"


class Cliente(BaseModel):
    """Tenant de HERMES. El plan y las preferencias vienen del onboarding (5.7)."""

    cliente_id: str
    nombre_negocio: str
    plan: str = "basico"
    correo_conectado: str | None = None
    telefono_whatsapp: str | None = None
    telefono_llamadas: str | None = None
    atencion_correo: bool = False
    atencion_llamada: bool = False
    nivel_servicio_llamada: NivelServicioLlamada | None = None
    grabacion_autorizada: bool = False
    jurisdiccion: str | None = None
    guion_aprobado: bool = True
    fecha_alta: datetime = Field(default_factory=ahora)

    @field_validator("cliente_id")
    @classmethod
    def _cliente_id_no_vacio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("cliente_id es obligatorio en todo registro de DEUS")
        return valor


class Lead(BaseModel):
    """Hoja Leads_[cliente_id] (Seccion 5.2.1)."""

    lead_id: str
    cliente_id: str
    nombre: str | None = None
    contacto: str
    canal_origen: Canal
    fuente: str | None = None
    mensaje_inicial: str = ""
    fecha_entrada: datetime = Field(default_factory=ahora)
    estatus: Estatus = Estatus.NUEVO
    etapa: Etapa = Etapa.APERTURA
    ultima_respuesta_lead: str | None = None
    ultimo_contacto: datetime | None = None
    contador_seguimientos: int = 0
    producto_interes: str | None = None
    notas_internas: str = ""
    nivel_decision: NivelDecision = NivelDecision.REVERSIBLE
    canal_preferido_lead: Canal | None = None


class Llamada(BaseModel):
    """Hoja Llamadas_[cliente_id] (Seccion 5.4.3)."""

    llamada_id: str
    cliente_id: str
    lead_id: str
    fecha_hora: datetime = Field(default_factory=ahora)
    nivel_servicio: NivelServicioLlamada
    duracion_segundos: int = 0
    url_grabacion: str | None = None
    transcripcion_texto: str = ""
    resultado: ResultadoLlamada = ResultadoLlamada.SIN_RESPUESTA
    etapa_resultante: Etapa = Etapa.APERTURA


class Mensaje(BaseModel):
    """Historial conversacional, canal-agnostico (Seccion 5.2)."""

    mensaje_id: str
    cliente_id: str
    lead_id: str
    canal: Canal
    direccion: str  # entrante / saliente
    texto: str
    fecha_hora: datetime = Field(default_factory=ahora)
    etapa: Etapa = Etapa.APERTURA
    decision_id: str | None = None


class Decision(BaseModel):
    """Bitacora_Decisiones (Seccion 4.5)."""

    decision_id: str
    fecha_hora: datetime = Field(default_factory=ahora)
    modulo_origen: str = "HERMES"
    cliente_id: str
    nivel: NivelDecision
    descripcion: str
    estado: EstadoDecision = EstadoDecision.PENDIENTE
    aprobado_por: str | None = None
    fecha_resolucion: datetime | None = None
    resultado_observado: str | None = None
    canal_confirmacion: CanalConfirmacion = CanalConfirmacion.NO_APLICA
    payload: dict = Field(default_factory=dict)


class MemoriaResumen(BaseModel):
    """Hoja Memoria_Resumen_DEUS (Seccion 3.5.1), alimentada por HERMES."""

    fecha: datetime = Field(default_factory=ahora)
    cliente_id: str
    resumen_evento: str
    modulo_relacionado: str = "HERMES"
    decision_id_referencia: str | None = None
