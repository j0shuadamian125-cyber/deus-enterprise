import { useEffect, useState } from "react";

import { api, borrarToken, guardarToken, leerToken } from "./api.js";
import {
  Accesos,
  Bandeja,
  Decisiones,
  Estado,
  Estrategias,
  Laboratorio,
  Pipeline,
  Reporte,
} from "./vistas.jsx";

const VISTAS_OPERADOR = [
  ["pipeline", "Pipeline"],
  ["bandeja", "Intervención"],
  ["estrategias", "Estrategias"],
  ["decisiones", "Gobernanza"],
  ["sandbox", "Sandbox"],
  ["reporte", "Reporte"],
  ["accesos", "Accesos"],
  ["estado", "Integraciones"],
];

// Reflejo de RUTAS_CLIENTE del backend; la restriccion real la impone la API.
const VISTAS_CLIENTE = [
  ["pipeline", "Pipeline"],
  ["bandeja", "Intervención"],
  ["estrategias", "Estrategias"],
  ["decisiones", "Aprobaciones"],
  ["reporte", "Reporte"],
];

// Reflejo de RUTAS_EMPLEADO: solo la cola de intervencion humana.
const VISTAS_EMPLEADO = [["bandeja", "Intervención"]];

function Acceso({ alEntrar }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");

  const entrar = async () => {
    guardarToken(token);
    try {
      await api.sesion();
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
  const [sesion, setSesion] = useState(null);
  const [clientes, setClientes] = useState([]);
  const [clienteId, setClienteId] = useState("");
  const [estado, setEstado] = useState(null);
  const [vista, setVista] = useState("pipeline");
  const [error, setError] = useState("");
  const [clientesCargados, setClientesCargados] = useState(false);
  const [intento, setIntento] = useState(0);

  const alError = (exc) => setError(exc.message || String(exc));

  useEffect(() => {
    if (!autenticado) return;
    let vigente = true;
    setError("");
    api
      .sesion()
      .then(async (actual) => {
        if (!vigente) return;
        setSesion(actual);
        if (actual.rol === "empleado") {
          setClientes([{ cliente_id: actual.cliente_id, nombre_negocio: actual.nombre_negocio }]);
          setClientesCargados(true);
          setClienteId(actual.cliente_id);
          setVista("bandeja");
          return;
        }
        if (actual.rol === "cliente") {
          const propio = await api.cliente(actual.cliente_id);
          if (!vigente) return;
          setClientes([propio]);
          setClientesCargados(true);
          setClienteId(actual.cliente_id);
          return;
        }
        const lista = await api.clientes();
        if (!vigente) return;
        setClientes(lista);
        setClientesCargados(true);
        setClienteId((previo) => previo || lista[0]?.cliente_id || "");
        const integraciones = await api.estado();
        if (vigente) setEstado(integraciones);
      })
      .catch((exc) => {
        if (!vigente) return;
        setClientesCargados(false);
        alError(exc);
      });
    return () => {
      vigente = false;
    };
  }, [autenticado, intento]);

  if (!autenticado) return <Acceso alEntrar={() => setAutenticado(true)} />;

  const esCliente = sesion?.rol === "cliente";
  const esEmpleado = sesion?.rol === "empleado";
  const vistas = esEmpleado ? VISTAS_EMPLEADO : esCliente ? VISTAS_CLIENTE : VISTAS_OPERADOR;
  const permitida = (clave) => vistas.some(([c]) => c === clave);
  const cliente = clientes.find((c) => c.cliente_id === clienteId);
  const propiedades = { clienteId, cliente, alError, esCliente };

  return (
    <div className="disposicion">
      <aside className="lateral">
        <h1 className="marca">HERMES</h1>
        {esCliente || esEmpleado ? (
          <p>{cliente?.nombre_negocio || "Cargando…"}</p>
        ) : (
          <select value={clienteId} onChange={(e) => setClienteId(e.target.value)}>
            {clientes.map((c) => (
              <option key={c.cliente_id} value={c.cliente_id}>
                {c.nombre_negocio}
              </option>
            ))}
            {clientes.length === 0 && (
              <option value="">
                {clientesCargados ? "Sin clientes dados de alta" : "Sin conexión con la API"}
              </option>
            )}
          </select>
        )}
        <nav>
          {vistas.map(([clave, rotulo]) => (
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
            setSesion(null);
            setClientes([]);
            setClienteId("");
            setVista("pipeline");
            setAutenticado(false);
          }}
        >
          Salir
        </button>
      </aside>
      <main className="contenido">
        {error && <div className="aviso">{error}</div>}
        {!clienteId && vista !== "estado" ? (
          clientesCargados ? (
            <p className="tenue">
              No hay clientes dados de alta. Usa POST /v1/onboarding para registrar el primero.
            </p>
          ) : (
            <p className="tenue">
              No se pudo cargar la lista de clientes: la API no respondió. No se muestra nada
              porque el estado real del sistema es desconocido.{" "}
              <button onClick={() => setIntento((n) => n + 1)}>Reintentar</button>
            </p>
          )
        ) : (
          <>
            {vista === "pipeline" && permitida("pipeline") && (
              <Pipeline key={clienteId} {...propiedades} />
            )}
            {vista === "bandeja" && <Bandeja key={clienteId} {...propiedades} />}
            {vista === "estrategias" && permitida("estrategias") && (
              <Estrategias key={clienteId} {...propiedades} />
            )}
            {vista === "decisiones" && permitida("decisiones") && (
              <Decisiones key={clienteId} {...propiedades} />
            )}
            {vista === "sandbox" && permitida("sandbox") && (
              <Laboratorio key={clienteId} {...propiedades} />
            )}
            {vista === "reporte" && permitida("reporte") && (
              <Reporte key={clienteId} {...propiedades} />
            )}
            {vista === "accesos" && permitida("accesos") && (
              <Accesos key={clienteId} {...propiedades} />
            )}
          </>
        )}
        {vista === "estado" && permitida("estado") && <Estado estado={estado} />}
      </main>
    </div>
  );
}
