// Frontend del M2: consume la API por JSON/SSE. No calcula RAG ni guarda
// credenciales; solo pinta tokens, rutas y citas verificables. Además es el
// shell SPA de las pantallas Corpus y Documento.
(() => {
  "use strict";

  const API = String(window.CARVAJAL_API || "http://localhost:8000").replace(/\/$/, "");
  const CLAVE_DOCUMENTO = "carvajal:documento";
  const TITULOS_VISTA = {
    faq: "Preguntas frecuentes",
    corpus: "Corpus",
    documento: "Documento",
  };

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
  const plantillaFuentePanel = document.getElementById("plantilla-fuente-panel");
  const panelFuentes = document.getElementById("panel-fuentes");
  const anuncios = document.getElementById("anuncios");
  const botonesSugerencia = [...document.querySelectorAll("[data-pregunta]")];

  const secciones = {
    asistente: document.getElementById("vista-asistente"),
    faq: document.getElementById("vista-faq"),
    corpus: document.getElementById("vista-corpus"),
    documento: document.getElementById("vista-documento"),
  };
  const enlacesNav = {
    faq: document.getElementById("nav-faq"),
    corpus: document.getElementById("nav-corpus"),
    documento: document.getElementById("nav-documento"),
  };

  let csrf = "";
  let enCurso = false;
  let ultimaPregunta = "";
  let ultimoTurno = "";
  let hiloActual = "";
  let vistaActual = null;
  let observadorDocumento = null;
  let temporizadorObservador = 0;
  const cacheFichas = new Map();

  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const AVISO = "No se pudo completar la respuesta. Revisa la conexión e inténtalo de nuevo.";
  const VACIO_PANEL =
    "Cuando hagas una pregunta, verás aquí las fuentes citadas en el último turno.";

  // Contrato expuesto al módulo de lectura (lectura.js) y a cualquier vista.
  window.CarvajalApp = { navegar, ruta, anunciar };

  document.addEventListener("DOMContentLoaded", iniciar);
  if (document.readyState !== "loading") iniciar();

  async function iniciar() {
    if (formulario.dataset.listo === "1") return;
    formulario.dataset.listo = "1";

    actualizarEnlaceDocumento();
    // `/` y `/corpus` son la misma vista: la URL se unifica antes de pintar
    // para que recargar o compartir el enlace se comporte igual.
    if (normalizarCamino(location.pathname) === "/") {
      history.replaceState({}, "", `/corpus${location.search}${location.hash}`);
    }
    renderizar();
    document.addEventListener("click", manejarClic);
    document.addEventListener("carvajal:documento-abierto", (evento) => {
      const id = evento.detail && evento.detail.id ? String(evento.detail.id) : "";
      if (id) recordarDocumento(id);
    });
    window.addEventListener("popstate", () => renderizar());
    formulario.addEventListener("submit", alEnviar);
    document.getElementById("nuevo-hilo").addEventListener("click", nuevoHilo);
    botonReintento.addEventListener("click", () => {
      if (!ultimaPregunta || enCurso) return;
      // Un reintento es un turno nuevo: reutilizar el id haría que el backend
      // devolviera el turno anterior sin persistir esta respuesta.
      enCurso = true;
      ultimoTurno = nuevoTurnoId();
      void responder(ultimaPregunta, ultimoTurno);
    });
    for (const boton of botonesSugerencia) {
      boton.addEventListener("click", () => {
        const texto = String(boton.dataset.pregunta || "");
        if (!texto || enCurso) return;
        pregunta.value = texto;
        if (typeof formulario.requestSubmit === "function") formulario.requestSubmit();
        else formulario.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      });
    }

    try {
      await cargarCsrf();
      await cargarHistorial();
      await comprobarSalud();
    } catch (error) {
      avisar(`No se pudo conectar con la API en ${API}.`, true);
    }
  }

  /* Enrutador SPA ---------------------------------------------------------- */

  function navegar(href, opciones = {}) {
    const destino = rutaInterna(href);
    if (!destino) {
      anunciar("Solo se admiten rutas internas de la aplicación.", { error: true });
      return false;
    }
    const actual = `${location.pathname}${location.search}${location.hash}`;
    if (destino === actual) {
      renderizar();
      return true;
    }
    if (opciones && opciones.replace) history.replaceState({}, "", destino);
    else history.pushState({}, "", destino);
    renderizar();
    return true;
  }

  function ruta() {
    return analizarRuta();
  }

  function analizarRuta() {
    const camino = normalizarCamino(location.pathname);
    const params = new URLSearchParams(location.search);
    if (camino === "/faq") return { vista: "faq", documentId: null, params };
    if (camino === "/corpus") return { vista: "corpus", documentId: null, params };
    if (camino === "/documents") return { vista: "documento", documentId: null, params };
    if (camino.startsWith("/documents/")) {
      const bruto = camino.slice("/documents/".length);
      if (bruto && !bruto.includes("/")) {
        let documentId = bruto;
        try {
          documentId = decodeURIComponent(bruto);
        } catch {
          documentId = bruto;
        }
        return { vista: "documento", documentId, params };
      }
    }
    return { vista: "corpus", documentId: null, params };
  }

  function normalizarCamino(camino) {
    const limpio = String(camino || "/")
      .replace(/\/{2,}/g, "/")
      .replace(/\/+$/, "");
    return limpio || "/";
  }

  function rutaInterna(valor) {
    if (typeof valor !== "string") return null;
    const href = valor.trim();
    if (!href.startsWith("/") || href.startsWith("//")) return null;
    try {
      const url = new URL(href, location.origin);
      if (url.origin !== location.origin) return null;
      return `${url.pathname}${url.search}${url.hash}`;
    } catch {
      return null;
    }
  }

  function renderizar(opciones = {}) {
    const { enfocar = true } = opciones;
    const actual = analizarRuta();
    const anterior = vistaActual;

    cancelarObservadorDocumento();

    for (const [nombre, seccion] of Object.entries(secciones)) {
      if (seccion) seccion.hidden = nombre !== actual.vista;
    }
    for (const [nombre, enlace] of Object.entries(enlacesNav)) {
      if (!enlace) continue;
      if (nombre === actual.vista) enlace.setAttribute("aria-current", "page");
      else enlace.removeAttribute("aria-current");
    }

    document.title = `${TITULOS_VISTA[actual.vista]} · Carvajal`;

    const cambioVista = anterior !== actual.vista;
    if ((anterior === "corpus" || anterior === "documento") && cambioVista) {
      desactivarLectura();
    }
    vistaActual = actual.vista;

    if (actual.vista === "documento" && !actual.documentId) {
      // El estado vacío de la pestaña debe apuntar a su propia URL, no al
      // último documento válido recordado.
      enlacesNav.documento?.setAttribute("href", "/documents");
    } else {
      actualizarEnlaceDocumento();
    }

    if (actual.vista === "corpus") {
      const pendiente = Promise.resolve(activarLectura(actual.params)).catch(() => {});
      if (enfocar && cambioVista) {
        pendiente.finally(() => {
          if (analizarRuta().vista === "corpus") enfocarVista("corpus");
        });
      }
    } else if (actual.vista === "documento") {
      const pendiente = Promise.resolve(
        mostrarDocumento(actual.documentId, actual.params),
      ).catch(() => {});
      if (enfocar) {
        pendiente.finally(() => {
          const ahora = analizarRuta();
          if (ahora.vista === "documento" && ahora.documentId === actual.documentId) {
            enfocarDocumento();
          }
        });
      }
    } else if (enfocar && cambioVista) {
      enfocarVista(actual.vista);
    }
  }

  function enfocarVista(nombre) {
    const seccion = secciones[nombre];
    if (!seccion || seccion.hidden) return;
    enfocarNodo(seccion.querySelector("h1") || seccion);
  }

  function enfocarNodo(nodo) {
    if (!nodo) return;
    if (!nodo.hasAttribute("tabindex")) nodo.setAttribute("tabindex", "-1");
    nodo.focus();
  }

  function enfocarDocumento() {
    const seccion = secciones.documento;
    if (!seccion || seccion.hidden) return;
    const h1 = seccion.querySelector("h1");
    if (h1) {
      enfocarNodo(h1);
      return;
    }
    // La ficha llega por JSON después de `mostrarDocumento`; se enfoca el h1
    // en cuanto el módulo de lectura lo inserta, sin robar el foco al usuario.
    enfocarVista("documento");
    if (typeof MutationObserver !== "function") return;
    const observador = new MutationObserver(() => {
      const titulo = seccion.querySelector("h1");
      if (!titulo) return;
      cancelarObservadorDocumento();
      const activo = document.activeElement;
      if (activo && activo !== document.body && activo !== seccion && !seccion.contains(activo)) {
        return;
      }
      enfocarNodo(titulo);
    });
    observadorDocumento = observador;
    observador.observe(seccion, { childList: true, subtree: true });
    temporizadorObservador = window.setTimeout(cancelarObservadorDocumento, 8000);
  }

  function cancelarObservadorDocumento() {
    if (observadorDocumento) {
      observadorDocumento.disconnect();
      observadorDocumento = null;
    }
    if (temporizadorObservador) {
      window.clearTimeout(temporizadorObservador);
      temporizadorObservador = 0;
    }
  }

  function manejarClic(evento) {
    if (evento.defaultPrevented || evento.button !== 0) return;
    if (evento.metaKey || evento.ctrlKey || evento.shiftKey || evento.altKey) return;
    const objetivo = evento.target.closest ? evento.target.closest("a[href]") : null;
    if (!objetivo) return;
    if (objetivo.target && objetivo.target !== "_self") return;
    if (objetivo.hasAttribute("download")) return;
    const destino = rutaInterna(objetivo.getAttribute("href"));
    if (!destino || !esRutaApp(destino)) return;
    evento.preventDefault();
    navegar(destino);
  }

  function esRutaApp(destino) {
    try {
      const camino = normalizarCamino(new URL(destino, location.origin).pathname);
      return (
        camino === "/" ||
        camino === "/faq" ||
        camino === "/corpus" ||
        camino === "/documents" ||
        camino.startsWith("/documents/")
      );
    } catch {
      return false;
    }
  }

  function anunciar(texto, opciones = {}) {
    if (!anuncios) return;
    const urgente = Boolean(opciones && (opciones.urgente || opciones.assertive));
    if (opciones && opciones.error) anuncios.classList.add("error");
    else anuncios.classList.remove("error");
    anuncios.setAttribute("aria-live", urgente ? "assertive" : "polite");
    anuncios.textContent = texto == null ? "" : String(texto);
  }

  function recordarDocumento(documentId) {
    try {
      window.sessionStorage.setItem(CLAVE_DOCUMENTO, String(documentId));
    } catch {
      // sessionStorage puede estar bloqueado; la navegación sigue funcionando.
    }
    actualizarEnlaceDocumento();
  }

  function leerDocumentoRecordado() {
    try {
      const valor = window.sessionStorage.getItem(CLAVE_DOCUMENTO);
      return valor && valor.trim() ? valor.trim() : "";
    } catch {
      return "";
    }
  }

  function actualizarEnlaceDocumento() {
    if (!enlacesNav.documento) return;
    const ultimo = leerDocumentoRecordado();
    enlacesNav.documento.setAttribute(
      "href",
      ultimo ? `/documents/${encodeURIComponent(ultimo)}` : "/documents",
    );
  }

  function activarLectura(params) {
    try {
      const modulo = window.CarvajalLectura;
      if (modulo && typeof modulo.activar === "function") return modulo.activar(params);
    } catch {
      // Se muestra el fallback de abajo.
    }
    mostrarFalloLectura(
      "No se pudo cargar el explorador del corpus. Recarga la página e inténtalo de nuevo.",
    );
    return undefined;
  }

  function mostrarDocumento(documentId, params) {
    try {
      const modulo = window.CarvajalLectura;
      if (modulo && typeof modulo.mostrarDocumento === "function") {
        return modulo.mostrarDocumento(documentId, params);
      }
    } catch {
      // Se muestra el fallback de abajo.
    }
    mostrarFalloLectura(
      "No se pudo cargar la ficha del documento. Recarga la página e inténtalo de nuevo.",
    );
    return undefined;
  }

  function mostrarFalloLectura(mensaje) {
    anunciar(mensaje, { error: true });
    const destino =
      vistaActual === "documento"
        ? document.getElementById("documento-contenido")
        : document.getElementById("corpus-estado");
    if (!destino) return;
    destino.replaceChildren();
    const aviso = document.createElement("p");
    aviso.className = "lectura-error";
    aviso.setAttribute("role", "alert");
    aviso.textContent = mensaje;
    destino.append(aviso);
  }

  function desactivarLectura() {
    try {
      const modulo = window.CarvajalLectura;
      if (modulo && typeof modulo.desactivar === "function") modulo.desactivar();
    } catch {
      // La vista ya está oculta; no conviene romper el enrutador por ello.
    }
  }

  /* Chat ------------------------------------------------------------------- */

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
    let ultimasFuentes = [];
    if (!Array.isArray(datos.messages) || datos.messages.length === 0) {
      const vacio = document.createElement("li");
      vacio.className = "vacio";
      vacio.textContent = "Todavía no hay mensajes en este hilo. Escribe la primera pregunta.";
      hilo.append(vacio);
      pintarPanelFuentes(ultimasFuentes);
      return;
    }
    for (const mensaje of datos.messages) {
      const turno = crearTurno(mensaje.role, mensaje.content, mensaje.status);
      pintarFuentes(turno, mensaje.sources || []);
      if (mensaje.role === "assistant") {
        ultimasFuentes = Array.isArray(mensaje.sources) ? mensaje.sources : [];
      }
      hilo.append(turno);
    }
    pintarPanelFuentes(ultimasFuentes);
    if (hiloActual) pintarHilo(hiloActual);
  }

  async function comprobarSalud() {
    const response = await fetch(`${API}/api/health`, { credentials: "include" });
    const datos = await response.json().catch(() => ({}));
    const indice = datos.index || {};
    if (response.ok && indice.status === "ok") {
      salud.textContent = "Índice listo";
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
    pintarPanelFuentes([]);
    const turnoUsuario = crearTurno("user", texto);
    const turnoAsistente = crearTurno("assistant", "");
    hilo.append(turnoUsuario, turnoAsistente);
    const destino = turnoAsistente.querySelector(".texto");
    pregunta.value = "";
    enviar.disabled = true;
    fijarSugerencias(false);
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
            pintarPanelFuentes(evento.datos.sources || []);
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
            pintarPanelFuentes(evento.datos.sources || []);
            recibioContenido = true;
            avisar("Respuesta completa sin streaming.");
            break;
          case "done":
            cerrado = true;
            avisar("Respuesta completa.");
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
      fijarSugerencias(true);
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
      pintarPanelFuentes([]);
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

  function fijarSugerencias(activas) {
    for (const boton of botonesSugerencia) boton.disabled = !activas;
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
    const seccion = turno.querySelector(".citas");
    if (!seccion) return;
    const lista = seccion.querySelector(".fuentes");
    lista.textContent = "";
    const validas = Array.isArray(fuentes) ? fuentes : [];
    if (validas.length === 0) {
      seccion.hidden = true;
      return;
    }
    for (const fuente of validas) lista.append(crearFuente(fuente));
    const conteo = seccion.querySelector(".citas-conteo");
    if (conteo) conteo.textContent = `(${validas.length})`;
    seccion.hidden = false;
  }

  function crearFuente(fuente) {
    const datos = fuente && typeof fuente === "object" ? fuente : {};
    const item = plantillaFuente.content.firstElementChild.cloneNode(true);
    const titulo = String(datos.title || datos.slug || "Fuente");
    const url = urlSegura(datos.url);
    const enlace = item.querySelector('[data-campo="url"]');
    enlace.querySelector('[data-campo="title"]').textContent = titulo;
    if (url) {
      enlace.href = url;
    } else {
      enlace.removeAttribute("href");
      enlace.removeAttribute("target");
    }

    const tipo = String(datos.source_type || "");
    const etiqueta = item.querySelector('[data-campo="source_type"]');
    etiqueta.textContent = etiquetaTipo(tipo);
    if (tipo) etiqueta.classList.add(esOficial(tipo) ? "etiqueta-oficial" : "etiqueta-externa");

    const campos = {
      section: datos.section,
      layer: etiquetaCapa(datos.layer),
      excerpt: datos.excerpt,
      score: Number.isFinite(Number(datos.score)) ? Number(datos.score).toFixed(3) : "",
    };
    for (const [campo, valor] of Object.entries(campos)) {
      const nodo = item.querySelector(`[data-campo="${campo}"]`);
      if (nodo) nodo.textContent = valor == null ? "" : String(valor);
    }

    const acciones = item.querySelector(".fuente-acciones");
    agregarAccionFuente(acciones, {
      slug: datos.slug,
      url,
      titulo,
    });
    return item;
  }

  function pintarPanelFuentes(fuentes) {
    panelFuentes.textContent = "";
    const validas = Array.isArray(fuentes) ? fuentes : [];
    if (validas.length === 0) {
      const vacio = document.createElement("p");
      vacio.className = "panel-vacio";
      vacio.textContent = VACIO_PANEL;
      panelFuentes.append(vacio);
      return;
    }
    for (const fuente of validas) {
      const datos = fuente && typeof fuente === "object" ? fuente : {};
      const item = plantillaFuentePanel.content.firstElementChild.cloneNode(true);
      const titulo = String(datos.title || datos.slug || "Fuente");
      const url = urlSegura(datos.url);
      const enlace = item.querySelector(".fuente-panel-enlace");
      enlace.querySelector('[data-campo="title"]').textContent = titulo;
      const detalle = [
        datos.section,
        etiquetaCapa(datos.layer),
        etiquetaTipo(datos.source_type),
      ]
        .filter((valor) => valor != null && valor !== "")
        .join(" · ");
      enlace.querySelector('[data-campo="detail"]').textContent = detalle;
      if (url) {
        enlace.href = url;
      } else {
        enlace.removeAttribute("href");
        enlace.removeAttribute("target");
      }
      const acciones = item.querySelector(".fuente-panel-acciones");
      agregarAccionFuente(acciones, {
        slug: datos.slug,
        url,
        titulo,
      });
      panelFuentes.append(item);
    }
  }

  function agregarAccionFuente(contenedor, datos) {
    const slug = datos.slug == null ? "" : String(datos.slug).trim();
    const url = urlSegura(datos.url);
    const titulo = datos.titulo || "la fuente";
    if (slug) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "boton-cita";
      boton.dataset.slug = slug;
      boton.dataset.url = url;
      boton.dataset.titulo = titulo;
      boton.textContent = "Ver ficha en el corpus";
      boton.setAttribute("aria-label", `Ver la ficha en el corpus de ${titulo}`);
      boton.addEventListener("click", () => void abrirCita(boton));
      contenedor.append(boton);
      return;
    }
    if (url) {
      const enlace = document.createElement("a");
      enlace.className = "boton-cita";
      enlace.href = url;
      enlace.target = "_blank";
      enlace.rel = "noopener noreferrer";
      enlace.textContent = "Abrir fuente";
      enlace.setAttribute("aria-label", `Abrir la fuente original de ${titulo}`);
      contenedor.append(enlace);
    }
  }

  async function abrirCita(boton) {
    const slug = String(boton.dataset.slug || "").trim();
    const url = urlSegura(boton.dataset.url);
    const titulo = boton.dataset.titulo || "la fuente";
    if (slug) {
      boton.disabled = true;
      boton.setAttribute("aria-busy", "true");
      anunciar(`Buscando la ficha de ${titulo} en el corpus…`);
      try {
        const documentId = await resolverDocumento(slug);
        if (documentId) {
          navegar(`/documents/${encodeURIComponent(documentId)}?from=asistente`);
          anunciar(`Abriendo la ficha de ${titulo}.`);
          return;
        }
      } catch {
        // Se conserva el enlace externo de la fuente como alternativa.
      } finally {
        boton.disabled = false;
        boton.removeAttribute("aria-busy");
      }
    }
    if (url) {
      const ventana = window.open(url, "_blank");
      if (ventana) {
        try {
          ventana.opener = null;
        } catch {
          // El navegador puede impedir tocar la ventana recién abierta.
        }
        anunciar("Fuente original abierta en otra pestaña.");
      } else {
        anunciar("No hay ficha en el corpus; abre el enlace de la fuente original.", {
          error: true,
        });
      }
      return;
    }
    anunciar("Esta fuente no tiene ficha ni enlace disponible.", { error: true });
  }

  function resolverDocumento(slug) {
    const clave = String(slug || "").trim();
    if (!clave) return Promise.resolve(null);
    if (cacheFichas.has(clave)) return cacheFichas.get(clave);
    const consulta = (async () => {
      const control = new AbortController();
      const temporizador = setTimeout(() => control.abort(), 10000);
      try {
        const response = await fetch(
          `${API}/api/documents/by-slug/${encodeURIComponent(clave)}`,
          {
            credentials: "include",
            headers: { Accept: "application/json" },
            signal: control.signal,
          },
        );
        if (!response.ok) return null;
        const datos = await response.json().catch(() => null);
        const id = datos && datos.document && datos.document.id;
        return id == null || id === "" ? null : String(id);
      } finally {
        clearTimeout(temporizador);
      }
    })();
    cacheFichas.set(clave, consulta);
    consulta
      .then((id) => {
        // Un 404/503 no se cachea: la siguiente cita puede resolverse si la
        // fuente se indexa o la API se recupera.
        if (!id) cacheFichas.delete(clave);
      })
      .catch(() => cacheFichas.delete(clave));
    return consulta;
  }

  function esOficial(tipo) {
    const normalizado = String(tipo || "").toLowerCase();
    return normalizado.includes("oficial") || normalizado === "official";
  }

  function etiquetaTipo(valor) {
    const texto = String(valor || "").trim();
    if (!texto) return "";
    const normalizado = texto.toLowerCase();
    if (normalizado === "oficial" || normalizado === "official") return "Oficial";
    if (normalizado === "tercero") return "Tercero";
    if (normalizado === "colaborador") return "Colaborador";
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function etiquetaCapa(valor) {
    const texto = String(valor || "").trim();
    if (!texto) return "";
    const normalizado = texto.toLowerCase();
    if (normalizado === "core") return "Núcleo";
    if (normalizado === "extended") return "Extendida";
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function urlSegura(valor) {
    if (typeof valor !== "string") return "";
    const limpio = valor.trim();
    return /^https?:\/\//i.test(limpio) ? limpio : "";
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
