# Datos y estados: chat por voz

No hay cambios al esquema, archivos de audio, blobs ni telemetría de voz. Las APIs
del navegador manejan el audio; la aplicación recibe texto y eventos. Solo el texto
enviado entra al historial normal del chat.

| Entidad | Campos y restricciones | Ciclo |
|---|---|---|
| Dictado | `transcript`: texto temporal; `draft`: texto editable de **máximo 500 caracteres**, sin cortar un par sustituto UTF-16; `locale`: español; **máximo 60 segundos por pregunta** | inactivo → aviso si aplica → escuchando → procesando → borrador/inactivo; error o cancelación preserva el borrador previo |
| Lectura | Referencia DOM del turno visible; texto y segmentos de tarjeta/cita construidos desde el render; sin URLs ni códigos técnicos en los segmentos | detenida → leyendo ↔ pausada → detenida; el disparo automático requiere `done` del stream; termina o cancela al ocultar/cambiar el hilo o al iniciar dictado |
| Preferencias del navegador | `autoRead`: booleano, **apagado por defecto**; `dictationNoticeAccepted`: booleano, **apagado por defecto** | `localStorage` del origen; ante almacenamiento no disponible, se conserva solo en memoria de la página |
| Sesión manos libres | `enabled`: booleano, **apagado al entrar o recargar**; identificador efímero del ciclo y del hilo; **borrador vacío** al activar y antes de insertar una transcripción; **10 segundos sin voz** para detener | desactivada → escuchando → esperando respuesta → leyendo → escuchando; escritura manual, «terminar» como orden aislada, parada manual, ocultación o error → desactivada |

Dictado y lectura son mutuamente excluyentes. Los callbacks tardíos de un ciclo
cancelado o de otro hilo no pueden sobrescribir el borrador, hablar ni enviar.
Los identificadores de ciclo no se persisten ni se envían a la red.
