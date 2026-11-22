/* Progreso de la captura web: consume el SSE y pinta etapas, log y resultado. */
(function () {
  "use strict";

  var ETAPAS = [
    "descubrir",
    "robots",
    "descargar",
    "extraer",
    "deduplicar",
    "enriquecer",
    "indice",
  ];
  var conectados = new WeakSet();

  function texto(valor) {
    return valor === undefined || valor === null ? "" : String(valor);
  }

  function elemento(etiqueta, clase, contenido) {
    var nodo = document.createElement(etiqueta);
    if (clase) {
      nodo.className = clase;
    }
    if (contenido !== undefined) {
      nodo.textContent = contenido;
    }
    return nodo;
  }

  function formActual() {
    return document.querySelector("[data-captura-form]");
  }

  function bloquearFormulario(bloqueado) {
    var form = formActual();
    if (!form) {
      return;
    }
    var boton = form.querySelector('button[type="submit"]');
    if (boton) {
      boton.disabled = bloqueado;
    }
  }

  function marcarEtapa(panel, etapa) {
    var indice = ETAPAS.indexOf(etapa);
    if (indice < 0) {
      return;
    }
    panel.querySelectorAll("[data-etapa]").forEach(function (nodo) {
      var posicion = ETAPAS.indexOf(nodo.dataset.etapa);
      nodo.classList.toggle("captura-etapa-completa", posicion < indice);
      nodo.classList.toggle("captura-etapa-activa", posicion === indice);
    });
  }

  function completarEtapas(panel) {
    panel.querySelectorAll("[data-etapa]").forEach(function (nodo) {
      nodo.classList.remove("captura-etapa-activa");
      nodo.classList.add("captura-etapa-completa");
    });
  }

  function agregarLog(panel, mensaje) {
    var log = panel.querySelector("[data-captura-log]");
    if (!log) {
      return;
    }
    var lineas = log.textContent ? log.textContent.split("\n") : [];
    lineas.push(mensaje);
    log.textContent = lineas.slice(-14).join("\n");
    log.scrollTop = log.scrollHeight;
  }

  function tarjeta(etiqueta, valor) {
    var nodo = elemento("div", "captura-metrica");
    nodo.appendChild(elemento("span", "captura-metrica-nombre", etiqueta));
    nodo.appendChild(elemento("strong", "", texto(valor)));
    return nodo;
  }

  function lista(titulo, valores) {
    if (!valores || !valores.length) {
      return null;
    }
    var bloque = elemento("div", "captura-incidencias");
    bloque.appendChild(elemento("h3", "", titulo));
    var ul = elemento("ul");
    valores.forEach(function (valor) {
      ul.appendChild(elemento("li", "", texto(valor)));
    });
    bloque.appendChild(ul);
    return bloque;
  }

  function pintarResultado(panel, outcome) {
    var contenedor = panel.querySelector("[data-captura-resultado]");
    if (!contenedor) {
      return;
    }
    contenedor.textContent = "";
    var metricas = elemento("div", "captura-metricas");
    metricas.appendChild(tarjeta("Escritas en Gold", outcome.written));
    metricas.appendChild(tarjeta("Importadas", outcome.documents));
    metricas.appendChild(tarjeta("Fragmentos", outcome.chunks));
    metricas.appendChild(tarjeta("Indexadas", outcome.indexed));
    metricas.appendChild(tarjeta("Descartadas", outcome.discarded));
    metricas.appendChild(tarjeta("Caracteres", Number(outcome.chars || 0).toLocaleString("es-CO")));
    metricas.appendChild(tarjeta("Duración", texto(outcome.duration_seconds) + " s"));
    contenedor.appendChild(metricas);

    var incidencias = [
      lista("Errores y descartes", outcome.errors),
      lista("Bloqueadas por robots.txt", outcome.robots_denied),
      lista("Duplicadas por hash", outcome.duplicates),
    ];
    incidencias.forEach(function (bloque) {
      if (bloque) {
        contenedor.appendChild(bloque);
      }
    });
  }

  function cerrarEstado(panel, etiqueta, clase) {
    var estado = panel.querySelector("[data-captura-estado]");
    if (estado) {
      estado.textContent = etiqueta;
      estado.className = "etiqueta " + clase;
    }
  }

  function iniciar(panel) {
    if (conectados.has(panel)) {
      return;
    }
    conectados.add(panel);
    var jobId = panel.dataset.capturaJob;
    var streamUrl = panel.dataset.capturaStream;
    if (!jobId || !streamUrl || typeof EventSource === "undefined") {
      return;
    }

    bloquearFormulario(true);
    var fuente = new EventSource(streamUrl);
    var procesadas = 0;
    var terminado = false;

    fuente.addEventListener("stage", function (evento) {
      var datos = {};
      try {
        datos = JSON.parse(evento.data);
      } catch (error) {
        return;
      }
      marcarEtapa(panel, datos.stage);
      if (datos.stage === "enriquecer") {
        procesadas += 1;
      }
      var linea = texto(datos.stage);
      if (datos.url) {
        linea += " · " + texto(datos.url).replace(/^https?:\/\//, "");
      }
      if (datos.detail) {
        linea += " · " + texto(datos.detail);
      }
      agregarLog(panel, linea);
      cerrarEstado(
        panel,
        procesadas ? procesadas + " página(s) procesadas" : "Capturando…",
        "etiqueta-externa"
      );
    });

    fuente.addEventListener("done", function (evento) {
      terminado = true;
      fuente.close();
      var outcome = {};
      try {
        outcome = JSON.parse(evento.data);
      } catch (error) {
        outcome = {};
      }
      completarEtapas(panel);
      var incidencias = (outcome.errors || []).length;
      cerrarEstado(
        panel,
        incidencias ? "Completada con incidencias" : "Completada",
        incidencias ? "etiqueta-externa" : "etiqueta-oficial"
      );
      pintarResultado(panel, outcome);
      bloquearFormulario(false);
    });

    fuente.addEventListener("error", function (evento) {
      if (evento.data) {
        terminado = true;
        fuente.close();
        completarEtapas(panel);
        cerrarEstado(panel, "Error", "etiqueta-externa");
        agregarLog(panel, "Error: " + texto(evento.data));
        bloquearFormulario(false);
      }
    });

    fuente.onerror = function () {
      if (terminado) {
        return;
      }
      if (fuente.readyState === EventSource.CLOSED) {
        cerrarEstado(panel, "Conexión cerrada", "etiqueta-externa");
        bloquearFormulario(false);
      }
    };
  }

  function iniciarPendientes() {
    document.querySelectorAll("[data-captura-job]").forEach(iniciar);
  }

  document.addEventListener("htmx:afterSettle", iniciarPendientes);

  document.addEventListener("htmx:responseError", function (evento) {
    var detalle = evento.detail || {};
    var origen = detalle.elt;
    if (!origen || !origen.closest || !origen.closest("[data-captura-form]")) {
      return;
    }
    var panel = document.getElementById("captura-panel");
    if (panel && detalle.xhr && detalle.xhr.responseText) {
      panel.innerHTML = detalle.xhr.responseText;
      if (window.htmx && typeof window.htmx.process === "function") {
        window.htmx.process(panel);
      }
      iniciarPendientes();
    }
  });

  document.addEventListener("htmx:sendError", function (evento) {
    var origen = evento.detail && evento.detail.elt;
    if (!origen || !origen.closest || !origen.closest("[data-captura-form]")) {
      return;
    }
    var panel = document.getElementById("captura-panel");
    if (panel) {
      panel.insertAdjacentHTML(
        "afterbegin",
        '<p class="aviso" role="alert">No se pudo contactar con el servidor. Inténtalo de nuevo.</p>'
      );
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciarPendientes);
  } else {
    iniciarPendientes();
  }
})();
