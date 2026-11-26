// Panel lateral de hilos anteriores del chat.
(() => {
  "use strict";

  const panel = document.getElementById("panel-hilos");
  if (!panel) return;

  const cerrar = panel.querySelector("[data-cerrar-hilos]");
  const reducido =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let disparador = null;
  let cerrando = false;

  for (const boton of document.querySelectorAll("[data-abrir-hilos]")) {
    boton.addEventListener("click", () => abrirPanel(boton));
  }
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
    if (disparador && typeof disparador.focus === "function") disparador.focus();
    disparador = null;
  });

  if (document.querySelector("[data-hilo-nuevo]")) {
    // «Nuevo hilo» (decisión del owner, 2026-11-26): el panel no se abre encima del chat.
    // «Nuevo hilo» es un POST que recarga la página y la plantilla nunca pinta el panel
    // abierto, así que basta con no abrirlo. El chat vacío queda a la vista y el foco en el
    // compositor; el aviso del saludo dice que el hilo anterior quedó guardado.
    enfocarCompositor();
    limpiarMarcaDeHiloNuevo();
  } else if (panel.dataset.abrir === "1") {
    abrirPanel(null);
  }

  function enfocarCompositor() {
    const compositor = document.getElementById("pregunta");
    if (!compositor || typeof compositor.focus !== "function") return;
    compositor.focus({ preventScroll: true });
    // En un teléfono bajo el compositor puede empezar bajo el pliegue: se acerca lo justo,
    // sin centrarlo, para que el aviso de justo encima siga a la vista.
    if (typeof compositor.scrollIntoView === "function") {
      compositor.scrollIntoView({ block: "nearest" });
    }
  }

  // Quita «nuevo=1» de la URL: al recargar o compartir el enlace no se repite el aviso.
  function limpiarMarcaDeHiloNuevo() {
    if (!window.history || typeof window.history.replaceState !== "function") return;
    try {
      const url = new URL(window.location.href);
      if (!url.searchParams.has("nuevo")) return;
      url.searchParams.delete("nuevo");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    } catch {
      // Sin URL o History utilizables, la marca se queda: el aviso es inofensivo.
    }
  }

  function abrirPanel(origen) {
    disparador = origen || document.activeElement;
    panel.removeAttribute("data-cerrando");
    if (!panel.open) {
      panel.showModal();
      document.body.classList.add("panel-abierto");
    }
    if (cerrar) cerrar.focus();
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
})();
