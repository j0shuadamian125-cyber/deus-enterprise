"""Capa de IA generativa de HERMES.

La especificacion recomienda Claude via API (Seccion 3.3). El servicio no debe
quedar inoperable sin llave: si no hay credencial configurada, HERMES responde
con el guion aprobado por etapa y canal, que es exactamente la excepcion de
bajo riesgo de la Seccion 4.4 (Nivel 1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.request import Request, urlopen

from .models import Canal, Etapa, Lead


@dataclass
class RespuestaGenerada:
    texto: str
    con_guion_aprobado: bool
    motor: str


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


def _ajustar_a_canal(texto: str, canal: Canal, negocio: str) -> str:
    if canal == Canal.WHATSAPP:
        return "\n".join(texto.split("\n")[:3])
    if canal == Canal.CORREO:
        cuerpo = " ".join(texto.split())
        palabras = cuerpo.split(" ")[:150]
        return f"Buen dia,\n\n{' '.join(palabras)}\n\nQuedo atento.\n{negocio}"
    return texto.replace("\n", " ").strip()


class MotorGuion:
    """Motor determinista de respaldo: solo plantillas aprobadas."""

    nombre = "guion_aprobado"

    def responder(self, lead: Lead, canal: Canal, negocio: str) -> RespuestaGenerada:
        plantilla = GUION_APROBADO[lead.etapa].format(nombre=lead.nombre or "")
        return RespuestaGenerada(
            texto=_ajustar_a_canal(plantilla.replace("  ", " ").strip(), canal, negocio),
            con_guion_aprobado=True,
            motor=self.nombre,
        )


class MotorClaude:
    """Cliente minimo de la API de Anthropic (recomendacion de la Seccion 3.3)."""

    nombre = "claude"

    def __init__(
        self,
        api_key: str,
        modelo: str = "claude-sonnet-4-20250514",
        timeout_seconds: int = 20,
        max_tokens: int = 500,
    ) -> None:
        self.api_key = api_key
        self.modelo = modelo
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    def generar(self, prompt: str, mensaje_lead: str) -> str:
        cuerpo = json.dumps(
            {
                "model": self.modelo,
                "max_tokens": self.max_tokens,
                "system": prompt,
                "messages": [{"role": "user", "content": mensaje_lead}],
            }
        ).encode("utf-8")
        peticion = Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            data=cuerpo,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
        return "".join(bloque.get("text", "") for bloque in datos.get("content", [])).strip()


class GeneradorRespuestas:
    """Decide entre IA generativa y guion aprobado, y garantiza el formato por canal."""

    def __init__(self, motor: MotorLLM | None = None) -> None:
        self.motor = motor
        self.respaldo = MotorGuion()

    def responder(
        self, lead: Lead, canal: Canal, negocio: str, prompt: str, mensaje_lead: str
    ) -> RespuestaGenerada:
        if self.motor is None:
            return self.respaldo.responder(lead, canal, negocio)
        try:
            texto = self.motor.generar(prompt, mensaje_lead)
        except Exception:
            # Sin respuesta del LLM se cae al guion aprobado: el lead nunca queda sin atencion.
            return self.respaldo.responder(lead, canal, negocio)
        if not texto:
            return self.respaldo.responder(lead, canal, negocio)
        return RespuestaGenerada(
            texto=_ajustar_a_canal(texto, canal, negocio),
            con_guion_aprobado=False,
            motor=self.motor.nombre,
        )
