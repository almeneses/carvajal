# Plan de implementación — Chat comercial unificado con datos reales

- **Rama**: `feat/chat-comercial-unificado`
- **Fecha**: 2026-11-22
- **Spec**: [043](spec.md)
- **Base**: `83e3b9e` con #222 y #223 en `main`.

## Resumen

Adoptar el contrato comercial que ya existe. Retirar la simulación y corregir
la cotización. Integrar información, ofertas y sedes en el servicio de turnos
del chat. Validar la instalación limpia y la actualización antes de retirar
la escritura y el compositor separados. Conservar un recorrido de lectura
para los historiales reales de la 042.

## Contexto técnico

| Área | Decisión |
| --- | --- |
| Aplicación | Python 3.13+, FastAPI, Jinja2/HTMX, JavaScript existente y SSE. Reusar el shell y el contrato del chat. |
| Agente y búsqueda | LangGraph y Qdrant. El turno mixto recupera evidencia documental y ejecuta las consultas comerciales aplicables. El importe y la disponibilidad salen del contrato comercial y del cálculo determinista. |
| Catálogo | `carvajal.commercial` incorporado por #223, con catálogo curado y evidencia separada. Trasladar solo geocodificación trazable. Mantener la presentación publicada y la paridad MX. |
| Persistencia | PostgreSQL, repositorios SQLAlchemy y migración Alembic si requiere cambiar el esquema. Usar escritura atómica de resultado comercial, cotización y mensajes. |
| Compatibilidad | Identidad anónima firmada, protección CSRF, cuota, lectura de históricos, dictado y clientes del chat. Mantener seguridad de operadores de #222. |
| Validación | Comandos del repo con `uv`, pruebas de contrato y navegador, Postgres real para concurrencia y actualización, `make check`. |
| Alcance del piloto | Empaques Colombia, precios referenciales, directorio por ciudad y stock comprobado cuando llegue evidencia. Referencias MX y flujo informativo conservados. |

## Constitution Check

| Principio | Aplicación |
| --- | --- |
| Constitución y alcance | Registrar 043 como borrador y especificar qué requisitos de 042 se sustituirán al activar el flujo. Conservar trazabilidad de las specs 036, 039 y 042. |
| Procedencia y ausencia | Usar precio observado del vendedor para el país y presentación. Separar precio, stock y contacto. Mantener el índice documental y el catálogo comercial en sus funciones actuales. |
| Seguridad | Las escrituras del corpus conservan el límite de operador. Los historiales heredados comprueban dueño. Publicar únicamente datos con procedencia y conservar CSP, CSRF y controles de voz. |
| Tecnología y pruebas | Reusar el stack. Ejecutar calidad, migraciones y evidencia de aceptación antes de retirar el camino anterior. |

**Revisión posterior al diseño**: el [modelo](data-model.md) y el
[contrato](contracts/chat-comercial.md) reutilizan el runtime y adaptadores
existentes. El diseño respeta la regla de precios de la spec 039 y la
separación de evidencia comercial. Las tareas incluyen compatibilidad y
comprobación de acceso para los cambios de persistencia.

## Estructura

| Responsabilidad | Código actual que se revisa |
| --- | --- |
| Datos estructurados | `data/catalog/empaques/`, `data/commercial/`, `packages/core/src/carvajal/commercial/` |
| Cálculo y recomendación | `packages/core/src/carvajal/quotation/`, `packages/core/src/carvajal/agent/` |
| Chat y contratos web | `apps/api/chat/`, `apps/api/views/`, `apps/api/templates/`, `apps/api/static/` |
| Historial y cotizaciones | `packages/core/src/carvajal/adapters/storage/`, `migrations/versions/` |
| Despliegue y pruebas | `Dockerfile`, `compose.prod.yml`, `scripts/deploy.sh`, `tests/` |

## Fases y dependencias

1. **Adopción y procedencia (Goal 2, US1).** Partir de #222 y #223. Congelar
   evidencia de paridad, migrar coordenadas trazables, cortar las lecturas de
   demo y retirar sus archivos, banderas y fichas. Preparar la clasificación no
   destructiva de históricos.
2. **Importes y compatibilidad de escritura (Goal 5, US2).** Hacer el cálculo
   con Decimal en la presentación del vendedor y versionar la persistencia
   para distinguir subtotal, total pendiente y vigencia. El motor compartido
   queda a cargo de un agente a la vez durante el corte de las fases 1 y 2.
3. **Hilo e interfaz unificados (Goal 4, US3).** Usar el catálogo y el resultado
   estructurado para integrar ambas intenciones. Atender el reintento y la
   recarga desde una escritura idempotente. La interfaz puede adelantarse
   cuando se acuerden los campos y el contrato SSE.
4. **Validación y retiro (Goals 6 y 7, US4).** Preparar primero el arranque,
   compatibilidad de historial y pruebas en paralelo. La validación integrada
   precede la retirada del compositor separado y el generador público de FAQ.
   Ejecutar nuevamente los recorridos después del retiro.

La depuración irreversible de históricos de una base real se tramita por
separado, con inventario, respaldo y autorización. FAISS y el archivo ZIP
quedan fuera de las tareas del piloto.

## Despliegue y reversión

El catálogo se actualiza y valida por versión con independencia de la
siembra inicial de Gold. Se distingue el catálogo ausente, inválido o con
versión inesperada de un índice Qdrant sin colección. `/live` mide la
vitalidad del proceso. La comprobación de preparación comercial debe
participar en la decisión del despliegue.

Aplicar primero cambios de esquema compatibles con la lectura anterior o
demostrar la compatibilidad explícitamente antes del corte. La reversión de
imagen actual conserva el esquema migrado. Ensayar lectura cruzada y documentar
una reversión que mantenga desactivada la publicación de datos simulados. Si
una reversión a una imagen anterior reactivaría la demo, restablecer el
servicio comercial con una revisión compatible o mantener ese recorrido
deshabilitado hasta corregirlo.

## Salidas del diseño

- [Investigación y decisiones](research.md)
- [Modelo de datos](data-model.md)
- [Contrato del chat comercial](contracts/chat-comercial.md)
- [Validación del piloto](quickstart.md)
- [Tareas, dependencias y reparto entre subagentes](tasks.md)
