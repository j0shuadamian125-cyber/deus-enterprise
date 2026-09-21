"""Capa de entrada/salida por canal (Seccion 5.2).

Cada adaptador solo traduce el formato del proveedor a `ContactoEntrante` y de
vuelta: la logica de negocio vive completa en `hermes.pipeline`.
"""
from .correo import AdaptadorCorreo
from .llamada import AdaptadorLlamada
from .whatsapp import AdaptadorWhatsApp

__all__ = ["AdaptadorWhatsApp", "AdaptadorCorreo", "AdaptadorLlamada"]
