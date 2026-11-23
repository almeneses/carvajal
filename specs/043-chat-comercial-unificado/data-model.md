# Modelo de datos — Spec 043

## Datos comerciales

| Entidad | Identidad y campos | Reglas |
| --- | --- | --- |
| Producto | País, SKU, nombre, material, usos, presentación técnica y procedencia | El SKU se interpreta en su mercado. Conservar la diferencia entre fuente oficial y ficha de distribuidor. |
| Vendedor y sede | IDs estables de #223, ciudad, dirección, teléfono, fuente, fecha, coordenadas y precisión cuando apliquen | Un contacto del directorio puede existir sin ofrecer el SKU. Una coordenada de ciudad identifica una zona. |
| Oferta publicada | ID, SKU, vendedor, sede si está respaldada, moneda, importe, presentación, unidades, IVA declarado, URL, fecha y vigencia de vendedor cuando exista | Un precio de venta en línea puede no corresponder a inventario físico. La vista normalizada a caja no sustituye la presentación que vende el comercio. |
| Evidencia de stock o entrega | Sede o canal, SKU y presentación o alcance de entrega, cantidad cuando corresponda, dato, fuente y observación | Confirmar solo con evidencia vigente aplicable. Diferenciar cero confirmado de dato desconocido. Si la evidencia vence, regresar a «por confirmar». |

## Conversación y cotización

| Entidad | Identidad y campos | Reglas |
| --- | --- | --- |
| Contexto comercial | Hilo, país, ciudad, producto, intención, cantidad, entrega o retiro y líneas pendientes | Cada corrección conserva los datos compatibles. Cambiar SKU revalida presentación y condiciones. El contexto pertenece al hilo. |
| Envío | Visitante, hilo si ya existe y `client_turn_id` | El reintento recupera un único turno. Para el primer envío, resolver el hilo de forma idempotente antes de aplicar la unicidad por hilo. |
| Resultado del turno | Mensajes, fuentes documentales, resultado comercial estructurado, folio si se emitió | Guardar lo necesario para reconstruir la misma tarjeta al recargar o reintentar. La cotización y sus mensajes se confirman juntos. |
| Cotización | Folio, dueño, hilo, envío, versión, cantidad, presentación, moneda, precio por presentación, subtotal, IVA declarado, envío, total, fuente, fecha de emisión y vigencia publicada | Importes Decimal. Envío y total admiten ausencia. El total se conoce cuando sus componentes se conocen. La vigencia comercial puede estar ausente. |

## Datos heredados

La lectura distingue tres estados de procedencia. **Real verificable** se
puede abrir con el dueño original y se interpreta con los campos válidos
para su versión. **Simulado** incluye cualquier campo o texto comercial
generado por la demo. **Ambiguo** carece de evidencia suficiente para
publicación. Los dos últimos quedan excluidos del recorrido público y del
contexto del agente.

Usar el folio del payload y el dueño para correlacionar mensajes y
cotizaciones antiguas cuando falte `message_id`. Conservar el identificador
original cuando se habilite la lectura compatible. Separar el estado de
visibilidad de cualquier futura depuración de base. Los registros ambiguos
requieren revisión antes de resolver su procedencia.
