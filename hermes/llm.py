"""Capa de IA generativa de HERMES.

La especificacion recomienda Claude via API (Seccion 3.3). El servicio no debe
quedar inoperable sin llave: si no hay credencial configurada, o si el proveedor
falla tras los reintentos, HERMES responde con el guion aprobado por etapa y
canal, que es exactamente la excepcion de bajo riesgo de la Seccion 4.4.

El fallback es un mecanismo de seguridad, no un sustituto del modelo: la
respuesta declara siempre que motor la produjo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Canal, Etapa, Lead
from .resilience import logger, redactar, reintentar

URL_ANTHROPIC = "https://api.anthropic.com/v1/messages"
VERSION_ANTHROPIC = "2023-06-01"


class MotorError(RuntimeError):
    """Fallo del proveedor de IA, ya saneado de credenciales."""


@dataclass
class RespuestaGenerada:
    texto: str
    con_guion_aprobado: bool
    motor: str
    error: str | None = None


class MotorLLM(Protocol):
    nombre: str

    def generar(self, prompt: str, mensaje_lead: str) -> str: ...


GUION_APROBADO: dict[Etapa, str] = {
    Etapa.APERTURA: (
        "Gracias por escribirnos, {nombre}. Con gusto le ayudamos. "
        "Para orientarle mejor, cual producto o servicio le interesa?"
    ),
    Etapa.PROSPECCION: (
        "Perfecto, {nombre}. Para darle la mejor opcion, "
        "para cuando lo necesita?"
    ),
    Etapa.CIERRE: (
        "Con la informacion que me comparte ya podemos avanzar, {nombre}. "
        "Le parece si agendamos el siguiente paso hoy mismo?"
    ),
    Etapa.SEGUIMIENTO: (
        "Retomo su solicitud, {nombre}, por si sigue interesado. "
        "Quiere que le comparta la propuesta actualizada?"
    ),
}


def _ajustar_a_canal(texto: str, canal: Canal, negocio: str, saludo: str = "Buen dia") -> str:
    if canal == Canal.WHATSAPP:
        return "\n".join(texto.split("\n")[:3])
    if canal == Canal.CORREO:
        cuerpo = " ".join(texto.split())
        palabras = cuerpo.split(" ")[:150]
        return f"{saludo},\n\n{' '.join(palabras)}\n\nQuedo atento.\n{negocio}"
    return texto.replace("\n", " ").strip()


class MotorGuion:
    """Motor determinista de respaldo: solo plantillas aprobadas."""

    nombre = "guion_aprobado"

    def responder(
        self, lead: Lead, canal: Canal, negocio: str, saludo: str = "Buen dia"
    ) -> RespuestaGenerada:
        plantilla = GUION_APROBADO[lead.etapa].format(nombre=lead.nombre or "")
        return RespuestaGenerada(
            texto=_ajustar_a_canal(plantilla.replace("  ", " ").strip(), canal, negocio, saludo),
            con_guion_aprobado=True,
            motor=self.nombre,
        )


class MotorClaude:
    """Cliente de la API de mensajes de Anthropic (Seccion 3.3).

    Contrato: POST /v1/messages con cabeceras `x-api-key` y `anthropic-version`,
    cuerpo {model, max_tokens, system, messages} y respuesta con una lista
    `content` de bloques tipados.
    """

    nombre = "claude"

    def __init__(
        self,
        api_key: str,
        modelo: str = "claude-sonnet-4-20250514",
        timeout_seconds: int = 20,
        max_tokens: int = 500,
        intentos: int = 3,
    ) -> None:
        self.api_key = api_key
        self.modelo = modelo
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.intentos = intentos

    def generar(self, prompt: str, mensaje_lead: str) -> str:
        return reintentar(
            "anthropic.messages",
            lambda: self._llamar(prompt, mensaje_lead),
            intentos=self.intentos,
        )

    def _llamar(self, prompt: str, mensaje_lead: str) -> str:
        cuerpo = json.dumps(
            {
                "model": self.modelo,
                "max_tokens": self.max_tokens,
                "system": prompt,
                "messages": [{"role": "user", "content": mensaje_lead or "(sin texto)"}],
            }
        ).encode("utf-8")
        peticion = Request(
            URL_ANTHROPIC,
            method="POST",
            data=cuerpo,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": VERSION_ANTHROPIC,
            },
        )
        try:
            with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
                crudo = respuesta.read().decode("utf-8")
        except HTTPError as exc:
            detalle = exc.read().decode("utf-8", "ignore")[:300]
            raise MotorError(
                f"Anthropic respondio {exc.code}: {redactar(detalle)}"
            ) from None
        except URLError as exc:
            raise MotorError(f"No se pudo contactar a Anthropic: {redactar(str(exc.reason))}") from None
        except TimeoutError:
            raise MotorError("Anthropic excedio el tiempo de espera") from None

        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError:
            raise MotorError("Anthropic devolvio una respuesta que no es JSON valido") from None
        bloques = datos.get("content")
        if not isinstance(bloques, list):
            raise MotorError("Respuesta de Anthropic sin bloques de contenido")
        return "".join(
            str(bloque.get("text", ""))
            for bloque in bloques
            if isinstance(bloque, dict) and bloque.get("type", "text") == "text"
        ).strip()


class GeneradorRespuestas:
    """Decide entre IA generativa y guion aprobado, y garantiza el formato por canal."""

    def __init__(self, motor: MotorLLM | None = None) -> None:
        self.motor = motor
        self.respaldo = MotorGuion()

    @property
    def motor_real_disponible(self) -> bool:
        return self.motor is not None

    def responder(
        self,
        lead: Lead,
        canal: Canal,
        negocio: str,
        prompt: str,
        mensaje_lead: str,
        saludo: str = "Buen dia",
    ) -> RespuestaGenerada:
        if self.motor is None:
            return self.respaldo.responder(lead, canal, negocio, saludo)
        try:
            texto = self.motor.generar(prompt, mensaje_lead)
        except Exception as exc:  # noqa: BLE001 - el lead nunca queda sin atencion
            mensaje = redactar(str(exc))
            logger.error("Fallo del motor %s: %s", self.motor.nombre, mensaje)
            respuesta = self.respaldo.responder(lead, canal, negocio, saludo)
            respuesta.error = mensaje
            return respuesta
        if not texto:
            respuesta = self.respaldo.responder(lead, canal, negocio, saludo)
            respuesta.error = "respuesta vacia del motor"
            return respuesta
        return RespuestaGenerada(
            texto=_ajustar_a_canal(texto, canal, negocio, saludo),
            con_guion_aprobado=False,
            motor=self.motor.nombre,
        )
