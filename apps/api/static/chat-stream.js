// Stream SSE del chat (W3.4).
// Degrada al POST normal si falta soporte de streaming o la conexión falla
// antes del primer token. Detener interrumpe la recepción, no el historial.
(() => {
  "use strict";

  const form = document.querySelector("form[data-chat-stream]");
  if (!form) return;

  const soportaStreaming =
    typeof window.fetch === "function" &&
    typeof window.TextDecoder === "function" &&
    typeof window.ReadableStream === "function";
  const input = form.querySelector('[name="question"]');
  const csrf = form.querySelector('input[name="csrf_token"]');
  const threadInput = form.querySelector("[data-thread-id]");
  const turnIdInput = form.querySelector("[data-client-turn-id]");
  const boton = form.querySelector('button[type="submit"]');
  const detener = form.querySelector("[data-chat-detener]");
  const traza = new URLSearchParams(window.location.search).get("debug") === "1"
    ? document.getElementById("chat-traza")
    : null;
  const hilo = document.getElementById("hilo");
  const historial = document.querySelector(".historial-chat");
  const irAlFinal = document.querySelector("[data-chat-final]");
  const estado = document.getElementById("chat-stream-estado");
  const plantillaTurno = document.getElementById("plantilla-turno");
  const formateador = window.CarvajalTexto;
  if (!input || !hilo || !estado || !historial) return;

  const raizMonitoreo = document.querySelector("[data-monitoring]");
  const AVISO = "No se pudo completar la respuesta. Vuelve a intentarlo.";
  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const SEPARADOR = /\r?\n\r?\n/;
  let enCurso = false;
  let seguir = true;
  let detenerTurno = null;

  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!enCurso) form.requestSubmit();
  });

  const bienvenida = hilo.querySelector(".chat-bienvenida");
  const sugerencias = hilo.querySelector("[data-chat-sugerencias]");
  if (sugerencias) sugerencias.hidden = false;
  hilo.addEventListener("click", (event) => {
    const sugerencia = event.target.closest("[data-chat-sugerencia]");
    if (!sugerencia || enCurso) return;
    input.value = sugerencia.textContent.trim();
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus({ preventScroll: true });
  });

  historial.addEventListener("scroll", () => {
    seguir = cercaDelFinal();
    actualizarAccesoAlFinal();
  }, { passive: true });
  irAlFinal?.addEventListener("click", () => {
    seguir = true;
    historial.focus({ preventScroll: true });
    ajustarHistorial();
  });
  if (typeof window.ResizeObserver === "function") {
    new window.ResizeObserver(ajustarHistorial).observe(historial);
  }
  ajustarHistorial();

  if (!soportaStreaming || !plantillaTurno || !formateador) return;
  detener?.addEventListener("click", () => detenerTurno?.());

  form.addEventListener("submit", (event) => {
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
    seguir = true;
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
    const controlesVoz = turnoAsistente.querySelector("[data-voz-turno]");
    if (controlesVoz) controlesVoz.hidden = true;
    lista.append(turnoUsuario, turnoAsistente);
    const texto = turnoAsistente.querySelector(".texto");
    const flujo = formateador.crearFlujo(texto);
    const espera = setTimeout(() => avisar("Pensando…"), 300);
    // Progreso del agente (evento status, spec 044): vive en la misma línea de
    // estado que «Pensando…» y desaparece con el primer token, fallback o error.
    let progreso = "";
    let progresoCerrado = false;
    function quitarProgreso() {
      clearTimeout(espera);
      progresoCerrado = true;
      const vigente = estado.textContent;
      if (vigente === "Pensando…" || (progreso && vigente === progreso)) avisar("");
      progreso = "";
    }
    let cantidadFuentes = 0;
    let lote = [];
    let repintado;
    let completado = false;
    let errorRecibido = false;
    let cancelado = false;
    let finalizado = false;
    function pintarLote(provisional, interrumpido = false) {
      clearTimeout(repintado);
      repintado = undefined;
      if (lote.length) flujo.agregar(lote.join(""), cantidadFuentes);
      lote = [];
      if (provisional) flujo.adelantar(cantidadFuentes);
      else {
        flujo.terminar(cantidadFuentes, interrumpido);
        finalizado = true;
      }
      if (texto.textContent || !provisional) quitarProgreso();
      ajustarHistorial();
    }
    const guardarFuentes = (fuentes) => {
      const validas = Array.isArray(fuentes) ? fuentes : [];
      cantidadFuentes = validas.length;
      turnoAsistente.dataset.vozFuentes = JSON.stringify(
        validas.map((fuente) => fuente?.title || fuente?.slug || ""),
      );
    };
    input.value = "";
    form.setAttribute("aria-busy", "true");
    const focoEnEnviar = document.activeElement === boton;
    if (boton) boton.hidden = true;
    if (detener) {
      detener.disabled = false;
      detener.hidden = false;
      if (focoEnEnviar) detener.focus({ preventScroll: true });
    }
    ajustarHistorial();
    avisar("");

    const control = new AbortController();
    detenerTurno = () => {
      if (control.signal.aborted || completado) return;
      cancelado = true;
      control.abort();
    };
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
          case "trace":
            if (threadInput && evento.datos.thread_id) {
              threadInput.value = String(evento.datos.thread_id);
            }
            pintarTraza(evento.datos);
            break;
          case "meta":
            guardarFuentes(evento.datos.sources);
            break;
          case "products":
            pintarProductos(turnoAsistente, String(evento.datos.html ?? ""));
            ajustarHistorial();
            break;
          case "status": {
            // Solo texto: el mensaje sale de plantillas fijas, pero puede citar lo
            // que escribió la persona. Nunca se persiste ni llega en el replay.
            const aviso = String(evento.datos.text ?? "").trim();
            if (!aviso || progresoCerrado) break;
            clearTimeout(espera);
            progreso = aviso;
            avisar(aviso);
            break;
          }
          case "token": {
            const fragmento = String(evento.datos.text ?? "");
            if (!fragmento) break;
            if (!progresoCerrado) quitarProgreso();
            recibioContenido = true;
            lote.push(fragmento);
            if (repintado === undefined) repintado = setTimeout(() => pintarLote(true), 40);
            break;
          }
          case "fallback":
            quitarProgreso();
            avisar("");
            clearTimeout(repintado);
            repintado = undefined;
            lote = [];
            guardarFuentes(evento.datos.sources);
            flujo.reemplazar(String(evento.datos.text ?? ""), cantidadFuentes);
            recibioContenido = true;
            ajustarHistorial();
            break;
          case "done":
            cerrado = true;
            if (threadInput && evento.datos.thread_id) {
              threadInput.value = String(evento.datos.thread_id);
            }
            if (evento.datos.quote_id) {
              turnoAsistente.dataset.quoteId = String(evento.datos.quote_id);
            }
            pintarLote(false, errorRecibido);
            crearMarcas(turnoAsistente, evento.datos.request_id);
            completado = true;
            if (detener) detener.disabled = true;
            break;
          case "error":
            cerrado = true;
            errorRecibido = true;
            quitarProgreso();
            pintarLote(false, true);
            if (!texto.textContent) {
              flujo.reemplazar(String(evento.datos.message ?? INCOMPLETA));
            }
            avisar(String(evento.datos.message ?? INCOMPLETA), true);
            break;
          default:
            break;
        }
      });

      if (cancelado) throw control.signal.reason;
      if (!finalizado) pintarLote(false, !completado || errorRecibido);
      if (!response.ok && !texto.textContent) {
        flujo.reemplazar(AVISO);
        avisar(AVISO, true);
      } else if (!cerrado) {
        avisar(INCOMPLETA, true);
        if (!texto.textContent) flujo.reemplazar(INCOMPLETA);
      }
    } catch (error) {
      if (!finalizado) pintarLote(false, true);
      if (cancelado) {
        if (texto.textContent || turnoAsistente.querySelector(".productos")) {
          const marca = turnoAsistente.querySelector("[data-chat-detenido]");
          if (marca) marca.hidden = false;
        } else {
          turnoUsuario.remove();
          turnoAsistente.remove();
          if (!lista.children.length && bienvenida) hilo.append(bienvenida);
          if (!input.value) input.value = pregunta;
        }
        avisar("Respuesta detenida.");
        return;
      }
      if (manosLibres) {
        // Manos libres nunca reenvía por el POST normal: devuelve el texto al
        // formulario para revisión y deja que el controlador apague el modo.
        if (!recibioContenido && !cerrado) {
          if (!input.value) input.value = pregunta;
          turnoUsuario.remove();
          turnoAsistente.remove();
          avisar("La conexión falló; revisa el texto y envíalo cuando quieras.", true);
          return;
        }
        avisar(error && error.name === "AbortError" ? INCOMPLETA : AVISO, true);
        if (!texto.textContent) turnoAsistente.remove();
      } else if (!recibioContenido && !cerrado && !input.value) {
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
      clearTimeout(espera);
      clearTimeout(repintado);
      detenerTurno = null;
      form.removeAttribute("aria-busy");
      if (boton) boton.hidden = false;
      if (detener) {
        const focoEnDetener = document.activeElement === detener;
        detener.hidden = true;
        if (focoEnDetener) input.focus({ preventScroll: true });
      }
      enCurso = false;
      if (controlesVoz && (texto.textContent || turnoAsistente.querySelector(".productos"))) {
        controlesVoz.hidden = false;
      }
      ajustarHistorial();
      // Notificación interna (spec 040): solo tras un stream con done, con el
      // turno ya formateado y el formulario liberado. No hay audio ni red.
      // El contrato no emite turno completo si hubo error, aunque llegue done.
      if (completado && !errorRecibido && !cancelado) {
        avisar("Respuesta lista.", false, true);
        notificarTurnoCompleto(turnoAsistente, manosLibres);
      } else if (manosLibres) {
        // Sin `done` el servidor no persiste el turno: se devuelve siempre la
        // pregunta para que la persona pueda revisarla y reenviarla.
        if (!input.value.trim()) input.value = pregunta;
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
    turno.querySelector("[data-turno-autor]").textContent = rol === "user" ? "Tú" : "Carva";
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
    const decisionWrap = campo("decision-wrap");
    const decision = campo("decision");
    if (decision && decisionWrap) {
      const texto = textoDecision(datos.gatekeeper);
      decisionWrap.hidden = !texto;
      decision.textContent = texto;
    }
    traza.hidden = false;
  }

  // Spec 046: «rag · jev 0,83», «saludo · reglas», «todas · llm», «cautela · jev 0,41».
  // Solo metadatos de la decisión; solo se ve con ``?debug=1``.
  const ALCANCES = { direct: "directa", rag: "rag", structured: "estructurada", all: "todas" };
  const REGLAS = {
    greeting: "saludo",
    farewell: "despedida",
    memory_forget: "olvido",
    memory_unavailable: "olvido",
    memory_write: "preferencia",
    commercial_follow_up: "seguimiento",
  };

  function textoDecision(gatekeeper) {
    if (!gatekeeper || typeof gatekeeper !== "object") return "";
    const fuente = String(gatekeeper.source || "");
    if (!fuente) return "";
    let etiqueta;
    if (gatekeeper.cautious) etiqueta = "cautela";
    else if (fuente === "reglas" && gatekeeper.rule)
      etiqueta = REGLAS[gatekeeper.rule] || String(gatekeeper.rule);
    else etiqueta = ALCANCES[gatekeeper.scope] || String(gatekeeper.gate_class || "");
    const confianza = Number(gatekeeper.confidence);
    const cifra =
      fuente === "jev" && Number.isFinite(confianza)
        ? ` ${confianza.toFixed(2).replace(".", ",")}`
        : "";
    return `${etiqueta} · ${fuente}${cifra}`;
  }

  function quitarVacio() {
    bienvenida?.remove();
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

  function cercaDelFinal() {
    return historial.scrollHeight - historial.clientHeight - historial.scrollTop <= 64;
  }

  function actualizarAccesoAlFinal() {
    if (irAlFinal) irAlFinal.hidden = cercaDelFinal();
  }

  function ajustarHistorial() {
    if (seguir) historial.scrollTop = historial.scrollHeight;
    actualizarAccesoAlFinal();
  }

  function avisar(mensaje, esError = false, soloLector = false) {
    estado.textContent = mensaje;
    estado.classList.toggle("aviso", esError);
    estado.classList.toggle("sr-solo", soloLector);
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
