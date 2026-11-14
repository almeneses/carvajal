// Formato seguro y animación en cascada del texto generado.
// El texto llega como Markdown ligero; se construye con nodos, nunca con
// innerHTML, para que el contenido del modelo no pueda inyectar etiquetas.
(() => {
  "use strict";

  const REDUCIDO =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const PATRON_INLINE =
    /(\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s<]+|\[\d{1,2}\])/g;

  function formatear(texto, fuentes) {
    const listaFuentes = Array.isArray(fuentes) ? fuentes : [];
    const fragmento = document.createDocumentFragment();
    const lineas = String(texto == null ? "" : texto).replace(/\r\n/g, "\n").split("\n");
    let parrafo = [];
    let lista = null;
    let tipoLista = "";

    const cerrarLista = () => {
      if (!lista) return;
      fragmento.append(lista);
      lista = null;
      tipoLista = "";
    };

    const volcarParrafo = () => {
      if (parrafo.length === 0) return;
      const nodo = document.createElement("p");
      agregarInline(nodo, parrafo.join(" "), listaFuentes);
      fragmento.append(nodo);
      parrafo = [];
    };

    const agregarBloque = (nodo) => {
      volcarParrafo();
      cerrarLista();
      fragmento.append(nodo);
    };

    for (const linea of lineas) {
      const limpia = linea.trim();
      if (!limpia) {
        volcarParrafo();
        cerrarLista();
        continue;
      }

      const titulo = /^(#{1,6})\s+(.+)$/.exec(limpia);
      if (titulo) {
        const nodo = document.createElement(titulo[1].length <= 2 ? "h3" : "h4");
        nodo.className = "texto-titulo";
        agregarInline(nodo, titulo[2], listaFuentes);
        agregarBloque(nodo);
        continue;
      }

      const cita = /^>\s?(.*)$/.exec(limpia);
      if (cita) {
        const nodo = document.createElement("blockquote");
        agregarInline(nodo, cita[1], listaFuentes);
        agregarBloque(nodo);
        continue;
      }

      if (esCitaEntera(limpia)) {
        const nodo = document.createElement("blockquote");
        agregarInline(nodo, limpia, listaFuentes);
        agregarBloque(nodo);
        continue;
      }

      const item = /^[-*]\s+(.+)$/.exec(limpia);
      if (item) {
        if (!lista || tipoLista !== "ul") {
          volcarParrafo();
          cerrarLista();
          lista = document.createElement("ul");
          tipoLista = "ul";
        }
        const elemento = document.createElement("li");
        agregarInline(elemento, item[1], listaFuentes);
        lista.append(elemento);
        continue;
      }

      const numerada = /^\d+[.)]\s+(.+)$/.exec(limpia);
      if (numerada) {
        if (!lista || tipoLista !== "ol") {
          volcarParrafo();
          cerrarLista();
          lista = document.createElement("ol");
          tipoLista = "ol";
        }
        const elemento = document.createElement("li");
        agregarInline(elemento, numerada[1], listaFuentes);
        lista.append(elemento);
        continue;
      }

      parrafo.push(limpia);
    }

    volcarParrafo();
    cerrarLista();

    if (fragmento.childNodes.length === 0) {
      const nodo = document.createElement("p");
      agregarInline(nodo, String(texto == null ? "" : texto), listaFuentes);
      fragmento.append(nodo);
    }
    return fragmento;
  }

  function esCitaEntera(texto) {
    return (
      texto.length > 2 &&
      ((texto.startsWith('"') && texto.endsWith('"')) ||
        (texto.startsWith("“") && texto.endsWith("”")))
    );
  }

  function agregarInline(padre, texto, fuentes) {
    const valor = String(texto == null ? "" : texto);
    let ultimo = 0;
    let coincidencia;
    PATRON_INLINE.lastIndex = 0;
    while ((coincidencia = PATRON_INLINE.exec(valor)) !== null) {
      if (coincidencia.index > ultimo) {
        padre.append(document.createTextNode(valor.slice(ultimo, coincidencia.index)));
      }
      padre.append(crearInline(coincidencia[0], fuentes));
      ultimo = PATRON_INLINE.lastIndex;
    }
    if (ultimo < valor.length) {
      padre.append(document.createTextNode(valor.slice(ultimo)));
    }
  }

  function crearInline(token, fuentes) {
    if (token.startsWith("**") && token.endsWith("**")) {
      return envolver(token.slice(2, -2), "strong");
    }
    if (token.startsWith("__") && token.endsWith("__")) {
      return envolver(token.slice(2, -2), "strong");
    }
    if (token.startsWith("*") && token.endsWith("*")) {
      return envolver(token.slice(1, -1), "em");
    }
    if (token.startsWith("_") && token.endsWith("_")) {
      return envolver(token.slice(1, -1), "em");
    }
    if (token.startsWith("`") && token.endsWith("`")) {
      return envolver(token.slice(1, -1), "code");
    }

    const enlace = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
    if (enlace) {
      return crearEnlace(enlace[1], enlace[2]);
    }

    if (/^\[\d{1,2}\]$/.test(token)) {
      return crearCita(token, fuentes);
    }

    if (/^https?:\/\//i.test(token)) {
      let url = token;
      let sufijo = "";
      while (url && ".,;:!?)]}".includes(url[url.length - 1])) {
        sufijo = url[url.length - 1] + sufijo;
        url = url.slice(0, -1);
      }
      return crearEnlace(url, url, sufijo);
    }

    return document.createTextNode(token);
  }

  function envolver(texto, etiqueta) {
    const nodo = document.createElement(etiqueta);
    nodo.textContent = texto;
    return nodo;
  }

  function crearEnlace(texto, url, sufijo = "") {
    if (!/^https?:\/\//i.test(url)) return document.createTextNode(texto + sufijo);
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.target = "_blank";
    enlace.rel = "noopener noreferrer";
    enlace.textContent = texto;
    if (!sufijo) return enlace;
    const envoltura = document.createDocumentFragment();
    envoltura.append(enlace, document.createTextNode(sufijo));
    return envoltura;
  }

  function crearCita(token, fuentes) {
    const numero = Number(token.slice(1, -1));
    if (!Array.isArray(fuentes) || numero < 1 || numero > fuentes.length) {
      return document.createTextNode(token);
    }
    const enlace = document.createElement("a");
    enlace.className = "cita-inline";
    enlace.href = `#cita-${numero}`;
    enlace.textContent = token;
    enlace.setAttribute("aria-label", `Abrir la fuente ${numero}`);
    return enlace;
  }

  function animar(raiz, opciones = {}) {
    if (!raiz || REDUCIDO) return;
    if (opciones.forzar !== true && raiz.dataset.animado === "1") return;
    raiz.dataset.animado = "1";

    const caminante = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    const nodos = [];
    let nodo;
    while ((nodo = caminante.nextNode()) !== null) {
      if (nodo.nodeValue && nodo.nodeValue.trim()) nodos.push(nodo);
    }

    let indice = 0;
    for (const texto of nodos) {
      const partes = texto.nodeValue.split(/(\s+)/);
      const fragmento = document.createDocumentFragment();
      for (const parte of partes) {
        if (!parte) continue;
        if (/^\s+$/.test(parte)) {
          fragmento.append(document.createTextNode(parte));
          continue;
        }
        const palabra = document.createElement("span");
        palabra.className = "palabra";
        palabra.style.setProperty("--i", String(Math.min(indice, 60)));
        palabra.textContent = parte;
        fragmento.append(palabra);
        indice += 1;
      }
      texto.replaceWith(fragmento);
    }
  }

  function animarDocumento(raiz = document) {
    for (const nodo of raiz.querySelectorAll(".texto-formateado")) {
      animar(nodo);
    }
  }

  window.CarvajalTexto = { animar, animarDocumento, formatear };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => animarDocumento());
  } else {
    animarDocumento();
  }
})();
