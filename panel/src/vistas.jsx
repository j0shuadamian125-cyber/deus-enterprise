import { useEffect, useState } from "react";

import { api, descargarReportePdf } from "./api.js";

export function Estado({ estado }) {
  if (!estado) return <p className="tenue">Cargando estado…</p>;
  const filas = [
    ["Modelo LLM real (Claude)", estado.llm_real, "ANTHROPIC_API_KEY"],
    ["WhatsApp (Twilio)", estado.whatsapp_configurado, "TWILIO_ACCOUNT_SID / AUTH_TOKEN / WHATSAPP_FROM"],
    ["Correo saliente (SMTP)", estado.correo_configurado, "SMTP_HOST / SMTP_USER / SMTP_PASSWORD"],
    ["Correo entrante (IMAP)", estado.correo_recepcion_configurada, "IMAP_HOST / IMAP_BUZON"],
    ["Voz", estado.voz_configurada, "TWILIO_VOICE_FROM / TWILIO_VOICE_TWIML_URL"],
    ["Entrega real activada", estado.envio_real, "HERMES_ENVIO_REAL=true"],
    ["Firma de webhook WhatsApp", estado.validacion_firma_whatsapp, "TWILIO_VALIDAR_FIRMA"],
    ["Token de webhook correo/voz", estado.token_webhook_configurado, "HERMES_WEBHOOK_TOKEN"],
  ];
  return (
    <>
      <h2>Estado de las integraciones</h2>
      <p className="tenue">
        Lo que este despliegue tiene realmente configurado. Lo que aparece pendiente no está
        conectado: requiere configuración externa.
      </p>
      <table>
        <thead>
          <tr>
            <th>Integración</th>
            <th>Estado</th>
            <th>Variables</th>
          </tr>
        </thead>
        <tbody>
          {filas.map(([nombre, activo, variables]) => (
            <tr key={nombre}>
              <td>{nombre}</td>
              <td>
                <span className={`etiqueta ${activo ? "ok" : "pendiente"}`}>
                  {activo ? "configurado" : "requiere configuración externa"}
                </span>
              </td>
              <td className="tenue">{variables}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function Pipeline({ clienteId, alError }) {
  const [leads, setLeads] = useState([]);
  const [detalle, setDetalle] = useState(null);

  const cargar = () => api.leads(clienteId).then(setLeads).catch(alError);
  useEffect(() => {
    if (clienteId) cargar();
  }, [clienteId]);

  const abrir = (leadId) => api.lead(clienteId, leadId).then(setDetalle).catch(alError);

  const etapas = ["apertura", "prospeccion", "cierre", "seguimiento"];
  return (
    <>
      <h2>Pipeline</h2>
      <div className="tarjetas">
        {etapas.map((etapa) => (
          <div className="tarjeta" key={etapa}>
            <div className="valor">{leads.filter((l) => l.etapa === etapa).length}</div>
            <div className="rotulo">{etapa}</div>
          </div>
        ))}
      </div>
      <div className="columnas">
        <table>
          <thead>
            <tr>
              <th>Lead</th>
              <th>Etapa</th>
              <th>Canal</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.lead_id} onClick={() => abrir(lead.lead_id)} style={{ cursor: "pointer" }}>
                <td>
                  {lead.nombre || lead.contacto}
                  {lead.requiere_humano && <span className="etiqueta pendiente"> humano</span>}
                </td>
                <td>{lead.etapa}</td>
                <td>{lead.canal_origen}</td>
              </tr>
            ))}
            {leads.length === 0 && (
              <tr>
                <td colSpan={3} className="tenue">
                  Sin leads todavía para este cliente.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {detalle && (
          <div>
            <div className="fila">
              <strong>{detalle.lead.nombre || detalle.lead.contacto}</strong>
              <span className="etiqueta">{detalle.lead.estatus}</span>
              <button
                onClick={() =>
                  api.seguimiento(clienteId, detalle.lead.lead_id).then(cargar).catch(alError)
                }
              >
                Programar seguimiento
              </button>
              <button
                onClick={() =>
                  api
                    .cierre(clienteId, detalle.lead.lead_id, "ganado")
                    .then(cargar)
                    .catch(alError)
                }
              >
                Marcar ganado
              </button>
              <button
                onClick={() =>
                  api
                    .cierre(clienteId, detalle.lead.lead_id, "perdido")
                    .then(cargar)
                    .catch(alError)
                }
              >
                Marcar perdido
              </button>
            </div>
            <p className="tenue">Objetivo: {detalle.lead.objetivo_actual}</p>
            <div className="conversacion">
              {detalle.historial.map((mensaje) => (
                <p key={mensaje.mensaje_id} className={mensaje.direccion}>
                  <span className="tenue">[{mensaje.direccion}] </span>
                  {mensaje.texto}
                </p>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export function Bandeja({ clienteId, alError }) {
  const [pendientes, setPendientes] = useState([]);
  const [caso, setCaso] = useState(null);
  const [texto, setTexto] = useState("");
  const [operador, setOperador] = useState("");

  const cargar = () =>
    api.escalamientos(clienteId, "pendiente").then(setPendientes).catch(alError);
  useEffect(() => {
    if (clienteId) cargar();
  }, [clienteId]);

  const abrir = (id) =>
    api
      .escalamiento(clienteId, id)
      .then((datos) => {
        setCaso(datos);
        setTexto(datos.escalamiento.respuesta_sugerida || "");
      })
      .catch(alError);

  const intervenir = (accion) =>
    api
      .intervenir(clienteId, caso.escalamiento.escalamiento_id, {
        atendido_por: operador,
        accion,
        respuesta: accion === "tomar" ? null : texto,
        resultado: accion === "tomar" ? null : "atendido",
      })
      .then(() => {
        setCaso(null);
        cargar();
      })
      .catch(alError);

  return (
    <>
      <h2>Intervención humana</h2>
      <div className="columnas">
        <table>
          <thead>
            <tr>
              <th>Motivo</th>
              <th>Lead</th>
            </tr>
          </thead>
          <tbody>
            {pendientes.map((esc) => (
              <tr
                key={esc.escalamiento_id}
                onClick={() => abrir(esc.escalamiento_id)}
                style={{ cursor: "pointer" }}
              >
                <td>{esc.motivo}</td>
                <td>{esc.lead_id}</td>
              </tr>
            ))}
            {pendientes.length === 0 && (
              <tr>
                <td colSpan={2} className="tenue">
                  Nada pendiente de intervención.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {caso && (
          <div>
            <h3>Contexto</h3>
            <p>
              <strong>Motivo:</strong> {caso.escalamiento.motivo}
              <br />
              <strong>Objetivo:</strong> {caso.escalamiento.objetivo}
              <br />
              <strong>Recomendación de HERMES:</strong> {caso.escalamiento.recomendacion}
            </p>
            <div className="conversacion">
              {caso.historial.map((mensaje) => (
                <p key={mensaje.mensaje_id} className={mensaje.direccion}>
                  <span className="tenue">[{mensaje.direccion}] </span>
                  {mensaje.texto}
                </p>
              ))}
            </div>
            <h3>Respuesta</h3>
            <textarea rows={4} value={texto} onChange={(e) => setTexto(e.target.value)} />
            <div className="fila" style={{ marginTop: 10 }}>
              <input
                placeholder="Quién atiende"
                value={operador}
                onChange={(e) => setOperador(e.target.value)}
                style={{ maxWidth: 190 }}
              />
              <button
                className="principal"
                disabled={!operador}
                onClick={() => intervenir("aprobar")}
              >
                Aprobar sugerida
              </button>
              <button disabled={!operador} onClick={() => intervenir("editar")}>
                Enviar editada
              </button>
              <button disabled={!operador} onClick={() => intervenir("tomar")}>
                Tomar conversación
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export function Estrategias({ clienteId, alError }) {
  const [estrategias, setEstrategias] = useState([]);
  const [patrones, setPatrones] = useState([]);
  const [decisiones, setDecisiones] = useState([]);
  const [borrador, setBorrador] = useState({
    nombre: "",
    objetivo: "",
    etapa: "apertura",
    plantilla: "",
    evidencia: "",
  });

  const cargar = () => {
    api.estrategias(clienteId).then(setEstrategias).catch(alError);
    api.patrones(clienteId).then(setPatrones).catch(alError);
    api.decisiones(clienteId, "pendiente").then(setDecisiones).catch(alError);
  };
  useEffect(() => {
    if (clienteId) cargar();
  }, [clienteId]);

  const decisionDe = (estrategiaId) =>
    decisiones.find((d) => d.payload?.estrategia_id === estrategiaId);

  return (
    <>
      <h2>Estrategias y aprendizaje</h2>
      <p className="tenue">
        Una estrategia nueva nace propuesta: se activa sólo con una decisión aprobada.
      </p>
      <table>
        <thead>
          <tr>
            <th>Nombre</th>
            <th>Etapa</th>
            <th>v</th>
            <th>Estado</th>
            <th>Usos / éxitos</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {estrategias.map((estrategia) => {
            const decision = decisionDe(estrategia.estrategia_id);
            return (
              <tr key={estrategia.estrategia_id}>
                <td>{estrategia.nombre}</td>
                <td>{estrategia.etapa}</td>
                <td>{estrategia.version}</td>
                <td>
                  <span
                    className={`etiqueta ${estrategia.estado === "activa" ? "ok" : "pendiente"}`}
                  >
                    {estrategia.estado}
                  </span>
                </td>
                <td>
                  {estrategia.usos} / {estrategia.exitos}
                </td>
                <td>
                  {estrategia.estado !== "activa" && (
                    <button
                      disabled={!decision || decision.estado !== "aprobada"}
                      title={
                        decision && decision.estado !== "aprobada"
                          ? "Requiere aprobar la decisión en Gobernanza"
                          : ""
                      }
                      onClick={() =>
                        api
                          .activarEstrategia(
                            clienteId,
                            estrategia.estrategia_id,
                            decision.decision_id,
                          )
                          .then(cargar)
                          .catch(alError)
                      }
                    >
                      Activar
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {estrategias.length === 0 && (
            <tr>
              <td colSpan={6} className="tenue">
                Sin estrategias registradas.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h3>Nueva estrategia</h3>
      <div className="fila">
        <input
          placeholder="Nombre"
          value={borrador.nombre}
          onChange={(e) => setBorrador({ ...borrador, nombre: e.target.value })}
          style={{ maxWidth: 200 }}
        />
        <input
          placeholder="Objetivo"
          value={borrador.objetivo}
          onChange={(e) => setBorrador({ ...borrador, objetivo: e.target.value })}
          style={{ maxWidth: 220 }}
        />
        <select
          value={borrador.etapa}
          onChange={(e) => setBorrador({ ...borrador, etapa: e.target.value })}
          style={{ maxWidth: 160 }}
        >
          {["apertura", "prospeccion", "cierre", "seguimiento"].map((etapa) => (
            <option key={etapa} value={etapa}>
              {etapa}
            </option>
          ))}
        </select>
      </div>
      <textarea
        rows={3}
        placeholder="Plantilla de respuesta"
        value={borrador.plantilla}
        onChange={(e) => setBorrador({ ...borrador, plantilla: e.target.value })}
      />
      <div className="fila" style={{ marginTop: 10 }}>
        <button
          className="principal"
          disabled={!borrador.nombre || !borrador.plantilla}
          onClick={() =>
            api
              .crearEstrategia(clienteId, borrador)
              .then(() => {
                setBorrador({ ...borrador, nombre: "", plantilla: "" });
                cargar();
              })
              .catch(alError)
          }
        >
          Proponer estrategia
        </button>
      </div>

      <h3>Patrones detectados</h3>
      <div className="fila">
        <button onClick={() => api.detectarPatrones(clienteId).then(cargar).catch(alError)}>
          Detectar patrones ahora
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Patrón</th>
            <th>Muestras</th>
            <th>Estado</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {patrones.map((patron) => (
            <tr key={patron.patron_id}>
              <td>{patron.descripcion}</td>
              <td>{patron.muestras}</td>
              <td>
                <span className={`etiqueta ${patron.decision_id ? "pendiente" : ""}`}>
                  {patron.decision_id ? "propuesto, en revisión" : "detectado"}
                </span>
              </td>
              <td>
                {!patron.decision_id && (
                  <button
                    onClick={() =>
                      api.proponerPatron(clienteId, patron.patron_id).then(cargar).catch(alError)
                    }
                  >
                    Proponer cambio
                  </button>
                )}
              </td>
            </tr>
          ))}
          {patrones.length === 0 && (
            <tr>
              <td colSpan={4} className="tenue">
                Sin patrones con muestra suficiente todavía.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

export function Decisiones({ clienteId, alError }) {
  const [decisiones, setDecisiones] = useState([]);
  const [operador, setOperador] = useState("");

  const cargar = () => api.decisiones(clienteId, "pendiente").then(setDecisiones).catch(alError);
  useEffect(() => {
    if (clienteId) cargar();
  }, [clienteId]);

  return (
    <>
      <h2>Gobernanza</h2>
      <div className="fila">
        <input
          placeholder="Quién aprueba"
          value={operador}
          onChange={(e) => setOperador(e.target.value)}
          style={{ maxWidth: 200 }}
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>Nivel</th>
            <th>Acción</th>
            <th>Descripción</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {decisiones.map((decision) => (
            <tr key={decision.decision_id}>
              <td>{decision.nivel}</td>
              <td>{decision.accion}</td>
              <td>{decision.descripcion}</td>
              <td>
                <button
                  disabled={!operador}
                  onClick={() => api.aprobar(decision.decision_id, operador).then(cargar).catch(alError)}
                >
                  Aprobar
                </button>{" "}
                <button
                  disabled={!operador}
                  onClick={() =>
                    api.rechazar(decision.decision_id, operador, "").then(cargar).catch(alError)
                  }
                >
                  Rechazar
                </button>
              </td>
            </tr>
          ))}
          {decisiones.length === 0 && (
            <tr>
              <td colSpan={4} className="tenue">
                Sin decisiones pendientes.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

export function Laboratorio({ clienteId, cliente, alError }) {
  const [escenarios, setEscenarios] = useState([]);
  const [resultados, setResultados] = useState([]);

  useEffect(() => {
    api.escenarios().then(setEscenarios).catch(alError);
  }, []);

  if (cliente && !cliente.sandbox) {
    return (
      <>
        <h2>Sandbox</h2>
        <p className="tenue">
          Este cliente no es de pruebas. El sandbox sólo corre sobre tenants marcados como
          sandbox para no ensuciar datos reales.
        </p>
      </>
    );
  }

  return (
    <>
      <h2>Sandbox</h2>
      <p className="tenue">
        Ejecuta el mismo núcleo que producción, sin entregar mensajes a canales externos.
      </p>
      <div className="fila">
        <button
          className="principal"
          onClick={() => api.sandboxTodos(clienteId).then(setResultados).catch(alError)}
        >
          Ejecutar los {escenarios.length} escenarios
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Escenario</th>
            <th>Descripción</th>
            <th>Escaló</th>
            <th>Esperado</th>
          </tr>
        </thead>
        <tbody>
          {(resultados.length ? resultados : escenarios).map((fila) => (
            <tr key={fila.clave || fila.escenario}>
              <td>{fila.clave || fila.escenario}</td>
              <td className="tenue">{fila.descripcion}</td>
              <td>{"escalo" in fila ? String(fila.escalo) : "—"}</td>
              <td>
                {"coincide_con_lo_esperado" in fila ? (
                  <span className={`etiqueta ${fila.coincide_con_lo_esperado ? "ok" : "mal"}`}>
                    {fila.coincide_con_lo_esperado ? "coincide" : "revisar"}
                  </span>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function Reporte({ clienteId, alError }) {
  const [reporte, setReporte] = useState(null);
  const [resultados, setResultados] = useState([]);

  useEffect(() => {
    if (!clienteId) return;
    api.reporte(clienteId).then(setReporte).catch(alError);
    api.resultados(clienteId).then(setResultados).catch(alError);
  }, [clienteId]);

  if (!reporte) return <p className="tenue">Cargando reporte…</p>;

  const metricas = Object.entries(reporte).filter(([, valor]) => typeof valor === "number");
  const desgloses = Object.entries(reporte).filter(
    ([, valor]) => valor && typeof valor === "object",
  );
  return (
    <>
      <div className="fila">
        <h2>Reporte</h2>
        <button className="principal" onClick={() => descargarReportePdf(clienteId).catch(alError)}>
          Descargar PDF
        </button>
      </div>
      <p className="tenue">
        Datos reales del CRM de este cliente. No hay estimaciones: lo que no se ha medido
        aparece en cero.
      </p>
      <div className="tarjetas">
        {metricas.map(([clave, valor]) => (
          <div className="tarjeta" key={clave}>
            <div className="valor">{valor}</div>
            <div className="rotulo">{clave.replaceAll("_", " ")}</div>
          </div>
        ))}
      </div>
      {desgloses.map(([clave, valores]) => (
        <div key={clave}>
          <h3>{clave.replaceAll("_", " ")}</h3>
          <div className="tarjetas">
            {Object.entries(valores).map(([nombre, valor]) => (
              <div className="tarjeta" key={nombre}>
                <div className="valor">{valor}</div>
                <div className="rotulo">{nombre}</div>
              </div>
            ))}
          </div>
        </div>
      ))}

      <h3>Objetivo → acción → resultado</h3>
      <table>
        <thead>
          <tr>
            <th>Objetivo</th>
            <th>Acción</th>
            <th>Resultado</th>
            <th>Impacto</th>
            <th>Segundos</th>
          </tr>
        </thead>
        <tbody>
          {resultados.map((registro) => (
            <tr key={registro.resultado_id}>
              <td>{registro.objetivo}</td>
              <td>{registro.accion}</td>
              <td>{registro.resultado}</td>
              <td>{registro.impacto}</td>
              <td>{registro.segundos_hasta_resultado ?? "—"}</td>
            </tr>
          ))}
          {resultados.length === 0 && (
            <tr>
              <td colSpan={5} className="tenue">
                Sin resultados registrados todavía.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
