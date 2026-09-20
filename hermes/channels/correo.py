"""Canal correo electronico (Seccion 5.3).

Entrada: o bien un webhook del proveedor (SendGrid/Mailgun/Make) que entrega
remitente, asunto y cuerpo, o bien lectura directa por IMAP contra el buzon que
el cliente conecto en el onboarding. Salida: SMTP, para no atar el modulo a un
proveedor concreto.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
from collections.abc import Mapping
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from ..models import Canal
from ..pipeline import ContactoEntrante
from ..resilience import logger, redactar, reintentar

_DIRECCION = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class CorreoError(RuntimeError):
    pass


def extraer_direccion(remitente: str) -> str:
    coincidencia = _DIRECCION.search(remitente or "")
    if not coincidencia:
        raise ValueError(f"Remitente sin direccion valida: {remitente!r}")
    return coincidencia.group(0).lower()


def _decodificar(valor: str | None) -> str:
    if not valor:
        return ""
    return str(make_header(decode_header(valor)))


def _cuerpo_de(mensaje: email.message.Message) -> str:
    if mensaje.is_multipart():
        for parte in mensaje.walk():
            if parte.get_content_type() == "text/plain":
                carga = parte.get_payload(decode=True)
                if isinstance(carga, bytes):
                    return carga.decode(parte.get_content_charset() or "utf-8", "ignore")
        return ""
    carga = mensaje.get_payload(decode=True)
    if isinstance(carga, bytes):
        return carga.decode(mensaje.get_content_charset() or "utf-8", "ignore")
    return str(mensaje.get_payload())


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
        imap_host: str | None = None,
        imap_puerto: int = 993,
        imap_buzon: str = "INBOX",
    ) -> None:
        self.host = host or os.environ.get("SMTP_HOST")
        self.puerto = int(os.environ.get("SMTP_PORT", puerto))
        self.usuario = usuario or os.environ.get("SMTP_USER")
        self.password = password or os.environ.get("SMTP_PASSWORD")
        self.remitente = remitente or os.environ.get("SMTP_FROM") or self.usuario
        self.timeout_seconds = timeout_seconds
        self.imap_host = imap_host or os.environ.get("IMAP_HOST")
        self.imap_puerto = int(os.environ.get("IMAP_PORT", imap_puerto))
        self.imap_buzon = os.environ.get("IMAP_BUZON", imap_buzon)

    @property
    def configurado(self) -> bool:
        return bool(self.host and self.usuario and self.password and self.remitente)

    @property
    def recepcion_configurada(self) -> bool:
        return bool(self.imap_host and self.usuario and self.password)

    # --- entrada -----------------------------------------------------------
    @staticmethod
    def identificador_evento(carga: Mapping[str, Any]) -> str | None:
        valor = carga.get("message_id") or carga.get("Message-ID")
        return str(valor) if valor else None

    def normalizar(self, cliente_id: str, carga: Mapping[str, Any]) -> ContactoEntrante:
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
            metadatos={"asunto": asunto, "message_id": self.identificador_evento(carga)},
        )

    def leer_no_leidos(self, limite: int = 20) -> list[dict[str, Any]]:
        """Lee el buzon por IMAP y devuelve cargas listas para `normalizar`."""
        if not self.recepcion_configurada:
            raise CorreoError("Faltan credenciales IMAP (IMAP_HOST/SMTP_USER/SMTP_PASSWORD)")
        host, usuario, password = self.imap_host, self.usuario, self.password
        assert host and usuario and password
        cargas: list[dict[str, Any]] = []
        with imaplib.IMAP4_SSL(host, self.imap_puerto, timeout=self.timeout_seconds) as buzon:
            buzon.login(usuario, password)
            buzon.select(self.imap_buzon)
            estado, datos = buzon.search(None, "UNSEEN")
            if estado != "OK":
                raise CorreoError(f"IMAP respondio {estado} al buscar mensajes nuevos")
            identificadores = datos[0].split()[:limite]
            for identificador in identificadores:
                estado, crudo = buzon.fetch(identificador, "(RFC822)")
                if estado != "OK" or not crudo or not isinstance(crudo[0], tuple):
                    logger.warning("No se pudo leer el mensaje IMAP %s", identificador)
                    continue
                mensaje = email.message_from_bytes(crudo[0][1])
                cargas.append(
                    {
                        "from": _decodificar(mensaje.get("From")),
                        "subject": _decodificar(mensaje.get("Subject")),
                        "body": _cuerpo_de(mensaje),
                        "message_id": mensaje.get("Message-ID"),
                    }
                )
        return cargas

    # --- salida -------------------------------------------------------------
    def enviar(self, destino: str, texto: str, asunto: str = "Seguimiento a su solicitud") -> None:
        host, usuario, password = self.host, self.usuario, self.password
        if not (host and usuario and password and self.remitente):
            raise CorreoError("Faltan credenciales SMTP (SMTP_HOST/USER/PASSWORD/FROM)")

        def _enviar() -> None:
            mensaje = EmailMessage()
            mensaje["From"] = self.remitente
            mensaje["To"] = destino
            mensaje["Subject"] = asunto
            mensaje.set_content(texto)
            try:
                with smtplib.SMTP(host, self.puerto, timeout=self.timeout_seconds) as servidor:
                    servidor.starttls()
                    servidor.login(usuario, password)
                    servidor.send_message(mensaje)
            except smtplib.SMTPException as exc:
                raise CorreoError(f"SMTP rechazo el envio: {redactar(str(exc))}") from None

        reintentar("smtp.send", _enviar)
