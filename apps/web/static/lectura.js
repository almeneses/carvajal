// Vistas de lectura del M2 (Corpus y Documento). Consume la API por JSON y
// delega la navegación en el router del frontend (`window.CarvajalApp`).
// Solo `content_html` —ya saneado por la API— se inserta como HTML.
(() => {
  "use strict";

  const API = String(window.CARVAJAL_API || "http://localhost:8000").replace(/\/$/, "");
  const TAMANO_PAGINA = 24;
  const DEBOUNCE_MS = 250;
  const MAX_BUSQUEDA = 200;

  let enlazado = false;
  let temporizadorBusqueda = 0;
  let peticionCorpus = null;
  let peticionDocumento = null;
  let tokenCorpus = 0;
  let tokenDocumento = 0;
  let parametrosActuales = new URLSearchParams();
  let enfocarListaPendiente = false;
  const TIMEOUT_MS = 20000;

  const MENSAJE_CORPUS_VACIO = "No hay documentos que coincidan con los filtros.";
  const MENSAJE_RED = "No se pudo conectar con la API del corpus.";
  const MENSAJE_DOCUMENTO = "No se pudo cargar el documento.";

  // ---------------------------------------------------------------------------
  // Utilidades de DOM y navegación
  // ---------------------------------------------------------------------------

  function porId(id) {
    return document.getElementById(id);
  }

  function crear(etiqueta, clase, texto) {
    const nodo = document.createElement(etiqueta);
    if (clase) nodo.className = clase;
    if (texto !== undefined && texto !== null) nodo.textContent = String(texto);
    return nodo;
  }

  function navegarRuta(destino, opciones) {
    if (window.CarvajalApp && typeof window.CarvajalApp.navegar === "function") {
      window.CarvajalApp.navegar(destino, opciones || {});
      return;
    }
    // Sin router (por ejemplo, al abrir el archivo aislado) se conserva una
    // navegación equivalente para no dejar los enlaces muertos.
    if (opciones && opciones.replace) {
      window.history.replaceState(null, "", destino);
    } else {
      window.location.assign(destino);
    }
  }

  function esClicNormal(evento) {
    return (
      !evento.defaultPrevented &&
      evento.button === 0 &&
      !evento.metaKey &&
      !evento.ctrlKey &&
      !evento.shiftKey &&
      !evento.altKey
    );
  }

  function interceptarEnlace(enlace, destino) {
    enlace.addEventListener("click", (evento) => {
      if (!esClicNormal(evento)) return;
      evento.preventDefault();
      navegarRuta(destino);
    });
  }

  function urlDocumentoVersion(documentId, versionId, params) {
    const consulta = new URLSearchParams();
    if (versionId) consulta.set("version", versionId);
    for (const clave of ["from", "back"]) {
      const valor = params instanceof URLSearchParams ? params.get(clave) : "";
      if (valor) consulta.set(clave, valor);
    }
    const texto = consulta.toString();
    const base = `/documents/${encodeURIComponent(documentId)}`;
    return texto ? `${base}?${texto}` : base;
  }

  function interceptarEnlaceVersion(enlace, destino) {
    interceptarEnlace(enlace, destino);
  }

  function urlCorpus(params) {
    const consulta = params.toString();
    return consulta ? `/corpus?${consulta}` : "/corpus";
  }

  function normalizarParams(params) {
    const fuente = params instanceof URLSearchParams ? params : new URLSearchParams(params || "");
    const limpios = new URLSearchParams();
    for (const clave of ["q", "section", "source_type", "layer"]) {
      const valor = String(fuente.get(clave) || "").trim();
      if (valor) limpios.set(clave, clave === "q" ? valor.slice(0, MAX_BUSQUEDA) : valor);
    }
    const pagina = parsearPagina(fuente.get("page"));
    if (pagina > 1) limpios.set("page", String(pagina));
    return limpios;
  }

  function parsearPagina(valor) {
    const numero = Number.parseInt(String(valor || "1"), 10);
    return Number.isFinite(numero) && numero >= 1 ? numero : 1;
  }

  function urlDocumentoDesdeCorpus(documentId, params) {
    const destino = urlCorpus(params);
    return `/documents/${encodeURIComponent(documentId)}?from=corpus&back=${encodeURIComponent(
      destino
    )}`;
  }

  function dominioDe(url) {
    try {
      return new URL(String(url || "")).hostname.replace(/^www\./i, "");
    } catch {
      return "";
    }
  }

  function urlSegura(url) {
    return /^https?:\/\//i.test(String(url || "")) ? String(url) : "";
  }

  function rutaCorpusSegura(back) {
    const texto = String(back || "").trim();
    if (!texto) return "";
    try {
      const url = new URL(texto, window.location.origin);
      if (url.origin !== window.location.origin) return "";
      if (url.pathname !== "/corpus") return "";
      return `${url.pathname}${url.search}`;
    } catch {
      return "";
    }
  }

  function destinoVolver(params) {
    const desde = String(params.get("from") || "");
    if (desde === "asistente") {
      return { etiqueta: "Volver al asistente", destino: "/" };
    }
    if (desde === "corpus") {
      return {
        etiqueta: "Volver al corpus",
        destino: rutaCorpusSegura(params.get("back")) || "/corpus",
      };
    }
    return { etiqueta: "Volver al corpus", destino: "/corpus" };
  }

  function valorDe(id) {
    const nodo = porId(id);
    return nodo ? String(nodo.value || "") : "";
  }

  function fijarValor(id, valor) {
    const nodo = porId(id);
    if (!nodo) return;
    const texto = String(valor || "");
    const existe = Array.from(nodo.options || []).some((opcion) => opcion.value === texto);
    nodo.value = existe ? texto : "";
  }

  function etiquetaTipo(sourceType) {
    const tipo = String(sourceType || "").trim();
    if (tipo === "oficial") return "Oficial";
    if (tipo === "tercero") return "Tercero";
    if (!tipo) return "Sin tipo";
    return tipo.charAt(0).toUpperCase() + tipo.slice(1);
  }

  function etiquetaCapa(layer) {
    const capa = String(layer || "").trim();
    if (capa === "core") return "Núcleo";
    if (capa === "extended") return "Extendida";
    if (!capa) return "Sin capa";
    return capa.charAt(0).toUpperCase() + capa.slice(1);
  }

  function crearEtiqueta(texto, modificador) {
    const etiqueta = crear("span", `lectura-badge ${modificador || ""}`.trim(), texto);
    return etiqueta;
  }

  function crearEtiquetaTipo(sourceType) {
    const tipo = String(sourceType || "").trim();
    let modificador = "lectura-badge--otro";
    if (tipo === "oficial") modificador = "lectura-badge--oficial";
    else if (tipo === "tercero") modificador = "lectura-badge--tercero";
    return crearEtiqueta(etiquetaTipo(tipo), modificador);
  }

  function crearEtiquetaCapa(layer) {
    const capa = String(layer || "").trim();
    const modificador =
      capa === "core"
        ? "lectura-badge--core"
        : capa === "extended"
          ? "lectura-badge--extended"
          : "lectura-badge--otro";
    return crearEtiqueta(etiquetaCapa(capa), modificador);
  }

  function fechaCorta(iso) {
    const texto = String(iso || "");
    return texto ? texto.slice(0, 10) : "sin fecha";
  }

  // ---------------------------------------------------------------------------
  // Corpus: filtros, listado y paginación
  // ---------------------------------------------------------------------------

  function elementosCorpus() {
    return {
      filtros: porId("corpus-filtros"),
      q: porId("corpus-q"),
      seccion: porId("corpus-section"),
      fuente: porId("corpus-source"),
      capa: porId("corpus-layer"),
      limpiar: porId("corpus-limpiar"),
      conteo: porId("corpus-conteo"),
      lista: porId("corpus-lista"),
      estado: porId("corpus-estado"),
      paginacion: porId("corpus-paginacion"),
    };
  }

  function enlazarCorpus() {
    if (enlazado) return;
    const c = elementosCorpus();
    if (!c.q || !c.seccion || !c.fuente || !c.capa || !c.limpiar) return;
    if (!c.lista || !c.estado || !c.paginacion) return;

    c.q.addEventListener("input", () => {
      window.clearTimeout(temporizadorBusqueda);
      temporizadorBusqueda = window.setTimeout(aplicarFiltros, DEBOUNCE_MS);
    });
    c.filtros.addEventListener("submit", (evento) => {
      // Enter en la búsqueda no debe recargar la SPA ni perder los filtros.
      evento.preventDefault();
      aplicarFiltros();
    });
    for (const selector of [c.seccion, c.fuente, c.capa]) {
      selector.addEventListener("change", aplicarFiltros);
    }
    c.limpiar.addEventListener("click", limpiarFiltros);
    prepararCapa(c.capa);
    enlazado = true;
  }

  function prepararCapa(select) {
    if (select.dataset.lecturaLista === "1") return;
    select.replaceChildren();
    for (const [valor, etiqueta] of [
      ["", "Todas las capas"],
      ["core", "Núcleo"],
      ["extended", "Extendida"],
    ]) {
      const opcion = crear("option", "", etiqueta);
      opcion.value = valor;
      select.append(opcion);
    }
    select.dataset.lecturaLista = "1";
  }

  function aplicarFiltros() {
    window.clearTimeout(temporizadorBusqueda);
    temporizadorBusqueda = 0;
    const params = new URLSearchParams();
    const q = valorDe("corpus-q").trim().slice(0, MAX_BUSQUEDA);
    const seccion = valorDe("corpus-section").trim();
    const fuente = valorDe("corpus-source").trim();
    const capa = valorDe("corpus-layer").trim();
    if (q) params.set("q", q);
    if (seccion) params.set("section", seccion);
    if (fuente) params.set("source_type", fuente);
    if (capa) params.set("layer", capa);
    irAPagina(params, { replace: true });
  }

  function limpiarFiltros(evento) {
    if (evento) evento.preventDefault();
    window.clearTimeout(temporizadorBusqueda);
    temporizadorBusqueda = 0;
    const c = elementosCorpus();
    if (c.q) c.q.value = "";
    if (c.seccion) c.seccion.value = "";
    if (c.fuente) c.fuente.value = "";
    if (c.capa) c.capa.value = "";
    irAPagina(new URLSearchParams(), { replace: true });
  }

  function irAPagina(params, opciones) {
    const limpios = normalizarParams(params);
    navegarRuta(urlCorpus(limpios), opciones);
    // El router puede haber llamado a `activar` de forma síncrona; en ese caso
    // la petición ya está en curso y no se duplica.
    if (peticionCorpus && peticionCorpus.clave === limpios.toString()) return;
    void cargarCorpus(limpios);
  }

  function activar(params) {
    const c = elementosCorpus();
    if (!c.lista || !c.estado || !c.paginacion) return;
    enlazarCorpus();
    const limpios = normalizarParams(params);
    parametrosActuales = limpios;

    if (c.q) c.q.value = limpios.get("q") || "";
    fijarValor("corpus-section", limpios.get("section"));
    fijarValor("corpus-source", limpios.get("source_type"));
    fijarValor("corpus-layer", limpios.get("layer"));

    const clave = limpios.toString();
    if (peticionCorpus && peticionCorpus.clave === clave) return;
    void cargarCorpus(limpios);
  }

  async function cargarCorpus(params) {
    const c = elementosCorpus();
    if (!c.lista || !c.estado || !c.paginacion) return;

    if (peticionCorpus) peticionCorpus.control.abort();
    const control = new AbortController();
    const clave = params.toString();
    const token = ++tokenCorpus;
    peticionCorpus = { clave, control };
    parametrosActuales = normalizarParams(params);

    const pagina = parsearPagina(params.get("page"));
    const consulta = new URLSearchParams();
    for (const campo of ["q", "section", "source_type", "layer"]) {
      const valor = params.get(campo);
      if (valor) consulta.set(campo, valor);
    }
    consulta.set("page", String(pagina));
    consulta.set("page_size", String(TAMANO_PAGINA));

    c.lista.replaceChildren();
    c.lista.setAttribute("aria-busy", "true");
    c.paginacion.replaceChildren();
    c.paginacion.hidden = true;
    if (c.conteo) c.conteo.textContent = "Buscando documentos…";
    mostrarEstadoCorpus("Cargando corpus…", "");
    let agotado = false;
    const temporizador = window.setTimeout(() => {
      agotado = true;
      control.abort();
    }, TIMEOUT_MS);

    try {
      const response = await fetch(`${API}/api/corpus?${consulta.toString()}`, {
        credentials: "include",
        signal: control.signal,
      });
      const datos = await response.json().catch(() => ({}));
      if (token !== tokenCorpus) return;
      if (!response.ok) {
        throw new Error(String(datos.detail || `La API respondió ${response.status}.`));
      }
      pintarFacetas(datos);
      pintarResultados(datos, parametrosActuales);
    } catch (error) {
      if (error && error.name === "AbortError" && !agotado) return;
      if (token !== tokenCorpus) return;
      const mensaje = agotado
        ? "La API del corpus tardó demasiado en responder."
        : error && error.message && !/^(Failed to fetch|NetworkError|Load failed)/i.test(error.message)
          ? error.message
          : MENSAJE_RED;
      mostrarErrorCorpus(mensaje);
      if (c.conteo) c.conteo.textContent = "Sin resultados";
      enfocarListaPendiente = false;
    } finally {
      window.clearTimeout(temporizador);
      if (token === tokenCorpus) {
        c.lista.removeAttribute("aria-busy");
        if (peticionCorpus && peticionCorpus.control === control) peticionCorpus = null;
      }
    }
  }

  function pintarFacetas(datos) {
    const secciones = Array.isArray(datos.sections) ? datos.sections.map(String) : [];
    const tipos = Array.isArray(datos.source_types) ? datos.source_types.map(String) : [];
    rellenarSelect(
      "corpus-section",
      "Todas las secciones",
      secciones,
      parametrosActuales.get("section")
    );
    rellenarSelect(
      "corpus-source",
      "Todos los tipos",
      tipos,
      parametrosActuales.get("source_type")
    );
    fijarValor("corpus-layer", parametrosActuales.get("layer"));
  }

  function rellenarSelect(id, etiquetaTodos, valores, seleccionado) {
    const select = porId(id);
    if (!select) return;
    const firma = valores.join("\u0001");
    if (select.dataset.lecturaFirma !== firma) {
      select.replaceChildren();
      const todas = crear("option", "", etiquetaTodos);
      todas.value = "";
      select.append(todas);
      for (const valor of valores) {
        const rotulo = id === "corpus-source" ? etiquetaTipo(valor) : valor;
        const opcion = crear("option", "", rotulo);
        opcion.value = valor;
        select.append(opcion);
      }
      select.dataset.lecturaFirma = firma;
    }
    fijarValor(id, seleccionado);
  }

  function pintarResultados(datos, params) {
    const c = elementosCorpus();
    if (!c.lista || !c.estado || !c.paginacion) return;
    const documentos = Array.isArray(datos.documents) ? datos.documents : [];
    const total = Number(datos.total || 0);
    const pagina = Number(datos.page || 1);
    const tamano = Number(datos.page_size || TAMANO_PAGINA);

    c.lista.replaceChildren();
    if (documentos.length === 0) {
      const totalPaginas = Math.max(1, Math.ceil(total / tamano));
      if (total > 0) {
        mostrarEstadoCorpus(
          `No hay resultados en esta página. La última página disponible es la ${totalPaginas}.`,
          "vacio"
        );
        const estado = porId("corpus-estado");
        if (estado) {
          const enlace = crear("a", "lectura-accion", `Ir a la página ${totalPaginas}`);
          const destino = urlCorpus(conPagina(params, totalPaginas));
          enlace.href = destino;
          interceptarEnlace(enlace, destino);
          estado.append(enlace);
        }
        if (c.conteo) {
          c.conteo.textContent = `${total} ${total === 1 ? "documento" : "documentos"} en total`;
        }
      } else {
        mostrarEstadoCorpus(MENSAJE_CORPUS_VACIO, "vacio");
        if (c.conteo) c.conteo.textContent = "0 documentos";
      }
      c.paginacion.replaceChildren();
      c.paginacion.hidden = true;
      enfocarListaPendiente = false;
      return;
    }

    const contenedorLista = /^(UL|OL)$/.test(c.lista.tagName) ? c.lista : null;
    for (const documento of documentos) {
      const destino = urlDocumentoDesdeCorpus(documento.id, params);
      const enlace = crear("a", "lectura-doc");
      enlace.href = destino;
      interceptarEnlace(enlace, destino);

      const cabecera = crear("span", "lectura-doc__cabecera");
      const prefijo = crear("span", "sr-solo", "Abrir ficha: ");
      const flecha = crear("span", "lectura-doc__flecha", "›");
      flecha.setAttribute("aria-hidden", "true");
      cabecera.append(prefijo, crear("span", "lectura-doc__titulo", documento.title || "Sin título"), flecha);

      const origen = dominioDe(documento.url) || "origen desconocido";
      const meta = crear(
        "span",
        "lectura-doc__meta",
        [documento.section, origen].filter(Boolean).join(" · ")
      );

      const etiquetas = crear("span", "lectura-doc__etiquetas");
      etiquetas.append(crearEtiquetaTipo(documento.source_type));
      etiquetas.append(crearEtiquetaCapa(documento.layer));

      enlace.append(cabecera, meta, etiquetas);
      if (contenedorLista) {
        const item = crear("li", "lectura-doc__item");
        item.append(enlace);
        contenedorLista.append(item);
      } else {
        c.lista.append(enlace);
      }
    }

    const desde = (pagina - 1) * tamano + 1;
    const hasta = desde + documentos.length - 1;
    if (c.conteo) {
      c.conteo.textContent = `Mostrando ${desde}–${hasta} de ${total} ${
        total === 1 ? "documento" : "documentos"
      }`;
    }
    mostrarEstadoCorpus("", "");
    pintarPaginacion(datos, params);
    if (enfocarListaPendiente) {
      enfocarListaPendiente = false;
      const primero = c.lista.querySelector(".lectura-doc");
      if (primero) primero.focus();
      else c.lista.focus({ preventScroll: false });
    }
  }

  function mostrarEstadoCorpus(mensaje, tipo) {
    const estado = porId("corpus-estado");
    if (!estado) return;
    estado.replaceChildren();
    estado.className = `lectura-estado${tipo ? ` lectura-estado--${tipo}` : ""}`;
    if (!mensaje) {
      estado.hidden = true;
      return;
    }
    estado.hidden = false;
    estado.append(crear("span", "lectura-estado__texto", mensaje));
  }

  function mostrarErrorCorpus(mensaje) {
    const estado = porId("corpus-estado");
    if (!estado) return;
    estado.replaceChildren();
    estado.hidden = false;
    estado.className = "lectura-estado lectura-estado--error error";
    estado.append(crear("span", "lectura-estado__texto", mensaje));
    const reintentar = crear("button", "lectura-reintentar", "Reintentar");
    reintentar.type = "button";
    reintentar.addEventListener("click", () => void cargarCorpus(parametrosActuales));
    estado.append(reintentar);
  }

  function pintarPaginacion(datos, params) {
    const contenedor = porId("corpus-paginacion");
    if (!contenedor) return;
    const total = Number(datos.total || 0);
    const pagina = Number(datos.page || 1);
    const hasNext = Boolean(datos.has_next);
    if (total === 0) {
      contenedor.replaceChildren();
      contenedor.hidden = true;
      return;
    }
    const totalPaginas = Math.max(1, Math.ceil(total / Number(datos.page_size || TAMANO_PAGINA)));

    contenedor.replaceChildren();
    contenedor.classList.add("lectura-paginacion");
    if (!contenedor.getAttribute("aria-label")) {
      contenedor.setAttribute("aria-label", "Paginación del corpus");
    }
    const nav = contenedor;

    if (pagina > 1) {
      const anterior = crear("a", "lectura-pagina", "Anterior");
      const destino = urlCorpus(conPagina(params, pagina - 1));
      anterior.href = destino;
      anterior.setAttribute("rel", "prev");
      anterior.setAttribute("aria-label", "Página anterior");
      interceptarEnlace(anterior, destino);
      anterior.addEventListener("click", () => {
        enfocarListaPendiente = true;
      });
      nav.append(anterior);
    } else {
      const inactiva = crear("span", "lectura-pagina lectura-pagina--inactiva", "Anterior");
      inactiva.setAttribute("aria-disabled", "true");
      nav.append(inactiva);
    }

    const actual = crear(
      "span",
      "lectura-pagina lectura-pagina--actual",
      `Página ${pagina} de ${totalPaginas}`
    );
    actual.setAttribute("aria-current", "page");
    nav.append(actual);

    if (hasNext) {
      const siguiente = crear("a", "lectura-pagina", "Siguiente");
      const destino = urlCorpus(conPagina(params, pagina + 1));
      siguiente.href = destino;
      siguiente.setAttribute("rel", "next");
      siguiente.setAttribute("aria-label", "Página siguiente");
      interceptarEnlace(siguiente, destino);
      siguiente.addEventListener("click", () => {
        enfocarListaPendiente = true;
      });
      nav.append(siguiente);
    } else {
      const inactiva = crear("span", "lectura-pagina lectura-pagina--inactiva", "Siguiente");
      inactiva.setAttribute("aria-disabled", "true");
      nav.append(inactiva);
    }

    contenedor.hidden = false;
  }

  function conPagina(params, pagina) {
    const copia = new URLSearchParams(params);
    if (pagina <= 1) copia.delete("page");
    else copia.set("page", String(pagina));
    return copia;
  }

  // ---------------------------------------------------------------------------
  // Documento: ficha, HTML saneado y versiones
  // ---------------------------------------------------------------------------

  function activarDocumento(documentId, params) {
    const contenedor = porId("documento-contenido");
    if (!contenedor) return;
    const fuente = params instanceof URLSearchParams ? params : new URLSearchParams(params || "");
    const id = String(documentId || "").trim();

    if (!id) {
      if (peticionDocumento) peticionDocumento.control.abort();
      peticionDocumento = null;
      tokenDocumento += 1;
      delete contenedor.dataset.documento;
      pintarDocumentoVacio(contenedor);
      return;
    }

    contenedor.dataset.documento = id;
    void cargarDocumento(id, fuente);
  }

  function pintarDocumentoVacio(contenedor) {
    contenedor.replaceChildren();
    const vacio = crear("div", "lectura-vacio lectura-vacio--documento");
    vacio.setAttribute("role", "status");
    vacio.append(crear("h1", "lectura-vacio__titulo", "Documento"));
    vacio.append(
      crear(
        "p",
        "lectura-vacio__texto",
        "Todavía no hay un documento abierto. Explora el corpus y elige una fuente para leer su contenido completo."
      )
    );
    const abrir = crear("a", "lectura-accion", "Abrir el Corpus");
    abrir.href = "/corpus";
    interceptarEnlace(abrir, "/corpus");
    vacio.append(abrir);
    contenedor.append(vacio);
  }

  async function cargarDocumento(documentId, params) {
    const contenedor = porId("documento-contenido");
    if (!contenedor) return;
    const version = String(params.get("version") || "").trim();
    const clave = `${documentId}?${version}`;
    if (peticionDocumento && peticionDocumento.clave === clave) return;
    if (peticionDocumento) peticionDocumento.control.abort();

    const control = new AbortController();
    const token = ++tokenDocumento;
    peticionDocumento = { clave, control };

    contenedor.replaceChildren();
    const cargando = crear("p", "lectura-estado lectura-estado--cargando", "Cargando documento…");
    cargando.setAttribute("role", "status");
    contenedor.append(cargando);

    const consulta = version ? `?version=${encodeURIComponent(version)}` : "";
    let agotado = false;
    const temporizador = window.setTimeout(() => {
      agotado = true;
      control.abort();
    }, TIMEOUT_MS);
    try {
      const response = await fetch(
        `${API}/api/documents/${encodeURIComponent(documentId)}${consulta}`,
        { credentials: "include", signal: control.signal }
      );
      const datos = await response.json().catch(() => ({}));
      if (token !== tokenDocumento) return;
      if (!response.ok) {
        const detalle =
          response.status === 404
            ? "El documento solicitado no existe o la versión no pertenece a él."
            : String(datos.detail || MENSAJE_DOCUMENTO);
        mostrarErrorDocumento(contenedor, detalle, params, { reintentar: response.status !== 404 });
        return;
      }
      pintarDocumento(contenedor, datos, params);
    } catch (error) {
      if (error && error.name === "AbortError" && !agotado) return;
      if (token !== tokenDocumento) return;
      const mensaje = agotado
        ? "La API del documento tardó demasiado en responder."
        : MENSAJE_DOCUMENTO;
      mostrarErrorDocumento(contenedor, mensaje, params, { reintentar: true });
    } finally {
      window.clearTimeout(temporizador);
      if (token === tokenDocumento) {
        if (peticionDocumento && peticionDocumento.control === control) peticionDocumento = null;
      }
    }
  }

  function mostrarErrorDocumento(contenedor, mensaje, params, opciones) {
    contenedor.replaceChildren();
    const volver = destinoVolver(params);
    const caja = crear("div", "lectura-error");
    caja.setAttribute("role", "alert");
    caja.append(crear("h1", "lectura-error__titulo", "No se pudo abrir el documento"));
    caja.append(crear("p", "lectura-error__texto", mensaje));

    const acciones = crear("div", "lectura-error__acciones");
    const regresar = crear("a", "lectura-accion", volver.etiqueta);
    regresar.href = volver.destino;
    interceptarEnlace(regresar, volver.destino);
    acciones.append(regresar);

    if (opciones && opciones.reintentar) {
      const reintentar = crear("button", "lectura-reintentar", "Reintentar");
      reintentar.type = "button";
      reintentar.addEventListener("click", () => {
        const id = String(contenedor.dataset.documento || "").trim();
        if (id) void cargarDocumento(id, params);
      });
      acciones.append(reintentar);
    }
    caja.append(acciones);
    contenedor.append(caja);
  }

  function pintarDocumento(contenedor, datos, params) {
    contenedor.replaceChildren();
    const documento = datos && typeof datos.document === "object" ? datos.document : {};
    const documentId = String(documento.id || "").trim();
    if (documentId) {
      contenedor.dataset.documento = documentId;
      try {
        window.sessionStorage.setItem("carvajal:documento", documentId);
      } catch {
        // sessionStorage puede estar bloqueado; la vista sigue funcionando.
      }
      document.dispatchEvent(
        new CustomEvent("carvajal:documento-abierto", { detail: { id: documentId } })
      );
    }

    const raiz = crear("article", "lectura-documento");

    const volver = destinoVolver(params);
    const nav = crear("nav", "lectura-volver");
    nav.setAttribute("aria-label", "Volver");
    const enlaceVolver = crear("a", "lectura-volver__enlace", `← ${volver.etiqueta}`);
    enlaceVolver.href = volver.destino;
    interceptarEnlace(enlaceVolver, volver.destino);
    nav.append(enlaceVolver);
    raiz.append(nav);

    const grid = crear("div", "lectura-documento__grid");
    grid.append(pintarArticulo(datos, documento));
    grid.append(pintarFicha(datos, documento, params));
    raiz.append(grid);
    contenedor.append(raiz);
  }

  function pintarArticulo(datos, documento) {
    const articulo = crear("div", "lectura-articulo");
    const cabecera = crear("header", "lectura-articulo__cabecera");
    cabecera.append(
      crear("h1", "lectura-articulo__titulo", documento.title || "Documento sin título")
    );

    const partes = [
      documento.section,
      etiquetaTipo(documento.source_type),
      etiquetaCapa(documento.layer),
    ].filter((parte) => String(parte || "").trim());
    if (partes.length) {
      cabecera.append(crear("p", "lectura-articulo__meta", partes.join(" · ")));
    }
    articulo.append(cabecera);

    const contenido = crear("div", "lectura-contenido");
    const html = typeof datos.content_html === "string" ? datos.content_html.trim() : "";
    if (html) {
      // Único HTML permitido: la API lo entrega ya saneado con nh3.
      contenido.innerHTML = html;
      degradarEncabezados(contenido);
    } else {
      contenido.append(
        crear(
          "p",
          "lectura-vacio__texto",
          "Este documento no tiene contenido capturado. Puedes abrir la fuente original desde la ficha."
        )
      );
    }
    articulo.append(contenido);
    return articulo;
  }

  function degradarEncabezados(contenido) {
    // El título de la ficha ya es el h1 de la página. Solo los h1 del Markdown
    // bajan a h2; los h2 originales se conservan para no saltar niveles.
    for (const anterior of [...contenido.querySelectorAll("h1")]) {
      const nuevo = document.createElement("h2");
      for (const atributo of anterior.attributes) {
        nuevo.setAttribute(atributo.name, atributo.value);
      }
      while (anterior.firstChild) nuevo.append(anterior.firstChild);
      anterior.replaceWith(nuevo);
    }
  }

  function pintarFicha(datos, documento, params) {
    const ficha = crear("aside", "lectura-ficha");
    ficha.setAttribute("aria-labelledby", "lectura-ficha-titulo");
    const titulo = crear("h2", "lectura-ficha__titulo", "Ficha de la fuente");
    titulo.id = "lectura-ficha-titulo";
    ficha.append(titulo);

    const lista = crear("dl", "lectura-ficha__lista");
    const origen = dominioDe(documento.url) || "Origen desconocido";
    const url = urlSegura(datos.document_url);
    const filaOrigen = crear("div", "lectura-ficha__fila");
    filaOrigen.append(crear("dt", "", "Origen"));
    const ddOrigen = crear("dd", "");
    if (url) {
      const enlace = crear("a", "lectura-ficha__enlace", origen);
      enlace.href = url;
      enlace.target = "_blank";
      enlace.rel = "noopener noreferrer";
      ddOrigen.append(enlace);
    } else {
      ddOrigen.textContent = origen;
    }
    filaOrigen.append(ddOrigen);
    lista.append(filaOrigen);

    const filaTipo = crear("div", "lectura-ficha__fila");
    filaTipo.append(crear("dt", "", "Tipo"));
    const ddTipo = crear("dd", "");
    ddTipo.append(crearEtiquetaTipo(documento.source_type));
    filaTipo.append(ddTipo);
    lista.append(filaTipo);

    const filaCapa = crear("div", "lectura-ficha__fila");
    filaCapa.append(crear("dt", "", "Capa del corpus"));
    const ddCapa = crear("dd", "");
    ddCapa.append(crearEtiquetaCapa(documento.layer));
    filaCapa.append(ddCapa);
    lista.append(filaCapa);

    const filaFecha = crear("div", "lectura-ficha__fila");
    filaFecha.append(crear("dt", "", "Fecha de captura"));
    const ddFecha = crear("dd", "");
    const fecha = crear("time", "", datos.captured_display || fechaCorta(datos.captured_iso));
    if (datos.captured_iso) fecha.dateTime = String(datos.captured_iso);
    ddFecha.append(fecha);
    filaFecha.append(ddFecha);
    lista.append(filaFecha);

    const filaHash = crear("div", "lectura-ficha__fila");
    filaHash.append(crear("dt", "", "Huella"));
    const ddHash = crear("dd", "");
    ddHash.append(crear("code", "", datos.content_hash || "—"));
    filaHash.append(ddHash);
    lista.append(filaHash);

    const filaVersion = crear("div", "lectura-ficha__fila");
    filaVersion.append(crear("dt", "", "Versión"));
    filaVersion.append(crear("dd", "", datos.version ? "Archivada" : "Actual"));
    lista.append(filaVersion);

    ficha.append(lista);

    if (url) {
      const fuente = crear("a", "lectura-ficha__fuente", "Abrir la fuente original");
      fuente.href = url;
      fuente.target = "_blank";
      fuente.rel = "noopener noreferrer";
      ficha.append(fuente);
    }

    pintarVersiones(ficha, datos, documento, params);
    return ficha;
  }

  function pintarVersiones(ficha, datos, documento, params) {
    const versiones = Array.isArray(datos.versions) ? datos.versions : [];
    if (versiones.length === 0) return;
    const documentId = String(documento.id || "");

    const seccion = crear("section", "lectura-versiones");
    seccion.append(crear("h3", "lectura-versiones__titulo", "Versiones disponibles"));
    const lista = crear("ul", "lectura-versiones__lista");

    const actual = crear("li", "lectura-versiones__item");
    const enlaceActual = crear("a", "lectura-versiones__enlace", "Versión actual");
    const destinoActual = urlDocumentoVersion(documentId, "", params);
    enlaceActual.href = destinoActual;
    if (!datos.version) enlaceActual.setAttribute("aria-current", "page");
    interceptarEnlaceVersion(enlaceActual, destinoActual);
    actual.append(enlaceActual);
    lista.append(actual);

    for (const version of versiones) {
      const id = String(version.id || "").trim();
      if (!id) continue;
      const item = crear("li", "lectura-versiones__item");
      const enlace = crear("a", "lectura-versiones__enlace");
      const destino = urlDocumentoVersion(documentId, id, params);
      enlace.href = destino;
      enlace.append(
        crear("span", "", `Versión del ${fechaCorta(version.captured_at)}`),
        crear("code", "", version.content_hash || "")
      );
      if (datos.version === id) enlace.setAttribute("aria-current", "page");
      interceptarEnlaceVersion(enlace, destino);
      item.append(enlace);
      lista.append(item);
    }
    seccion.append(lista);
    ficha.append(seccion);
  }

  function desactivar() {
    window.clearTimeout(temporizadorBusqueda);
    if (peticionCorpus) {
      peticionCorpus.control.abort();
      peticionCorpus = null;
    }
    if (peticionDocumento) {
      peticionDocumento.control.abort();
      peticionDocumento = null;
    }
    tokenCorpus += 1;
    tokenDocumento += 1;
  }

  window.CarvajalLectura = {
    activar,
    mostrarDocumento: activarDocumento,
    desactivar,
  };
})();
