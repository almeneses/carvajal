# Especificación 043 — Chat comercial unificado con datos reales

- **Rama**: `feat/chat-comercial-unificado`
- **Fecha**: 2026-11-22
- **Estado**: Implementada (as-built) el 2026-11-23; T029 aprobada por el owner.
- **Base de diseño**: `42a816c` en `main`, con #222–#226 incorporadas.

## Objetivo y alcance

Una persona puede informarse sobre Carvajal y sus empaques, cotizar y encontrar
dónde comprar en el mismo hilo. El piloto se centra en Empaques Colombia. Los
precios son referencias fechadas del vendedor y el canal de compra lleva a la
tienda o al distribuidor. Los flujos existentes de México, voz e información
institucional forman parte de la compatibilidad. Pedidos, pagos, factura y
explorador de catálogo quedan fuera de este incremento.

La protección de la escritura del corpus (Goal 1, #222) y el contrato de datos
comerciales compartido (Goal 3, #223) son capacidades incorporadas. Esta spec
adopta el contrato existente para los recorridos pendientes. La adopción incluye
retirar sus partes dependientes de la simulación.

### Relación con especificaciones existentes

La [042](../042-cotizador/spec.md) documenta el Cotizador original. En la entrega
043 se sustituyen FR-001, FR-002 y FR-016 de la 042 por el hilo único, y FR-014,
FR-015 y los requisitos de datos simulados por fuentes verificables. En el
historial de la 042, sus folios y fechas se conservan como datos de la emisión
original. La nueva interfaz solo muestra vigencia comercial si la publicó el
vendedor. Se mantiene la regla de la [039](../039-catalogo-colombia/spec.md):
precios de Colombia publicados en COP, con moneda, IVA declarado, fecha y fuente.

## Escenarios y pruebas

### US1 · Consultar productos y sedes con datos reales (P1)

Una persona busca un empaque o un distribuidor en su ciudad. Quiere saber qué
tiene respaldo y a quién contactar si falta precio o disponibilidad.

**Prueba independiente**: consultar el catálogo en una instalación sin el
dataset anterior y buscar el mismo tipo de producto en Bogotá, Cali y una ciudad
fuera del directorio.

1. Si hay un precio publicado, el resultado identifica vendedor, presentación,
   importe, moneda, IVA declarado, fuente y fecha capturada.
2. Si la ciudad tiene sedes y falta precio o cantidad, la persona recibe contactos
   del directorio. La tarjeta indica si existe un vínculo documentado con el SKU.
3. Si el inventario carece de evidencia vigente por sede, se muestra
   «disponibilidad por confirmar». Si la ciudad carece de cobertura, se indica y
   se presentan los canales que tengan respaldo.

### US2 · Entender cuánto se conoce de una cotización (P1)

Una persona quiere comprar cierta cantidad y entender la presentación, los
sobrantes, el precio y las condiciones pendientes.

**Prueba independiente**: cotizar 300 vasos de 3,5 oz en Bogotá con domicilio
desconocido y comparar el cálculo, lo guardado y lo que se muestra.

1. Si la cantidad exige paquetes completos, la cotización muestra el número de
   paquetes, las unidades compradas y el sobrante.
2. Si falta el envío, el subtotal queda visible y el total final queda pendiente.
   La vigencia comercial se muestra cuando la fuente la declara.
3. Si el mensaje pide vasos y platos, el chat pide elegir qué línea cotizar
   primero y conserva la otra como pendiente.

### US3 · Continuar la compra en el mismo chat (P1)

Una persona pregunta por el material apropiado para comida caliente, solicita
precio y quiere saber dónde comprar. Puede cambiar ciudad o cantidad después.

**Prueba independiente**: hacer la consulta mixta en un hilo nuevo, cambiar un
dato y repetir un turno después de interrumpir la conexión.

1. Si hay evidencia comercial e informativa, el mismo turno muestra ambas y sus
   fuentes. Cuando falta evidencia para una parte, entrega la parte respaldada y
   explica el dato que falta.
2. Un cambio de ciudad o cantidad usa el resto del contexto aportado. La persona
   puede abrir la ficha, cambiar el dato o contactar una sede desde el hilo.
3. Si el mismo envío se reintenta o llega dos veces, recupera la misma respuesta
   y cotización, con las mismas tarjetas, tras su confirmación.

### US4 · Conservar el acceso a las cotizaciones durante la transición (P1)

Una persona que ya había cotizado quiere volver a un registro real desde un
enlace conocido. Un operador necesita verificar la instalación antes de retirar
la sección anterior.

**Prueba independiente**: actualizar un entorno que tenga historial de la 042 y
probar con dos identidades. Validar también una instalación limpia.

1. Un acceso antiguo abre el chat o el historial de lectura autorizado. El dueño
   conserva el acceso a los turnos reales, folios y fuentes que sigan disponibles.
2. Una identidad distinta recibe el mismo rechazo de acceso que para un hilo ajeno
   del chat. El histórico simulado o de procedencia ambigua se excluye del recorrido
   público y del contexto que recupera el chat.
3. En la instalación limpia y en la actualización, el catálogo cargado tiene la
   versión esperada. Luego de verificar los recorridos se retira el segundo
   compositor y se reemplaza el generador público de FAQ por accesos al chat.

### Casos límite

- Un precio publicado desactualizado, una sede geocodificada solo a nivel ciudad
  o una oferta del vendedor en línea sin inventario de una sucursal.
- Una sede confirma existencia para una presentación diferente. La interfaz
  preserva la presentación comprobada y declara pendiente la solicitada.
- El mismo identificador de envío llega simultáneamente desde dos peticiones,
  incluso en el primer turno de una conversación.
- Un historial antiguo mezcla precio auténtico con stock simulado o tiene un
  enlace ambiguo. La clasificación considera la evidencia de cada campo y el
  historial ambiguo queda fuera de lectura pública hasta revisión.

## Requisitos funcionales

| ID | Requisito |
| --- | --- |
| FR-001 | Retirar del producto y del repositorio el dataset comercial simulado, su generador, fichas, banderas y rutas exclusivas. Una instalación limpia deberá servir el flujo comercial a partir de las fuentes publicadas. |
| FR-002 | Mostrar cada precio referencial con vendedor, SKU, presentación, moneda, IVA declarado, fuente y fecha. Distinguir origen oficial, distribuidor y directorio. |
| FR-003 | Consultar sedes por ciudad con independencia de precio y cantidad. Mostrar contacto, precisión de ubicación y, cuando exista, evidencia de vínculo con el producto. |
| FR-004 | Mostrar existencias, plazos y costo de envío como confirmados solo con evidencia atribuible, con `source_url`, `observed_at` y `valid_until` no vencido, para la sede, producto o pedido que corresponda. Sin `valid_until` o con evidencia vencida, mostrar el dato como pendiente. La fecha del precio no caduca por antigüedad. |
| FR-005 | Separar precio por presentación, unidades solicitadas y compradas, sobrantes, subtotal, IVA declarado, envío y total final. El total queda pendiente si falta un componente necesario. Registrar por separado generación y vigencia comercial. |
| FR-006 | Conservar las líneas de una solicitud múltiple mientras el usuario elige cuál cotizar primero. En el piloto se emite una referencia por cotización. |
| FR-007 | Resolver consulta informativa, recomendación, cotización y búsqueda de sedes en un mismo hilo, con las citas y la ficha aplicables. Mantener continuidad ante cambios de ciudad, cantidad y retiro o entrega. |
| FR-008 | Reemitir el mismo resultado y folio ante reintentos de un envío, sin duplicar una cotización o el par de mensajes. Conservar streaming, dictado, lectura y aislamiento por dueño. |
| FR-009 | Mantener acceso autorizado a las cotizaciones e hilos históricos reales, con lectura compatible y acceso desde `/cotizador`, `/cotizador/stream`, `/cotizador/nuevo` y `/cotizador/ficha/{slug}`. Clasificar cada histórico como real, simulado o ambiguo con la matriz de `data-model.md`, excluir los dos últimos del recorrido público y preparar su depuración con respaldo y aprobación previa para un borrado irreversible. |
| FR-010 | Verificar versión y legibilidad del catálogo al desplegar en una instalación nueva y al actualizar un volumen existente. Conservar datos propios del usuario y cargas documentales. |
| FR-011 | Retirar la sección y el flujo de escritura separados después de validar el chat integrado. Redirigir los accesos anteriores. Sustituir la generación pública de FAQ por accesos al chat y conservar los enlaces a fuentes. |
| FR-012 | Mantener el comportamiento comercial de México, la lectura pública del corpus y el control de escritura para operadores entregado en #222. |

### Entidades principales

| Entidad | Qué representa |
| --- | --- |
| Producto, oferta, sede | Ficha y presentación del producto, precio atribuible a un vendedor y canal de contacto. Se reutiliza el contrato de #223. |
| Evidencia de disponibilidad o entrega | Dato observado, fuente, fecha y alcance de sede, producto o pedido. |
| Contexto comercial del hilo | Producto o necesidad, país, ciudad, cantidad, retiro o entrega y líneas pendientes. |
| Cotización referencial | Resultado determinista con folio, importes, fuente y momento de emisión, asociado a un turno. |
| Historial heredado | Conversación y cotizaciones de la 042, con dueño, identificador, procedencia y estado de acceso. |

## Criterios de éxito

| ID | Criterio |
| --- | --- |
| SC-001 | Cero datos comerciales simulados servidos en el flujo público. Las 29 observaciones de Colombia de la captura de aceptación permanecen consultables como precios fechados, incluidas las tres filtradas por la demo anterior. Se conserva paridad para 98 referencias de México de esa misma base. |
| SC-002 | El 100 % de los precios de los casos de aceptación identifica vendedor, fuente y fecha. Cada caso de stock confirmado incluye evidencia con `source_url`, `observed_at` y `valid_until` no vencido para la sede y presentación; sin esos campos queda «por confirmar». |
| SC-003 | Los escenarios de consulta mixta, búsqueda sin precio, cambio de ciudad o cantidad y falta de cobertura se resuelven en el mismo hilo con los estados esperados. |
| SC-004 | Reenvíos consecutivos y concurrentes del mismo envío generan una cotización y un turno. Los historiales reales probados conservan acceso para su dueño y cero acceso cruzado. |
| SC-005 | Instalación limpia y actualización verifican catálogo y completan una cotización. Se registra mediana y p95 de primer contenido útil y cierre del turno con condiciones y tamaño de muestra. |

## Aclaraciones y supuestos

- El piloto puede operar con precios fechados y contactos aunque todavía no exista
  inventario verificable de proveedores. Una confirmación vigente es necesaria
  para afirmar stock o condiciones de entrega.
- Una cotización histórica auténtica sigue siendo un registro de su momento.
  El inventario y el precio actual requieren una consulta nueva.
- Diez sesiones concurrentes es el escenario inicial propuesto para medir el
  piloto. Se registra una línea base y se justifica cualquier limitación del entorno.
- La depuración irreversible en una base real requiere autorización sobre un
  inventario verificable. La clasificación y el bloqueo de lectura son parte del
  cambio técnico. FAISS y el ZIP se evalúan en mantenimiento independiente.
