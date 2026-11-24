// Confirmación plegable de la cotización (rediseño 049, Stitch «Confirmación y transición a
// chat»). Cada <section data-coti> de _productos.html recibe un estado en data-coti-estado:
//
// - «recien»: cotización emitida en este turno. Carva celebra y una cuenta de 10 s (CSS,
//   pausada mientras la persona mira la tarjeta o escucha la respuesta) la minimiza sola.
// - «abierta» / «plegada»: el botón «Ver detalle / Ocultar detalle» alterna el detalle.
//
// El movimiento vive en chat.css (grid-template-rows). Aquí solo hay estado, foco y avisos
// para lectores de pantalla. Sin este script la tarjeta queda abierta y sin el botón.
"use strict";

(() => {
  const hilo = document.getElementById("hilo");
  if (!hilo) return;

  // Una cotización consultada después (otro turno, otra visita) no se celebra.
  const RECIENTE_MS = 3 * 60 * 1000;
  const PULSO_MS = 1100;
  let secuencia = 0;

  const anuncio = document.createElement("p");
  anuncio.className = "sr-solo";
  anuncio.setAttribute("role", "status");
  anuncio.setAttribute("aria-live", "polite");
  document.body.append(anuncio);

  function avisar(texto) {
    anuncio.textContent = "";
    // Un cambio en el siguiente cuadro asegura que se lea aunque el texto se repita.
    requestAnimationFrame(() => {
      anuncio.textContent = texto;
    });
  }

  function partes(seccion) {
    return {
      detalle: seccion.querySelector("[data-coti-detalle]"),
      celebra: seccion.querySelector("[data-coti-celebra]"),
      alternar: seccion.querySelector("[data-coti-alternar]"),
      texto: seccion.querySelector("[data-coti-alternar-texto]"),
    };
  }

  function pintar(seccion, estado) {
    const { detalle, celebra, alternar, texto } = partes(seccion);
    seccion.dataset.cotiEstado = estado;
    const plegada = estado === "plegada";
    if (detalle) detalle.inert = plegada;
    if (celebra) celebra.inert = estado !== "recien";
    if (alternar) alternar.setAttribute("aria-expanded", String(!plegada));
    if (texto) texto.textContent = plegada ? "Ver detalle" : "Ocultar detalle";
  }

  function folio(seccion) {
    return seccion.dataset.cotiFolio ? `#${seccion.dataset.cotiFolio}` : "";
  }

  function cambiar(seccion, estado) {
    if (seccion.dataset.cotiEstado === estado) return;
    const { alternar } = partes(seccion);
    const focoDentro = seccion.contains(document.activeElement);
    pintar(seccion, estado);
    if (estado === "plegada") {
      seccion.classList.remove("coti-pulso");
      void seccion.offsetWidth;
      seccion.classList.add("coti-pulso");
      setTimeout(() => seccion.classList.remove("coti-pulso"), PULSO_MS);
      avisar(["Cotización", folio(seccion), "minimizada. Usa Ver detalle para abrirla."]
        .filter(Boolean).join(" "));
    } else {
      avisar("Detalle de la cotización abierto.");
    }
    // El foco no se pierde dentro de una parte que acaba de quedar inerte.
    const activo = document.activeElement;
    if (focoDentro && alternar && (!seccion.contains(activo) || activo?.closest("[inert]"))) {
      alternar.focus({ preventScroll: true });
    }
  }

  function esReciente(seccion) {
    if (!seccion.dataset.cotiFolio) return false;
    const emitida = Date.parse(seccion.dataset.cotiEmitida || "");
    return Number.isNaN(emitida) || Date.now() - emitida < RECIENTE_MS;
  }

  function preparar(seccion, estadoInicial) {
    if (seccion.dataset.cotiEstado) return;
    const { detalle, alternar } = partes(seccion);
    if (detalle && !detalle.id) {
      secuencia += 1;
      detalle.id = `coti-detalle-${secuencia}`;
    }
    if (alternar && detalle) {
      alternar.setAttribute("aria-controls", detalle.id);
      alternar.hidden = false;
    }
    pintar(seccion, estadoInicial);
  }

  // Historial recargado: solo la última cotización queda abierta; las demás, resumidas.
  const previas = Array.from(hilo.querySelectorAll("section[data-coti]"));
  previas.forEach((seccion, indice) => {
    preparar(seccion, indice === previas.length - 1 ? "abierta" : "plegada");
  });

  // Turnos nuevos: chat-stream.js inserta el HTML de productos mientras llega la respuesta.
  new MutationObserver((cambios) => {
    for (const cambio of cambios) {
      for (const nodo of cambio.addedNodes) {
        if (nodo.nodeType !== Node.ELEMENT_NODE) continue;
        const secciones = nodo.matches("section[data-coti]")
          ? [nodo]
          : Array.from(nodo.querySelectorAll("section[data-coti]"));
        for (const seccion of secciones) {
          const nueva = Boolean(seccion.closest(".turno-nuevo"));
          preparar(seccion, nueva && esReciente(seccion) ? "recien" : "abierta");
        }
      }
    }
  }).observe(hilo, { childList: true, subtree: true });

  hilo.addEventListener("click", (event) => {
    const minimizar = event.target.closest("[data-coti-minimizar]");
    const alternar = event.target.closest("[data-coti-alternar]");
    const seccion = (minimizar || alternar)?.closest("section[data-coti]");
    if (!seccion) return;
    if (minimizar) cambiar(seccion, "plegada");
    else cambiar(seccion, seccion.dataset.cotiEstado === "plegada" ? "abierta" : "plegada");
  });

  // Fin de la cuenta regresiva (animación CSS de la barra): se minimiza sola.
  hilo.addEventListener("animationend", (event) => {
    if (!event.target.matches?.("[data-coti-relleno]")) return;
    const seccion = event.target.closest("section[data-coti]");
    if (seccion?.dataset.cotiEstado === "recien") cambiar(seccion, "plegada");
  });
})();
