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
  const plantillaFuente = document.getElementById("plantilla-fuente");
  if (!input || !hilo || !estado || !plantillaTurno || !plantillaFuente) return;

  const AVISO = "No se pudo completar la respuesta. Vuelve a intentarlo.";
  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const SEPARADOR = /\r?\n\r?\n/;
  let enCurso = false;

  form.addEventListener("submit", (event) => {
    if (!soportaStreaming) return; // el navegador envía el POST de siempre
    event.preventDefault();
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
    const turnId = nuevoTurnoId();
    if (turnIdInput) turnIdInput.value = turnId;
    void responder(pregunta, turnId);
  });

  function nuevoTurnoId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `turno-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function responder(pregunta, turnId) {
    quitarVacio();
    const lista = asegurarLista();
    const turnoUsuario = crearTurno("user", pregunta);
    const turnoAsistente = crearTurno("assistant", "");
    turnoAsistente._fuentes = [];
    lista.append(turnoUsuario, turnoAsistente);
    const texto = turnoAsistente.querySelector(".texto");
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
                : evento.datos.route === "direct"
                  ? "Respondiendo sin búsqueda…"
                  : "Buscando en el corpus…",
            );
            break;
          case "meta":
            turnoAsistente._fuentes = Array.isArray(evento.datos.sources)
              ? evento.datos.sources
              : [];
            pintarFuentes(turnoAsistente, evento.datos.sources);
            avisar("Redactando…");
            break;
          case "token": {
            const fragmento = String(evento.datos.text ?? "");
            agregarFragmento(texto, fragmento);
            if (fragmento) recibioContenido = true;
            break;
          }
          case "fallback":
            delete texto.dataset.formateado;
            texto.textContent = String(evento.datos.text ?? "");
            turnoAsistente._fuentes = Array.isArray(evento.datos.sources)
              ? evento.datos.sources
              : [];
            pintarFuentes(turnoAsistente, evento.datos.sources);
            formatearTurno(turnoAsistente, texto, false);
            recibioContenido = true;
            avisar("Respuesta completa sin streaming.");
            break;
          case "done":
            cerrado = true;
            if (threadInput && evento.datos.thread_id) {
              threadInput.value = String(evento.datos.thread_id);
            }
            formatearTurno(turnoAsistente, texto, false);
            avisar("");
            break;
          case "error":
            cerrado = true;
            if (!texto.textContent) {
              texto.textContent = String(evento.datos.message ?? INCOMPLETA);
            }
            formatearTurno(turnoAsistente, texto, true);
            avisar(String(evento.datos.message ?? INCOMPLETA), true);
            break;
          default:
            break;
        }
      });

      if (!response.ok && !texto.textContent) {
        texto.textContent = AVISO;
        avisar(AVISO, true);
      } else if (!cerrado) {
        avisar(INCOMPLETA, true);
        if (!texto.textContent) texto.textContent = INCOMPLETA;
      }
      formatearTurno(turnoAsistente, texto, false);
    } catch (error) {
      if (!recibioContenido && !cerrado) {
        // Falló antes del primer token y el servidor no persistió el turno:
        // restaura la pregunta y reintenta por el POST normal del formulario.
        input.value = pregunta;
        turnoUsuario.remove();
        turnoAsistente.remove();
        avisar("La conexión falló; enviando la pregunta sin streaming…");
        form.submit(); // HTMLFormElement.submit() no dispara el listener de submit
        return;
      }
      avisar(error && error.name === "AbortError" ? INCOMPLETA : AVISO, true);
      formatearTurno(turnoAsistente, texto, true);
      if (!texto.textContent) turnoAsistente.remove();
    } finally {
      if (texto.textContent.trim() && texto.dataset.formateado !== "1") {
        formatearTurno(turnoAsistente, texto, false);
      }
      clearTimeout(temporizador);
      form.removeAttribute("aria-busy");
      if (boton) boton.disabled = false;
      input.focus();
      turnoAsistente.scrollIntoView({ block: "nearest" });
      enCurso = false;
      if (girando) girando.hidden = true;
    }
  }

  function crearTurno(rol, contenido) {
    const turno = plantillaTurno.content.firstElementChild.cloneNode(true);
    turno.classList.add(`turno-${rol}`);
    turno.querySelector(".turno-rol").textContent = rol === "user" ? "Tú" : "Asistente";
    turno.querySelector(".texto").textContent = contenido;
    return turno;
  }

  function agregarFragmento(nodo, fragmento) {
    if (!fragmento || nodo.dataset.formateado === "1") return;
    const span = document.createElement("span");
    span.className = "fragmento";
    span.textContent = fragmento;
    nodo.append(span);
  }

  function formatearTurno(turno, nodo, animar) {
    if (!nodo || nodo.dataset.formateado === "1") return;
    const contenido = nodo.textContent;
    if (!contenido.trim()) return;
    const formateador = window.CarvajalTexto;
    if (!formateador) return;
    const fragmento = formateador.formatear(contenido, turno._fuentes || []);
    nodo.replaceChildren(fragmento);
    nodo.classList.add("texto-formateado");
    nodo.dataset.formateado = "1";
    if (animar) formateador.animar(nodo, { forzar: true });
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

  function pintarFuentes(turno, fuentes) {
    if (!Array.isArray(fuentes) || fuentes.length === 0) return;
    const seccion = turno.querySelector(".citas");
    if (!seccion) return;
    const lista = seccion.querySelector(".fuentes");
    if (!lista) return;
    lista.textContent = "";
    for (const fuente of fuentes) {
      const item = plantillaFuente.content.firstElementChild.cloneNode(true);
      const titulo = (fuente && (fuente.title || fuente.slug)) || "Fuente";
      const excerpt = String((fuente && fuente.excerpt) || "");
      const url = String((fuente && fuente.url) || "");
      const campos = {
        section: fuente && fuente.section,
        excerpt,
      };
      for (const [campo, valor] of Object.entries(campos)) {
        const nodo = item.querySelector(`[data-campo="${campo}"]`);
        if (nodo) nodo.textContent = valor == null ? "" : String(valor);
      }

      const nodoTitulo = item.querySelector('[data-campo="title"]');
      if (nodoTitulo) nodoTitulo.textContent = titulo;
      const boton = item.querySelector('[data-campo="documento"]');
      const slug = String((fuente && fuente.slug) || "").trim();
      if (boton) {
        if (slug) {
          boton.dataset.documentoSlug = slug;
          boton.dataset.documentoTitulo = titulo;
          boton.dataset.documentoExcerpt = excerpt;
          if (/^https?:\/\//.test(url)) boton.dataset.documentoUrl = url;
          boton.setAttribute("aria-label", `Abrir el documento ${titulo} en el panel`);
        } else {
          boton.disabled = true;
          boton.removeAttribute("aria-haspopup");
        }
      }

      const capa = String((fuente && fuente.layer) || "");
      const nodoCapa = item.querySelector('[data-campo="layer"]');
      if (nodoCapa) {
        nodoCapa.textContent =
          capa === "core" ? "Núcleo" : capa === "extended" ? "Extendida" : capa;
      }
      const etiquetaTipo = item.querySelector('[data-campo="badge-tipo"]');
      const textoTipo = item.querySelector('[data-campo="source_type"]');
      if (etiquetaTipo && textoTipo) {
        const tipo = String((fuente && fuente.source_type) || "oficial").toLowerCase();
        const texto = tipo.charAt(0).toUpperCase() + tipo.slice(1);
        textoTipo.textContent = texto;
        etiquetaTipo.setAttribute("aria-label", `Tipo de fuente: ${texto}`);
        const esOficial = tipo === "oficial" || tipo === "official";
        etiquetaTipo.classList.toggle("etiqueta-oficial", esOficial);
        etiquetaTipo.classList.toggle("etiqueta-externa", !esOficial);
      }

      const acciones = item.querySelector('[data-campo="acciones"]');
      const enlace = item.querySelector('[data-campo="url"]');
      if (acciones && enlace) {
        if (/^https?:\/\//.test(url)) {
          enlace.href = url;
          enlace.title = "Abrir la fuente original";
          acciones.hidden = false;
        } else {
          enlace.removeAttribute("href");
          acciones.hidden = true;
        }
      }

      const puntuacion = Number(fuente && fuente.score);
      const nodoScore = item.querySelector('[data-campo="score"]');
      if (nodoScore) nodoScore.textContent = Number.isFinite(puntuacion)
        ? puntuacion.toFixed(3)
        : "";
      lista.append(item);
    }
    const conteo = seccion.querySelector(".citas-conteo");
    if (conteo) conteo.textContent = `(${fuentes.length})`;
    seccion.open = false;
    seccion.hidden = false;
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
