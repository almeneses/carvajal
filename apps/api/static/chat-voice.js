// Chat por voz (spec 040): dictado y lectura con las APIs del navegador.
//
// El audio nunca sale del navegador hacia el servidor del proyecto: aquí solo se
// manejan transcripciones, preferencias booleanas del visitante y la lectura de
// texto ya renderizado. La detección es por capacidad, no por marca ni versión.
(() => {
  "use strict";

  const MAX_BORRADOR = 500;
  const MAX_GRABACION_MS = 60000;
  const SILENCIO_MANOS_LIBRES_MS = 10000;
  const CLAVES = {
    aviso: "carvajal_voz_aviso_dictado",
    autoLectura: "carvajal_voz_autolectura",
  };
  const MENSAJES = {
    sinSoporte: "Tu navegador no permite dictar por voz. Puedes escribir tu pregunta.",
    sinContexto: "El dictado necesita una conexión segura (HTTPS o localhost). Puedes escribir tu pregunta.",
    sinMicrofono: "No se detecta un micrófono. Puedes escribir tu pregunta.",
    permiso: "No diste permiso al micrófono. Puedes escribir tu pregunta.",
    silencio: "No se oyó ninguna voz. Inténtalo de nuevo o escribe tu pregunta.",
    error: "No se pudo completar el dictado. Puedes escribir tu pregunta.",
  };

  /** Almacenamiento tolerante: si el navegador lo bloquea, solo hay memoria. */
  function crearAlmacenSeguro(almacen) {
    const memoria = new Map();
    let disponible = false;
    try {
      if (almacen) {
        const prueba = "__carvajal_voz__";
        almacen.setItem(prueba, "1");
        almacen.removeItem(prueba);
        disponible = true;
      }
    } catch {
      disponible = false;
    }
    return {
      get(clave) {
        if (disponible) {
          try {
            return almacen.getItem(clave);
          } catch {
            disponible = false;
          }
        }
        return memoria.has(clave) ? memoria.get(clave) : null;
      },
      set(clave, valor) {
        memoria.set(clave, String(valor));
        if (!disponible) return;
        try {
          almacen.setItem(clave, String(valor));
        } catch {
          disponible = false;
        }
      },
    };
  }

  function crearPreferencias(almacen) {
    const aviso = almacen.get(CLAVES.aviso) === "1";
    const autoLectura = almacen.get(CLAVES.autoLectura) === "1";
    return {
      get avisoAceptado() {
        return aviso;
      },
      get autoLectura() {
        return autoLectura;
      },
      aceptarAviso() {
        if (aviso) return;
        almacen.set(CLAVES.aviso, "1");
      },
      fijarAutoLectura(valor) {
        almacen.set(CLAVES.autoLectura, valor ? "1" : "0");
      },
    };
  }

  function createVoiceController(opciones) {
    const vista = opciones.vista || {};
    const documento = opciones.documento || document;
    const almacen = crearAlmacenSeguro(opciones.almacen);
    const preferencias = crearPreferencias(almacen);
    const Reconocimiento = opciones.reconocimiento || null;
    const sintesis = opciones.sintesis || null;
    const Utterance = opciones.Utterance || null;
    const reloj = opciones.reloj || { setTimeout, clearTimeout, now: () => Date.now() };
    const segura = Boolean(opciones.segura);

    const soporte = {
      dictado: Boolean(Reconocimiento) && segura,
      sintesis: Boolean(sintesis && Utterance),
      streaming: Boolean(opciones.streaming),
    };

    function mount() {
      if (vista.raiz) vista.raiz.hidden = false;
      if (documento.documentElement) {
        documento.documentElement.dataset.voz = "1";
        if (soporte.sintesis) documento.documentElement.dataset.vozTts = "1";
      }
      prepararBotonDictar();
      prepararLectura();
    }

    function prepararBotonDictar() {
      const boton = vista.botonDictar;
      if (!boton) return;
      if (!soporte.dictado) {
        boton.hidden = true;
        if (vista.nota) {
          vista.nota.hidden = false;
          vista.nota.textContent = segura ? MENSAJES.sinSoporte : MENSAJES.sinContexto;
        }
        return;
      }
      boton.hidden = false;
      boton.setAttribute("type", "button");
      boton.setAttribute("aria-pressed", "false");
      boton.setAttribute("aria-label", "Dictar una pregunta");
      if (vista.etiquetaDictar) vista.etiquetaDictar.textContent = "Dictar";
      if (vista.nota) vista.nota.hidden = true;
    }

    function prepararLectura() {
      const casilla = vista.autoLectura;
      if (!casilla) return;
      if (!soporte.sintesis) {
        casilla.hidden = true;
        return;
      }
      casilla.hidden = false;
      casilla.checked = preferencias.autoLectura;
    }

    return {
      mount,
      soporte,
      preferencias,
      /** Estado interno observable por las pruebas; no se envía a la red. */
      estado: { ciclo: 0 },
      destruir() {},
    };
  }

  function init() {
    const raiz = document.querySelector("[data-voz]");
    if (!raiz) return;
    const buscar = (selector) => raiz.querySelector(selector);
    const win = typeof window === "undefined" ? {} : window;
    // Acceder a localStorage puede lanzar con el almacenamiento bloqueado; en
    // ese caso el controlador conserva las preferencias solo en memoria.
    let almacen = null;
    try {
      almacen = win.localStorage || null;
    } catch {
      almacen = null;
    }
    const Reconocimiento = win.SpeechRecognition || win.webkitSpeechRecognition || null;
    const sintesis = win.speechSynthesis || null;
    const Utterance = win.SpeechSynthesisUtterance || null;
    const controlador = createVoiceController({
      vista: {
        raiz,
        botonDictar: buscar("[data-voz-dictar]"),
        etiquetaDictar: buscar("[data-voz-dictar-texto]"),
        estado: buscar("[data-voz-estado]"),
        nota: buscar("[data-voz-nota]"),
        autoLectura: buscar("[data-voz-autolectura]"),
      },
      documento: document,
      almacen,
      reconocimiento: Reconocimiento,
      sintesis,
      Utterance,
      segura: Boolean(win.isSecureContext),
      streaming: typeof win.fetch === "function" && typeof win.ReadableStream === "function",
    });
    controlador.mount();
    raiz._controladorVoz = controlador;
  }

  const api = {
    MAX_BORRADOR,
    MAX_GRABACION_MS,
    SILENCIO_MANOS_LIBRES_MS,
    MENSAJES,
    createVoiceController,
  };
  window.CarvajalVoz = api;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})();
