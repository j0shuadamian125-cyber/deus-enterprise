"""Canal WhatsApp via Twilio (Seccion 5.8).

Twilio entrega el webhook como formulario `application/x-www-form-urlencoded`
con las claves From/To/Body. Aqui solo se traduce a `ContactoEntrante`.
"""
from __future__ import annotations

import json
import os
from base64 import b64encode
from collections.abc import Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import Canal
from ..pipeline import ContactoEntrante


class EnvioError(RuntimeError):
    pass


class AdaptadorWhatsApp:
    canal = Canal.WHATSAPP

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        numero_remitente: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.numero_remitente = numero_remitente or os.environ.get("TWILIO_WHATSAPP_FROM")
        self.timeout_seconds = timeout_seconds

    @property
    def configurado(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.numero_remitente)

    def normalizar(self, cliente_id: str, carga: Mapping[str, str]) -> ContactoEntrante:
        remitente = str(carga.get("From", "")).replace("whatsapp:", "").strip()
        if not remitente:
            raise ValueError("El webhook de Twilio no trae el campo 'From'")
        return ContactoEntrante(
            cliente_id=cliente_id,
            canal=Canal.WHATSAPP,
            contacto=remitente,
            texto=str(carga.get("Body", "")).strip(),
            nombre=(str(carga.get("ProfileName", "")).strip() or None),
            fuente=str(carga.get("fuente", "WhatsApp")),
            metadatos={"message_sid": carga.get("MessageSid")},
        )

    def enviar(self, destino: str, texto: str) -> dict:
        if not self.configurado:
            raise EnvioError("Faltan credenciales de Twilio (TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM)")
        datos = urlencode(
            {
                "From": f"whatsapp:{self.numero_remitente}",
                "To": f"whatsapp:{destino}",
                "Body": texto,
            }
        ).encode("utf-8")
        credenciales = b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        peticion = Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            method="POST",
            data=datos,
            headers={
                "Authorization": f"Basic {credenciales}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
