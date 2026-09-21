"""Aprendizaje observacional por tenant.

HERMES detecta patrones y propone; no cambia reglas por su cuenta. Toda
propuesta nace como decision de Nivel 3 (cambio estructural) y el aprendizaje
de un tenant nunca se traslada a otro (Seccion 29 de la directiva).
"""
from __future__ import annotations

from collections import defaultdict

from .governance import Gobernanza
from .models import Estatus, Etapa, NivelDecision, Patron
from .storage import Almacen

MUESTRAS_MINIMAS = 5


class Aprendizaje:
    def __init__(self, almacen: Almacen, gobernanza: Gobernanza) -> None:
        self.almacen = almacen
        self.gobernanza = gobernanza

    def detectar_patrones(self, cliente_id: str) -> list[Patron]:
        """Solo con datos propios del tenant y con muestra suficiente."""
        leads = self.almacen.listar_leads(cliente_id)
        resultados = self.almacen.listar_resultados(cliente_id)
        patrones: list[Patron] = []

        if len(leads) >= MUESTRAS_MINIMAS:
            por_canal: dict[str, list[int]] = defaultdict(list)
            for lead in leads:
                por_canal[lead.canal_origen.value].append(
                    1 if lead.estatus == Estatus.CERRADO else 0
                )
            tasas = {
                canal: sum(valores) / len(valores)
                for canal, valores in por_canal.items()
                if len(valores) >= MUESTRAS_MINIMAS
            }
            if len(tasas) >= 2:
                mejor = max(tasas, key=lambda canal: tasas[canal])
                peor = min(tasas, key=lambda canal: tasas[canal])
                if tasas[mejor] - tasas[peor] >= 0.2:
                    patrones.append(
                        self._registrar(
                            cliente_id,
                            descripcion=(
                                f"Los leads que entran por {mejor} cierran mas que los de {peor}"
                            ),
                            evidencia={canal: round(tasa, 2) for canal, tasa in tasas.items()},
                            muestras=len(leads),
                            propuesta=(
                                f"Priorizar tiempo de respuesta y seguimiento en {mejor}"
                            ),
                        )
                    )

            estancados = [
                lead
                for lead in leads
                if lead.etapa == Etapa.SEGUIMIENTO and lead.contador_seguimientos >= 2
            ]
            if len(estancados) >= MUESTRAS_MINIMAS:
                patrones.append(
                    self._registrar(
                        cliente_id,
                        descripcion=(
                            "Varios leads acumulan 2 o mas seguimientos sin avanzar de etapa"
                        ),
                        evidencia={"leads_estancados": len(estancados), "total": len(leads)},
                        muestras=len(estancados),
                        propuesta=(
                            "Proponer una estrategia de recuperacion distinta al recordatorio "
                            "actual (nuevo motivo de contacto)"
                        ),
                    )
                )

        if len(resultados) >= MUESTRAS_MINIMAS:
            por_estrategia: dict[str, list[int]] = defaultdict(list)
            for registro in resultados:
                clave = registro.estrategia_id or "sin_estrategia"
                por_estrategia[clave].append(1 if registro.resultado == "ganado" else 0)
            comparables = {
                clave: sum(valores) / len(valores)
                for clave, valores in por_estrategia.items()
                if len(valores) >= MUESTRAS_MINIMAS
            }
            if len(comparables) >= 2:
                mejor = max(comparables, key=lambda clave: comparables[clave])
                patrones.append(
                    self._registrar(
                        cliente_id,
                        descripcion=f"La estrategia {mejor} muestra la mejor tasa de cierre",
                        evidencia={k: round(v, 2) for k, v in comparables.items()},
                        muestras=len(resultados),
                        propuesta=f"Proponer {mejor} como estrategia por defecto de su etapa",
                    )
                )
        return patrones

    def proponer_cambio(self, cliente_id: str, patron_id: str) -> Patron:
        """Convierte un patron en una propuesta formal de Nivel 3."""
        patrones = {p.patron_id: p for p in self.almacen.listar_patrones(cliente_id)}
        patron = patrones.get(patron_id)
        if patron is None:
            raise ValueError(f"Patron inexistente para {cliente_id}: {patron_id}")
        decision = self.gobernanza.registrar(
            cliente_id=cliente_id,
            accion="cambio_de_estrategia",
            descripcion=f"Propuesta a partir del patron {patron_id}: {patron.propuesta}",
            payload={"patron_id": patron_id, "evidencia": patron.evidencia},
            nivel=NivelDecision.CAMBIO_ESTRUCTURAL,
        )
        patron.decision_id = decision.decision_id
        return self.almacen.guardar_patron(patron)

    def _registrar(
        self,
        cliente_id: str,
        descripcion: str,
        evidencia: dict,
        muestras: int,
        propuesta: str,
    ) -> Patron:
        existentes = {p.descripcion: p for p in self.almacen.listar_patrones(cliente_id)}
        previo = existentes.get(descripcion)
        patron = Patron(
            patron_id=(
                previo.patron_id
                if previo
                else self.almacen.siguiente_folio(f"patron:{cliente_id}", "PAT")
            ),
            cliente_id=cliente_id,
            descripcion=descripcion,
            evidencia=evidencia,
            muestras=muestras,
            propuesta=propuesta,
            decision_id=previo.decision_id if previo else None,
        )
        return self.almacen.guardar_patron(patron)
