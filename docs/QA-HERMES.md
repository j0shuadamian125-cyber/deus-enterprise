# HERMES — QA y auditoria tecnica

Estado del documento: auditoria ejecutada sobre la rama `devin/1789602623-hermes-modulo-multicanal`
(PR #1). En el momento de escribirlo el PR seguia **abierto**: `main` solo contiene
`README.md`, por lo que la validacion es **pre-merge**, no post-merge.

## 1. Comandos ejecutados y resultado

| Comando | Resultado |
| --- | --- |
| `pip install --user -r requirements-dev.txt` | OK |
| `cd panel && npm ci` | OK (lockfile sincronizado) |
| `cd panel && npm run build` | OK (`panel/dist`) |
| `python -m pytest -q` | 80 passed |
| `python -m ruff check .` | Sin hallazgos |
| `python -m mypy hermes --ignore-missing-imports` | Sin errores |

El E2E de recuperacion del panel contra el backend real (ejecutado sobre `db4aab8`)
termino con **7 comprobaciones pasadas**; ese resultado se conserva sin modificar.

## 2. Hallazgos de la auditoria

| # | Area | Hallazgo | Severidad | Estado |
| --- | --- | --- | --- | --- |
| 1 | Autenticacion | Sin `HERMES_PANEL_TOKEN` la API se servia abierta (fail-open) | Critico | Corregido: `503` salvo `HERMES_PERMITIR_SIN_TOKEN=true` |
| 2 | Autenticacion | Igual para los webhooks sin firma (correo, voz y WhatsApp con firma desactivada) | Critico | Corregido |
| 3 | Autenticacion | Comparacion de tokens no constante en tiempo | Medio | Corregido con `compare_digest` |
| 4 | Configuracion | Las variables de entorno se leian al importar el modulo: un despliegue que exporta variables despues del import se quedaba sin token | Alto | Corregido con `default_factory` por instancia |
| 5 | Idempotencia | Entre "consultar evento" y "marcar evento" habia una ventana de carrera: dos webhooks simultaneos podian crear leads gemelos | Alto | Corregido con candado por tenant |
| 6 | Concurrencia | Los webhooks ejecutaban trabajo sincrono (SQLite, HTTP saliente) dentro del bucle async, bloqueando al resto | Alto | Corregido con `run_in_threadpool` |
| 7 | Abuso | `LimitadorTasa` existia pero ningun endpoint lo usaba | Alto | Corregido: limite por tenant y canal (`HERMES_LIMITE_WEBHOOK`, `429`) |
| 8 | Persistencia | SQLite sin WAL ni `busy_timeout`: el worker IMAP en otro proceso provoca `database is locked` | Medio | Corregido (WAL + `busy_timeout=5000`) |
| 9 | Autorizacion | `/v1/clientes`, `/v1/decisiones` y `/v1/memoria` son vistas globales del operador de DEUS, no tenant-scoped; hoy las protege el mismo token de panel | Medio | Corregido: rol cliente con token por tenant (hash SHA-256, revocable), lista cerrada `RUTAS_CLIENTE` y 403 en todo lo demas; `tests/test_roles.py` recorre todas las rutas |
| 10 | Escala | Candado de idempotencia y limite de tasa son **por proceso**; con varias instancias del API no coordinan | Medio | Documentado como limitacion |
| 11 | Reportes | El PDF no incluye graficos | Bajo | Pendiente (mejora) |

Areas auditadas sin hallazgos nuevos: aislamiento multi-tenant en storage y API
(toda consulta exige `cliente_id`, folios tenant-scoped, `404` explicito para tenant
inexistente, vistas del panel remontadas con `key={clienteId}`), pipeline y
persistencia de mensajes, gobernanza (el silencio nunca aprueba; Nivel 4 exige
segunda confirmacion), aprendizaje (solo propone, nunca activa por su cuenta;
nunca cruza datos entre tenants), sandbox (usa el nucleo productivo) y reportes
(solo conteos reales, oportunidades marcadas como potenciales).

## 3. Estado real de los canales

| Canal | Estado |
| --- | --- |
| Claude / LLM | Implementado — requiere `ANTHROPIC_API_KEY`. No probado contra Anthropic. |
| WhatsApp / Twilio | Implementado (webhook firmado, idempotencia por `MessageSid`, envio) — no probado contra Twilio. |
| Correo SMTP/IMAP | Implementado (worker IMAP, idempotencia por `Message-ID`) — no probado contra un buzon real. |
| Voz / Twilio | Implementado (webhook de fin de llamada y transcripcion) — no probado con proveedor. |

Ningun canal externo puede declararse funcional: solo existe la implementacion local.

## 4. Limitaciones vigentes

- SQLite (WAL) y workers por proceso; sin colas distribuidas.
- Idempotencia y limite de tasa coordinados solo dentro de un proceso.
- Escala no medida: no se afirma ninguna cifra de concurrencia.
- PDF sin graficos.
- Solo dos roles: operador de DEUS y cliente (dueño del tenant). No hay roles para empleados o vendedores del cliente.
- El token de cliente viaja en cabecera: en produccion exige HTTPS.

## 5. Requisitos para una prueba controlada de WhatsApp

No se ha conectado ninguna credencial. Para iniciar el piloto de un solo canal:

**Cuenta y configuracion**
- Cuenta Twilio con remitente de WhatsApp aprobado (sandbox o numero productivo).
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`.
- `TWILIO_VALIDAR_FIRMA=true` (obligatorio en produccion).
- `HERMES_URL_PUBLICA` con la URL HTTPS **exacta** del webhook: Twilio firma sobre ella.
- `HERMES_PANEL_TOKEN` productivo y `HERMES_WEBHOOK_TOKEN` para correo/voz.
- `HERMES_DB` en una ruta persistente fuera del repositorio.
- `HERMES_ENVIO_REAL=false` en la primera pasada (dry run: registra y no entrega).

**Plan de prueba**
1. Dry run: mensaje real entrante, respuesta generada y registrada sin entregar.
2. Firma valida y firma invalida (debe devolver `403`/`401`).
3. `MessageSid` duplicado: un solo lead y una sola respuesta.
4. Enrutado por tenant: el `cliente_id` de la URL decide; verificar que no cruza datos.
5. `HERMES_ENVIO_REAL=true` con un unico numero de prueba.
6. Fallo de entrega y timeout del proveedor: el mensaje queda `FALLIDA` con el error, sin romper el pipeline.
7. Superar `HERMES_LIMITE_WEBHOOK` y comprobar el `429`.

**Riesgos**: mensajes reales a clientes finales durante el dry run mal configurado,
plantillas de WhatsApp no aprobadas, coste por mensaje, y la URL publica debe
coincidir exactamente o toda firma fallara.

**Rollback**: `HERMES_ENVIO_REAL=false` y retirar la URL del webhook en Twilio;
los datos ya registrados permanecen en la base.

**Observabilidad**: estado de entrega por mensaje en el CRM, bitacora de decisiones,
`GET /v1/estado` para ver que hay configurado y los avisos de `logger` del worker.
