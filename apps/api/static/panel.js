// Panel lateral de documentos citados: carga la ficha por slug, pinta el HTML
// saneado que entrega la API y resalta el fragmento que sustenta la cita.
(() => {
  "use strict";

  const panel = document.getElementById("panel-documento");
  if (!panel) return;

  const titulo = document.getElementById("panel-documento-titulo");
  const meta = document.getElementById("panel-documento-meta");
  const estado = document.getElementById("panel-documento-estado");
  const contenido = document.getElementById("panel-documento-contenido");
  const origen = document.getElementById("panel-documento-origen");
  const cerrar = panel.querySelector("[data-cerrar-panel]");
  const cache = new Map();
  const reducido =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let disparador = null;
  let cerrando = false;

  document.addEventListener("click", (evento) => {
    const cita = evento.target.closest('a[href^="#cita-"]');
    if (cita) {
      const numero = Number(cita.getAttribute("href").slice("#cita-".length));
      const turno = cita.closest(".turno, .respuesta");
      const fuentes = turno ? turno.querySelectorAll(".fuente") : [];
      const fuente = fuentes[numero - 1];
      const boton = fuente ? fuente.querySelector("[data-documento-slug]") : null;
      if (boton) {
        evento.preventDefault();
        abrirDesde(boton, cita);
      }
      return;
    }

    const boton = evento.target.closest("[data-documento-slug]");
    if (boton) {
      evento.preventDefault();
      abrirDesde(boton);
    }
  });

  if (cerrar) cerrar.addEventListener("click", cerrarPanel);
  panel.addEventListener("click", (evento) => {
    if (evento.target === panel) cerrarPanel();
  });
  panel.addEventListener("cancel", (evento) => {
    evento.preventDefault();
    cerrarPanel();
  });
  panel.addEventListener("close", () => {
    document.body.classList.remove("panel-abierto");
    panel.removeAttribute("data-cerrando");
    panel.querySelectorAll(".resaltado").forEach((nodo) => {
      nodo.classList.remove("resaltado");
    });
    if (disparador && typeof disparador.focus === "function") disparador.focus();
    disparador = null;
  });

  function abrirDesde(boton, origen) {
    const slug = String(boton.dataset.documentoSlug || "").trim();
    if (!slug) return;
    abrir(
      {
        slug,
        titulo: boton.dataset.documentoTitulo || "",
        excerpt: boton.dataset.documentoExcerpt || "",
        url: boton.dataset.documentoUrl || "",
      },
      origen || boton,
    );
  }

  async function abrir(datos, boton) {
    disparador = boton || document.activeElement;
    panel.removeAttribute("data-cerrando");
    if (!panel.open) {
      panel.showModal();
      document.body.classList.add("panel-abierto");
    }
    if (cerrar) cerrar.focus();

    titulo.textContent = datos.titulo || "Documento";
    meta.textContent = "Cargando ficha…";
    estado.textContent = "Cargando documento…";
    contenido.replaceChildren();
    origen.hidden = true;

    const guardado = cache.get(datos.slug);
    if (guardado) {
      pintar(guardado, datos);
      return;
    }

    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 12000);
    try {
      const response = await fetch(
        `/api/documents/by-slug/${encodeURIComponent(datos.slug)}`,
        {
          credentials: "include",
          headers: { Accept: "application/json" },
          signal: control.signal,
        },
      );
      if (!response.ok) throw new Error(`documento ${response.status}`);
      const payload = await response.json().catch(() => null);
      if (!payload || !payload.document) throw new Error("respuesta vacía");
      cache.set(datos.slug, payload);
      pintar(payload, datos);
    } catch {
      estado.textContent =
        "No se pudo cargar el documento. Puedes abrir la fuente original.";
      if (urlSegura(datos.url)) {
        origen.href = datos.url;
        origen.hidden = false;
      }
    } finally {
      clearTimeout(temporizador);
    }
  }

  function pintar(payload, datos) {
    const documento = payload.document && typeof payload.document === "object"
      ? payload.document
      : {};
    titulo.textContent = documento.title || datos.titulo || "Documento";
    meta.textContent = [
      documento.section,
      etiquetaCapa(documento.layer),
      etiquetaTipo(documento.source_type),
      payload.captured_display,
    ]
      .filter((valor) => valor != null && valor !== "")
      .join(" · ");

    const html = String(payload.content_html || "");
    if (html.trim()) {
      const documentoHtml = sanear(html);
      contenido.replaceChildren(...Array.from(documentoHtml.body.childNodes));
    } else {
      contenido.textContent = "Este documento no tiene contenido capturado.";
    }

    const url = urlSegura(payload.document_url || documento.url);
    if (url) {
      origen.href = url;
      origen.hidden = false;
    } else {
      origen.hidden = true;
    }

    contenido.scrollTop = 0;
    requestAnimationFrame(() => {
      const encontrado = buscarPunto(contenido, datos.excerpt);
      estado.textContent = encontrado
        ? "Documento abierto en el punto citado."
        : "Documento abierto; no se localizó el fragmento exacto.";
    });
  }

  function cerrarPanel() {
    if (!panel.open || cerrando) return;
    if (reducido) {
      panel.close();
      return;
    }
    cerrando = true;
    panel.dataset.cerrando = "1";
    setTimeout(() => {
      cerrando = false;
      panel.close();
    }, 160);
  }

  function sanear(html) {
    const documento = new DOMParser().parseFromString(html, "text/html");
    documento.body
      .querySelectorAll("script, style, iframe, object, embed, form, base, link, meta")
      .forEach((nodo) => nodo.remove());
    for (const nodo of documento.body.querySelectorAll("*")) {
      for (const atributo of Array.from(nodo.attributes)) {
        const nombre = atributo.name.toLowerCase();
        const valor = atributo.value.trim().toLowerCase();
        const peligroso =
          nombre.startsWith("on") ||
          ((nombre === "href" || nombre === "src") && valor.startsWith("javascript:"));
        if (peligroso) nodo.removeAttribute(atributo.name);
      }
    }
    return documento;
  }

  function buscarPunto(raiz, excerpt) {
    raiz.querySelectorAll(".resaltado").forEach((nodo) => {
      nodo.classList.remove("resaltado");
    });
    const aguja = normalizar(excerpt);
    if (!aguja) return false;

    const { texto, mapa } = mapaTexto(raiz);
    let indice = texto.indexOf(aguja);
    if (indice < 0 && aguja.length > 90) {
      indice = texto.indexOf(aguja.slice(0, 90));
    }
    if (indice < 0) {
      const palabras = aguja
        .split(" ")
        .filter((palabra) => palabra.length > 3)
        .slice(0, 6)
        .join(" ");
      if (palabras) indice = texto.indexOf(palabras);
    }
    if (indice < 0) return false;

    const referencia = mapa[indice];
    if (!referencia) return false;
    const padre = referencia.nodo.parentElement;
    if (!padre) return false;
    const bloque =
      padre.closest("p, li, blockquote, h1, h2, h3, h4, td, pre") || padre;
    bloque.classList.add("resaltado");
    bloque.scrollIntoView({
      block: "center",
      behavior: reducido ? "auto" : "smooth",
    });
    return true;
  }

  function mapaTexto(raiz) {
    const caminante = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    let nodo;
    let texto = "";
    let espacioPrevio = true;
    const mapa = [];
    while ((nodo = caminante.nextNode()) !== null) {
      const valor = nodo.nodeValue || "";
      for (let indice = 0; indice < valor.length; indice += 1) {
        const caracter = normalizarCaracter(valor[indice]);
        if (!caracter) continue;
        if (/\s/.test(caracter)) {
          if (espacioPrevio) continue;
          texto += " ";
          mapa.push({ nodo, indice });
          espacioPrevio = true;
        } else {
          texto += caracter;
          mapa.push({ nodo, indice });
          espacioPrevio = false;
        }
      }
      if (!espacioPrevio) {
        texto += " ";
        mapa.push(null);
        espacioPrevio = true;
      }
    }
    return { texto, mapa };
  }

  function normalizarCaracter(caracter) {
    return caracter
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function normalizar(valor) {
    return String(valor || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
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
    if (texto.toLowerCase() === "core") return "Núcleo";
    if (texto.toLowerCase() === "extended") return "Extendida";
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function urlSegura(valor) {
    if (typeof valor !== "string") return "";
    const limpio = valor.trim();
    return /^https?:\/\//i.test(limpio) ? limpio : "";
  }
})();
