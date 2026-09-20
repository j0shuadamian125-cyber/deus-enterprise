// Cliente HTTP del panel. El token del panel vive solo en memoria/sessionStorage
// del operador; ninguna credencial de proveedor llega nunca al navegador.
const BASE = import.meta.env.VITE_HERMES_API || "";

export class ErrorApi extends Error {
  constructor(estado, detalle) {
    super(detalle);
    this.estado = estado;
  }
}

export function leerToken() {
  return sessionStorage.getItem("hermes_panel_token") || "";
}

export function guardarToken(token) {
  sessionStorage.setItem("hermes_panel_token", token);
}

export function borrarToken() {
  sessionStorage.removeItem("hermes_panel_token");
}

async function peticion(ruta, { metodo = "GET", cuerpo } = {}) {
  const respuesta = await fetch(`${BASE}${ruta}`, {
    method: metodo,
    headers: {
      "Content-Type": "application/json",
      "X-Panel-Token": leerToken(),
    },
    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
  const texto = await respuesta.text();
  const datos = texto ? JSON.parse(texto) : null;
  if (!respuesta.ok) {
    throw new ErrorApi(respuesta.status, datos?.detail || respuesta.statusText);
  }
  return datos;
}

export const api = {
  estado: () => peticion("/v1/estado"),
  clientes: () => peticion("/v1/clientes"),
  cliente: (id) => peticion(`/v1/clientes/${id}`),
  leads: (id) => peticion(`/v1/clientes/${id}/leads`),
  lead: (id, leadId) => peticion(`/v1/clientes/${id}/leads/${leadId}`),
  seguimiento: (id, leadId) =>
    peticion(`/v1/clientes/${id}/leads/${leadId}/seguimiento`, { metodo: "POST" }),
  cierre: (id, leadId, resultado) =>
    peticion(`/v1/clientes/${id}/leads/${leadId}/cierre?resultado=${resultado}`, {
      metodo: "POST",
    }),
  reporte: (id) => peticion(`/v1/clientes/${id}/reporte`),
  resultados: (id) => peticion(`/v1/clientes/${id}/resultados`),
  escalamientos: (id, estado) =>
    peticion(`/v1/clientes/${id}/escalamientos${estado ? `?estado=${estado}` : ""}`),
  escalamiento: (id, escId) => peticion(`/v1/clientes/${id}/escalamientos/${escId}`),
  intervenir: (id, escId, cuerpo) =>
    peticion(`/v1/clientes/${id}/escalamientos/${escId}/intervenir`, {
      metodo: "POST",
      cuerpo,
    }),
  estrategias: (id) => peticion(`/v1/clientes/${id}/estrategias`),
  crearEstrategia: (id, cuerpo) =>
    peticion(`/v1/clientes/${id}/estrategias`, { metodo: "POST", cuerpo }),
  activarEstrategia: (id, estrategiaId, decisionId) =>
    peticion(`/v1/clientes/${id}/estrategias/${estrategiaId}/activar`, {
      metodo: "POST",
      cuerpo: { decision_id: decisionId },
    }),
  patrones: (id) => peticion(`/v1/clientes/${id}/patrones`),
  detectarPatrones: (id) =>
    peticion(`/v1/clientes/${id}/patrones/detectar`, { metodo: "POST" }),
  proponerPatron: (id, patronId) =>
    peticion(`/v1/clientes/${id}/patrones/${patronId}/proponer`, { metodo: "POST" }),
  decisiones: (id, estado) =>
    peticion(`/v1/decisiones?cliente_id=${id}${estado ? `&estado=${estado}` : ""}`),
  aprobar: (decisionId, aprobadoPor) =>
    peticion(`/v1/decisiones/${decisionId}/aprobar`, {
      metodo: "POST",
      cuerpo: { aprobado_por: aprobadoPor },
    }),
  rechazar: (decisionId, rechazadoPor, motivo) =>
    peticion(`/v1/decisiones/${decisionId}/rechazar`, {
      metodo: "POST",
      cuerpo: { rechazado_por: rechazadoPor, motivo },
    }),
  escenarios: () => peticion("/v1/sandbox/escenarios"),
  sandbox: (id, escenario) =>
    peticion(`/v1/clientes/${id}/sandbox`, { metodo: "POST", cuerpo: { escenario } }),
  sandboxTodos: (id) => peticion(`/v1/clientes/${id}/sandbox/todos`, { metodo: "POST" }),
  onboarding: (cuerpo) => peticion("/v1/onboarding", { metodo: "POST", cuerpo }),
};
