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

  const AVISO = "No se pudo completar la respuesta. Vuelve a intentarlo.";
  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const SEPARADOR = /\r?\n\r?\n/;
  let enCurso = false;
  let seguir = true;
  // El historial sigue el final hasta que la persona lo mueve. Al cargar, el compositor y la
  // cotización cambian de alto después del primer ajuste, y el «scroll» de ese ajuste llegaba
  // con el final ya lejos: dejaba de seguir y en el teléfono quedaba 110 px arriba con «Ir al
  // final» visible (auditoría del 2026-11-27).
  let tocado = false;
  let detenerTurno = null;

  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!enCurso) form.requestSubmit();
  });

  const bienvenida = hilo.querySelector(".chat-bienvenida");
  const sugerencias = hilo.querySelector("[data-chat-sugerencias]");
  if (sugerencias) sugerencias.hidden = false;
  mostrarAcciones(hilo);
  actualizarAcciones();
  hilo.addEventListener("click", (event) => {
    const sugerencia = event.target.closest("[data-chat-sugerencia]");
    if (!sugerencia || enCurso) return;
    // Los temas del inicio muestran título y subtítulo; la pregunta va en data-pregunta.
    const pregunta = (sugerencia.dataset.pregunta || sugerencia.textContent).trim();
    input.value = pregunta;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus({ preventScroll: true });
    // Spec 053: «Elegir» y «Confirmar cotización» envían la frase en el acto, igual que si
    // la persona la escribiera o la dijera.
    if ("chatEnviar" in sugerencia.dataset) form.requestSubmit?.();
  });

  for (const tipo of ["wheel", "touchstart", "pointerdown"]) {
    historial.addEventListener(tipo, () => { tocado = true; }, { passive: true });
  }
  // Del teclado solo cuentan las teclas que desplazan; Tab o un atajo no mueven el historial.
  const teclasDeDesplazamiento = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
  historial.addEventListener("keydown", (event) => {
    if (teclasDeDesplazamiento.has(event.key)) tocado = true;
  });
  // Arrastrar la barra de desplazamiento no siempre emite eventos de puntero (Firefox): todo
  // scroll que sube cuenta como de la persona, porque los ajustes del código solo bajan.
  let ultimaPosicion = historial.scrollTop;
  historial.addEventListener("scroll", () => {
    if (historial.scrollTop < ultimaPosicion - 4) tocado = true;
    ultimaPosicion = historial.scrollTop;
    if (seguir && !tocado) {
      ajustarHistorial();
      return;
    }
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
    // chat-voice.js marca en captura que el borrador se dictó (rediseño 052).
    const porVoz = form.dataset.vozHablado === "1";
    delete form.dataset.vozHablado;
    if (enCurso) return;

    // Spec 051: con una foto adjunta (chat-photo.js) el texto es opcional.
    const foto = fotoAdjunta();
    const pregunta = input.value.trim();
    if (!pregunta && !foto) {
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
    void responder(pregunta, turnId, manosLibres, foto, porVoz);
  });

  function fotoAdjunta() {
    const api = window.CarvajalFoto;
    return api && typeof api.tomar === "function" ? api.tomar() : null;
  }

  function marcaFoto(pregunta) {
    return pregunta ? `📷 Foto de un alimento: ${pregunta}` : "📷 Foto de un alimento";
  }

  // Spec 053 (FR-016): como en WhatsApp, la foto arriba y debajo el texto que la acompaña.
  // Sin vista local (navegador sin `URL.createObjectURL`) queda la marca de texto de siempre.
  function pintarFotoEnviada(turno, foto, pregunta) {
    const api = window.CarvajalFoto;
    const url = api && typeof api.vista === "function" ? api.vista(foto) : "";
    const burbuja = turno.querySelector(".burbuja");
    const texto = turno.querySelector(".texto");
    if (!url || !burbuja || !texto || typeof burbuja.insertBefore !== "function") return "";
    const imagen = document.createElement("img");
    imagen.className = "turno-foto";
    imagen.alt = "Foto del alimento que enviaste";
    imagen.src = url;
    burbuja.insertBefore(imagen, texto);
    texto.textContent = pregunta;
    texto.hidden = !pregunta;
    return url;
  }

  // Spec 053 (FR-020): Carva con lupa sobre la foto mientras Jev la analiza.
  function mostrarMirando(turno, url) {
    const plantilla = document.getElementById("plantilla-mirando");
    const modelo = plantilla && plantilla.content && plantilla.content.firstElementChild;
    if (!modelo) return null;
    const bloque = modelo.cloneNode(true);
    const imagen = bloque.querySelector("[data-mirando-foto]");
    if (imagen && url) imagen.src = url;
    else if (imagen) imagen.closest(".foto-mirando-imagen")?.remove();
    turno.dataset.mirando = "1";
    turno.querySelector(".burbuja")?.prepend?.(bloque);
    return bloque;
  }

  function peticion(pregunta, turnId, foto, signal, hablada = false) {
    const thread = threadInput ? threadInput.value : "";
    if (foto) {
      // Multipart con el token CSRF en cabecera (se valida antes de leer el cuerpo).
      const cuerpo = new window.FormData();
      cuerpo.append("foto", foto, foto.name || "foto.jpg");
      cuerpo.append("texto", pregunta);
      cuerpo.append("client_turn_id", turnId);
      cuerpo.append("thread_id", thread);
      return fetch("/chat/foto", {
        method: "POST",
        headers: { Accept: "text/event-stream", "X-CSRF-Token": csrf ? csrf.value : "" },
        body: cuerpo,
        signal,
      });
    }
    return fetch("/chat/stream", {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        question: pregunta,
        csrf_token: csrf ? csrf.value : "",
        client_turn_id: turnId,
        thread_id: thread,
        // Solo una pregunta hablada lo envía: el servidor la guarda con la marca «voice»
        // y el historial ofrece audio únicamente en esas respuestas.
        ...(hablada ? { voz: "1" } : {}),
      }),
      signal,
    });
  }

  function nuevoTurnoId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `turno-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function responder(pregunta, turnId, manosLibres, foto = null, porVoz = false) {
    const turnoUsuario = crearTurno("user", foto ? marcaFoto(pregunta) : pregunta);
    const urlFoto = foto ? pintarFotoEnviada(turnoUsuario, foto, pregunta) : "";
    // Audio solo si la persona habló (borrador dictado o manos libres): una pregunta escrita
    // no trae nota ni «Escuchar». La marca coincide con la del historial recargado, y por eso
    // una foto no cuenta (/chat/foto no guarda el origen hablado): ni «por voz», ni audio, y
    // `spoken: false` en chat:turn-complete para que chat-voice.js tampoco la lea.
    const hablada = Boolean((porVoz || manosLibres) && !foto);
    const marcaVoz = turnoUsuario.querySelector("[data-turno-por-voz]");
    if (hablada && marcaVoz) {
      turnoUsuario.dataset.porVoz = "1";
      marcaVoz.hidden = false;
    }
    const turnoAsistente = crearTurno("assistant", "");
    if (hablada) turnoAsistente.dataset.vozPreguntaHablada = "1";
    // Mientras no llega texto, Carva «piensa» (puntos animados en chat.css); con foto,
    // «mira» con su lupa (spec 053).
    turnoAsistente.dataset.pensando = "1";
    const mirando = foto ? mostrarMirando(turnoAsistente, urlFoto) : null;
    const textoEspera = foto ? "Mirando tu foto…" : "Pensando…";
    const controlesVoz = turnoAsistente.querySelector("[data-voz-turno]");
    if (controlesVoz) controlesVoz.hidden = true;
    let lista;
    // Al salir del inicio el compositor baja de su sitio centrado al pie: se anima
    // con FLIP para que no salte. El DOM cambia en el acto (el resto del flujo lo usa).
    deslizar(form, Boolean(bienvenida && bienvenida.isConnected), () => {
      quitarVacio();
      lista = asegurarLista();
      lista.append(turnoUsuario, turnoAsistente);
    });
    actualizarAcciones();
    const texto = turnoAsistente.querySelector(".texto");
    const flujo = formateador.crearFlujo(texto);
    const espera = setTimeout(() => avisar(textoEspera), 300);
    // Progreso del agente (evento status, spec 044): vive en la misma línea de
    // estado que «Pensando…» y desaparece con el primer token, fallback o error.
    let progreso = "";
    let progresoCerrado = false;
    function quitarProgreso() {
      delete turnoAsistente.dataset.pensando;
      delete turnoAsistente.dataset.mirando;
      mirando?.remove?.();
      clearTimeout(espera);
      progresoCerrado = true;
      const vigente = estado.textContent;
      if (vigente === textoEspera || (progreso && vigente === progreso)) avisar("");
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
    if (foto) window.CarvajalFoto.limpiar();
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
      const response = await peticion(pregunta, turnId, foto, control.signal, hablada);
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
            // Spec 047: la lectura en voz del servidor pide el audio por el id de la
            // respuesta guardada, nunca por el texto de la página.
            if (evento.datos.message_id) {
              turnoAsistente.dataset.vozMensaje = String(evento.datos.message_id);
            }
            // Spec 048: resumen hablado preparado por el servidor (texto plano); la
            // lectura de respaldo del navegador lo prefiere a leer todo el turno.
            if (typeof evento.datos.voz_resumen === "string" && evento.datos.voz_resumen.trim()) {
              turnoAsistente.dataset.vozResumen = evento.datos.voz_resumen;
            }
            pintarLote(false, errorRecibido);
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
          if (foto) window.CarvajalFoto.restaurar(foto);
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
      } else if (foto && !recibioContenido && !cerrado) {
        // La foto no tiene respaldo sin streaming (el formulario sin JS no la lleva):
        // se devuelven la foto y el texto para reintentar.
        if (!input.value) input.value = pregunta;
        window.CarvajalFoto.restaurar(foto);
        turnoUsuario.remove();
        turnoAsistente.remove();
        avisar("No se pudo enviar la foto. Inténtalo de nuevo.", true);
        return;
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
      delete turnoAsistente.dataset.pensando;
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
      if (
        controlesVoz &&
        hablada &&
        (texto.textContent || turnoAsistente.querySelector(".productos"))
      ) {
        controlesVoz.hidden = false;
      }
      ajustarHistorial();
      // Notificación interna (spec 040): solo tras un stream con done, con el
      // turno ya formateado y el formulario liberado. No hay audio ni red.
      // El contrato no emite turno completo si hubo error, aunque llegue done.
      if (completado && !errorRecibido && !cancelado) {
        avisar("Respuesta lista.", false, true);
        notificarTurnoCompleto(turnoAsistente, manosLibres, hablada);
      } else if (manosLibres) {
        // Sin `done` el servidor no persiste el turno: se devuelve siempre la
        // pregunta para que la persona pueda revisarla y reenviarla.
        if (!input.value.trim()) input.value = pregunta;
        notificarTurnoFallido(turnoAsistente, threadInput ? threadInput.value : "", true);
      }
    }
  }

  function notificarTurnoCompleto(turno, manosLibres, hablada = false) {
    if (typeof window.CustomEvent !== "function") return;
    document.dispatchEvent(
      new window.CustomEvent("chat:turn-complete", {
        detail: {
          turno,
          threadId: threadInput ? String(threadInput.value || "") : "",
          handsFree: Boolean(manosLibres),
          spoken: Boolean(hablada),
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
    // «turno-nuevo» anima solo los turnos de esta visita, no el historial recargado.
    turno.classList.add(`turno-${rol}`, "turno-nuevo");
    turno.querySelector("[data-turno-autor]").textContent = rol === "user" ? "Tú" : "Carva";
    turno.querySelector(".texto").textContent = contenido;
    return turno;
  }

  // Los «Elegir» y «Confirmar» de las specs 053/054 solo sirven con este script: sin él
  // quedan ocultos.
  function mostrarAcciones(raiz) {
    const botones = raiz.querySelectorAll?.("[data-chat-enviar]") ?? [];
    for (const boton of botones) boton.hidden = false;
  }

  // Spec 053: solo la recomendación de foto más reciente ofrece «Elegir». Spec 054: solo los
  // botones del flujo del último turno se muestran, porque responden a la pregunta pendiente.
  function actualizarAcciones() {
    const fotos = Array.from(hilo.querySelectorAll?.(".productos-foto") ?? []);
    fotos.forEach((lista, indice) => {
      const vigente = indice === fotos.length - 1;
      for (const boton of lista.querySelectorAll("[data-chat-enviar]")) boton.hidden = !vigente;
    });
    const turnos = Array.from(hilo.querySelectorAll?.(".turno") ?? []);
    const ultimo = turnos[turnos.length - 1];
    for (const acciones of hilo.querySelectorAll?.("[data-flujo]") ?? []) {
      acciones.hidden = !(ultimo && typeof ultimo.contains === "function" && ultimo.contains(acciones));
    }
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
    if (tarjetas) mostrarAcciones(tarjetas);
    actualizarAcciones();
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

  function deslizar(elemento, animar, mutar) {
    const medible = animar && elemento && typeof elemento.getBoundingClientRect === "function"
      && typeof elemento.animate === "function";
    const reducido = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!medible || reducido) {
      mutar();
      return;
    }
    const antes = elemento.getBoundingClientRect().top;
    mutar();
    const desplazamiento = antes - elemento.getBoundingClientRect().top;
    if (Math.abs(desplazamiento) < 2) return;
    elemento.animate(
      [{ transform: `translateY(${desplazamiento}px)` }, { transform: "none" }],
      { duration: 520, easing: "cubic-bezier(0.2, 0, 0, 1)" },
    );
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
