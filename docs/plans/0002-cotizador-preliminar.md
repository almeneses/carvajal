# Plan preliminar — Cotizador conversacional

**Fecha:** 2026-11-21 · **Rama:** `prototype`, basada en `dev`.
**Estado:** propuesta para revisión; todavía no es una especificación de Spec Kit.

## 1. Objetivo

Agregar una sección **Cotizador** con la misma interfaz del chat, dedicada a productos
de Carvajal. Ejemplo: «Cotízame 20 vasos para bebida caliente».

La respuesta debe distinguir:

- Producto encontrado en el catálogo, existencia confirmada y existencia desconocida.
- Cantidad solicitada, presentación de venta, cantidad comprable y sobrantes.
- Precio por presentación, moneda, impuestos, subtotal, envío y total conocido.
- Proveedores asociados al producto, con dirección y mapa cuando haya ubicación.
- Entrega estimada si se solicita domicilio y existen datos suficientes.
- Alternativas similares compatibles con el uso, explicando sus diferencias.

No incluye pedidos, reservas de inventario ni pagos. Una estimación no garantiza entrega.

## 2. Auditoría del catálogo existente

Revisión local ejecutada con `load_catalog()` y las herramientas comerciales, sin consultar
tiendas en vivo. Los conteos describen los archivos versionados, no existencias actuales.

| Dato | Colombia | México |
|---|---:|---:|
| Referencias | 102 | 98 |
| Referencias con precio | 29 | 98 |
| Referencias sin precio | 73 | 0 |
| Referencias con más de una oferta | 0 | 0 |
| Referencias sin tamaño de paquete | 24 | 4 |

- Las 29 ofertas colombianas proceden de **EDEXA**, capturadas el 2026-11-18,
  con IVA incluido y sin fecha de vigencia declarada.
- Las referencias mexicanas tienen precios oficiales capturados el 2026-10-23 o
  el 2026-11-18. No deben convertirse a COP ni usarse para completar precios colombianos.
- El directorio colombiano contiene **29 registros, 24 nombres de empresa y 10 ciudades**:
  Bogotá, Bucaramanga, Villavicencio, Pereira, Medellín, Barranquilla, Pasto, Cali,
  Manizales y Neiva. Hay 28 direcciones y 29 teléfonos; no hay coordenadas.
- El directorio no acredita qué SKU vende cada sede ni su inventario.
- Hay reglas para 5 materiales. Ninguna de las 102 referencias CO declara un rango de
  temperatura; se aplican reglas de material o usos documentados. No inventar propiedades
  técnicas ni asumir que cualquier vaso aceptado para «caliente» sirve para toda temperatura.
- El catálogo cubre **empaques**, no todo el portafolio empresarial de Carvajal.

### Hallazgo clave: unidad, paquete y caja

El flujo actual calcula cajas completas. Para 20 vasos calientes en Colombia devuelve,
como primera opción, una caja de 2.750 unidades por 206.305 COP, con 2.730 sobrantes.

Sin embargo, `fuentes/precios_co.csv` conserva la presentación publicada: para ese SKU,
el paquete de 50 costaba 3.751 COP en la captura. El nuevo cotizador puede aprovechar
esa información para mostrar **solicitadas: 20; compra: 1 paquete de 50; sobrantes: 30**.
No significa que el proveedor tenga stock hoy ni que venda 20 unidades sueltas.

Las reglas actuales aceptan 10 vasos CO para caliente; solo 2 tienen precio. Los 4 vasos
térmicos EPS no tienen precio. Antes de la demo se deben revisar los productos elegidos
y su aptitud documentada, sin alterar reglas técnicas para hacer funcionar el ejemplo.

## 3. Datos faltantes y posibilidad de demostración

| Dato necesario | Situación actual | Para una demo | Para operación real |
|---|---|---|---|
| Precio de todos los productos elegidos | Cobertura CO parcial | Precio ficticio identificado, solo donde falte | Precio publicado o lista autorizada, fuente y fecha |
| Oferta por proveedor y sede | Precios de EDEXA; directorio separado | Asociaciones sintéticas SKU–proveedor–sede | Catálogo o confirmación de cada proveedor |
| Presentación y mínimo de compra | Hay cajas, algunos paquetes y capturas originales | Reutilizar presentación conocida; reglas comerciales ficticias explícitas | Confirmar unidad/paquete/caja, múltiplos y mínimos por oferta |
| Existencias y actualización | No existen | Cantidades controladas por SKU y sede | Inventario actualizado y fecha de consulta |
| Coordenadas | Solo direcciones | Sedes ficticias con puntos ilustrativos | Geocodificar y verificar direcciones; conservar precisión y procedencia |
| Cobertura de domicilio | No existe | Ciudades o zonas simuladas | Cobertura y restricciones del proveedor |
| Costo de envío | No existe | Tarifa fija por zona, marcada como simulación | Tarifario o integración de transporte |
| Plazo de entrega | No existe | Ventana determinista por zona | Preparación, tránsito, horarios de corte, calendario y stock |
| Destino del cliente | No forma parte de la solicitud estructurada | Preguntar ciudad/zona | Datos mínimos necesarios para calcular cobertura y plazo |

**Sí se pueden crear datos para una demo**, pero no acreditar stock, tarifas ni compromisos
de empresas reales. Se propone:

- Reutilizar productos reales y características documentadas.
- Preparar un conjunto de **10–15 referencias CO** y **3 proveedores ficticios**, con
  al menos dos en una misma ciudad para comparar ofertas.
- Incluir productos disponibles, agotados, stock insuficiente y existencia desconocida;
  precios por paquete/caja, mínimos, envíos y ventanas de entrega.
- Etiquetar proveedores y sedes como ficticios y coordenadas como ilustrativas. No usar
  teléfonos inventados que puedan pertenecer a personas ni identidades de clientes.
- Guardar los datos en un archivo separado, por ejemplo
  `data/demo/cotizador.json`, sin sobrescribir el catálogo curado ni indexarlos como fuentes.
- Activarlos explícitamente y mostrar **«Modo demo: inventario, ofertas y entregas
  simulados»** en la sección y en cada resultado que dependa de ellos. Si una respuesta
  combina un dato real y uno simulado, identificar la procedencia de cada uno.
- Conservar un modo sin simulación: declarar datos desconocidos, nunca rellenarlos.

Las coordenadas de una sede real solo se incorporarán como verificadas después de
comprobarlas; un punto por ciudad no debe presentarse como ubicación exacta.

## 4. Flujo propuesto

1. Interpretar producto, uso, cantidad, capacidad y preferencias; conservarlos en el hilo.
2. Pedir lo imprescindible que falte. Propuesta inicial: Colombia/COP visible como mercado
   predeterminado; consultar ciudad para proveedores y zona si solicita domicilio.
3. Buscar en el catálogo y validar compatibilidad de material/uso.
4. Consultar las ofertas y existencias de cada proveedor, distinguiendo fuente real/demo.
5. Calcular con `Decimal` y reglas deterministas:
   presentaciones = redondeo hacia arriba de solicitadas / unidades por presentación;
   unidades comprables = presentaciones × unidades por presentación, respetando mínimos.
   Evaluar el stock sobre las unidades comprables, no solo sobre las solicitadas.
6. Mostrar opción principal y hasta dos alternativas. Una diferencia de capacidad,
   presentación, material o precio debe ser explícita; nunca relajar seguridad de uso.
7. Mostrar proveedores asociados y mapa con marcadores identificados, no solo enlaces.
   Mantener dirección y enlace de apertura como alternativa si el mapa falla.
8. Si pide domicilio, calcular cobertura, envío y ventana estimada desde reglas conocidas.
   Si falta destino o calendario, preguntar o declarar la limitación; no inventar una fecha.
9. Mantener seguimientos: «mejor 100», «en Cali», «con domicilio», «otra opción más barata».
   Una consulta ajena a productos se deriva al chat general, sin responderla en Cotizador.

El resumen separará subtotal e impuestos según la fuente, envío y total. Si el envío
se desconoce, no presentar el subtotal como total final. No sumar monedas distintas.

## 5. Implementación prevista para después de Spec Kit

| Etapa | Trabajo | Evidencia de cierre |
|---|---|---|
| 1. Datos y contrato | Definir ofertas por presentación, proveedor/sede, stock y entrega; validar fixtures demo | Datos consistentes, trazables y separados del catálogo real |
| 2. Motor | Reutilizar búsqueda/materiales; añadir cálculo por oferta, stock y entrega deterministas | Pruebas de cantidades, dinero, ausencia de datos y calendario |
| 3. Conversación | Ruta comercial exclusiva; aclaraciones y seguimientos; LLM solo interpreta/redacta | No responde temas ajenos ni inventa cifras |
| 4. Sección web | Navegación Cotizador, chat reutilizado, resumen de cotización y mapa | Flujo completo móvil/escritorio, sin romper el chat |
| 5. Validación | Escenarios demo, regresión y documentación del alcance real/simulado | Pruebas y demostración manual; actualización de CONSTITUTION.md |

### Puntos de integración y cautelas

- Reutilizar `apps/api/templates/chat.html`, sus parciales, estilos, SSE y voz existente,
  evitando mantener dos interfaces divergentes.
- Reutilizar `catalog/loader.py`, las capturas de `catalog/prices.py` y las herramientas de
  `agent/commercial.py`; extender sin cambiar silenciosamente el cálculo del chat actual.
- Separar hilos, historial y estado comercial entre Chat y Cotizador. Validar el modo de
  conversación en servidor, también al recibir un `thread_id`; no basta cambiar el prompt.
  Definir en Spec Kit la mínima adaptación de persistencia/checkpoints necesaria.
- Conservar identidad anónima, CSRF, cuota, manejo de errores, idempotencia y saneamiento
  de respuestas. No filtrar ubicaciones del cliente a trazas.
- El mapa se incrusta desde OpenStreetMap; la CSP habilita solo `frame-src` para ese origen.
  OpenStreetMap ve la IP del visitante, no la conversación ni el perfil.
- No hace falta geolocalización del navegador para mostrar proveedores. Pedir ciudad/zona
  es suficiente inicialmente. Si se reutiliza el dictado en `/cotizador`, adaptar la
  política de micrófono solo para ese documento, como ya ocurre en `/chat`.
- Incluir fixtures en la imagen Docker únicamente según la activación explícita de demo;
  comprobar que un despliegue no confunda datos sintéticos con el catálogo real.

## 6. Escenarios mínimos de demostración y prueba

1. «Cotízame 20 vasos para bebida caliente»: valida uso y explica presentación y sobrantes.
2. «Mejor 100, en Bogotá»: conserva producto y recalcula sin mezclar hilos.
3. Comparar dos ofertas para la misma referencia, con precio, stock y sede distintos.
4. Producto sin stock o stock insuficiente: no afirmar disponibilidad; ofrecer alternativa.
5. Producto existente sin precio: declarar ausencia, sin convertir precios mexicanos.
6. «Lo quiero a domicilio»: pedir destino faltante; luego mostrar envío y ventana estimada.
7. Destino sin cobertura, pedido después del corte o en fin de semana: respuesta coherente.
8. Mapa con marcadores y etiquetas; sin coordenadas o sin red, conservar datos textuales.
9. Solicitud institucional o producto fuera del catálogo: explicar límite, no inventar.
10. Modo demo desactivado: no quedan stock, proveedores ni entregas ficticios en resultados.
11. Pruebas de cero/negativos, mínimos, múltiplos, impuestos, redondeo y stock desconocido.
12. Regresión de chat, historial, CSRF, cuota, SSE y aislamiento de conversaciones.

## 7. Conjunto de demostración generado (2026-11-21)

`scripts/generar_demo_cotizador.py` escribe en `data/demo/cotizador/` un conjunto
reproducible que no toca el catálogo curado ni se indexa como fuente del corpus.

| Archivo | Contenido |
|---|---|
| `productos.csv` | Solo las 48 referencias simuladas de Colombia; las 102 reales se leen del catálogo curado |
| `ofertas.csv` | 160 ofertas de Colombia (29 con precio real de EDEXA) y 98 de México con precio oficial |
| `proveedores.csv` | Las 29 sedes del directorio oficial con coordenadas aproximadas |
| `geocode_sedes.json` | Caché de la geocodificación, para regenerar sin red |
| `fichas/*.md` | Una ficha citable por oferta, con procedencia y campos simulados |
| `manifest.json` | Conteos, fuentes, reglas de simulación, aviso de demo y limitaciones |

- **Qué es real:** los productos vienen del catálogo curado (una sola fuente), y también son
  reales sus materiales, capacidades, unidades por presentación, precios
  de EDEXA y de la tienda mexicana, y el directorio de sedes con su teléfono y dirección.
- **Qué es simulado:** los 48 productos colombianos que solo existen en México (marcados
  `origin=simulada` con nota), el precio cuando ninguna fuente publica uno, todas las
  existencias, los plazos y el costo de envío. Cada oferta declara sus campos simulados
  en la columna `simulated`.
- **Coordenadas:** 12 con precisión de dirección, 12 de vía y 5 de ciudad (centroide). Se
  obtuvieron con OpenStreetMap/Nominatim el 2026-11-21 y son aproximadas: la demo debe
  decir «ubicación aproximada» y nunca presentar el centroide como una sede exacta.
- **Reglas de simulación:** corte a las 15:00, días hábiles con festivos de Colombia,
  envío de 12.000 COP en Bogotá, 18.000 en Medellín y Cali y 25.000 en el resto, gratis
  desde 500.000 COP. Hay casos forzados de agotado, insuficiente y desconocido para que la
  demo pueda mostrar que el producto no está.
- **Límite:** una oferta simulada no es una oferta comercial. El aviso del manifiesto es el
  texto que la interfaz debe mostrar al entrar al Cotizador.

### 7.1 Presentación del flujo

Todo el flujo se presenta **inline en el hilo del chat**, con los tokens del mockup
institucional (`prompt-carvajal-ui-institucional.md`): tarjetas de oferta dentro de la
respuesta, lista de proveedores con su marcador, mapa y ventana de entrega en el mismo
turno, sin panel lateral ni pantalla aparte. En móvil los mismos bloques se apilan y el
mapa conserva la lista textual como respaldo.

## 8. Decisiones que llevaremos a Spec Kit

- Confirmar Colombia/COP y empaques como alcance inicial; otras líneas necesitan catálogo.
- Confirmar simulación explícita, conjunto demo y ciudades de la demostración.
- Acordar venta por presentaciones conocidas; no asumir unidades sueltas.
- Elegir mapa y política de recursos externos; no prometer funcionamiento sin conexión.
- Precisar días hábiles, cortes y festivos del calendario simulado antes de mostrar fechas.
- Definir comportamiento de precios de referencia frente a ofertas simuladas.

**Siguiente paso:** revisar este alcance y convertirlo en especificación, aclaraciones,
plan técnico y tareas mediante Spec Kit. Este documento no autoriza todavía cambios de
runtime ni representa integraciones comerciales existentes.
