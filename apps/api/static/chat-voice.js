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
    escuchando: "Escuchando…",
    listo: "Dictado listo. Revisa y envía.",
    cancelado: "Dictado cancelado.",
    recortado: "Se recortó el dictado al límite de 500 caracteres.",
    deteniendo: "Procesando el dictado…",
    limite: "Se alcanzó el límite de 60 segundos.",
  };

  // --- Normalización conservadora de cantidades dictadas ---------------------
  // Solo convierte números escritos con palabras cuando van seguidos de una
  // unidad o de un producto comercial. Fechas, años y texto suelto no cambian.
  const VALORES = {
    cero: 0,
    uno: 1,
    dos: 2,
    tres: 3,
    cuatro: 4,
    cinco: 5,
    seis: 6,
    siete: 7,
    ocho: 8,
    nueve: 9,
    diez: 10,
    once: 11,
    doce: 12,
    trece: 13,
    catorce: 14,
    quince: 15,
    dieciseis: 16,
    dieciséis: 16,
    diecisiete: 17,
    dieciocho: 18,
    diecinueve: 19,
    veinte: 20,
    veintiun: 21,
    veintiún: 21,
    veintiuna: 21,
    veintidos: 22,
    veintidós: 22,
    veintitres: 23,
    veintitrés: 23,
    veinticuatro: 24,
    veinticinco: 25,
    veintiseis: 26,
    veintiséis: 26,
    veintisiete: 27,
    veintiocho: 28,
    veintinueve: 29,
    treinta: 30,
    cuarenta: 40,
    cincuenta: 50,
    sesenta: 60,
    setenta: 70,
    ochenta: 80,
    noventa: 90,
    cien: 100,
    ciento: 100,
    doscientos: 200,
    trescientos: 300,
    cuatrocientos: 400,
    quinientos: 500,
    seiscientos: 600,
    setecientos: 700,
    ochocientos: 800,
    novecientos: 900,
  };
  const NUMERO = Object.keys(VALORES)
    .sort((a, b) => b.length - a.length)
    .join("|");
  const UNIDAD = [
    "oz",
    "onzas?",
    "ml",
    "mililitros?",
    "litros?",
    "unidades?",
    "piezas?",
    "pzas?",
    "cajas?",
    "vasos?",
    "tapas?",
    "domos?",
    "contenedores?",
    "recipientes?",
    "portacomidas?",
    "cazuelas?",
    "bowls?",
    "charolas?",
    "bandejas?",
    "cubiertos?",
    "tenedores?",
    "cucharas?",
    "cucharitas?",
    "cuchillos?",
    "envases?",
    "platos?",
  ].join("|");
  const SECUENCIA = new RegExp(
    `\\b((?:${NUMERO}|mil)(?:\\s+(?:y\\s+)?(?:${NUMERO}|mil))*)\\b(?=\\s+(?:${UNIDAD})\\b)`,
    "gi",
  );

  function valorDePalabras(frase) {
    let total = 0;
    let parcial = 0;
    for (const palabra of frase.toLowerCase().split(/\s+/)) {
      if (palabra === "y") continue;
      if (palabra === "mil") {
        parcial = (parcial || 1) * 1000;
        total += parcial;
        parcial = 0;
        continue;
      }
      parcial += VALORES[palabra] || 0;
    }
    return total + parcial;
  }

  function formatearNumero(numero) {
    if (numero < 1000) return String(numero);
    return String(numero).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function normalizarCantidades(texto) {
    if (typeof texto !== "string" || !texto) return texto;
    return texto.replace(SECUENCIA, (coincidencia) => {
      const valor = valorDePalabras(coincidencia);
      return valor > 0 ? formatearNumero(valor) : coincidencia;
    });
  }

  /** Recorta por puntos de código: nunca parte un par sustituto UTF-16. */
  function recortarTexto(texto, maximo) {
    if (maximo <= 0) return "";
    let resultado = "";
    for (const caracter of Array.from(texto)) {
      if (resultado.length + caracter.length > maximo) break;
      resultado += caracter;
    }
    return resultado;
  }

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
    let aviso = almacen.get(CLAVES.aviso) === "1";
    let autoLectura = almacen.get(CLAVES.autoLectura) === "1";
    return {
      get avisoAceptado() {
        return aviso;
      },
      get autoLectura() {
        return autoLectura;
      },
      aceptarAviso() {
        if (aviso) return;
        aviso = true;
        almacen.set(CLAVES.aviso, "1");
      },
      fijarAutoLectura(valor) {
        autoLectura = Boolean(valor);
        almacen.set(CLAVES.autoLectura, autoLectura ? "1" : "0");
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
    const estado = { ciclo: 0, escuchando: false, mensaje: "", transcripcion: "" };
    let reconocimiento = null;
    let temporizador = null;
    let montado = false;

    function mount() {
      // Montar dos veces no debe duplicar listeners ni ciclos de voz.
      if (montado) return;
      montado = true;
      if (vista.raiz) vista.raiz.hidden = false;
      if (documento.documentElement) {
        documento.documentElement.dataset.voz = "1";
        if (soporte.sintesis) documento.documentElement.dataset.vozTts = "1";
      }
      prepararBotonDictar();
      prepararLectura();
      prepararAviso();
      documento.addEventListener("keydown", alPulsarTecla);
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
      boton.addEventListener("click", () => {
        if (estado.escuchando) detenerDictado();
        else iniciarDictado();
      });
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

    function prepararAviso() {
      if (!vista.aviso) return;
      if (vista.avisoAceptar) {
        vista.avisoAceptar.addEventListener("click", () => {
          preferencias.aceptarAviso();
          cerrarAviso();
          comenzarDictado();
        });
      }
      if (vista.avisoCancelar) {
        vista.avisoCancelar.addEventListener("click", () => {
          cerrarAviso();
          avisar(MENSAJES.cancelado);
        });
      }
      vista.aviso.addEventListener("cancel", (evento) => {
        if (evento && evento.preventDefault) evento.preventDefault();
        cerrarAviso();
        avisar(MENSAJES.cancelado);
      });
    }

    function cerrarAviso() {
      if (!vista.aviso || !vista.aviso.open) return;
      if (typeof vista.aviso.close === "function") vista.aviso.close();
      else vista.aviso.open = false;
    }

    function iniciarDictado() {
      if (!soporte.dictado) {
        avisar(segura ? MENSAJES.sinSoporte : MENSAJES.sinContexto);
        return;
      }
      if (preferencias.avisoAceptado) {
        comenzarDictado();
        return;
      }
      if (vista.aviso) {
        if (typeof vista.aviso.showModal === "function") vista.aviso.showModal();
        else vista.aviso.open = true;
        if (vista.avisoAceptar && typeof vista.avisoAceptar.focus === "function") {
          vista.avisoAceptar.focus();
        }
      }
    }

    function comenzarDictado() {
      if (!soporte.dictado) return;
      estado.ciclo += 1;
      const ciclo = estado.ciclo;
      estado.transcripcion = "";
      try {
        reconocimiento = new Reconocimiento();
      } catch {
        fallar(ciclo, { error: "audio-capture" });
        return;
      }
      reconocimiento.lang = "es-CO";
      reconocimiento.continuous = false;
      reconocimiento.interimResults = true;
      reconocimiento.maxAlternatives = 1;
      reconocimiento.onresult = (evento) => acumular(ciclo, evento);
      reconocimiento.onerror = (evento) => fallar(ciclo, evento);
      reconocimiento.onend = () => finalizar(ciclo);
      marcarEscuchando(true);
      avisar(MENSAJES.escuchando);
      temporizador = reloj.setTimeout(() => {
        if (estado.ciclo !== ciclo || !reconocimiento) return;
        avisar(MENSAJES.limite);
        reconocimiento.stop();
      }, MAX_GRABACION_MS);
      try {
        reconocimiento.start();
      } catch {
        fallar(ciclo, { error: "audio-capture" });
      }
    }

    function acumular(ciclo, evento) {
      if (ciclo !== estado.ciclo) return;
      const resultados = evento && evento.results ? evento.results : [];
      const inicio = Number(evento && evento.resultIndex) || 0;
      for (let indice = inicio; indice < resultados.length; indice += 1) {
        const resultado = resultados[indice];
        if (!resultado || !resultado.isFinal) continue;
        const alternativa = resultado[0];
        const texto = alternativa && alternativa.transcript ? alternativa.transcript : "";
        if (texto) estado.transcripcion += `${estado.transcripcion ? " " : ""}${texto.trim()}`;
      }
    }

    function fallar(ciclo, evento) {
      if (ciclo !== estado.ciclo) return;
      const codigo = evento && evento.error ? String(evento.error) : "";
      if (codigo === "aborted") return;
      let mensaje = MENSAJES.error;
      if (codigo === "not-allowed" || codigo === "service-not-allowed") {
        mensaje = MENSAJES.permiso;
        mostrarNota(MENSAJES.permiso);
      } else if (codigo === "no-speech") {
        mensaje = MENSAJES.silencio;
      } else if (codigo === "audio-capture") {
        mensaje = MENSAJES.sinMicrofono;
        mostrarNota(MENSAJES.sinMicrofono);
      }
      finalizarCiclo(mensaje);
    }

    function finalizar(ciclo) {
      if (ciclo !== estado.ciclo || !reconocimiento) return;
      const texto = estado.transcripcion.trim();
      if (!texto) {
        finalizarCiclo(MENSAJES.silencio);
        return;
      }
      const resultado = insertarBorrador(normalizarCantidades(texto));
      finalizarCiclo(resultado.recortado ? MENSAJES.recortado : MENSAJES.listo);
    }

    function finalizarCiclo(mensaje) {
      if (temporizador !== null) {
        reloj.clearTimeout(temporizador);
        temporizador = null;
      }
      reconocimiento = null;
      estado.ciclo += 1;
      estado.transcripcion = "";
      marcarEscuchando(false);
      avisar(mensaje);
    }

    function detenerDictado() {
      if (!estado.escuchando || !reconocimiento) return;
      avisar(MENSAJES.deteniendo);
      reconocimiento.stop();
    }

    function cancelarDictado() {
      if (!estado.escuchando) return;
      const anterior = reconocimiento;
      estado.ciclo += 1;
      reconocimiento = null;
      if (temporizador !== null) {
        reloj.clearTimeout(temporizador);
        temporizador = null;
      }
      estado.transcripcion = "";
      marcarEscuchando(false);
      avisar(MENSAJES.cancelado);
      if (anterior && typeof anterior.abort === "function") anterior.abort();
    }

    function alPulsarTecla(evento) {
      if (!evento || evento.key !== "Escape") return;
      if (estado.escuchando) {
        if (evento.preventDefault) evento.preventDefault();
        cancelarDictado();
      }
    }

    function marcarEscuchando(activo) {
      estado.escuchando = Boolean(activo);
      const boton = vista.botonDictar;
      if (boton) {
        boton.setAttribute("aria-pressed", activo ? "true" : "false");
        boton.setAttribute("aria-label", activo ? "Detener el dictado" : "Dictar una pregunta");
      }
      if (vista.etiquetaDictar) {
        vista.etiquetaDictar.textContent = activo ? "Detener dictado" : "Dictar";
      }
      if (documento.documentElement) {
        if (activo) documento.documentElement.dataset.vozEscuchando = "1";
        else delete documento.documentElement.dataset.vozEscuchando;
      }
    }

    function insertarBorrador(texto) {
      const entrada = vista.entrada;
      if (!entrada || !texto) return { insertado: false, recortado: false };
      const actual = String(entrada.value || "");
      const inicio = Number.isInteger(entrada.selectionStart)
        ? Math.min(entrada.selectionStart, actual.length)
        : actual.length;
      const fin = Number.isInteger(entrada.selectionEnd)
        ? Math.min(Math.max(entrada.selectionEnd, inicio), actual.length)
        : inicio;
      const disponible = Math.max(0, MAX_BORRADOR - (actual.length - (fin - inicio)));
      const recortado = texto.length > disponible;
      const fragmento = recortarTexto(texto, disponible);
      if (!fragmento) return { insertado: false, recortado: true };
      const nuevo = actual.slice(0, inicio) + fragmento + actual.slice(fin);
      entrada.value = nuevo;
      if (typeof entrada.setSelectionRange === "function") {
        const cursor = inicio + fragmento.length;
        entrada.setSelectionRange(cursor, cursor);
      }
      if (typeof entrada.focus === "function") entrada.focus();
      return { insertado: true, recortado };
    }

    function avisar(mensaje) {
      estado.mensaje = mensaje || "";
      if (vista.estado) vista.estado.textContent = estado.mensaje;
    }

    function mostrarNota(mensaje) {
      if (!vista.nota) return;
      vista.nota.hidden = false;
      vista.nota.textContent = mensaje;
    }

    return {
      mount,
      soporte,
      preferencias,
      estado,
      iniciarDictado,
      detenerDictado,
      cancelarDictado,
      insertarBorrador,
      destruir() {
        documento.removeEventListener("keydown", alPulsarTecla);
      },
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
        entrada: document.getElementById("pregunta"),
        formulario: document.querySelector("form[data-chat-stream]"),
        aviso: document.querySelector("[data-voz-aviso]"),
        avisoAceptar: document.querySelector("[data-voz-aviso-aceptar]"),
        avisoCancelar: document.querySelector("[data-voz-aviso-cancelar]"),
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
    normalizarCantidades,
    recortarTexto,
  };
  // El script puede cargarse en un runner o en SSR sin `window`; nunca debe
  // romper la carga por publicar la API o inicializar.
  const global = typeof window !== "undefined" ? window : globalThis;
  global.CarvajalVoz = api;

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})();
