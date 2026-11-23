# Investigación — Spec 043

**Base revisada**: `42a816c`. Evidencia del repositorio local, complementada por
dos subagentes que revisaron datos comerciales y flujo de chat por separado.

| Tema | Evidencia confirmada | Decisión y alternativa |
| --- | --- | --- |
| Contrato de #223 | `commercial/loader.py` construye el libro con catálogo, productos y ofertas de demo y una unión del directorio con coordenadas de demo. La lectura de ofertas CO puede añadir condiciones simuladas. | Reusar modelos, IDs y consultas. Migrar coordenadas con procedencia, fecha y precisión a datos comerciales curados. Retirar la rama simulada del contrato público y del repositorio. Una bandera sola conserva las dependencias y los filtros. |
| Paridad comercial | La base tiene 29 observaciones CO, 29 sedes en 10 ciudades y 98 precios MX. La fuente CO conserva 18 presentaciones de paquete y 11 de caja. La evidencia de disponibilidad versionada todavía está vacía. | Probar la presentación publicada y la paridad con los datos versionados, no exigir esos conteos cuando el catálogo real se actualice. Las ubicaciones con coincidencia dudosa quedan sin marcador preciso. |
| Procedencia histórica | El Cotizador persiste el mismo resultado en `quotes.payload` y `messages.sources`. El folio permite relacionarlos, aunque `quotes.message_id` puede faltar. Hay marcas por producto, precio y stock, pero el envío de demo puede carecer de marca propia. El texto del asistente también contiene cifras. | Clasificar por dueño, conversación, folio y evidencia de campo, incluidas alternativas y envío. Excluir texto y tarjeta contaminados del historial visible y del contexto del agente. El prefijo `cot:` y la bandera de modo solos son insuficientes para decidir un borrado. |
| Importes y transición | La migración `0009_quotes.py` exige `total` y `valid_until`. El motor anterior compara un subtotal serializado como texto en un paso de selección. El rollback del deploy cambia la imagen y deja Alembic aplicado. | Calcular y ordenar con Decimal. Versionar el resultado y usar una migración que permita distinguir ausencias y leer la versión anterior. Ensayar coexistencia y reversión. La fecha emitida por la aplicación anterior queda como metadato histórico. |
| Consulta mixta | El router ya distingue `commercial_mixed` y puede proporcionar la subconsulta informativa. El grafo ejecuta el nodo comercial y pide reformular el resto. | Usar el resultado del router para ejecutar la recuperación documental y la consulta comercial aplicable en el mismo turno. Conservar citas separables y manejar la ausencia parcial. |
| Reenvío y recarga | `ChatService` busca reintentos por hilo y `client_turn_id`. El POST HTML actual no propaga ese ID y el replay no reconstruye tarjetas. El índice por hilo no protege el primer envío concurrente sin hilo. | Unir HTML y SSE en el mismo servicio y persistir el resultado de la cotización con los mensajes. Asegurar unicidad también en la resolución del primer hilo para una identidad estable. Probar la recuperación del resultado ganador. |
| Salud y despliegue | `compose.prod.yml` siembra el volumen solo si Gold está vacío. `scripts/deploy.sh` admite la falta inicial de una colección Qdrant en un caso concreto. | Actualizar y validar la versión comercial al instalar y al desplegar sobre un volumen existente. Clasificar sus errores aparte de la colección ausente. Conservar las capas del corpus. |
| Rutas y FAQ | El Cotizador usa un espacio de dueño `cot:`; el historial se renderiza desde el resultado persistido. La página pública de FAQ y el POST de API son contratos diferentes. | Usar una redirección o un recorrido heredado de lectura autorizada y mantener los folios. Inventariar consumidores de la API y del SSE antes de deprecar. FAISS y ZIP quedan para mantenimiento independiente. |

## Fuentes de contraste

- [ADR del contrato comercial](../../docs/decisions/0003-contrato-datos-comerciales.md)
  y [validación del contrato](../../tests/test_commercial_contract.py).
- [ADR de escritura del corpus](../../docs/decisions/0003-escritura-corpus-operadores.md).
- [Cargador comercial](../../packages/core/src/carvajal/commercial/loader.py),
  [motor](../../packages/core/src/carvajal/quotation/engine.py) y
  [Cotizador anterior](../../apps/api/views/cotizador.py).
- [Servicio de turnos](../../apps/api/chat/service.py),
  [repositorio de chat](../../packages/core/src/carvajal/adapters/storage/chat.py)
  y [esquema de cotizaciones](../../migrations/versions/0009_quotes.py).
- [Grafo del agente](../../packages/core/src/carvajal/agent/graph.py),
  [Compose de producción](../../compose.prod.yml) y [despliegue](../../scripts/deploy.sh).

## Inventario de consumidores para el retiro (T003)

| Ruta o contrato | Consumidores a revisar | Acción prevista |
| --- | --- | --- |
| `/cotizador` | Navegación de `apps/api/templates/base.html`, pruebas de `tests/test_api_cotizador.py`, enlaces de documentación | Redirigir al chat y conservar lectura histórica autorizada |
| `/cotizador/stream` | `apps/api/static/cotizador.js`, pruebas de stream | Redirigir o devolver el evento de cierre compatible mientras se retira el compositor |
| `/cotizador/nuevo` | `apps/api/templates/cotizador.html`, pruebas de hilos | Retirar junto con el compositor; conservar el acceso al historial |
| `/cotizador/ficha/{slug}` | `_cotizacion.html`, pruebas de ficha, citas históricas | Mantener la ficha o redirigir a una ruta equivalente con el mismo folio |
| `/faq` y `/api/faq` | `faq.js`, `tests/test_api_faq.py`, navegación | Conservar la API con consumidores; sustituir la generación pública por accesos al chat |
| Historial `cot:<owner_key>` | `apps/api/views/cotizador.py`, `quotes`, mensajes | Aplicar la matriz real/simulado/ambiguo de `data-model.md` |

La prueba de arranque limpio y actualización vive en
[`tests/browser/arranque_043.cjs`](../../tests/browser/arranque_043.cjs) y se
documenta en [`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md). Verifica
`/live`, `/health` con la versión y los conteos del catálogo, las redirecciones
de las rutas retiradas y el rechazo de un histórico ajeno; el mismo arnés sirve
tras un arranque limpio y tras actualizar un volumen existente.

## Comprobaciones que corresponden a implementación

Confirmar qué coordenadas se pueden conciliar con el directorio. Medir la
proporción de históricos que se pueden clasificar sin ambigüedad. Verificar
consumidores actuales de las rutas que se retiren y capacidad del entorno de
pruebas. Las respuestas del piloto permanecen acotadas a la evidencia que
respalde cada dato, mientras falten fuentes de inventario o entrega.
