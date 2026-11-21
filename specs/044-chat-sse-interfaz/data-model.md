# Data Model — 044 Reglas compartidas, lector SSE único y HTMX por pantalla

No hay entidades persistidas nuevas ni cambios de esquema. Los "datos" de esta
refactorización son contratos en memoria y de cable.

## Entidades

### Entrada del chat (`ChatEntry`)

| Campo | Tipo | Restricciones |
|---|---|---|
| `path` | `str` | Uno de `/chat`, `/chat/stream`, `/api/chat/stream`; no cambia. |
| `method` | `str` | `POST`; `GET` en `/chat` solo para la página. |
| `question` | `str` | Se recorta con `strip`; vacía ⇒ 422; longitud > 500 ⇒ 422. |
| `csrf_token` | `str` | Se compara con la cookie CSRF en bytes; inválido ⇒ 403. |
| `thread_id` | `str` | Opcional; vacío ⇒ `None`; hilo ajeno ⇒ 404. |
| `client_turn_id` | `str` | Opcional, solo streams; vacío ⇒ `None`; no se valida aquí. |

### Visitante (`Visitor`)

| Campo | Tipo | Restricciones |
|---|---|---|
| `identity` | `ChatIdentity` | Inyectada en `app.state.chat_identity` o la del proceso. |
| `owner_key` | `str` | Dueño derivado por la identidad (HMAC) o `new_owner_key()`. |
| `new_owner` | `bool` | `True` si no había cookie/visitante válido; decide si se emite cookie. |

### Cookie de identidad (`IdentityCookie`)

| Propiedad | Valor | Nota |
|---|---|---|
| Nombre | `carvajal_sesion` | Sin cambio. |
| `HttpOnly` | sí | Sin cambio. |
| `SameSite` | `Lax` | Sin cambio. |
| `Path` | `/` | Sin cambio. |
| `Max-Age` | 180 días | Sin cambio. |
| `Secure` | solo si la petición es HTTPS | Sin cambio. |
| Emisión | 303/200 aceptados; 429/503 con visitante nuevo; **no** en 403/422 de streams | Comportamiento actual que se conserva. |

### Evento SSE (`ChatEvent`)

| Campo | Tipo | Restricciones |
|---|---|---|
| `name` | Literal | `open`, `trace`, `meta`, `products`, `token`, `fallback`, `done`, `error`. |
| `data` | Mapping JSON | Serialización `ensure_ascii=False`; una línea `data:` por evento en el servidor. |

### Bloque SSE leído (`SseBlock`)

| Campo | Tipo | Restricciones |
|---|---|---|
| `nombre` | `str` | `event:`; por defecto `"message"`. |
| `datos` | objeto JSON | Una o más líneas `data:` unidas con `\n`; JSON inválido ⇒ se descarta. |

### Lector SSE (`CarvajalSSE`)

| Operación | Entrada | Salida |
|---|---|---|
| `leerEventos` | `response` con `body.getReader()`, callback | Invoca el callback por bloque; al cerrar procesa el último bloque sin separador. |
| `interpretar` | bloque `str` | `{nombre, datos}` o `null` si está vacío/sin `data:`/JSON inválido. |

## Reglas de validación (fuente única)

| Regla | Valor | Mensaje |
|---|---|---|
| Longitud máxima | 500 caracteres (`strip` previo) | `La pregunta no puede superar los 500 caracteres.` |
| Pregunta vacía | tras `strip` | `La pregunta no puede estar vacía.` |
| Cuota agotada | `QuotaResult.allowed == False` | `Has alcanzado el límite diario de preguntas. Vuelve a intentarlo mañana.` |
| CSRF inválido | token distinto o ausente | `La sesión del formulario expiró. Recarga la página e inténtalo de nuevo.` |
| Servicio no disponible | error de proveedor/BD | `El servicio de chat no está disponible en este momento.` |

## Transiciones (orden de controles)

```text
POST /chat:          identidad → CSRF → validación → cuota → proveedor → 303
POST /chat/stream:   CSRF → validación → identidad → cuota → stream
POST /api/chat/stream: CSRF → validación → identidad → cuota → stream
POST /api/faq:       CSRF → validación de exclude → identidad → preparar → cuota → stream
```

Estas transiciones no cambian; las pruebas de caracterización las fijan.
