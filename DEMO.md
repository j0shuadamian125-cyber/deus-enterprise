# DEMO de HERMES

Guía para levantar HERMES desde cero y mostrarlo en 5 minutos con el Sandbox de WhatsApp de Twilio.
Todo corre en local; ngrok publica el webhook. No hace falta hosting.

## 1. Levantar HERMES desde cero

Requisitos: Python 3.10+, Node 18+, ngrok reciente (gratis; la 3.19 fue rechazada, la 3.39 funcionó) y una cuenta de Twilio con el Sandbox de WhatsApp.

```bash
git clone https://github.com/j0shuadamian125-cyber/deus-enterprise.git
cd deus-enterprise
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
(cd panel && npm install)
```

**Terminal 1: túnel.** Usa el dominio fijo gratuito que ngrok asigna en su dashboard, sin `https://`:

```bash
ngrok config add-authtoken "$NGROK_AUTHTOKEN"
ngrok http --url="https://$NGROK_DOMAIN" 8000
```

**Terminal 2: API.** Exporta las variables de la sección 4 y luego:

```bash
export HERMES_URL_PUBLICA="https://$NGROK_DOMAIN"   # debe coincidir EXACTAMENTE con lo que configures en Twilio
export HERMES_DB="$HOME/hermes-demo.db"             # fuera del repo
export TWILIO_VALIDAR_FIRMA=true
export TWILIO_WHATSAPP_FROM="+14155238886"          # remitente del Sandbox
export HERMES_ENVIO_REAL=false                      # pásalo a true solo durante la demo
uvicorn hermes.main:app --host 127.0.0.1 --port 8000
```

**Terminal 3: panel.**

```bash
cd panel && npm run dev     # http://localhost:5173 (hace proxy a la API en :8000)
```

**Twilio Sandbox.** Ve a Console → Messaging → Try it out → *Send a WhatsApp message*.
1. Desde cada WhatsApp que vaya a escribir, manda `join <código>` al `+1 415 523 8886`.
2. En *Sandbox settings*, rellena "When a message comes in" con
   `https://<NGROK_DOMAIN>/v1/webhooks/whatsapp/demo_tienda`, método **POST**, sin parámetros al final. Guarda.

**Comprobación:**

```bash
curl -s https://$NGROK_DOMAIN/health
curl -s -H "X-Panel-Token: $HERMES_PANEL_TOKEN" http://127.0.0.1:8000/v1/estado
```

`/v1/estado` debe mostrar WhatsApp configurado y `envio_real` con el valor que esperas.

## 2. Cargar el tenant de demo

El script crea `demo_tienda` ("Muebles Aurora (demo)"), plan 1, solo WhatsApp:
- 7 leads repartidos por el pipeline: uno ganado, uno perdido y 3 casos en Intervención humana.
- Una estrategia propuesta, pendiente de que la jefa la apruebe.
- Un token de **jefe** (rol cliente) y otro de **empleado**. Se muestran **una sola vez**: cópialos.

Ejecútalo desde la raíz del repositorio, con el mismo `HERMES_DB` que usa la API. Si `demo_tienda` ya existe se detiene sin tocar nada.

```bash
HERMES_DB="$HOME/hermes-demo.db" python - <<'PY'
"""Carga el tenant de demo `demo_tienda` con leads de ejemplo en la base de HERMES_DB."""
import hashlib
import os
import secrets
import sys

from hermes.config import construir_servicio
from hermes.models import Etapa
from hermes.onboarding import RespuestasOnboarding, alta_cliente
from hermes.pipeline import ContactoEntrante

CLIENTE = "demo_tienda"
servicio = construir_servicio()
if servicio.almacen.obtener_cliente(CLIENTE) is not None:
    sys.exit(f"{CLIENTE} ya existe en {servicio.config.ruta_base_datos}; usa otra HERMES_DB.")

alta_cliente(
    servicio.almacen,
    RespuestasOnboarding(
        cliente_id=CLIENTE,
        nombre_negocio="Muebles Aurora (demo)",
        representante="Ana Jefa",
        industria="Muebles y decoracion",
        descripcion_negocio="Tienda de muebles con entrega a domicilio en la ciudad.",
        productos=["Sala modular", "Comedor 6 sillas", "Recamara matrimonial"],
        objetivos=["Agendar visitas a showroom", "Cerrar ventas por WhatsApp"],
        zona_horaria="America/Mexico_City",
        telefono_whatsapp="+14155238886",
    ),
)

CONVERSACIONES = {
    "+5215500000001": ("Laura", ["Hola, buenas tardes"]),
    "+5215500000002": ("Carlos", ["Hola, me interesa una sala modular"]),
    "+5215500000003": ("Marta", ["Quiero informacion del comedor", "Lo necesito para manana"]),
    "+5215500000004": ("Jorge", ["Me interesa la recamara", "Cuando la pueden entregar?",
                                 "De acuerdo, como pago?"]),
    "+5215500000005": ("Sofia", ["Me interesa la sala", "Quiero hablar con una persona"]),
    "+5215500000006": ("Raul", ["Precio del comedor?", "Me haces un descuento?"]),
    "+5215500000007": ("Elena", ["Me interesa la recamara", "No gracias, ya compre"]),
}
leads = {}
for contacto, (nombre, mensajes) in CONVERSACIONES.items():
    for texto in mensajes:
        r = servicio.hermes.atender(
            ContactoEntrante(CLIENTE, servicio.whatsapp.canal, contacto, texto, nombre=nombre)
        )
    leads[nombre] = r.lead.lead_id
servicio.hermes.cerrar_lead(CLIENTE, leads["Jorge"], "ganado")

estrategia = servicio.hermes.estrategias.crear(
    cliente_id=CLIENTE,
    nombre="Invitar al showroom",
    objetivo="Agendar visitas presenciales",
    etapa=Etapa.PROSPECCION,
    plantilla="$saludo $nombre, le invitamos a ver $producto en nuestro showroom. Que dia le acomoda?",
    evidencia="Demo: propuesta para que la jefa la apruebe",
)
servicio.gobernanza.registrar(
    cliente_id=CLIENTE,
    accion="activar_estrategia",
    descripcion=f"Alta de la estrategia {estrategia.nombre} v{estrategia.version} "
    f"({estrategia.etapa.value}); requiere aprobacion para activarse",
    payload={"estrategia_id": estrategia.estrategia_id},
)

for rol in ("cliente", "empleado"):
    token = secrets.token_urlsafe(32)
    servicio.almacen.crear_acceso(CLIENTE, hashlib.sha256(token.encode()).hexdigest(), rol)
    print(f"Token {'jefe' if rol == 'cliente' else 'empleado'} (se muestra una sola vez): {token}")

for lead in servicio.almacen.listar_leads(CLIENTE):
    print(f"{lead.lead_id} {lead.nombre:<7} {lead.etapa.value:<12} {lead.estatus.value:<11}"
          f" humano={lead.requiere_humano}")
print(f"Listo: {CLIENTE} en {os.path.abspath(servicio.config.ruta_base_datos)}")
PY
```

Salida esperada sin `ANTHROPIC_API_KEY`:

```text
LEAD-00001 Laura   Apertura     En_proceso  humano=False
LEAD-00002 Carlos  Prospeccion  En_proceso  humano=False
LEAD-00003 Marta   Cierre       En_proceso  humano=True
LEAD-00004 Jorge   Seguimiento  Cerrado     humano=False
LEAD-00005 Sofia   Prospeccion  En_proceso  humano=True
LEAD-00006 Raul    Prospeccion  En_proceso  humano=True
LEAD-00007 Elena   Prospeccion  Perdido     humano=False
```

Para repetir la demo desde cero, borra el archivo de `HERMES_DB` (más `-wal` y `-shm`) con la API detenida y vuelve a correr el script.
Si pierdes un token, el operador emite otro desde la vista **Accesos** del panel.

## 3. Guion de demo (5 minutos)

**Preparación:**
- Abre tres ventanas del navegador en `http://localhost:5173`: **operador** (`HERMES_PANEL_TOKEN`), **jefe** y **empleado**. Cada pestaña guarda su propio token en `sessionStorage`.
- Reinicia la API con `HERMES_ENVIO_REAL=true` para que el bot conteste por WhatsApp.
- La cuenta Trial de Twilio solo permite **5 mensajes salientes al día**. Este guion usa 3.

| Min | Acción | Qué se ve |
| --- | --- | --- |
| 0:00 | **Jefe:** abre Pipeline | Solo ve su tienda, sin selector de tenants. 7 leads repartidos en Apertura, Prospección, Cierre y Seguimiento, con conteos por etapa. Al abrir un lead se ve su conversación completa. |
| 0:45 | Desde tu WhatsApp, al Sandbox: `Hola, me interesa una sala modular` | Llega la respuesta del guion, literal y sin tildes: "Perfecto, <nombre>. Para darle la mejor opcion, para cuando lo necesita?". En el Pipeline del jefe (refresca) aparece un lead nuevo en **Prospección**. |
| 1:30 | WhatsApp: `Cuanto cuesta? la quiero comprar esta semana` | El bot propone agendar. El lead pasa a **Cierre** y su detalle muestra intención, urgencia y señales de compra. |
| 2:15 | WhatsApp: `Quiero hablar con una persona` | El bot **no** contesta: escala. |
| 2:30 | **Empleado:** abre Intervención | Solo existe esta vista. Aparece tu caso con el motivo `solicita_humano`, la conversación y la recomendación de HERMES, junto a los 3 casos precargados. |
| 3:00 | **Empleado:** escribe una respuesta propia y pulsa **Enviar editada** | Te llega por WhatsApp. El caso queda resuelto, firmado como `empleado:demo_tienda/<nombre>`. |
| 3:45 | **Jefe:** Aprobaciones → aprueba "Invitar al showroom"; Estrategias → la activa | El jefe decide qué estrategia usa la IA. El empleado no puede hacerlo: la API le responde 403. |
| 4:20 | **Jefe:** Reporte → descarga el PDF | Conteos por etapa, ganados y perdidos, escalamientos y conclusión. |
| 4:45 | Cierre | Cada tenant y cada rol ve solo lo suyo; toda acción queda registrada en la bitácora. |

Al terminar, vuelve a `HERMES_ENVIO_REAL=false`.

## 4. Variables de entorno

Solo nombres; los valores no se guardan en el repositorio.

| Variable | Obligatoria para la demo |
| --- | --- |
| `HERMES_PANEL_TOKEN` | Sí (sin ella la API responde 503) |
| `HERMES_WEBHOOK_TOKEN` | No (solo webhooks de correo y voz; WhatsApp usa la firma de Twilio) |
| `HERMES_DB` | Sí |
| `HERMES_URL_PUBLICA` | Sí |
| `HERMES_ENVIO_REAL` | Sí |
| `TWILIO_ACCOUNT_SID` | Sí |
| `TWILIO_AUTH_TOKEN` | Sí |
| `TWILIO_WHATSAPP_FROM` | Sí |
| `TWILIO_VALIDAR_FIRMA` | Sí |
| `NGROK_AUTHTOKEN` | Sí (para ngrok) |
| `NGROK_DOMAIN` | Sí (para ngrok) |
| `ANTHROPIC_API_KEY` | No (sin ella responde con el guion aprobado) |
| `HERMES_MODELO` | No |
| `HERMES_LIMITE_WEBHOOK` | No (por defecto 120 por minuto) |
| `HERMES_ORIGENES_PANEL` | No (solo si el panel se sirve desde otro origen) |
| `HERMES_PERMITIR_SIN_TOKEN` | No (solo para desarrollo; nunca en la demo) |
| `HERMES_API` | No (destino del proxy del panel; por defecto `http://127.0.0.1:8000`) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `IMAP_HOST`, `IMAP_PORT`, `IMAP_BUZON` | No (correo) |
| `TWILIO_VOICE_FROM`, `TWILIO_VOICE_TWIML_URL` | No (voz) |
| `NEXUS_URL`, `NEXUS_INTERNAL_API_TOKEN` | No |

## 5. Configurar el bot de un tenant sin tocar código

| Qué | Dónde | Quién |
| --- | --- | --- |
| Nombre del negocio, industria, descripción, productos, objetivos, zona horaria, horario comercial, número de WhatsApp | `POST /v1/onboarding` con el mismo `cliente_id` | Operador |
| Reglas de escalamiento: `confianza_minima`, `escalar_quejas`, `escalar_solicitud_humano`, `escalar_temas_legales`, `escalar_descuentos`, `descuento_maximo_por_ciento`, `palabras_clave_extra` | Campo `reglas_escalamiento` del mismo `POST /v1/onboarding` | Operador |
| Estrategias por etapa: plantilla con variables `$nombre`, `$negocio`, `$saludo`, `$producto`, `$etapa`, `$dia_semana`, `$seguimientos` y condiciones opcionales | Panel → Estrategias (crear), luego Aprobaciones (aprobar) y Estrategias (activar) | Las crea el operador; las aprueba y activa el jefe o el operador |

El onboarding **reemplaza el registro completo** del tenant, así que envía todos los campos, no solo el que cambias. Los leads y conversaciones no se tocan. Ejemplo:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/onboarding \
  -H "X-Panel-Token: $HERMES_PANEL_TOKEN" -H "Content-Type: application/json" \
  -d '{"cliente_id":"demo_tienda","nombre_negocio":"Muebles Aurora (demo)",
       "representante":"Ana Jefa","industria":"Muebles y decoracion",
       "descripcion_negocio":"Tienda de muebles con entrega a domicilio en la ciudad.",
       "productos":["Sala modular","Comedor 6 sillas","Recamara matrimonial"],
       "objetivos":["Agendar visitas a showroom","Cerrar ventas por WhatsApp"],
       "zona_horaria":"America/Mexico_City","telefono_whatsapp":"+14155238886",
       "reglas_escalamiento":{"palabras_clave_extra":["garantia"],"descuento_maximo_por_ciento":0}}'
```

**Límites:**
- Lo que el tenant configura (negocio, productos, descripción y estrategia activa) entra en el prompt de **Claude**.
- Sin `ANTHROPIC_API_KEY`, el bot responde con un guion fijo por etapa, igual para todos los tenants.
- En ese modo la estrategia activa solo aparece como respuesta sugerida en Intervención humana.
- Las reglas base del prompt (`REGLAS_ESTRICTAS` en `hermes/prompts.py`) y el guion de respaldo (`GUION_APROBADO` en `hermes/llm.py`) **son código**. Hoy no se pueden editar por tenant.

## 6. Problemas conocidos y cómo resolverlos

| Síntoma | Causa | Solución |
| --- | --- | --- |
| Debugger de Twilio: **11200** con HTTP 404 | El túnel ngrok está caído: se cerró la terminal o la máquina se suspendió | Levanta ngrok y prueba `curl https://$NGROK_DOMAIN/health`; luego reenvía el mensaje. |
| El webhook responde **403** | La firma no coincide: `HERMES_URL_PUBLICA` o la URL del Sandbox difieren (barra final, `http`, parámetros) | Deja ambas idénticas: `https://<dominio>` y `.../v1/webhooks/whatsapp/<cliente_id>`. |
| El webhook responde **404** | El `cliente_id` de la URL no existe en esa `HERMES_DB` | Carga el tenant (sección 2) o corrige la URL. |
| Todo responde **503** | Falta `HERMES_PANEL_TOKEN` | Exporta la variable y reinicia la API. |
| Responde **429** | Superaste `HERMES_LIMITE_WEBHOOK` por minuto, tenant y canal | Espera un minuto o súbelo. |
| El bot no contesta por WhatsApp | `HERMES_ENVIO_REAL=false`, el mensaje se escaló o no hay credenciales | Revisa `/v1/estado` y la vista Intervención. `GET /v1/clientes/<id>/leads/<lead_id>` devuelve el `estado_entrega` de cada mensaje (`pendiente`, `enviada` o `fallida`) con su error. |
| Mensaje `fallida` con error **63038** | La cuenta Trial ya mandó sus 5 mensajes del día | Espera al día siguiente o pasa la cuenta a pago. |
| Twilio no entrega a un número | Ese número no se unió al Sandbox o la unión caducó (unos 3 días) | Reenvía `join <código>`. |
| Fallan los mensajes fuera de las 24 h | WhatsApp solo permite plantillas aprobadas fuera de esa ventana | Haz que el lead escriba primero; los seguimientos automáticos de 48 h fallarán en el Sandbox. |
| ngrok rechaza la sesión por versión | El agente de ngrok es demasiado viejo para la cuenta (la 3.19 fue rechazada; la 3.39 funcionó) | Actualiza ngrok (`ngrok update` o descarga la última). |
| El panel no carga datos | La API no está en `:8000`, o el panel se sirve desde otro origen | Usa `npm run dev`, o define `HERMES_API`, o añade el origen en `HERMES_ORIGENES_PANEL`. |
| El bot repite preguntas, escala frases normales ("para esta semana") o tras "como pago?" contesta "Retomo su solicitud…" | Sin Claude, HERMES clasifica por palabras clave y usa el guion fijo por etapa | Configura `ANTHROPIC_API_KEY`, o usa en la demo los mensajes de la sección 3, que están probados. |
| Una respuesta se queda `pendiente` para siempre | La entrega corre en segundo plano en memoria: si la API se reinicia justo después de recibir el mensaje, esa entrega se pierde | Limitación conocida, aceptada para el piloto. Responde desde el panel. Antes de manejar volumen real hace falta un outbox persistente. |
| Dos respuestas al mismo lead llegan en desorden | Las entregas en segundo plano no se encolan por lead | Limitación conocida, aceptada para el piloto. Se corrige con una cola por lead. |
| El token viaja sin cifrar | Sin HTTPS en local; ngrok sí da HTTPS al webhook | En producción es obligatorio HTTPS; va con el despliegue. |
