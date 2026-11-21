// Stream SSE del chat (W3.4).
// Degrada al POST normal si falta soporte de streaming o la conexión falla
// antes del primer token; el servidor solo persiste el turno al recibir done.
(() => {
  "use strict";

  const form = document.querySelector("form[data-chat-stream]");
  if (!form) return;

  const soportaStreaming =
    typeof window.fetch === "function" &&
    typeof window.TextDecoder === "function" &&
    typeof window.ReadableStream === "function";
  if (!soportaStreaming) return;

  const input = form.querySelector('input[name="question"]');
  const csrf = form.querySelector('input[name="csrf_token"]');
  const threadInput = form.querySelector("[data-thread-id]");
  const turnIdInput = form.querySelector("[data-client-turn-id]");
  const boton = form.querySelector('button[type="submit"]');
  const traza = document.getElementById("chat-traza");
  const hilo = document.getElementById("hilo");
  const estado = document.getElementById("chat-stream-estado");
  const estadoTexto = estado ? estado.querySelector("[data-estado-texto]") : null;
  const girando = estado ? estado.querySelector("[data-girando]") : null;
  const plantillaTurno = document.getElementById("plantilla-turno");
  const formateador = window.CarvajalTexto;
  if (!input || !hilo || !estado || !plantillaTurno || !formateador) return;

  const raizMonitoreo = document.querySelector("[data-monitoring]");
  const AVISO = "No se pudo completar la respuesta. Vuelve a intentarlo.";
  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const SEPARADOR = /\r?\n\r?\n/;
  let enCurso = false;
  let completado = false;
  let errorRecibido = false;

  form.addEventListener("submit", (event) => {
    if (!soportaStreaming) return; // el navegador envía el POST de siempre
    event.preventDefault();
    // Origen solo en el cliente: se lee y se limpia antes de cualquier salida
    // temprana para no tratar un envío manual posterior como manos libres.
    const manosLibres = form.dataset.vozOrigen === "manos-libres";
    delete form.dataset.vozOrigen;
    if (enCurso) return;

    const pregunta = input.value.trim();
    if (!pregunta) {
      avisar("Escribe una pregunta antes de enviar.", true);
      input.focus();
      return;
    }
    if (pregunta.length > 500) {
      avisar("La pregunta no puede superar los 500 caracteres.", true);
      input.focus();
      return;
    }
    enCurso = true;
    completado = false;
    errorRecibido = false;
    const turnId = nuevoTurnoId();
    if (turnIdInput) turnIdInput.value = turnId;
    void responder(pregunta, turnId, manosLibres);
  });

  // Marcas del visitante (spec 038, US4): solo con el monitoreo activo y con
  // el request_id del turno. No recargan la página y no fuerzan una traza.
  function monitoreoActivo() {
    return Boolean(raizMonitoreo) && raizMonitoreo.dataset.monitoring === "1";
  }

  function crearMarcas(turno, requestId) {
    if (!monitoreoActivo() || !requestId) return;
    const contenedor = turno.querySelector("[data-marcas]");
    if (!contenedor) return;
    contenedor.dataset.requestId = String(requestId);
    contenedor.hidden = false;
    contenedor.querySelectorAll("button[data-marca]").forEach((boton) => {
      boton.addEventListener("click", () => void marcar(contenedor, boton));
    });
  }

  async function marcar(contenedor, boton) {
    const requestId = contenedor.dataset.requestId || "";
    if (!requestId || contenedor.dataset.enviada === "1") return;
    const botones = contenedor.querySelectorAll("button[data-marca]");
    botones.forEach((cada) => {
      cada.disabled = true;
    });
    try {
      const respuesta = await fetch("/chat/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          request_id: requestId,
          useful: boton.dataset.marca === "1" ? "1" : "0",
          csrf_token: csrf ? csrf.value : "",
        }),
      });
      if (!respuesta.ok) throw new Error(`respuesta ${respuesta.status}`);
      contenedor.dataset.enviada = "1";
      const gracias = contenedor.querySelector("[data-marcas-gracias]");
      if (gracias) gracias.hidden = false;
      botones.forEach((cada) => {
        cada.hidden = cada !== boton;
      });
    } catch {
      botones.forEach((cada) => {
        cada.disabled = false;
      });
      avisar("No se pudo guardar tu marca. Inténtalo de nuevo.", true);
    }
  }

  function nuevoTurnoId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `turno-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function responder(pregunta, turnId, manosLibres) {
    quitarVacio();
    const lista = asegurarLista();
    const turnoUsuario = crearTurno("user", pregunta);
    const turnoAsistente = crearTurno("assistant", "");
    lista.append(turnoUsuario, turnoAsistente);
    const texto = turnoAsistente.querySelector(".texto");
    const flujo = formateador.crearFlujo(texto);
    let cantidadFuentes = 0;
    const guardarFuentes = (fuentes) => {
      const validas = Array.isArray(fuentes) ? fuentes : [];
      cantidadFuentes = validas.length;
      turnoAsistente.dataset.vozFuentes = JSON.stringify(
        validas.map((fuente) => fuente?.title || fuente?.slug || ""),
      );
    };
    input.value = "";
    form.setAttribute("aria-busy", "true");
    if (boton) boton.disabled = true;
    avisar("");

    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 120000);
    let cerrado = false;
    let recibioContenido = false;
    try {
      const response = await fetch("/chat/stream", {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          question: pregunta,
          csrf_token: csrf ? csrf.value : "",
          client_turn_id: turnId,
          thread_id: threadInput ? threadInput.value : "",
        }),
        signal: control.signal,
      });
      const tipo = response.headers.get("content-type") ?? "";
      // Los errores SSE de la API traen su propio mensaje. Un reenvío
      // automático puede volver a consumir cuota para el mismo turno.
      if (!response.body || !tipo.includes("text/event-stream")) {
        if (!response.ok) cerrado = true;
        throw new Error(`respuesta ${response.status}`);
      }
      if (!response.ok) cerrado = true;

      await leerEventos(response, (evento) => {
        switch (evento.nombre) {
          case "open":
            avisar("Recuperando fuentes…");
            break;
          case "trace":
            if (threadInput && evento.datos.thread_id) {
              threadInput.value = String(evento.datos.thread_id);
            }
            pintarTraza(evento.datos);
            avisar(
              evento.datos.route === "tool"
                ? "Consultando la herramienta…"
                : evento.datos.route === "commercial"
                  ? "Consultando el catálogo de empaques…"
                  : evento.datos.route === "direct"
                  ? "Respondiendo sin búsqueda…"
                  : "Buscando en el corpus…",
            );
            break;
          case "meta":
            guardarFuentes(evento.datos.sources);
            avisar("Redactando…");
            break;
          case "products":
            pintarProductos(turnoAsistente, String(evento.datos.html ?? ""));
            break;
          case "token": {
            const fragmento = String(evento.datos.text ?? "");
            flujo.agregar(fragmento, cantidadFuentes);
            if (fragmento) recibioContenido = true;
            break;
          }
          case "fallback":
            guardarFuentes(evento.datos.sources);
            flujo.reemplazar(String(evento.datos.text ?? ""), cantidadFuentes);
            recibioContenido = true;
            avisar("Respuesta completa sin streaming.");
            break;
          case "done":
            cerrado = true;
            if (threadInput && evento.datos.thread_id) {
              threadInput.value = String(evento.datos.thread_id);
            }
            flujo.terminar(cantidadFuentes);
            crearMarcas(turnoAsistente, evento.datos.request_id);
            avisar("");
            completado = true;
            break;
          case "error":
            cerrado = true;
            errorRecibido = true;
            flujo.terminar(cantidadFuentes);
            if (!texto.textContent) {
              flujo.reemplazar(String(evento.datos.message ?? INCOMPLETA));
            }
            avisar(String(evento.datos.message ?? INCOMPLETA), true);
            break;
          default:
            break;
        }
      });

      flujo.terminar(cantidadFuentes);
      if (!response.ok && !texto.textContent) {
        flujo.reemplazar(AVISO);
        avisar(AVISO, true);
      } else if (!cerrado) {
        avisar(INCOMPLETA, true);
        if (!texto.textContent) flujo.reemplazar(INCOMPLETA);
      }
    } catch (error) {
      flujo.terminar(cantidadFuentes);
      if (manosLibres) {
        // Manos libres nunca reenvía por el POST normal: devuelve el texto al
        // formulario para revisión y deja que el controlador apague el modo.
        if (!recibioContenido && !cerrado) {
          input.value = pregunta;
          turnoUsuario.remove();
          turnoAsistente.remove();
          avisar("La conexión falló; revisa el texto y envíalo cuando quieras.", true);
          return;
        }
        avisar(error && error.name === "AbortError" ? INCOMPLETA : AVISO, true);
        if (!texto.textContent) turnoAsistente.remove();
      } else if (!recibioContenido && !cerrado) {
        // Falló antes del primer token y el servidor no persistió el turno:
        // restaura la pregunta y reintenta por el POST normal del formulario.
        input.value = pregunta;
        turnoUsuario.remove();
        turnoAsistente.remove();
        avisar("La conexión falló; enviando la pregunta sin streaming…");
        form.submit(); // HTMLFormElement.submit() no dispara el listener de submit
        return;
      } else {
        avisar(error && error.name === "AbortError" ? INCOMPLETA : AVISO, true);
        if (!texto.textContent) turnoAsistente.remove();
      }
    } finally {
      clearTimeout(temporizador);
      form.removeAttribute("aria-busy");
      if (boton) boton.disabled = false;
      input.focus();
      turnoAsistente.scrollIntoView({ block: "nearest" });
      enCurso = false;
      if (girando) girando.hidden = true;
      // Notificación interna (spec 040): solo tras un stream con done, con el
      // turno ya formateado y el formulario liberado. No hay audio ni red.
      // El contrato no emite turno completo si hubo error, aunque llegue done.
      if (completado && !errorRecibido) {
        notificarTurnoCompleto(turnoAsistente, manosLibres);
      } else if (manosLibres) {
        // Solo se devuelve el texto si el turno no llegó a persistirse; con
        // contenido recibido, reenviarlo duplicaría la pregunta.
        if (!recibioContenido && !input.value.trim()) input.value = pregunta;
        notificarTurnoFallido(turnoAsistente, threadInput ? threadInput.value : "", true);
      }
    }
  }

  function notificarTurnoCompleto(turno, manosLibres) {
    if (typeof window.CustomEvent !== "function") return;
    document.dispatchEvent(
      new window.CustomEvent("chat:turn-complete", {
        detail: {
          turno,
          threadId: threadInput ? String(threadInput.value || "") : "",
          handsFree: Boolean(manosLibres),
        },
      }),
    );
  }

  function notificarTurnoFallido(turno, threadId, manosLibres) {
    if (typeof window.CustomEvent !== "function") return;
    document.dispatchEvent(
      new window.CustomEvent("chat:turn-failed", {
        detail: {
          turno,
          threadId: threadId === undefined || threadId === null ? "" : String(threadId),
          handsFree: Boolean(manosLibres),
        },
      }),
    );
  }

  function crearTurno(rol, contenido) {
    const turno = plantillaTurno.content.firstElementChild.cloneNode(true);
    turno.classList.add(`turno-${rol}`);
    turno.querySelector(".turno-rol").textContent = rol === "user" ? "Tú" : "Carva";
    turno.querySelector(".texto").textContent = contenido;
    return turno;
  }

  // Tarjetas de la ruta comercial (spec 036). El HTML llega renderizado y escapado por
  // Jinja en el servidor (_productos.html), igual que los parciales que inserta app.js.
  function pintarProductos(turno, html) {
    turno.querySelector(".productos")?.remove();
    if (!html) return;
    const plantilla = document.createElement("template");
    plantilla.innerHTML = html;
    const tarjetas = plantilla.content.firstElementChild;
    if (tarjetas) turno.querySelector(".burbuja")?.after(tarjetas);
  }

  function pintarTraza(datos) {
    if (!traza) return;
    const campo = (nombre) => traza.querySelector(`[data-campo="${nombre}"]`);
    const route = campo("route");
    if (route) route.textContent = String(datos.route || "");
    const toolWrap = campo("tool-wrap");
    const tool = campo("tool");
    if (tool && toolWrap) {
      const nombre = String(datos.tool || "");
      toolWrap.hidden = !nombre;
      tool.textContent = nombre;
    }
    const memoryWrap = campo("memory-wrap");
    const memory = campo("memory");
    if (memory && memoryWrap) {
      const cantidad = Number(datos.memory || 0);
      memoryWrap.hidden = !Number.isFinite(cantidad) || cantidad <= 0;
      memory.textContent = String(cantidad || 0);
    }
    const thread = campo("thread");
    if (thread) thread.textContent = String(datos.thread_id || "");
    traza.hidden = false;
  }

  function quitarVacio() {
    const vacio = hilo.querySelector(".vacio");
    if (vacio) vacio.remove();
  }

  function asegurarLista() {
    let lista = hilo.querySelector("ol.hilo");
    const sueltos = Array.from(hilo.querySelectorAll(":scope > li.turno"));
    if (!lista) {
      lista = document.createElement("ol");
      lista.className = "hilo";
      hilo.append(lista);
    }
    if (sueltos.length > 0) lista.append(...sueltos);
    return lista;
  }

  function avisar(mensaje, esError = false) {
    const texto = String(mensaje == null ? "" : mensaje);
    const ocupado = enCurso && !esError;
    const visible = ocupado ? (texto ? `Consultando… · ${texto}` : "Consultando…") : texto;
    if (estadoTexto) estadoTexto.textContent = visible;
    else estado.textContent = visible;
    if (girando) girando.hidden = !ocupado;
    estado.classList.toggle("aviso", esError);
    estado.classList.toggle("ayuda", !esError);
  }

  async function leerEventos(response, alEvento) {
    const lector = response.body.getReader();
    const decodificador = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      buffer += decodificador.decode(value, { stream: true });
      buffer = vaciarBloques(buffer, alEvento);
    }
    buffer += decodificador.decode();
    buffer = vaciarBloques(buffer, alEvento);
    const ultimo = interpretar(buffer);
    if (ultimo) alEvento(ultimo);
  }

  function vaciarBloques(buffer, alEvento) {
    for (;;) {
      const corte = SEPARADOR.exec(buffer);
      if (!corte) return buffer;
      const bloque = buffer.slice(0, corte.index);
      buffer = buffer.slice(corte.index + corte[0].length);
      const evento = interpretar(bloque);
      if (evento) alEvento(evento);
    }
  }

  function interpretar(bloque) {
    if (!bloque.trim()) return null;
    let nombre = "message";
    const datos = [];
    for (const linea of bloque.split(/\r?\n/)) {
      if (linea.startsWith("event:")) nombre = linea.slice(6).trim();
      else if (linea.startsWith("data:")) datos.push(linea.slice(5).trimStart());
    }
    if (datos.length === 0) return null;
    try {
      return { nombre, datos: JSON.parse(datos.join("\n")) };
    } catch {
      return null;
    }
  }
})();
