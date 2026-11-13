// Frontend del M2: consume la API por JSON/SSE. No calcula RAG ni guarda
// credenciales; solo pinta tokens, rutas y citas verificables.
(() => {
  "use strict";

  const API = String(window.CARVAJAL_API || "http://localhost:8000").replace(/\/$/, "");
  const formulario = document.getElementById("formulario");
  const pregunta = document.getElementById("pregunta");
  const enviar = document.getElementById("enviar");
  const hilo = document.getElementById("hilo");
  const estado = document.getElementById("estado");
  const salud = document.getElementById("salud");
  const traza = document.getElementById("traza");
  const reintento = document.getElementById("reintento");
  const botonReintento = document.getElementById("boton-reintento");
  const plantillaTurno = document.getElementById("plantilla-turno");
  const plantillaFuente = document.getElementById("plantilla-fuente");

  let csrf = "";
  let enCurso = false;
  let ultimaPregunta = "";
  let ultimoTurno = "";
  let hiloActual = "";

  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const AVISO = "No se pudo completar la respuesta. Revisa la conexión e inténtalo de nuevo.";

  document.addEventListener("DOMContentLoaded", iniciar);
  if (document.readyState !== "loading") iniciar();

  async function iniciar() {
    if (formulario.dataset.listo === "1") return;
    formulario.dataset.listo = "1";
    try {
      await cargarCsrf();
      await cargarHistorial();
      await comprobarSalud();
    } catch (error) {
      avisar(`No se pudo conectar con la API en ${API}.`, true);
    }
    formulario.addEventListener("submit", alEnviar);
    document.getElementById("nuevo-hilo").addEventListener("click", nuevoHilo);
    botonReintento.addEventListener("click", () => {
      if (ultimaPregunta) void responder(ultimaPregunta, ultimoTurno);
    });
  }

  async function cargarCsrf() {
    const response = await fetch(`${API}/api/csrf`, { credentials: "include" });
    if (!response.ok) throw new Error(`csrf ${response.status}`);
    const datos = await response.json();
    csrf = String(datos.token || "");
  }

  async function cargarHistorial() {
    const response = await fetch(`${API}/api/chat/history`, { credentials: "include" });
    if (!response.ok) throw new Error(`historial ${response.status}`);
    const datos = await response.json();
    hilo.textContent = "";
    hiloActual = datos.thread_id || "";
    if (!Array.isArray(datos.messages) || datos.messages.length === 0) {
      const vacio = document.createElement("li");
      vacio.className = "vacio";
      vacio.textContent = "Todavía no hay mensajes en este hilo. Escribe la primera pregunta.";
      hilo.append(vacio);
      return;
    }
    for (const mensaje of datos.messages) {
      const turno = crearTurno(mensaje.role, mensaje.content, mensaje.status);
      pintarFuentes(turno, mensaje.sources || []);
      hilo.append(turno);
    }
    if (hiloActual) pintarHilo(hiloActual);
  }

  async function comprobarSalud() {
    const response = await fetch(`${API}/api/health`, { credentials: "include" });
    const datos = await response.json().catch(() => ({}));
    const indice = datos.index || {};
    if (response.ok && indice.status === "ok") {
      salud.textContent = `Índice listo · ${indice.points} fragmentos`;
      salud.classList.add("ok");
    } else {
      salud.textContent = `Índice ${indice.status || "no disponible"}`;
      salud.classList.add("degradado");
    }
    salud.title = `${datos.documents || 0} documentos, ${datos.chunks || 0} fragmentos`;
  }

  function alEnviar(evento) {
    evento.preventDefault();
    if (enCurso) return;
    const texto = pregunta.value.trim();
    if (!texto) {
      avisar("Escribe una pregunta antes de enviar.", true);
      pregunta.focus();
      return;
    }
    if (texto.length > 500) {
      avisar("La pregunta no puede superar los 500 caracteres.", true);
      return;
    }
    enCurso = true;
    ultimaPregunta = texto;
    ultimoTurno = nuevoTurnoId();
    reiniciarReintento();
    void responder(texto, ultimoTurno);
  }

  async function responder(texto, turnoId) {
    quitarVacio();
    const turnoUsuario = crearTurno("user", texto);
    const turnoAsistente = crearTurno("assistant", "");
    hilo.append(turnoUsuario, turnoAsistente);
    const destino = turnoAsistente.querySelector(".texto");
    pregunta.value = "";
    enviar.disabled = true;
    formulario.setAttribute("aria-busy", "true");
    avisar("Generando respuesta…");

    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 120000);
    let cerrado = false;
    let recibioContenido = false;
    try {
      const cuerpo = new URLSearchParams({
        question: texto,
        csrf_token: csrf,
        client_turn_id: turnoId,
      });
      const response = await fetch(`${API}/api/chat/stream`, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: cuerpo,
        signal: control.signal,
      });
      const tipo = response.headers.get("content-type") || "";
      if (!tipo.includes("text/event-stream")) {
        // Un 403/429/503 del backend puede llegar como JSON; se muestra y no
        // se reenvía el turno. Un error de red sí ofrece reintento manual.
        const datos = await response.json().catch(() => ({}));
        const mensaje = datos.detail || `La API respondió ${response.status}.`;
        destino.textContent = mensaje;
        cerrado = true;
        avisar(mensaje, true);
        return;
      }
      if (!response.ok) cerrado = true;

      await leerEventos(response, (evento) => {
        switch (evento.nombre) {
          case "open":
            avisar("Recuperando fuentes…");
            break;
          case "trace":
            pintarTraza(evento.datos);
            avisar(`Ruta ${evento.datos.route || ""}…`);
            break;
          case "meta":
            pintarFuentes(turnoAsistente, evento.datos.sources || []);
            avisar("Redactando…");
            break;
          case "token": {
            const fragmento = String(evento.datos.text || "");
            destino.textContent += fragmento;
            if (fragmento) recibioContenido = true;
            break;
          }
          case "fallback":
            destino.textContent = String(evento.datos.text || "");
            pintarFuentes(turnoAsistente, evento.datos.sources || []);
            recibioContenido = true;
            avisar("Respuesta completa sin streaming.");
            break;
          case "done":
            cerrado = true;
            avisar("");
            break;
          case "error":
            cerrado = true;
            if (!destino.textContent) destino.textContent = String(evento.datos.message || INCOMPLETA);
            marcarIncompleta(turnoAsistente, evento.datos.incomplete);
            avisar(String(evento.datos.message || INCOMPLETA), true);
            break;
          default:
            break;
        }
      });

      if (!cerrado) {
        avisar(INCOMPLETA, true);
        marcarIncompleta(turnoAsistente, true);
      } else if (!response.ok && !destino.textContent) {
        destino.textContent = AVISO;
        avisar(AVISO, true);
      }
    } catch (error) {
      if (error && error.name === "AbortError") {
        avisar(INCOMPLETA, true);
        marcarIncompleta(turnoAsistente, true);
      } else if (!recibioContenido) {
        turnoUsuario.remove();
        turnoAsistente.remove();
        reiniciarReintento();
        reintento.hidden = false;
        avisar(AVISO, true);
      } else {
        avisar(AVISO, true);
        marcarIncompleta(turnoAsistente, true);
      }
    } finally {
      clearTimeout(temporizador);
      enviar.disabled = false;
      formulario.removeAttribute("aria-busy");
      pregunta.focus();
      enCurso = false;
    }
  }

  async function nuevoHilo() {
    try {
      const cuerpo = new URLSearchParams({ csrf_token: csrf });
      const response = await fetch(`${API}/api/chat/new`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: cuerpo,
      });
      if (!response.ok) throw new Error(`nuevo hilo ${response.status}`);
      const datos = await response.json();
      hiloActual = datos.thread_id || "";
      hilo.textContent = "";
      traza.hidden = true;
      const vacio = document.createElement("li");
      vacio.className = "vacio";
      vacio.textContent = "Hilo nuevo. La memoria de preferencias sigue disponible para este dueño.";
      hilo.append(vacio);
      avisar("Hilo nuevo listo.");
      if (hiloActual) pintarHilo(hiloActual);
    } catch (error) {
      avisar("No se pudo abrir un hilo nuevo.", true);
    }
  }

  function crearTurno(rol, contenido, status) {
    const turno = plantillaTurno.content.firstElementChild.cloneNode(true);
    turno.classList.add(`turno-${rol}`);
    turno.querySelector(".rol").textContent = rol === "user" ? "Tú" : "Asistente";
    turno.querySelector(".texto").textContent = contenido;
    if (status === "incomplete") marcarIncompleta(turno, true);
    return turno;
  }

  function marcarIncompleta(turno, incompleta) {
    if (!incompleta) return;
    const etiqueta = document.createElement("span");
    etiqueta.className = "estado-incompleto";
    etiqueta.textContent = "respuesta incompleta";
    turno.querySelector(".rol").append(etiqueta);
  }

  function pintarFuentes(turno, fuentes) {
    if (!Array.isArray(fuentes) || fuentes.length === 0) return;
    const seccion = turno.querySelector(".citas");
    const lista = seccion.querySelector(".fuentes");
    lista.textContent = "";
    for (const fuente of fuentes) {
      const item = plantillaFuente.content.firstElementChild.cloneNode(true);
      const titulo = item.querySelector('[data-campo="title"]');
      const enlace = item.querySelector('[data-campo="url"]');
      const url = String((fuente && fuente.url) || "");
      titulo.textContent = String((fuente && (fuente.title || fuente.slug)) || "Fuente");
      if (/^https?:\/\//.test(url)) {
        enlace.href = url;
      } else {
        enlace.removeAttribute("href");
        enlace.removeAttribute("target");
      }
      const campos = {
        section: fuente.section,
        layer: fuente.layer,
        source_type: fuente.source_type,
        excerpt: fuente.excerpt,
        score: Number.isFinite(Number(fuente.score)) ? Number(fuente.score).toFixed(3) : "",
      };
      for (const [campo, valor] of Object.entries(campos)) {
        const nodo = item.querySelector(`[data-campo="${campo}"]`);
        if (nodo) nodo.textContent = valor == null ? "" : String(valor);
      }
      lista.append(item);
    }
    seccion.hidden = false;
  }

  function pintarTraza(datos) {
    traza.hidden = false;
    document.getElementById("traza-ruta").textContent = String(datos.route || "");
    const tool = String(datos.tool || "");
    document.getElementById("traza-tool-wrap").hidden = !tool;
    document.getElementById("traza-tool").textContent = tool;
    const memoria = Number(datos.memory || 0);
    document.getElementById("traza-memoria-wrap").hidden = !(memoria > 0);
    document.getElementById("traza-memoria").textContent = String(memoria);
    const fuentes = Number(datos.retrieved || 0);
    document.getElementById("traza-fuentes-wrap").hidden = !(fuentes > 0);
    document.getElementById("traza-fuentes").textContent = String(fuentes);
    pintarHilo(String(datos.thread_id || ""));
  }

  function pintarHilo(threadId) {
    if (threadId) document.getElementById("traza-hilo").textContent = threadId;
  }

  function avisar(mensaje, error = false) {
    estado.textContent = mensaje;
    estado.classList.toggle("error", error);
  }

  function reiniciarReintento() {
    reintento.hidden = true;
  }

  function quitarVacio() {
    const vacio = hilo.querySelector(".vacio");
    if (vacio) vacio.remove();
  }

  function nuevoTurnoId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `turno-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    vaciarBloques(buffer, alEvento);
  }

  function vaciarBloques(buffer, alEvento) {
    for (;;) {
      const corte = /\r?\n\r?\n/.exec(buffer);
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
