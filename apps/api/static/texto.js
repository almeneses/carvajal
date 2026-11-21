// Formato seguro del texto generado.
// El texto llega como Markdown ligero; se construye con nodos, nunca con
// innerHTML, para que el contenido del modelo no pueda inyectar etiquetas.
(() => {
  "use strict";

  const REDUCIDO =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const PATRON_INLINE =
    /(\*\*[^*\n]+\*\*|__[^_\n]+__|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_)|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/(?:\[[^\]\s]+\]|[^\s<)[\]])+)/g;
  const PATRON_PREVIO = new RegExp(
    PATRON_INLINE.source + String.raw`|\[([^\]\n]*)(?:\]\([^)\n]*)?$`,
    "g",
  );
  const PATRON_CITA =
    /`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s<)]+|[ \t]*\[(\d{1,2})\](?!\()/g;

  function formatear(texto, cantidadFuentes, provisional = false) {
    const fragmento = document.createDocumentFragment();
    let limpio = texto.replace(PATRON_CITA, (token, numero) =>
      numero >= 1 && numero <= cantidadFuentes ? "" : token,
    );
    if (provisional) {
      limpio = limpio.replace(/(^|\n)[ \t]*(?:#{1,6}|[-*>]|\d+[.)])[ \t]*$/, "$1");
    }
    const lineas = limpio.split("\n");
    const inline = (nodo, contenido) => agregarInline(nodo, contenido, provisional);
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
      inline(nodo, parrafo.join(" "));
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
        inline(nodo, titulo[2]);
        agregarBloque(nodo);
        continue;
      }

      const cita = /^>\s?(.*)$/.exec(limpia);
      if (cita) {
        const nodo = document.createElement("blockquote");
        inline(nodo, cita[1]);
        agregarBloque(nodo);
        continue;
      }

      if (esCitaEntera(limpia)) {
        const nodo = document.createElement("blockquote");
        inline(nodo, limpia);
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
        inline(elemento, item[1]);
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
        inline(elemento, numerada[1]);
        lista.append(elemento);
        continue;
      }

      cerrarLista();
      parrafo.push(limpia);
    }

    volcarParrafo();
    cerrarLista();

    return fragmento;
  }

  function crearFlujo(nodo) {
    nodo.classList.add("texto-formateado");
    let linea = "";
    let bloque = [];
    let previa = [];

    function quitarPrevia() {
      previa.forEach((elemento) => elemento.remove());
      previa = [];
    }

    function mostrar(texto, cantidadFuentes, provisional = false) {
      quitarPrevia();
      const fragmento = formatear(texto, cantidadFuentes, provisional);
      if (provisional) previa = Array.from(fragmento.children);
      nodo.append(fragmento);
    }

    function cerrarBloque(cantidadFuentes, interrumpido = false) {
      if (bloque.length === 0) return;
      mostrar(bloque.join("\n"), cantidadFuentes, interrumpido);
      previa = [];
      bloque = [];
    }

    return {
      agregar(texto, cantidadFuentes) {
        // La línea vacía sella el bloque para no volver a tocar sus nodos.
        const lineas = texto.split("\n");
        linea += lineas[0];
        for (const siguiente of lineas.slice(1)) {
          if (linea.trim()) bloque.push(linea);
          else cerrarBloque(cantidadFuentes);
          linea = siguiente;
        }
      },
      adelantar(cantidadFuentes) {
        // Solo la vista previa del bloque activo cambia entre lotes.
        mostrar([...bloque, linea].join("\n"), cantidadFuentes, true);
      },
      terminar(cantidadFuentes, interrumpido = false) {
        if (linea.trim()) bloque.push(linea);
        linea = "";
        cerrarBloque(cantidadFuentes, interrumpido);
      },
      reemplazar(texto, cantidadFuentes = 0) {
        linea = "";
        bloque = [];
        previa = [];
        nodo.replaceChildren();
        mostrar(texto, cantidadFuentes);
      },
    };
  }

  function esCitaEntera(texto) {
    return (
      texto.length > 2 &&
      ((texto.startsWith('"') && texto.endsWith('"')) ||
        (texto.startsWith("“") && texto.endsWith("”")))
    );
  }

  function agregarInline(padre, texto, provisional) {
    const valor = String(texto == null ? "" : texto);
    const agregarLiteral = (literal) => {
      const limpio = provisional
        ? literal.replace(/(^|[\s(])(?:\*{1,2}|_{1,2}|`)(?=\S|$)|(?:\*{1,2}|_{1,2}|`)$/g, "$1")
        : literal;
      if (limpio) padre.append(document.createTextNode(limpio));
    };
    let ultimo = 0;
    let coincidencia;
    const patron = provisional ? PATRON_PREVIO : PATRON_INLINE;
    patron.lastIndex = 0;
    while ((coincidencia = patron.exec(valor)) !== null) {
      if (coincidencia.index > ultimo) {
        agregarLiteral(valor.slice(ultimo, coincidencia.index));
      }
      const etiqueta = coincidencia[2];
      if (etiqueta === undefined) padre.append(crearInline(coincidencia[0]));
      else if (!/^\d{0,2}$/.test(etiqueta)) agregarLiteral(etiqueta);
      ultimo = patron.lastIndex;
    }
    if (ultimo < valor.length) {
      agregarLiteral(valor.slice(ultimo));
    }
  }

  function crearInline(token) {
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

    if (/^https?:\/\//i.test(token)) {
      let url = token;
      let sufijo = "";
      while (url && ".,;:!?)}".includes(url[url.length - 1])) {
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

  function animar(raiz) {
    if (!raiz || REDUCIDO) return;
    if (raiz.dataset.animado === "1") return;
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
      if (nodo.closest(".hilo")) continue;
      animar(nodo);
    }
  }

  window.CarvajalTexto = { animarDocumento, crearFlujo };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => animarDocumento());
  } else {
    animarDocumento();
  }
})();
