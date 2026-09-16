"""Canal correo electronico (Seccion 5.3).

Equivalente al 'Watch emails' de Make.com: el proveedor entrega remitente,
asunto y cuerpo, y HERMES los trata igual que un mensaje de WhatsApp. El envio
usa SMTP para no atar el modulo a un proveedor concreto.
"""
from __future__ import annotations

import os
import re
import smtplib
from collections.abc import Mapping
from email.message import EmailMessage

from ..models import Canal
from ..pipeline import ContactoEntrante

_DIRECCION = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def extraer_direccion(remitente: str) -> str:
    coincidencia = _DIRECCION.search(remitente or "")
    if not coincidencia:
        raise ValueError(f"Remitente sin direccion valida: {remitente!r}")
    return coincidencia.group(0).lower()


class AdaptadorCorreo:
    canal = Canal.CORREO

    def __init__(
        self,
        host: str | None = None,
        puerto: int = 587,
        usuario: str | None = None,
        password: str | None = None,
        remitente: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.host = host or os.environ.get("SMTP_HOST")
        self.puerto = int(os.environ.get("SMTP_PORT", puerto))
        self.usuario = usuario or os.environ.get("SMTP_USER")
        self.password = password or os.environ.get("SMTP_PASSWORD")
        self.remitente = remitente or os.environ.get("SMTP_FROM") or self.usuario
        self.timeout_seconds = timeout_seconds

    @property
    def configurado(self) -> bool:
        return bool(self.host and self.usuario and self.password and self.remitente)

    def normalizar(self, cliente_id: str, carga: Mapping[str, str]) -> ContactoEntrante:
        direccion = extraer_direccion(str(carga.get("from") or carga.get("remitente") or ""))
        asunto = str(carga.get("subject") or carga.get("asunto") or "").strip()
        cuerpo = str(carga.get("body") or carga.get("cuerpo") or "").strip()
        texto = f"{asunto}\n\n{cuerpo}".strip() if asunto else cuerpo
        nombre = str(carga.get("nombre") or "").strip() or None
        return ContactoEntrante(
            cliente_id=cliente_id,
            canal=Canal.CORREO,
            contacto=direccion,
            texto=texto,
            nombre=nombre,
            fuente=str(carga.get("fuente", "Correo")),
            metadatos={"asunto": asunto, "message_id": carga.get("message_id")},
        )

    def enviar(self, destino: str, texto: str, asunto: str = "Seguimiento a su solicitud") -> None:
        host, usuario, password = self.host, self.usuario, self.password
        if not (host and usuario and password and self.remitente):
            raise RuntimeError("Faltan credenciales SMTP (SMTP_HOST/USER/PASSWORD/FROM)")
        mensaje = EmailMessage()
        mensaje["From"] = self.remitente
        mensaje["To"] = destino
        mensaje["Subject"] = asunto
        mensaje.set_content(texto)
        with smtplib.SMTP(host, self.puerto, timeout=self.timeout_seconds) as servidor:
            servidor.starttls()
            servidor.login(usuario, password)
            servidor.send_message(mensaje)
