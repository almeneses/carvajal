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

  if (panel.dataset.abrir === "1") {
    abrirPanel(null);
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
