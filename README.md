# deus-enterprise

Repositorio de DEUS. Este repositorio contiene **HERMES**, el modulo de ventas y
atencion multicanal descrito en la Seccion 5 de la Especificacion Tecnica v4.0.

DEUS es el sistema interno y privado; HERMES es el modulo que se muestra y se
vende al mercado. Ningun cliente accede al panel global ni a datos de otro
cliente.

## Que hace HERMES

- Atiende leads por **WhatsApp, correo y llamada telefonica** con un unico CRM.
- Mueve cada lead por el pipeline **Apertura -> Prospeccion -> Cierre -> Seguimiento**,
  independientemente del canal por el que escriba.
- Genera respuestas con **Claude via API** y cae a un **guion aprobado** si el LLM
  no responde o no hay credencial, de modo que ningun lead queda sin atencion.
- Aisla cada tenant por `cliente_id`: toda consulta lo exige y un `cliente_id`
  vacio levanta `AislamientoError`.
- Registra cada accion en la **Bitacora de Decisiones** con su nivel de autonomia
  (0 a 4) y alimenta la **memoria resumida** de DEUS.

## Instalacion

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Ejecucion

```bash
uvicorn hermes.main:app --reload
```

`GET /health` responde sin configuracion. El resto del panel exige la cabecera
`X-Panel-Token` cuando `HERMES_PANEL_TOKEN` esta definido.

## Variables de entorno

| Variable | Para que sirve |
| --- | --- |
| `HERMES_DB` | Ruta del archivo SQLite (por defecto `hermes.db`) |
| `HERMES_PANEL_TOKEN` | Token del panel interno; sin el, los endpoints de panel quedan abiertos |
| `HERMES_ENVIO_REAL` | `true` para entregar los mensajes por Twilio/SMTP en vez de solo registrarlos |
| `ANTHROPIC_API_KEY` | Habilita Claude; sin ella HERMES usa el guion aprobado |
| `HERMES_MODELO` | Modelo de Anthropic a usar |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` | Envio por WhatsApp |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Envio por correo |
| `NEXUS_URL`, `NEXUS_INTERNAL_API_TOKEN` | Registro del modulo y reporte de errores a NEXUS |

Ninguna credencial se guarda en el repositorio.

## Endpoints

| Metodo y ruta | Proposito |
| --- | --- |
| `GET /health` | Estado del modulo |
| `POST /v1/onboarding` | Alta de cliente con el cuestionario de la Seccion 5.7 |
| `GET /v1/clientes` | Vista global (solo operador de DEUS) |
| `POST /v1/webhooks/whatsapp/{cliente_id}` | Webhook de Twilio (form o JSON) |
| `POST /v1/webhooks/correo/{cliente_id}` | Correo entrante |
| `POST /v1/webhooks/llamada/{cliente_id}` | Fin de llamada con transcripcion |
| `GET /v1/clientes/{cliente_id}/leads` | CRM del cliente, filtrable por etapa/estatus/canal |
| `GET /v1/clientes/{cliente_id}/leads/{lead_id}` | Lead con su historial multicanal |
| `GET /v1/clientes/{cliente_id}/hoja-leads` | Exportacion con las columnas de la hoja `Leads_[cliente_id]` |
| `POST /v1/clientes/{cliente_id}/leads/{lead_id}/seguimiento` | Seguimiento manual (maximo 3) |
| `POST /v1/clientes/{cliente_id}/leads/{lead_id}/guion-llamada` | Guion para llamada asistida |
| `GET /v1/clientes/{cliente_id}/llamadas` | Llamadas y transcripciones |
| `GET /v1/clientes/{cliente_id}/reporte` | Conteos por etapa, canal y pendientes |
| `GET /v1/decisiones` | Bitacora, filtrable por cliente y estado |
| `POST /v1/decisiones/{id}/aprobar` | Aprobar (Nivel 4 exige segunda confirmacion) |
| `POST /v1/decisiones/{id}/rechazar` | Rechazar |
| `POST /v1/decisiones/caducar-nivel-4` | Caducar Nivel 4 sin segunda confirmacion |
| `GET /v1/memoria` | Memoria resumida |

## Gobernanza

- Nivel 0 lectura, Nivel 1 reversible, Nivel 2 aprobacion previa, Nivel 3 cambio
  estructural, Nivel 4 irreversible.
- Toda accion nueva nace en **Nivel 2**; solo las excepciones de bajo riesgo de la
  Seccion 4.4 (responder con guion aprobado, registrar lead, transcripcion,
  avanzar etapa, seguimiento) son Nivel 1.
- **Nivel 4** requiere aprobacion en el chat **y** una segunda confirmacion por
  WhatsApp o Telegram. Si la segunda confirmacion no llega dentro del plazo, la
  decision vuelve a `pendiente`: el silencio nunca aprueba.
- Bajar una accion a Nivel 1 exige 15 aprobaciones consecutivas sin rechazo, y
  ese cambio de nivel es en si una decision de Nivel 3.

## Llamadas y grabacion

Hay dos modalidades: `voz_ia` (la IA habla) y `asistida` (la IA prepara el guion
y una persona llama). La transcripcion siempre queda asociada al `lead_id`. La
URL de grabacion **se descarta** si el cliente no la autorizo, y el onboarding
exige registrar la jurisdiccion antes de autorizarla.

## Pruebas

```bash
python -m pytest -q
python -m ruff check .
python -m mypy hermes --ignore-missing-imports
```
