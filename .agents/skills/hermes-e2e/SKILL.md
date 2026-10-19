---
name: hermes-panel-e2e
description: Ejecutar pruebas del panel HERMES con FastAPI real, SQLite aislada y proveedores externos desactivados.
---

# Preparación local

Desde el repo, instalar requirements-dev.txt y ejecutar `npm ci` en panel si faltan dependencias.
Arrancar FastAPI con HERMES_PANEL_TOKEN temporal, HERMES_DB apuntando a una SQLite fuera del repo,
TWILIO_VALIDAR_FIRMA=false y HERMES_ENVIO_REAL=false. Este bypass de firma es solo para pruebas locales.
Ejecutar `uvicorn hermes.main:app --host 0.0.0.0 --port 8000` y, desde panel,
`npm run dev -- --host 0.0.0.0`. Vite usa el proxy del backend.
Conservar DB y planes bajo /home/ubuntu: los procesos y /tmp pueden perderse entre interrupciones.

## Devin Secrets Needed

No se necesitan secretos de proveedores para el modo de guion aprobado.
Usar un HERMES_PANEL_TOKEN temporal compartido por backend y navegador.
No solicitar Anthropic, Twilio, SMTP, IMAP ni voz para esta prueba sin entregas reales.

## Datos y comprobaciones

Crear dos tenants mediante POST /v1/onboarding usando el contrato actual de tests/conftest.py.
Generar leads vía webhook WhatsApp con From, Body, ProfileName y MessageSid único.
Repetir exactamente el mismo MessageSid para comprobar idempotencia comparando historial,
lead y decisión antes/después. No confundir `enviada` con entrega externa:
consultar estado_entrega del mensaje.

Cambiar tenant con detalles abiertos en Pipeline, Intervención, Sandbox y Reporte.
La lista vacía no basta: comprobar que tampoco permanece historial/contexto del anterior.
Crear estrategia por UI, aprobar en Gobernanza y volver a Estrategias para activar.
Para ejercitar Apertura usar un contacto nuevo con Body=hola; un mensaje de interés
puede pasar a Prospeccion antes de seleccionar estrategia.

La intervención API requiere atendido_por, no operador. Aprobar sin sugerencia debe
devolver 422 sin mutar el caso. Para probar aprobación positiva, activar estrategia de
Apertura y generar un escalamiento legal nuevo que mantenga esa etapa.

## Evidencia y recuperación

Descargar PDF por UI y seleccionar el archivo más reciente: Chrome agrega (1), (2), etc.
No analizar por accidente una descarga de un run anterior. Validar con pypdf opcional,
pero revisar también ambas páginas visualmente en Chrome.
Detener solo FastAPI, recargar, reiniciar sobre la misma DB y pulsar Reintentar.
Comprobar tanto recuperación de datos como eliminación del error anterior.
Tras una interrupción conservar resultados verificados, pero iniciar una grabación nueva;
no presentar una grabación incompleta como si cubriera todo el recorrido.
