# Validación del piloto — Spec 043

Ejecutar después de incorporar los cambios, en un entorno local o de pruebas
autorizado, con PostgreSQL, Qdrant y el proveedor utilizado por la aplicación.
Usar las instrucciones del repo para arrancar el stack y registrar el commit,
versión del catálogo, fecha y servicios usados.

## Datos y calidad

Desde la raíz del repo:

```bash
uv run python -m carvajal.commercial validar
uv run python -m carvajal.commercial paridad-mx
make check
```

En la captura de referencia, comprobar 29 precios CO y paridad MX 98/98.
Verificar precios CO por SKU, fuente y presentación. Los SKU que la demo
ocultaba son `CO-PPAN-12`, `7704918105071` y `7704918100816`. Una
actualización posterior del catálogo requiere indicar la captura usada.

## Escenarios

| Caso | Evidencia esperada |
| --- | --- |
| Arranque limpio y actualización | El stack termina sin archivos de demo, informa el catálogo esperado y cotiza. La actualización conserva Gold y conversaciones y la reversión mantiene compatibilidad. |
| Sedes sin precio | Cali muestra cuatro contactos en la captura auditada. La cantidad no es obligatoria. El stock aparece por confirmar. Una ciudad ausente recibe el estado de falta de cobertura. |
| Cálculo y envío | SKU `7704918100083`, fuente EDEXA, paquete de 50 por 2.794 COP con IVA incluido en la captura del 18-11. 300 unidades producen 6 paquetes y subtotal de 16.764 COP. 301 producen 7 paquetes, 49 sobrantes y subtotal de 19.558 COP. Con envío desconocido queda pendiente el total. |
| Consulta mixta y seguimiento | Preguntar en el mismo turno por material para comida caliente y compra en Cali. Cambiar ciudad y cantidad. Verificar citas, ficha, estados y controles. |
| Reintento | Interrumpir después de persistir. Reenviar el mismo identificador por SSE y HTML. Repetir con dos peticiones concurrentes de primer turno y con un hilo existente. Contar un turno y un folio por envío. |
| Historial | Abrir un histórico real, uno mezclado y uno ambiguo. Verificar lectura autorizada, exclusión del texto y tarjetas no publicables y rechazo para otra identidad. |
| Después del retiro | Verificar redirecciones, navegación, enlaces a fuentes, dictado, protección de la ingesta y compatibilidad de consumidores de API. |

## Resultado

Registrar aprobados, fallidos y pendientes por escenario. Medir mediana y p95
del primer contenido útil y del cierre del turno. Probar 10 sesiones
concurrentes cuando el entorno lo permita y documentar las condiciones y la
muestra. Repetir las pruebas afectadas por cada corrección. La disponibilidad
externa se valida con evidencia del proveedor cuando exista.
