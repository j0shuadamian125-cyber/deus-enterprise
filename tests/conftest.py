from __future__ import annotations

import pytest

from hermes.channels import AdaptadorCorreo, AdaptadorLlamada, AdaptadorWhatsApp
from hermes.config import Configuracion, Servicio
from hermes.llm import GeneradorRespuestas
from hermes.models import NivelServicioLlamada, Plan
from hermes.onboarding import RespuestasOnboarding, alta_cliente
from hermes.pipeline import Hermes
from hermes.storage import Almacen


@pytest.fixture()
def almacen() -> Almacen:
    almacen = Almacen(":memory:")
    yield almacen
    almacen.cerrar()


@pytest.fixture()
def hermes(almacen: Almacen) -> Hermes:
    return Hermes(almacen, GeneradorRespuestas())


@pytest.fixture()
def servicio(almacen: Almacen, hermes: Hermes) -> Servicio:
    return Servicio(
        config=Configuracion(ruta_base_datos=":memory:", token_panel="token-de-prueba"),
        almacen=almacen,
        hermes=hermes,
        whatsapp=AdaptadorWhatsApp(validar_firmas=False),
        correo=AdaptadorCorreo(),
        llamada=AdaptadorLlamada(validar_firmas=False),
        nexus=None,
    )


def dar_de_alta(
    almacen: Almacen,
    cliente_id: str = "cli_001",
    correo: bool = True,
    llamadas: bool = True,
    grabacion: bool = False,
    plan: Plan | None = None,
):
    if plan is None:
        plan = Plan.PILOTO if (correo or llamadas) else Plan.PLAN_1
    return alta_cliente(
        almacen,
        RespuestasOnboarding(
            cliente_id=cliente_id,
            nombre_negocio=f"Negocio {cliente_id}",
            plan=plan,
            productos=["Servicio de prueba"],
            zona_horaria="America/Mexico_City",
            telefono_whatsapp="+521000000000",
            quiere_correo=correo,
            correo_conectado=f"ventas@{cliente_id}.com" if correo else None,
            quiere_llamadas=llamadas,
            nivel_servicio_llamada=NivelServicioLlamada.VOZ_IA if llamadas else None,
            telefono_llamadas="+521999999999" if llamadas else None,
            autoriza_grabacion=grabacion,
            jurisdiccion="MX" if grabacion else None,
        ),
    ).cliente
