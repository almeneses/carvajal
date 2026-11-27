// Métricas (spec 055): anuncia el resultado de «Reintentar» tras la redirección.
//
// El formulario funciona sin JS: el servidor responde 303 a /metricas con el resultado ya
// escrito en el estado de la fuente (role="status"). Una región viva solo anuncia cambios, no
// el texto que trae la página al cargar, así que aquí se vacía y se vuelve a escribir para que
// el lector de pantalla lo lea.
(() => {
  "use strict";

  const PAUSA_MS = 150;

  function anunciar(documento, programar) {
    const estado = documento.querySelector("[data-anunciar]");
    if (!estado) return null;
    const texto = estado.textContent.trim();
    if (!texto) return null;
    estado.textContent = "";
    programar(() => {
      estado.textContent = texto;
    }, PAUSA_MS);
    return estado;
  }

  const iniciar = () => anunciar(document, (tarea, ms) => window.setTimeout(tarea, ms));
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
