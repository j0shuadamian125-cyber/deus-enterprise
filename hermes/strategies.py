"""Estrategias comerciales versionadas y plantillas dinamicas.

Una estrategia es la forma aprobada de perseguir un objetivo en una etapa. Las
plantillas admiten variables de contexto y cada cambio crea una version nueva,
de modo que se pueden comparar resultados entre v1, v2, v3.
"""
from __future__ import annotations

from string import Template

from .models import (
    Cliente,
    EstadoEstrategia,
    Estrategia,
    Etapa,
    Lead,
    ahora,
)
from .storage import Almacen
from .tiempo import ContextoTemporal

VARIABLES_DISPONIBLES = (
    "nombre",
    "negocio",
    "saludo",
    "producto",
    "etapa",
    "dia_semana",
    "seguimientos",
)


class EstrategiaError(RuntimeError):
    pass


def variables_de_contexto(
    lead: Lead, cliente: Cliente, contexto: ContextoTemporal
) -> dict[str, str]:
    return {
        "nombre": lead.nombre or "",
        "negocio": cliente.nombre_negocio,
        "saludo": contexto.saludo,
        "producto": lead.producto_interes or "lo que necesita",
        "etapa": lead.etapa.value,
        "dia_semana": contexto.dia_semana,
        "seguimientos": str(lead.contador_seguimientos),
    }


def renderizar(plantilla: str, variables: dict[str, str]) -> str:
    """Las variables faltantes se dejan vacias: nunca se envia un '$nombre' crudo."""
    seguras = {clave: variables.get(clave, "") for clave in VARIABLES_DISPONIBLES}
    return " ".join(Template(plantilla).safe_substitute(seguras).split())


class Estrategias:
    def __init__(self, almacen: Almacen) -> None:
        self.almacen = almacen

    def crear(
        self,
        cliente_id: str,
        nombre: str,
        objetivo: str,
        etapa: Etapa,
        plantilla: str,
        condiciones: list[str] | None = None,
        evidencia: str = "",
    ) -> Estrategia:
        versiones = [
            estrategia
            for estrategia in self.almacen.listar_estrategias(cliente_id)
            if estrategia.nombre == nombre
        ]
        estrategia = Estrategia(
            estrategia_id=self.almacen.siguiente_folio(f"estrategia:{cliente_id}", "EST"),
            cliente_id=cliente_id,
            nombre=nombre,
            objetivo=objetivo,
            etapa=etapa,
            condiciones=condiciones or [],
            plantilla=plantilla,
            version=max((v.version for v in versiones), default=0) + 1,
            evidencia=evidencia,
        )
        return self.almacen.guardar_estrategia(estrategia)

    def activar(self, cliente_id: str, estrategia_id: str, decision_id: str) -> Estrategia:
        """Solo se activa despues de una decision aprobada (Seccion 20)."""
        estrategia = self.almacen.obtener_estrategia(cliente_id, estrategia_id)
        if estrategia is None:
            raise EstrategiaError(f"Estrategia inexistente para {cliente_id}: {estrategia_id}")
        for otra in self.almacen.listar_estrategias(cliente_id, etapa=estrategia.etapa):
            if otra.estado == EstadoEstrategia.ACTIVA and otra.nombre == estrategia.nombre:
                otra.estado = EstadoEstrategia.RETIRADA
                self.almacen.guardar_estrategia(otra)
        estrategia.estado = EstadoEstrategia.ACTIVA
        estrategia.decision_id = decision_id
        estrategia.fecha_activacion = ahora()
        return self.almacen.guardar_estrategia(estrategia)

    def medir(self, cliente_id: str) -> dict[str, dict[str, int]]:
        """Usos y cierres ganados por estrategia, contados sobre resultados reales."""
        medicion: dict[str, dict[str, int]] = {}
        for registro in self.almacen.listar_resultados(cliente_id):
            if not registro.estrategia_id:
                continue
            conteo = medicion.setdefault(registro.estrategia_id, {"usos": 0, "exitos": 0})
            conteo["usos"] += 1
            if registro.resultado == "ganado":
                conteo["exitos"] += 1
        return medicion

    def activa_para(
        self, cliente_id: str, etapa: Etapa, texto_lead: str = ""
    ) -> Estrategia | None:
        """La estrategia mas reciente cuyas condiciones aparecen en el mensaje."""
        candidatas = [
            estrategia
            for estrategia in self.almacen.listar_estrategias(
                cliente_id, etapa=etapa, estado=EstadoEstrategia.ACTIVA.value
            )
        ]
        if not candidatas:
            return None
        texto = texto_lead.lower()
        con_condiciones = [
            estrategia
            for estrategia in candidatas
            if estrategia.condiciones
            and any(condicion.lower() in texto for condicion in estrategia.condiciones)
        ]
        sin_condiciones = [e for e in candidatas if not e.condiciones]
        elegibles = con_condiciones or sin_condiciones
        if not elegibles:
            return None
        return max(elegibles, key=lambda e: e.version)
