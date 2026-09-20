"""Canal WhatsApp via Twilio (Seccion 5.8).

Twilio entrega el webhook como formulario `application/x-www-form-urlencoded`
con las claves From/To/Body y firma la peticion con HMAC-SHA1 sobre la URL
completa mas los parametros POST ordenados alfabeticamente y concatenados
(documentacion de seguridad de Twilio). Esa firma viaja en `X-Twilio-Signature`
y aqui se valida antes de tocar el CRM.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from base64 import b64encode
from collections.abc import Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import Canal
from ..pipeline import ContactoEntrante
from ..resilience import redactar, reintentar


class EnvioError(RuntimeError):
    pass


class FirmaInvalidaError(RuntimeError):
    pass


def firmar(auth_token: str, url: str, parametros: Mapping[str, str]) -> str:
    """Reproduce la firma de Twilio: URL + pares clave/valor ordenados, HMAC-SHA1."""
    cadena = url + "".join(
        f"{clave}{parametros[clave]}" for clave in sorted(parametros) if parametros[clave] is not None
    )
    digest = hmac.new(auth_token.encode("utf-8"), cadena.encode("utf-8"), hashlib.sha1).digest()
    return b64encode(digest).decode("utf-8")


class AdaptadorWhatsApp:
    canal = Canal.WHATSAPP

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        numero_remitente: str | None = None,
        timeout_seconds: int = 10,
        validar_firmas: bool | None = None,
    ) -> None:
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.numero_remitente = numero_remitente or os.environ.get("TWILIO_WHATSAPP_FROM")
        self.timeout_seconds = timeout_seconds
        if validar_firmas is None:
            validar_firmas = os.environ.get("TWILIO_VALIDAR_FIRMA", "true").lower() == "true"
        self.validar_firmas = validar_firmas

    @property
    def configurado(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.numero_remitente)

    # --- entrada ---------------------------------------------------------
    def verificar_firma(self, url: str, parametros: Mapping[str, str], firma: str | None) -> None:
        """Sin token configurado no hay verificacion posible: se exige explicitamente."""
        if not self.validar_firmas:
            return
        if not self.auth_token:
            raise FirmaInvalidaError(
                "TWILIO_AUTH_TOKEN no esta configurado y la validacion de firma esta activa"
            )
        if not firma:
            raise FirmaInvalidaError("Falta la cabecera X-Twilio-Signature")
        if not hmac.compare_digest(firmar(self.auth_token, url, parametros), firma):
            raise FirmaInvalidaError("La firma X-Twilio-Signature no coincide")

    @staticmethod
    def identificador_evento(carga: Mapping[str, str]) -> str | None:
        """MessageSid: clave de idempotencia cuando Twilio reintenta el webhook."""
        valor = carga.get("MessageSid") or carga.get("SmsMessageSid")
        return str(valor) if valor else None

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
            metadatos={"message_sid": self.identificador_evento(carga)},
        )

    # --- salida ----------------------------------------------------------
    def enviar(self, destino: str, texto: str) -> dict:
        if not self.configurado:
            raise EnvioError("Faltan credenciales de Twilio (TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM)")
        return reintentar("twilio.messages", lambda: self._enviar_una_vez(destino, texto))

    def _enviar_una_vez(self, destino: str, texto: str) -> dict:
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
        try:
            with urlopen(peticion, timeout=self.timeout_seconds) as respuesta:
                cuerpo = json.loads(respuesta.read().decode("utf-8"))
        except HTTPError as exc:
            detalle = exc.read().decode("utf-8", "ignore")[:300]
            raise EnvioError(f"Twilio respondio {exc.code}: {redactar(detalle)}") from None
        except Exception as exc:  # noqa: BLE001 - red, DNS, timeout
            raise EnvioError(f"Fallo de red hacia Twilio: {redactar(str(exc))}") from None
        if cuerpo.get("error_code"):
            raise EnvioError(
                f"Twilio rechazo el mensaje ({cuerpo.get('error_code')}): "
                f"{cuerpo.get('error_message', '')}"
            )
        return cuerpo
