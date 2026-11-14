/* Carga de archivos: botón con spinner y sincronización con el job en background. */
(function () {
  "use strict";

  function formulario() {
    return document.querySelector("[data-carga-form]");
  }

  function estado() {
    var nodo = document.querySelector("[data-carga-estado]");
    return nodo ? nodo.dataset.cargaEstado : "idle";
  }

  function setCargando(activo, etiqueta, bloquearCampos) {
    var form = formulario();
    if (!form) {
      return;
    }
    var boton = form.querySelector("[data-carga-submit]");
    var spinner = form.querySelector("[data-carga-spinner]");
    var texto = form.querySelector("[data-carga-texto]");
    form.setAttribute("aria-busy", activo ? "true" : "false");
    form.querySelectorAll('input[type="file"]').forEach(function (input) {
      input.disabled = activo && bloquearCampos;
    });
    if (boton) {
      boton.disabled = activo;
      if (activo) {
        boton.setAttribute("aria-busy", "true");
      } else {
        boton.removeAttribute("aria-busy");
      }
    }
    if (spinner) {
      spinner.hidden = !activo;
    }
    if (texto) {
      texto.textContent = activo ? etiqueta : "Procesar e indexar";
    }
  }

  function sincronizar() {
    if (estado() === "running") {
      setCargando(true, "Procesando…", true);
    } else {
      setCargando(false, "", false);
    }
  }

  function panel() {
    return document.getElementById("carga-estado");
  }

  document.addEventListener("htmx:beforeRequest", function (evento) {
    var origen = evento.detail && evento.detail.elt;
    if (origen && origen.closest && origen.closest("[data-carga-form]")) {
      setCargando(true, "Subiendo…", false);
    }
  });

  document.addEventListener("htmx:afterSettle", sincronizar);

  document.addEventListener("htmx:responseError", function (evento) {
    var detalle = evento.detail || {};
    var origen = detalle.elt;
    if (!origen || !origen.closest || !origen.closest("[data-carga-form]")) {
      return;
    }
    var destino = panel();
    if (destino && detalle.xhr && detalle.xhr.responseText) {
      destino.innerHTML = detalle.xhr.responseText;
      if (window.htmx && typeof window.htmx.process === "function") {
        window.htmx.process(destino);
      }
    }
    sincronizar();
  });

  document.addEventListener("htmx:sendError", function (evento) {
    var origen = evento.detail && evento.detail.elt;
    if (!origen || !origen.closest || !origen.closest("[data-carga-form]")) {
      return;
    }
    var destino = panel();
    if (destino) {
      destino.insertAdjacentHTML(
        "afterbegin",
        '<p class="aviso" role="alert">No se pudo contactar con el servidor. Inténtalo de nuevo.</p>'
      );
    }
    setCargando(false, "", false);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sincronizar);
  } else {
    sincronizar();
  }
})();
