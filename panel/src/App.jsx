import { useEffect, useState } from "react";

import { api, borrarToken, guardarToken, leerToken } from "./api.js";
import {
  Bandeja,
  Decisiones,
  Estado,
  Estrategias,
  Laboratorio,
  Pipeline,
  Reporte,
} from "./vistas.jsx";

const VISTAS = [
  ["pipeline", "Pipeline"],
  ["bandeja", "Intervención"],
  ["estrategias", "Estrategias"],
  ["decisiones", "Gobernanza"],
  ["sandbox", "Sandbox"],
  ["reporte", "Reporte"],
  ["estado", "Integraciones"],
];

function Acceso({ alEntrar }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");

  const entrar = async () => {
    guardarToken(token);
    try {
      await api.clientes();
      alEntrar();
    } catch (exc) {
      borrarToken();
      setError(exc.message || "Token invalido");
    }
  };

  return (
    <div className="acceso">
      <h1 className="marca">HERMES</h1>
      <p className="tenue">Panel de operación · DEUS</p>
      {error && <div className="aviso">{error}</div>}
      <input
        type="password"
        placeholder="Token del panel"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && entrar()}
      />
      <button className="principal" style={{ marginTop: 12 }} onClick={entrar}>
        Entrar
      </button>
    </div>
  );
}

export default function App() {
  const [autenticado, setAutenticado] = useState(Boolean(leerToken()));
  const [clientes, setClientes] = useState([]);
  const [clienteId, setClienteId] = useState("");
  const [estado, setEstado] = useState(null);
  const [vista, setVista] = useState("pipeline");
  const [error, setError] = useState("");

  const alError = (exc) => setError(exc.message || String(exc));

  useEffect(() => {
    if (!autenticado) return;
    api
      .clientes()
      .then((lista) => {
        setClientes(lista);
        setClienteId((actual) => actual || lista[0]?.cliente_id || "");
      })
      .catch(alError);
    api.estado().then(setEstado).catch(alError);
  }, [autenticado]);

  if (!autenticado) return <Acceso alEntrar={() => setAutenticado(true)} />;

  const cliente = clientes.find((c) => c.cliente_id === clienteId);
  const propiedades = { clienteId, cliente, alError };

  return (
    <div className="disposicion">
      <aside className="lateral">
        <h1 className="marca">HERMES</h1>
        <select value={clienteId} onChange={(e) => setClienteId(e.target.value)}>
          {clientes.map((c) => (
            <option key={c.cliente_id} value={c.cliente_id}>
              {c.nombre_negocio}
            </option>
          ))}
          {clientes.length === 0 && <option value="">Sin clientes dados de alta</option>}
        </select>
        <nav>
          {VISTAS.map(([clave, rotulo]) => (
            <button
              key={clave}
              className={vista === clave ? "activa" : ""}
              onClick={() => {
                setError("");
                setVista(clave);
              }}
            >
              {rotulo}
            </button>
          ))}
        </nav>
        <button
          style={{ marginTop: 20 }}
          onClick={() => {
            borrarToken();
            setAutenticado(false);
          }}
        >
          Salir
        </button>
      </aside>
      <main className="contenido">
        {error && <div className="aviso">{error}</div>}
        {!clienteId && vista !== "estado" ? (
          <p className="tenue">
            No hay clientes dados de alta. Usa POST /v1/onboarding para registrar el primero.
          </p>
        ) : (
          <>
            {vista === "pipeline" && <Pipeline {...propiedades} />}
            {vista === "bandeja" && <Bandeja {...propiedades} />}
            {vista === "estrategias" && <Estrategias {...propiedades} />}
            {vista === "decisiones" && <Decisiones {...propiedades} />}
            {vista === "sandbox" && <Laboratorio {...propiedades} />}
            {vista === "reporte" && <Reporte {...propiedades} />}
          </>
        )}
        {vista === "estado" && <Estado estado={estado} />}
      </main>
    </div>
  );
}
