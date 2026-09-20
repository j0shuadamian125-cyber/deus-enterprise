"""Persistencia de HERMES.

Estrategia de aislamiento 2 de la Seccion 2.3.3: una sola base con columna
cliente_id obligatoria en cada tabla. Toda lectura y escritura exige cliente_id;
`AislamientoError` se levanta si una consulta intenta omitirlo o si un registro
consultado pertenece a otro tenant (cruce de datos = Nivel 4, Seccion 2.3.1).
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from typing import Any

from .models import (
    Canal,
    Cliente,
    Decision,
    Escalamiento,
    EstadoEntrega,
    Estatus,
    Estrategia,
    Etapa,
    Lead,
    Llamada,
    MemoriaResumen,
    Mensaje,
    NivelDecision,
    Patron,
    RegistroResultado,
    ahora,
)


class AislamientoError(RuntimeError):
    """Se intento leer o escribir sin cliente_id, o cruzando tenants."""


ESQUEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    cliente_id TEXT PRIMARY KEY,
    datos TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    contacto TEXT NOT NULL,
    canal_origen TEXT NOT NULL,
    etapa TEXT NOT NULL,
    estatus TEXT NOT NULL,
    fecha_entrada TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, lead_id)
);
CREATE TABLE IF NOT EXISTS mensajes (
    mensaje_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    fecha_hora TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, mensaje_id)
);
CREATE TABLE IF NOT EXISTS llamadas (
    llamada_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    fecha_hora TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, llamada_id)
);
CREATE TABLE IF NOT EXISTS bitacora (
    decision_id TEXT PRIMARY KEY,
    cliente_id TEXT NOT NULL,
    nivel INTEGER NOT NULL,
    estado TEXT NOT NULL,
    fecha_hora TEXT NOT NULL,
    datos TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memoria_resumen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    datos TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contadores (
    nombre TEXT PRIMARY KEY,
    valor INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS eventos_procesados (
    cliente_id TEXT NOT NULL,
    canal TEXT NOT NULL,
    evento_id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    respuesta TEXT,
    PRIMARY KEY (cliente_id, canal, evento_id)
);
CREATE TABLE IF NOT EXISTS escalamientos (
    escalamiento_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    estado TEXT NOT NULL,
    fecha_creacion TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, escalamiento_id)
);
CREATE TABLE IF NOT EXISTS estrategias (
    estrategia_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    nombre TEXT NOT NULL,
    etapa TEXT NOT NULL,
    version INTEGER NOT NULL,
    estado TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, estrategia_id)
);
CREATE TABLE IF NOT EXISTS patrones (
    patron_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, patron_id)
);
CREATE TABLE IF NOT EXISTS resultados (
    resultado_id TEXT NOT NULL,
    cliente_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (cliente_id, resultado_id)
);
"""


def _volcar(modelo: Any) -> str:
    return modelo.model_dump_json()


def _exigir_cliente(cliente_id: str) -> str:
    if not cliente_id or not cliente_id.strip():
        raise AislamientoError("cliente_id es obligatorio en toda consulta de HERMES")
    return cliente_id


class Almacen:
    def __init__(self, ruta: str = ":memory:") -> None:
        self._conexion = sqlite3.connect(ruta, check_same_thread=False)
        self._conexion.row_factory = sqlite3.Row
        self._candado = threading.Lock()
        with self._candado:
            self._conexion.executescript(ESQUEMA)
            self._conexion.commit()

    def cerrar(self) -> None:
        self._conexion.close()

    def _ejecutar(self, sql: str, parametros: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._candado:
            cursor = self._conexion.execute(sql, tuple(parametros))
            self._conexion.commit()
            return cursor

    def _consultar(self, sql: str, parametros: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._candado:
            return list(self._conexion.execute(sql, tuple(parametros)))

    # --- folios -------------------------------------------------------
    def siguiente_folio(self, nombre: str, prefijo: str, ancho: int = 5) -> str:
        with self._candado:
            self._conexion.execute(
                "INSERT INTO contadores (nombre, valor) VALUES (?, 0) "
                "ON CONFLICT(nombre) DO UPDATE SET valor = valor + 1",
                (nombre,),
            )
            fila = self._conexion.execute(
                "SELECT valor FROM contadores WHERE nombre = ?", (nombre,)
            ).fetchone()
            self._conexion.commit()
        return f"{prefijo}-{fila['valor'] + 1:0{ancho}d}"

    # --- clientes -----------------------------------------------------
    def guardar_cliente(self, cliente: Cliente) -> Cliente:
        self._ejecutar(
            "INSERT INTO clientes (cliente_id, datos) VALUES (?, ?) "
            "ON CONFLICT(cliente_id) DO UPDATE SET datos = excluded.datos",
            (cliente.cliente_id, _volcar(cliente)),
        )
        return cliente

    def obtener_cliente(self, cliente_id: str) -> Cliente | None:
        _exigir_cliente(cliente_id)
        filas = self._consultar("SELECT datos FROM clientes WHERE cliente_id = ?", (cliente_id,))
        return Cliente.model_validate_json(filas[0]["datos"]) if filas else None

    def listar_clientes(self) -> list[Cliente]:
        """Vista global: exclusiva del operador de DEUS (Seccion 1.4)."""
        filas = self._consultar("SELECT datos FROM clientes")
        return [Cliente.model_validate_json(f["datos"]) for f in filas]

    # --- leads --------------------------------------------------------
    def guardar_lead(self, lead: Lead) -> Lead:
        _exigir_cliente(lead.cliente_id)
        self._ejecutar(
            "INSERT INTO leads (lead_id, cliente_id, contacto, canal_origen, etapa, estatus, "
            "fecha_entrada, datos) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cliente_id, lead_id) DO UPDATE SET contacto = excluded.contacto, "
            "canal_origen = excluded.canal_origen, etapa = excluded.etapa, "
            "estatus = excluded.estatus, datos = excluded.datos",
            (
                lead.lead_id,
                lead.cliente_id,
                lead.contacto,
                lead.canal_origen.value,
                lead.etapa.value,
                lead.estatus.value,
                lead.fecha_entrada.isoformat(),
                _volcar(lead),
            ),
        )
        return lead

    def obtener_lead(self, cliente_id: str, lead_id: str) -> Lead | None:
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM leads WHERE cliente_id = ? AND lead_id = ?", (cliente_id, lead_id)
        )
        return Lead.model_validate_json(filas[0]["datos"]) if filas else None

    def buscar_lead_por_contacto(self, cliente_id: str, contacto: str) -> Lead | None:
        """Un mismo lead avanza aunque cambie de canal (Seccion 5.5)."""
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM leads WHERE cliente_id = ? AND contacto = ? "
            "ORDER BY fecha_entrada DESC LIMIT 1",
            (cliente_id, contacto),
        )
        return Lead.model_validate_json(filas[0]["datos"]) if filas else None

    def listar_leads(
        self,
        cliente_id: str,
        etapa: Etapa | None = None,
        estatus: Estatus | None = None,
        canal_origen: Canal | None = None,
    ) -> list[Lead]:
        _exigir_cliente(cliente_id)
        sql = "SELECT datos FROM leads WHERE cliente_id = ?"
        parametros: list[Any] = [cliente_id]
        if etapa is not None:
            sql += " AND etapa = ?"
            parametros.append(etapa.value)
        if estatus is not None:
            sql += " AND estatus = ?"
            parametros.append(estatus.value)
        if canal_origen is not None:
            sql += " AND canal_origen = ?"
            parametros.append(canal_origen.value)
        sql += " ORDER BY fecha_entrada ASC"
        return [Lead.model_validate_json(f["datos"]) for f in self._consultar(sql, parametros)]

    # --- mensajes -----------------------------------------------------
    def guardar_mensaje(self, mensaje: Mensaje) -> Mensaje:
        _exigir_cliente(mensaje.cliente_id)
        self._ejecutar(
            "INSERT INTO mensajes (mensaje_id, cliente_id, lead_id, fecha_hora, datos) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                mensaje.mensaje_id,
                mensaje.cliente_id,
                mensaje.lead_id,
                mensaje.fecha_hora.isoformat(),
                _volcar(mensaje),
            ),
        )
        return mensaje

    def actualizar_entrega(
        self,
        cliente_id: str,
        mensaje_id: str,
        estado: EstadoEntrega,
        error: str | None = None,
    ) -> Mensaje | None:
        """Estado de entrega real del canal (enviada / fallida) sobre el mensaje ya guardado."""
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM mensajes WHERE cliente_id = ? AND mensaje_id = ?",
            (cliente_id, mensaje_id),
        )
        if not filas:
            return None
        mensaje = Mensaje.model_validate_json(filas[0]["datos"])
        mensaje.estado_entrega = estado
        mensaje.error_entrega = error
        self._ejecutar(
            "UPDATE mensajes SET datos = ? WHERE cliente_id = ? AND mensaje_id = ?",
            (_volcar(mensaje), cliente_id, mensaje_id),
        )
        return mensaje

    def historial(self, cliente_id: str, lead_id: str, limite: int = 20) -> list[Mensaje]:
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM mensajes WHERE cliente_id = ? AND lead_id = ? "
            "ORDER BY fecha_hora DESC, rowid DESC LIMIT ?",
            (cliente_id, lead_id, limite),
        )
        return list(reversed([Mensaje.model_validate_json(f["datos"]) for f in filas]))

    # --- llamadas -----------------------------------------------------
    def guardar_llamada(self, llamada: Llamada) -> Llamada:
        _exigir_cliente(llamada.cliente_id)
        self._ejecutar(
            "INSERT INTO llamadas (llamada_id, cliente_id, lead_id, fecha_hora, datos) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(cliente_id, llamada_id) DO UPDATE SET "
            "datos = excluded.datos",
            (
                llamada.llamada_id,
                llamada.cliente_id,
                llamada.lead_id,
                llamada.fecha_hora.isoformat(),
                _volcar(llamada),
            ),
        )
        return llamada

    def listar_llamadas(self, cliente_id: str, lead_id: str | None = None) -> list[Llamada]:
        _exigir_cliente(cliente_id)
        sql = "SELECT datos FROM llamadas WHERE cliente_id = ?"
        parametros: list[Any] = [cliente_id]
        if lead_id:
            sql += " AND lead_id = ?"
            parametros.append(lead_id)
        sql += " ORDER BY fecha_hora ASC"
        return [Llamada.model_validate_json(f["datos"]) for f in self._consultar(sql, parametros)]

    # --- bitacora -----------------------------------------------------
    def guardar_decision(self, decision: Decision) -> Decision:
        _exigir_cliente(decision.cliente_id)
        self._ejecutar(
            "INSERT INTO bitacora (decision_id, cliente_id, nivel, estado, fecha_hora, datos) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(decision_id) DO UPDATE SET "
            "estado = excluded.estado, datos = excluded.datos",
            (
                decision.decision_id,
                decision.cliente_id,
                int(decision.nivel),
                decision.estado.value,
                decision.fecha_hora.isoformat(),
                _volcar(decision),
            ),
        )
        return decision

    def obtener_decision(self, decision_id: str) -> Decision | None:
        filas = self._consultar("SELECT datos FROM bitacora WHERE decision_id = ?", (decision_id,))
        return Decision.model_validate_json(filas[0]["datos"]) if filas else None

    def listar_decisiones(
        self,
        cliente_id: str | None = None,
        estado: str | None = None,
        nivel: NivelDecision | None = None,
    ) -> list[Decision]:
        sql = "SELECT datos FROM bitacora WHERE 1 = 1"
        parametros: list[Any] = []
        if cliente_id is not None:
            sql += " AND cliente_id = ?"
            parametros.append(cliente_id)
        if estado is not None:
            sql += " AND estado = ?"
            parametros.append(estado)
        if nivel is not None:
            sql += " AND nivel = ?"
            parametros.append(int(nivel))
        sql += " ORDER BY fecha_hora ASC"
        return [Decision.model_validate_json(f["datos"]) for f in self._consultar(sql, parametros)]

    # --- memoria ------------------------------------------------------
    def guardar_memoria(self, entrada: MemoriaResumen) -> MemoriaResumen:
        _exigir_cliente(entrada.cliente_id)
        self._ejecutar(
            "INSERT INTO memoria_resumen (cliente_id, fecha, datos) VALUES (?, ?, ?)",
            (entrada.cliente_id, entrada.fecha.isoformat(), _volcar(entrada)),
        )
        return entrada

    def listar_memoria(self, cliente_id: str | None = None, limite: int = 60) -> list[MemoriaResumen]:
        sql = "SELECT datos FROM memoria_resumen WHERE 1 = 1"
        parametros: list[Any] = []
        if cliente_id is not None:
            sql += " AND cliente_id = ?"
            parametros.append(cliente_id)
        sql += " ORDER BY fecha DESC, id DESC LIMIT ?"
        parametros.append(limite)
        filas = self._consultar(sql, parametros)
        return list(reversed([MemoriaResumen.model_validate_json(f["datos"]) for f in filas]))

    # --- idempotencia de webhooks ---------------------------------------
    def evento_ya_procesado(self, cliente_id: str, canal: str, evento_id: str) -> str | None:
        """Devuelve la respuesta guardada si el proveedor reenvia el mismo evento."""
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT respuesta FROM eventos_procesados WHERE cliente_id = ? AND canal = ? "
            "AND evento_id = ?",
            (cliente_id, canal, evento_id),
        )
        return filas[0]["respuesta"] if filas else None

    def marcar_evento(
        self, cliente_id: str, canal: str, evento_id: str, respuesta: str | None = None
    ) -> None:
        _exigir_cliente(cliente_id)
        self._ejecutar(
            "INSERT INTO eventos_procesados (cliente_id, canal, evento_id, fecha, respuesta) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(cliente_id, canal, evento_id) DO UPDATE SET "
            "respuesta = excluded.respuesta",
            (cliente_id, canal, evento_id, ahora().isoformat(), respuesta),
        )

    # --- escalamientos ----------------------------------------------------
    def guardar_escalamiento(self, escalamiento: Escalamiento) -> Escalamiento:
        _exigir_cliente(escalamiento.cliente_id)
        self._ejecutar(
            "INSERT INTO escalamientos (escalamiento_id, cliente_id, lead_id, estado, "
            "fecha_creacion, datos) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cliente_id, escalamiento_id) DO UPDATE SET estado = excluded.estado, "
            "datos = excluded.datos",
            (
                escalamiento.escalamiento_id,
                escalamiento.cliente_id,
                escalamiento.lead_id,
                escalamiento.estado.value,
                escalamiento.fecha_creacion.isoformat(),
                _volcar(escalamiento),
            ),
        )
        return escalamiento

    def obtener_escalamiento(self, cliente_id: str, escalamiento_id: str) -> Escalamiento | None:
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM escalamientos WHERE cliente_id = ? AND escalamiento_id = ?",
            (cliente_id, escalamiento_id),
        )
        return Escalamiento.model_validate_json(filas[0]["datos"]) if filas else None

    def listar_escalamientos(
        self, cliente_id: str, estado: str | None = None, lead_id: str | None = None
    ) -> list[Escalamiento]:
        _exigir_cliente(cliente_id)
        sql = "SELECT datos FROM escalamientos WHERE cliente_id = ?"
        parametros: list[Any] = [cliente_id]
        if estado is not None:
            sql += " AND estado = ?"
            parametros.append(estado)
        if lead_id is not None:
            sql += " AND lead_id = ?"
            parametros.append(lead_id)
        sql += " ORDER BY fecha_creacion ASC"
        return [
            Escalamiento.model_validate_json(f["datos"]) for f in self._consultar(sql, parametros)
        ]

    # --- estrategias -------------------------------------------------------
    def guardar_estrategia(self, estrategia: Estrategia) -> Estrategia:
        _exigir_cliente(estrategia.cliente_id)
        self._ejecutar(
            "INSERT INTO estrategias (estrategia_id, cliente_id, nombre, etapa, version, estado, "
            "datos) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cliente_id, estrategia_id) DO UPDATE SET estado = excluded.estado, "
            "datos = excluded.datos",
            (
                estrategia.estrategia_id,
                estrategia.cliente_id,
                estrategia.nombre,
                estrategia.etapa.value,
                estrategia.version,
                estrategia.estado.value,
                _volcar(estrategia),
            ),
        )
        return estrategia

    def obtener_estrategia(self, cliente_id: str, estrategia_id: str) -> Estrategia | None:
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM estrategias WHERE cliente_id = ? AND estrategia_id = ?",
            (cliente_id, estrategia_id),
        )
        return Estrategia.model_validate_json(filas[0]["datos"]) if filas else None

    def listar_estrategias(
        self, cliente_id: str, etapa: Etapa | None = None, estado: str | None = None
    ) -> list[Estrategia]:
        _exigir_cliente(cliente_id)
        sql = "SELECT datos FROM estrategias WHERE cliente_id = ?"
        parametros: list[Any] = [cliente_id]
        if etapa is not None:
            sql += " AND etapa = ?"
            parametros.append(etapa.value)
        if estado is not None:
            sql += " AND estado = ?"
            parametros.append(estado)
        sql += " ORDER BY nombre ASC, version ASC"
        return [
            Estrategia.model_validate_json(f["datos"]) for f in self._consultar(sql, parametros)
        ]

    # --- patrones ----------------------------------------------------------
    def guardar_patron(self, patron: Patron) -> Patron:
        _exigir_cliente(patron.cliente_id)
        self._ejecutar(
            "INSERT INTO patrones (patron_id, cliente_id, fecha, datos) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cliente_id, patron_id) DO UPDATE SET datos = excluded.datos",
            (
                patron.patron_id,
                patron.cliente_id,
                patron.fecha_deteccion.isoformat(),
                _volcar(patron),
            ),
        )
        return patron

    def listar_patrones(self, cliente_id: str) -> list[Patron]:
        _exigir_cliente(cliente_id)
        filas = self._consultar(
            "SELECT datos FROM patrones WHERE cliente_id = ? ORDER BY fecha ASC", (cliente_id,)
        )
        return [Patron.model_validate_json(f["datos"]) for f in filas]

    # --- resultados --------------------------------------------------------
    def guardar_resultado(self, registro: RegistroResultado) -> RegistroResultado:
        _exigir_cliente(registro.cliente_id)
        self._ejecutar(
            "INSERT INTO resultados (resultado_id, cliente_id, lead_id, fecha, datos) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(cliente_id, resultado_id) DO UPDATE SET "
            "datos = excluded.datos",
            (
                registro.resultado_id,
                registro.cliente_id,
                registro.lead_id,
                registro.fecha.isoformat(),
                _volcar(registro),
            ),
        )
        return registro

    def listar_resultados(
        self, cliente_id: str, lead_id: str | None = None
    ) -> list[RegistroResultado]:
        _exigir_cliente(cliente_id)
        sql = "SELECT datos FROM resultados WHERE cliente_id = ?"
        parametros: list[Any] = [cliente_id]
        if lead_id is not None:
            sql += " AND lead_id = ?"
            parametros.append(lead_id)
        sql += " ORDER BY fecha ASC"
        return [
            RegistroResultado.model_validate_json(f["datos"])
            for f in self._consultar(sql, parametros)
        ]

    # --- exportacion a hojas -------------------------------------------
    def exportar_hoja_leads(self, cliente_id: str) -> list[dict[str, Any]]:
        """Formato de la hoja Leads_[cliente_id] tal como la describe la Seccion 5.2.1."""
        return [
            {
                "lead_id": lead.lead_id,
                "nombre": lead.nombre,
                "contacto": lead.contacto,
                "canal_origen": lead.canal_origen.value,
                "fuente": lead.fuente,
                "mensaje_inicial": lead.mensaje_inicial,
                "fecha_entrada": lead.fecha_entrada.isoformat(),
                "estatus": lead.estatus.value,
                "etapa": lead.etapa.value,
                "ultima_respuesta_lead": lead.ultima_respuesta_lead,
                "ultimo_contacto": (
                    lead.ultimo_contacto.isoformat() if lead.ultimo_contacto else None
                ),
                "contador_seguimientos": lead.contador_seguimientos,
                "producto_interes": lead.producto_interes,
                "notas_internas": lead.notas_internas,
                "nivel_decision": int(lead.nivel_decision),
                "canal_preferido_lead": (
                    lead.canal_preferido_lead.value if lead.canal_preferido_lead else None
                ),
            }
            for lead in self.listar_leads(cliente_id)
        ]
