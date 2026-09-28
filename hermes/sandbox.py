"""Laboratorio de pruebas de HERMES.

El sandbox NO tiene logica propia: arma eventos de contacto y los pasa por
`Hermes.atender`, el mismo nucleo que atiende produccion. Lo unico que cambia
es el tenant (marcado `sandbox=True`), que nunca entrega por un canal real.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Canal
from .pipeline import ContactoEntrante, Hermes, ResultadoAtencion


@dataclass(frozen=True)
class Escenario:
    clave: str
    descripcion: str
    mensajes: tuple[str, ...]
    espera_escalamiento: bool = False


ESCENARIOS: tuple[Escenario, ...] = (
    Escenario(
        "interesado",
        "Prospecto interesado que pregunta y avanza hasta el cierre",
        ("Hola, me interesa su servicio", "Cuanto cuesta?", "Quiero comprar, como pago?"),
    ),
    Escenario(
        "indeciso",
        "Prospecto que pide informacion y luego lo pospone",
        ("Quiero saber como funciona", "Lo pienso y le aviso mas adelante"),
    ),
    Escenario(
        "objecion_precio",
        "Objecion de precio explicita",
        ("Me interesa", "Esta muy caro, no me alcanza"),
    ),
    Escenario(
        "descuento",
        "Peticion de descuento fuera de limites",
        ("Me pueden hacer un descuento especial?",),
        espera_escalamiento=True,
    ),
    Escenario(
        "pregunta_desconocida",
        "Pregunta sin informacion disponible en el catalogo del tenant",
        ("Trabajan con certificacion ISO 45001 para obra civil en Canada?",),
    ),
    Escenario(
        "molesto",
        "Cliente molesto con una queja",
        ("Estoy molesto, el servicio fue pesimo, quiero poner una queja",),
        espera_escalamiento=True,
    ),
    Escenario(
        "pide_humano",
        "El prospecto pide hablar con una persona",
        ("Quiero hablar con una persona real",),
        espera_escalamiento=True,
    ),
    Escenario(
        "cierre",
        "Señal de compra directa",
        ("Acepto, confirmo la contratacion",),
    ),
    Escenario(
        "seguimiento",
        "Prospecto que no responde y recibe seguimiento",
        ("Me interesa", ""),
    ),
    Escenario(
        "recuperacion",
        "Prospecto frio que vuelve a mostrar interes",
        ("Ahora no, gracias", "Cambie de opinion, me interesa de nuevo"),
    ),
    Escenario(
        "escalamiento_legal",
        "Asunto legal que HERMES no debe responder solo",
        ("Mi abogado va a revisar el contrato, es un fraude?",),
        espera_escalamiento=True,
    ),
)

ESCENARIOS_POR_CLAVE = {escenario.clave: escenario for escenario in ESCENARIOS}


@dataclass
class ResultadoEscenario:
    escenario: str
    descripcion: str
    turnos: list[dict] = field(default_factory=list)
    escalo: bool = False
    coincide_con_lo_esperado: bool = True


class SandboxError(RuntimeError):
    pass


def ejecutar_escenario(
    hermes: Hermes,
    cliente_id: str,
    clave: str,
    canal: Canal = Canal.WHATSAPP,
    contacto: str | None = None,
) -> ResultadoEscenario:
    cliente = hermes.cliente(cliente_id)
    if not cliente.sandbox:
        raise SandboxError(
            f"{cliente_id} no es un tenant de pruebas; cree uno con sandbox=true en el onboarding"
        )
    escenario = ESCENARIOS_POR_CLAVE.get(clave)
    if escenario is None:
        raise SandboxError(f"Escenario desconocido: {clave}")

    contacto = contacto or f"+99900{abs(hash(clave)) % 10000:04d}"
    resultado = ResultadoEscenario(escenario=escenario.clave, descripcion=escenario.descripcion)
    for texto in escenario.mensajes:
        if not texto:
            atencion = hermes.programar_seguimiento(cliente_id, resultado.turnos[-1]["lead_id"])
        else:
            atencion = hermes.atender(
                ContactoEntrante(
                    cliente_id=cliente_id,
                    canal=canal,
                    contacto=contacto,
                    texto=texto,
                    fuente="sandbox",
                )
            )
        resultado.turnos.append(_turno(texto, atencion))
        resultado.escalo = resultado.escalo or atencion.escalamiento is not None

    resultado.coincide_con_lo_esperado = resultado.escalo == escenario.espera_escalamiento
    return resultado


def ejecutar_todos(hermes: Hermes, cliente_id: str, canal: Canal = Canal.WHATSAPP) -> list[dict]:
    salida: list[dict] = []
    for escenario in ESCENARIOS:
        resultado = ejecutar_escenario(hermes, cliente_id, escenario.clave, canal)
        salida.append(
            {
                "escenario": resultado.escenario,
                "descripcion": resultado.descripcion,
                "escalo": resultado.escalo,
                "coincide_con_lo_esperado": resultado.coincide_con_lo_esperado,
                "turnos": resultado.turnos,
            }
        )
    return salida


def _turno(texto: str, atencion: ResultadoAtencion) -> dict:
    return {
        "mensaje_lead": texto,
        "lead_id": atencion.lead.lead_id,
        "etapa": atencion.lead.etapa.value,
        "estatus": atencion.lead.estatus.value,
        "intencion": atencion.lead.intencion.value,
        "confianza": atencion.lead.confianza,
        "respuesta": atencion.respuesta,
        "enviada": atencion.enviada,
        "motivo": atencion.motivo,
        "motor": atencion.motor,
        "error_motor": atencion.error_motor,
        "escalamiento": (
            atencion.escalamiento.model_dump(mode="json") if atencion.escalamiento else None
        ),
    }


__all__ = [
    "ESCENARIOS",
    "Escenario",
    "ResultadoEscenario",
    "SandboxError",
    "ejecutar_escenario",
    "ejecutar_todos",
]
