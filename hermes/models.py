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


class Plan(str, Enum):
    """Un solo HERMES; el plan solo habilita capacidades."""

    PLAN_1 = "plan_1"
    PLAN_2 = "plan_2"
    PLAN_3 = "plan_3"
    PILOTO = "piloto"


CANALES_POR_PLAN: dict[Plan, frozenset[Canal]] = {
    Plan.PLAN_1: frozenset({Canal.WHATSAPP}),
    Plan.PLAN_2: frozenset({Canal.WHATSAPP, Canal.CORREO}),
    Plan.PLAN_3: frozenset({Canal.WHATSAPP, Canal.CORREO, Canal.LLAMADA}),
    Plan.PILOTO: frozenset({Canal.WHATSAPP, Canal.CORREO, Canal.LLAMADA}),
}


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


class Intencion(str, Enum):
    SALUDO = "saludo"
    INFORMACION = "informacion"
    PRECIO = "precio"
    DISPONIBILIDAD = "disponibilidad"
    COMPRA = "compra"
    OBJECION = "objecion"
    QUEJA = "queja"
    SOPORTE = "soporte"
    PIDE_HUMANO = "pide_humano"
    DESINTERES = "desinteres"
    DESCONOCIDA = "desconocida"


class MotivoEscalamiento(str, Enum):
    SOLICITA_HUMANO = "solicita_humano"
    BAJA_CONFIANZA = "baja_confianza"
    INFORMACION_INSUFICIENTE = "informacion_insuficiente"
    FUERA_DE_LIMITES = "fuera_de_limites"
    LEGAL = "legal"
    FINANCIERO_SENSIBLE = "financiero_sensible"
    QUEJA_COMPLEJA = "queja_compleja"
    REGLA_DEL_CLIENTE = "regla_del_cliente"
    RIESGO_OPERATIVO = "riesgo_operativo"


class EstadoEscalamiento(str, Enum):
    PENDIENTE = "pendiente"
    TOMADO = "tomado"
    RESUELTO = "resuelto"
    POSPUESTO = "pospuesto"
    RECHAZADO = "rechazado"


class EstadoEstrategia(str, Enum):
    PROPUESTA = "propuesta"
    ACTIVA = "activa"
    RETIRADA = "retirada"


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


class EstadoEntrega(str, Enum):
    PENDIENTE = "pendiente"
    ENVIADA = "enviada"
    FALLIDA = "fallida"


class ReglasEscalamiento(BaseModel):
    """Configurable por tenant (Seccion 17 de la directiva de finalizacion)."""

    confianza_minima: float = 0.45
    escalar_quejas: bool = True
    escalar_solicitud_humano: bool = True
    escalar_temas_legales: bool = True
    escalar_descuentos: bool = True
    palabras_clave_extra: list[str] = Field(default_factory=list)
    descuento_maximo_por_ciento: float = 0.0


class HorarioComercial(BaseModel):
    """Horario por tenant; los dias van de 0 (lunes) a 6 (domingo)."""

    dias: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    hora_inicio: int = 9
    hora_fin: int = 19


class Cliente(BaseModel):
    """Tenant de HERMES. El plan y las preferencias vienen del onboarding (5.7)."""

    cliente_id: str
    nombre_negocio: str
    plan: Plan = Plan.PLAN_1
    representante: str | None = None
    correo_representante: str | None = None
    industria: str | None = None
    descripcion_negocio: str | None = None
    productos: list[str] = Field(default_factory=list)
    objetivos: list[str] = Field(default_factory=list)
    zona_horaria: str = "UTC"
    horario_comercial: HorarioComercial = Field(default_factory=HorarioComercial)
    reglas_escalamiento: ReglasEscalamiento = Field(default_factory=ReglasEscalamiento)
    correo_conectado: str | None = None
    telefono_whatsapp: str | None = None
    telefono_llamadas: str | None = None
    atencion_correo: bool = False
    atencion_llamada: bool = False
    nivel_servicio_llamada: NivelServicioLlamada | None = None
    grabacion_autorizada: bool = False
    jurisdiccion: str | None = None
    guion_aprobado: bool = True
    sandbox: bool = False
    fecha_alta: datetime = Field(default_factory=ahora)

    @field_validator("cliente_id")
    @classmethod
    def _cliente_id_no_vacio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("cliente_id es obligatorio en todo registro de DEUS")
        return valor

    @property
    def canales_habilitados(self) -> frozenset[Canal]:
        canales = set(CANALES_POR_PLAN[self.plan])
        if Canal.CORREO in canales and not self.atencion_correo:
            canales.discard(Canal.CORREO)
        if Canal.LLAMADA in canales and not self.atencion_llamada:
            canales.discard(Canal.LLAMADA)
        return frozenset(canales)


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
    intencion: Intencion = Intencion.DESCONOCIDA
    confianza: float = 0.0
    urgencia: bool = False
    objeciones: list[str] = Field(default_factory=list)
    senales_compra: list[str] = Field(default_factory=list)
    requiere_humano: bool = False
    motivo_escalamiento: MotivoEscalamiento | None = None
    objetivo_actual: str | None = None
    estrategia_aplicada: str | None = None


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
    proveedor: str | None = None
    call_sid: str | None = None


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
    autor: str = "hermes"  # hermes / lead / humano
    estado_entrega: EstadoEntrega = EstadoEntrega.PENDIENTE
    error_entrega: str | None = None


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


class Escalamiento(BaseModel):
    """Bandeja de intervencion humana."""

    escalamiento_id: str
    cliente_id: str
    lead_id: str
    canal: Canal
    motivo: MotivoEscalamiento
    contexto: str = ""
    objetivo: str = ""
    recomendacion: str = ""
    respuesta_sugerida: str = ""
    estado: EstadoEscalamiento = EstadoEscalamiento.PENDIENTE
    atendido_por: str | None = None
    accion: str | None = None
    respuesta_final: str | None = None
    resultado: str | None = None
    decision_id: str | None = None
    fecha_creacion: datetime = Field(default_factory=ahora)
    fecha_resolucion: datetime | None = None
    segundos_hasta_resultado: int | None = None


class Estrategia(BaseModel):
    """Estrategia comercial versionada y aprobada por tenant."""

    estrategia_id: str
    cliente_id: str
    nombre: str
    objetivo: str
    etapa: Etapa
    condiciones: list[str] = Field(default_factory=list)
    plantilla: str = ""
    version: int = 1
    estado: EstadoEstrategia = EstadoEstrategia.PROPUESTA
    evidencia: str = ""
    resultados: dict = Field(default_factory=dict)
    decision_id: str | None = None
    fecha_creacion: datetime = Field(default_factory=ahora)
    fecha_activacion: datetime | None = None


class Patron(BaseModel):
    """Patron observado en los resultados del tenant; nunca se aplica solo."""

    patron_id: str
    cliente_id: str
    descripcion: str
    evidencia: dict = Field(default_factory=dict)
    muestras: int = 0
    propuesta: str = ""
    decision_id: str | None = None
    fecha_deteccion: datetime = Field(default_factory=ahora)


class RegistroResultado(BaseModel):
    """Objetivo -> recomendacion -> accion -> resultado -> impacto."""

    resultado_id: str
    cliente_id: str
    lead_id: str
    objetivo: str
    recomendacion: str = ""
    accion: str = ""
    resultado: str = ""
    impacto: str = ""
    estrategia_id: str | None = None
    decision_id: str | None = None
    segundos_hasta_resultado: int | None = None
    fecha: datetime = Field(default_factory=ahora)
