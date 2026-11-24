// Chat por voz (spec 040): dictado y lectura con las APIs del navegador.
//
// Con la voz del servidor (spec 047, `data-voz-servidor="1"`), el dictado graba con
// MediaRecorder y sube el audio a /api/voz/transcribir, y la lectura pide a
// /api/voz/hablar el audio de la respuesta guardada (por su id, nunca por el texto
// de la página). Ante cualquier error, 503 o 429 el controlador vuelve a la voz del
// navegador del resto de la sesión. Sin voz del servidor el audio nunca sale del
// navegador hacia el servidor del proyecto. La detección es por capacidad, no por
// marca ni versión.
(() => {
  "use strict";

  const MAX_BORRADOR = 500;
  const MAX_GRABACION_MS = 60000;
  const SILENCIO_MANOS_LIBRES_MS = 10000;
  // Voz del servidor (spec 047): fin del dictado tras una pausa y sin voz al empezar.
  const PAUSA_FIN_DICTADO_MS = 1500;
  const SIN_VOZ_DICTADO_MS = 8000;
  const UMBRAL_VOZ = 0.02;
  const BITS_GRABACION = 32000;
  // Plazo de cada petición a /api/voz/* si la página no trae el del servidor: sin él, una
  // red colgada dejaría «Transcribiendo…» para siempre.
  const PLAZO_SERVIDOR_MS = 35000;
  const CLAVES = {
    aviso: "carvajal_voz_aviso_dictado",
    // El aviso de la voz del servidor dice otra cosa (el audio pasa por el sitio): se
    // acepta aparte, aunque ya se hubiera aceptado el del navegador.
    avisoServidor: "carvajal_voz_aviso_dictado_servidor",
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
    sinVoz: "No hay una voz en español instalada en este navegador. Puedes leer la respuesta en pantalla.",
    leyendo: "Leyendo la respuesta…",
    pausa: "Lectura en pausa.",
    lecturaError: "No se pudo leer la respuesta. Inténtalo de nuevo.",
    borradorNoVacio: "Para activar manos libres, vacía el borrador. Tu texto no se ha borrado.",
    manosListo: "Manos libres activo. Habla cuando quieras; lo que dictes se envía sin revisar.",
    manosEnviado: "Enviado. Esperando la respuesta antes de volver a escuchar.",
    manosCancelado: "Manos libres no se activó.",
    manosApagado: "Manos libres desactivado.",
    manosSilencio: "Manos libres se apagó tras 10 segundos sin voz.",
    manosTerminar: "Manos libres se apagó con «terminar».",
    manosEscritura: "Manos libres se apagó porque escribiste. Tu texto se conserva sin enviarse.",
    manosFallo: "Manos libres se apagó: no se pudo completar el envío. Revisa el borrador.",
    manosError: "Manos libres se apagó por un error del micrófono. Puedes escribir o dictar de nuevo.",
    manosOculto: "Manos libres se apagó al ocultar la pestaña.",
    manosLectura: "Manos libres se apagó al detener la lectura.",
    manosHilo: "Manos libres se apagó porque cambió el hilo visible.",
    transcribiendo: "Transcribiendo el dictado…",
    preparandoLectura: "Preparando la lectura…",
    servidorDictado:
      "La transcripción del servidor no respondió. Dicta de nuevo: se usará el reconocimiento de tu navegador.",
    servidorSinRespaldo: "No se pudo transcribir el dictado. Puedes escribir tu pregunta.",
    manosSinSoporte: "Manos libres necesita dictado, lectura y streaming disponibles.",
    manosOcupado: "Espera a que termine la respuesta en curso para activar manos libres.",
    manosLeyendo: "Espera a que termine la lectura para activar manos libres.",
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

  // --- Lectura: segmentos construidos desde el DOM renderizado --------------
  /** Quita URLs, SKU y códigos técnicos antes de pronunciar la prosa. */
  function limpiarTextoHablado(texto) {
    return texto
      .replace(/\bhttps?:\/\/\S+/gi, " ")
      .replace(/\bwww\.\S+/gi, " ")
      .replace(/\bsku\b\s*:?/gi, " ")
      .replace(/\b(?=[A-Za-z0-9-]*\d)[A-Za-z]{2,}(?:-[A-Za-z0-9]+)+\b/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function textoVisible(nodo) {
    if (!nodo || typeof nodo.textContent !== "string") return "";
    return nodo.textContent
      .replace(/\[\d+\]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function listaDe(raiz, selector) {
    if (!raiz || typeof raiz.querySelectorAll !== "function") return [];
    return Array.from(raiz.querySelectorAll(selector) || []);
  }

  function campoDe(raiz, selector) {
    if (!raiz || typeof raiz.querySelector !== "function") return "";
    return textoVisible(raiz.querySelector(selector));
  }

  /**
   * Resumen hablado del turno (spec 048): lo prepara el servidor con la misma función
   * que /api/voz/hablar y llega en `data-voz-resumen` (hilo) o en el evento `done`.
   * Un párrafo por locución; vacío si el turno no lo trae.
   */
  function segmentosDelResumen(turno) {
    if (!turno || !turno.dataset) return [];
    const resumen = String(turno.dataset.vozResumen || "");
    return resumen
      .split(/\n\s*\n/)
      .map((parrafo) => limpiarTextoHablado(parrafo))
      .filter(Boolean);
  }

  /**
   * Prefiere el resumen hablado del servidor; sin él, las tarjetas se leen del DOM y las
   * fuentes de los metadatos del turno (comportamiento anterior).
   */
  function construirSegmentos(turno) {
    if (!turno) return [];
    const resumen = segmentosDelResumen(turno);
    if (resumen.length) return resumen;
    const segmentos = [];
    const texto = limpiarTextoHablado(textoVisible(turno.querySelector(".texto")));
    if (texto) segmentos.push(texto);
    for (const tarjeta of listaDe(turno, ".productos .producto")) {
      const nombre = campoDe(tarjeta, '[data-campo="producto-nombre"]');
      if (!nombre) continue;
      const cajas = campoDe(tarjeta, '[data-campo="producto-cajas"]');
      const base = [`${nombre}.`];
      if (cajas) base.push(`Cajas: ${cajas}.`);
      const ofertas = listaDe(tarjeta, ".producto-oferta");
      if (ofertas.length === 0) {
        const precio = campoDe(tarjeta, '[data-campo="producto-precio"]');
        const detalle = campoDe(tarjeta, ".producto-precio-detalle");
        if (precio) base.push(`Precio: ${precio}${detalle ? `, ${detalle}` : ""}.`);
        const fecha = campoDe(tarjeta, '[data-campo="producto-fecha"]');
        if (fecha) base.push(`${fecha}.`);
        segmentos.push(base.join(" "));
        continue;
      }
      // Una tarjeta puede publicar varias ofertas: se lee cada precio visible
      // con su calificador y su fecha, sin inventar ni promediar.
      for (const oferta of ofertas) {
        const partes = base.slice();
        const precio = campoDe(oferta, '[data-campo="producto-precio"]');
        const detalle = campoDe(oferta, ".producto-precio-detalle");
        if (precio) partes.push(`Precio: ${precio}${detalle ? `, ${detalle}` : ""}.`);
        const fecha = campoDe(oferta, '[data-campo="producto-fecha"]');
        if (fecha) partes.push(`${fecha}.`);
        segmentos.push(partes.join(" "));
      }
    }
    const titulos = titulosDeFuentes(turno);
    for (const titulo of titulos) {
      const hablado = limpiarTextoHablado(String(titulo || ""));
      if (hablado) segmentos.push(`Según ${hablado}.`);
    }
    return segmentos;
  }

  /** Títulos de las fuentes del turno; un atributo roto no impide la lectura. */
  function titulosDeFuentes(turno) {
    if (!turno || !turno.dataset) return [];
    try {
      const titulos = JSON.parse(turno.dataset.vozFuentes || "[]");
      return Array.isArray(titulos) ? titulos : [];
    } catch {
      return [];
    }
  }

  /** Prefiere es-CO, luego es-MX y luego cualquier voz en español. */
  function elegirVoz(voces) {
    if (!Array.isArray(voces)) return null;
    const espanolas = voces.filter(
      (voz) => voz && typeof voz.lang === "string" && voz.lang.toLowerCase().startsWith("es"),
    );
    return (
      espanolas.find((voz) => voz.lang.toLowerCase() === "es-co") ||
      espanolas.find((voz) => voz.lang.toLowerCase() === "es-mx") ||
      espanolas[0] ||
      null
    );
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

  function crearPreferencias(almacen, claveAviso = CLAVES.aviso) {
    let aviso = almacen.get(claveAviso) === "1";
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
        almacen.set(claveAviso, "1");
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
    const Reconocimiento = opciones.reconocimiento || null;
    const sintesis = opciones.sintesis || null;
    const Utterance = opciones.Utterance || null;
    // Los timers del navegador exigen `this === window`: se envuelven para no
    // invocarlos como método de otro objeto (TypeError: Illegal invocation).
    const reloj = opciones.reloj || {
      setTimeout: (funcion, ms) => window.setTimeout(funcion, ms),
      clearTimeout: (id) => window.clearTimeout(id),
      now: () => window.performance.now(),
    };
    const segura = Boolean(opciones.segura);
    // Voz del servidor (spec 047): opcional; cada capacidad se apaga sola al primer fallo
    // y el resto de la sesión sigue con la voz del navegador.
    const servidor = opciones.servidor || null;
    const servidorActivo = {
      dictado: Boolean(servidor && servidor.dictado) && segura,
      lectura: Boolean(servidor && servidor.lectura),
    };
    const navegador = {
      dictado: Boolean(Reconocimiento) && segura,
      sintesis: Boolean(sintesis && Utterance),
    };
    const preferencias = crearPreferencias(
      almacen,
      servidorActivo.dictado ? CLAVES.avisoServidor : CLAVES.aviso,
    );

    const soporte = { streaming: Boolean(opciones.streaming) };
    function calcularSoporte() {
      soporte.dictado = navegador.dictado || servidorActivo.dictado;
      soporte.sintesis = navegador.sintesis || servidorActivo.lectura;
      soporte.manosLibres = soporte.dictado && soporte.sintesis && soporte.streaming;
      soporte.servidor = { dictado: servidorActivo.dictado, lectura: servidorActivo.lectura };
    }
    calcularSoporte();
    const estado = { ciclo: 0, escuchando: false, leyendo: false, mensaje: "", transcripcion: "" };
    let reconocimiento = null;
    let temporizador = null;
    let montado = false;
    let turnoLectura = null;
    let segmentosLectura = [];
    let indiceLectura = 0;
    let cicloLectura = 0;
    const manosLibres = { activo: false, reanudarTrasLeer: false };
    let cicloManosLibres = false;
    let temporizadorSilencio = null;
    let opcionesPendientes = null;
    let grabacion = null;
    let grabacionActiva = false;
    let subiendo = false;
    // AbortController de la transcripción en curso: cancelar el dictado corta la subida.
    let subida = null;
    let reproduccion = null;

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
      prepararManosLibres();
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
      if (casilla) {
        const campo = vista.autoLecturaCampo || casilla;
        if (!soporte.sintesis) {
          campo.hidden = true;
        } else {
          campo.hidden = false;
          casilla.checked = preferencias.autoLectura;
          casilla.addEventListener("change", () => {
            preferencias.fijarAutoLectura(Boolean(casilla.checked));
          });
        }
      }
      documento.addEventListener("chat:turn-complete", alCompletarTurno);
      documento.addEventListener("visibilitychange", alCambiarVisibilidad);
      documento.addEventListener("click", alClicTurno);
    }

    function prepararAviso() {
      if (!vista.aviso) return;
      if (vista.avisoAceptar) {
        vista.avisoAceptar.addEventListener("click", () => {
          preferencias.aceptarAviso();
          cerrarAviso();
          const pendientes = opcionesPendientes || {};
          opcionesPendientes = null;
          comenzarDictado(pendientes);
        });
      }
      if (vista.avisoCancelar) {
        vista.avisoCancelar.addEventListener("click", () => {
          cerrarAviso();
          avisar(MENSAJES.cancelado);
          // Si se canceló el aviso del micrófono, el modo no puede quedar
          // «activo» sin escucha ni temporizador.
          if (manosLibres.activo && !estado.escuchando) desactivarManosLibres("manual");
        });
      }
      vista.aviso.addEventListener("cancel", (evento) => {
        if (evento && evento.preventDefault) evento.preventDefault();
        cerrarAviso();
        avisar(MENSAJES.cancelado);
        if (manosLibres.activo && !estado.escuchando) desactivarManosLibres("manual");
      });
    }

    function cerrarAviso() {
      if (!vista.aviso || !vista.aviso.open) return;
      if (typeof vista.aviso.close === "function") vista.aviso.close();
      else vista.aviso.open = false;
    }

    function iniciarDictado(opciones = {}) {
      if (!soporte.dictado) {
        avisar(segura ? MENSAJES.sinSoporte : MENSAJES.sinContexto);
        return;
      }
      if (preferencias.avisoAceptado) {
        comenzarDictado(opciones);
        return;
      }
      opcionesPendientes = opciones;
      if (vista.aviso) {
        if (typeof vista.aviso.showModal === "function") vista.aviso.showModal();
        else vista.aviso.open = true;
        if (vista.avisoAceptar && typeof vista.avisoAceptar.focus === "function") {
          vista.avisoAceptar.focus();
        }
      }
    }

    function comenzarDictado(opciones = {}) {
      if (!soporte.dictado) return;
      detenerLectura();
      // Un ciclo anterior nunca convive con el nuevo: se aborta y se limpia
      // su temporizador para no dejar dos micrófonos ni callbacks vivos.
      if (estado.escuchando) {
        const anterior = reconocimiento;
        estado.ciclo += 1;
        if (temporizador !== null) {
          reloj.clearTimeout(temporizador);
          temporizador = null;
        }
        limpiarSilencio();
        soltarGrabacion();
        if (anterior && typeof anterior.abort === "function") anterior.abort();
      }
      if (servidorActivo.dictado) {
        comenzarGrabacion(opciones);
        return;
      }
      if (!navegador.dictado) return;
      cicloManosLibres = Boolean(opciones.manosLibres) && manosLibres.activo;
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
      avisar(
        cicloManosLibres ? `Manos libres · ${MENSAJES.escuchando}` : MENSAJES.escuchando,
      );
      temporizador = reloj.setTimeout(() => {
        if (estado.ciclo !== ciclo || !reconocimiento) return;
        avisar(MENSAJES.limite);
        try {
          reconocimiento.stop();
        } catch {
          finalizarCiclo(MENSAJES.error);
        }
      }, MAX_GRABACION_MS);
      if (cicloManosLibres) reiniciarSilencio(ciclo);
      try {
        reconocimiento.start();
      } catch {
        fallar(ciclo, { error: "audio-capture" });
      }
    }

    // --- Dictado con la voz del servidor (spec 047) ---------------------------

    function soltarGrabacion() {
      const anterior = grabacion;
      const envio = subida;
      grabacion = null;
      grabacionActiva = false;
      subiendo = false;
      subida = null;
      if (anterior && typeof anterior.cancelar === "function") anterior.cancelar();
      // Una transcripción descartada no debe seguir subiendo ni gastando cuota.
      if (envio) envio.abort();
    }

    function crearAbortador() {
      const Abortador = typeof AbortController === "function" ? AbortController : null;
      return Abortador ? new Abortador() : null;
    }

    // Sin detector de voz que funcione (sin Web Audio, o con el contexto suspendido),
    // nadie avisa de que la persona habla: el silencio de manos libres no se puede medir.
    function detectorOperativo() {
      return Boolean(
        grabacion && typeof grabacion.detectorActivo === "function" && grabacion.detectorActivo(),
      );
    }

    function comenzarGrabacion(opciones = {}) {
      cicloManosLibres = Boolean(opciones.manosLibres) && manosLibres.activo;
      estado.ciclo += 1;
      const ciclo = estado.ciclo;
      estado.transcripcion = "";
      reconocimiento = null;
      grabacionActiva = true;
      marcarEscuchando(true);
      avisar(
        cicloManosLibres ? `Manos libres · ${MENSAJES.escuchando}` : MENSAJES.escuchando,
      );
      const maximo = Number(servidor.maxMs) > 0 ? Number(servidor.maxMs) : MAX_GRABACION_MS;
      temporizador = reloj.setTimeout(() => {
        if (estado.ciclo !== ciclo || !grabacionActiva) return;
        avisar(MENSAJES.limite);
        terminarGrabacion(ciclo);
      }, maximo);
      if (cicloManosLibres) reiniciarSilencio(ciclo);
      let pendiente;
      try {
        pendiente = Promise.resolve(
          servidor.grabar({
            alVoz: () => {
              if (estado.ciclo === ciclo && cicloManosLibres) reiniciarSilencio(ciclo);
            },
            alPausa: () => {
              if (estado.ciclo === ciclo && grabacion) terminarGrabacion(ciclo);
            },
            alSinVoz: () => {
              // En manos libres manda su propio temporizador de 10 s.
              if (estado.ciclo !== ciclo || cicloManosLibres || !grabacion) return;
              soltarGrabacion();
              finalizarCiclo(MENSAJES.silencio);
            },
          }),
        );
      } catch (error) {
        pendiente = Promise.reject(error);
      }
      pendiente.then(
        (manejador) => {
          if (estado.ciclo !== ciclo || !grabacionActiva) {
            if (manejador && typeof manejador.cancelar === "function") manejador.cancelar();
            return;
          }
          grabacion = manejador;
        },
        (error) => fallarServidorDictado(ciclo, error),
      );
    }

    function terminarGrabacion(ciclo) {
      if (ciclo !== estado.ciclo || !grabacionActiva || subiendo) return;
      const manejador = grabacion;
      if (!manejador) {
        // El micrófono todavía no abrió: no hay nada que transcribir.
        cancelarDictado();
        return;
      }
      grabacion = null;
      subiendo = true;
      if (temporizador !== null) {
        reloj.clearTimeout(temporizador);
        temporizador = null;
      }
      // En manos libres el temporizador de silencio no corre mientras se transcribe.
      limpiarSilencio();
      avisar(MENSAJES.transcribiendo);
      const control = crearAbortador();
      subida = control;
      Promise.resolve(manejador.detener())
        .then((resultado) => {
          if (ciclo !== estado.ciclo) return null;
          const audio = resultado && resultado.audio ? resultado.audio : null;
          // Con detector de voz y sin voz oída no se sube nada: ahorra la llamada.
          if (!audio || audio.size === 0 || (resultado && resultado.voz === false)) return "";
          return servidor.transcribir(audio, { signal: control ? control.signal : undefined });
        })
        .then((texto) => {
          if (ciclo !== estado.ciclo || texto === null) return;
          estado.transcripcion = String(texto || "");
          finalizar(ciclo);
        })
        .catch((error) => fallarServidorDictado(ciclo, error));
    }

    function esSinVoz(error) {
      return Boolean(error && error.status === 422);
    }

    function fallarServidorDictado(ciclo, error) {
      if (ciclo !== estado.ciclo) return;
      const nombre = error && error.name ? String(error.name) : "";
      if (nombre === "NotAllowedError" || nombre === "SecurityError") {
        soltarGrabacion();
        fallar(ciclo, { error: "not-allowed" });
        return;
      }
      if (nombre === "NotFoundError" || nombre === "OverconstrainedError") {
        soltarGrabacion();
        fallar(ciclo, { error: "audio-capture" });
        return;
      }
      if (esSinVoz(error)) {
        estado.transcripcion = "";
        finalizar(ciclo);
        return;
      }
      // 503, 429, red, CSRF…: el resto de la sesión dicta con el navegador.
      servidorActivo.dictado = false;
      calcularSoporte();
      const eraManos = cicloManosLibres;
      cicloManosLibres = false;
      limpiarSilencio();
      soltarGrabacion();
      cerrarCiclo();
      actualizarBotonDictar();
      if (eraManos) {
        if (navegador.dictado && manosLibres.activo) {
          // El aviso (el audio puede ir a otro proveedor) queda en la nota visible y
          // delante del «Escuchando…», que si no lo pisaría antes de anunciarse.
          mostrarNota(MENSAJES.servidorDictado);
          comenzarDictado({ manosLibres: true });
          avisar(`${MENSAJES.servidorDictado} ${estado.mensaje}`.trim());
        } else {
          desactivarManosLibres("error");
        }
        return;
      }
      const mensaje = navegador.dictado ? MENSAJES.servidorDictado : MENSAJES.servidorSinRespaldo;
      avisar(mensaje);
      mostrarNota(mensaje);
    }

    function actualizarBotonDictar() {
      const boton = vista.botonDictar;
      if (boton) boton.hidden = !soporte.dictado;
      if (vista.botonManosLibres && !manosLibres.activo) {
        vista.botonManosLibres.hidden = !soporte.manosLibres;
      }
    }

    function acumular(ciclo, evento) {
      if (ciclo !== estado.ciclo) return;
      if (cicloManosLibres) reiniciarSilencio(ciclo);
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
      if (codigo === "aborted") {
        // Un abort externo no debe dejar vivo el temporizador de 60 s.
        if (temporizador !== null) {
          reloj.clearTimeout(temporizador);
          temporizador = null;
        }
        return;
      }
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
      const eraManos = cicloManosLibres;
      cicloManosLibres = false;
      limpiarSilencio();
      cerrarCiclo();
      if (eraManos) {
        desactivarManosLibres("error");
        return;
      }
      avisar(mensaje);
    }

    function finalizar(ciclo) {
      if (ciclo !== estado.ciclo || (!reconocimiento && !grabacionActiva)) return;
      const texto = normalizarCantidades(estado.transcripcion.trim());
      const eraManos = cicloManosLibres;
      cicloManosLibres = false;
      limpiarSilencio();
      if (eraManos) {
        cerrarCiclo();
        if (!texto) {
          desactivarManosLibres("silencio");
          return;
        }
        if (/^terminar[.!]?$/i.test(texto)) {
          desactivarManosLibres("terminar");
          return;
        }
        if (!manosLibres.activo) return;
        if (vista.entrada && String(vista.entrada.value || "").trim() !== "") {
          desactivarManosLibres("escritura");
          return;
        }
        const insertado = insertarBorrador(texto);
        if (insertado.recortado) mostrarNota(MENSAJES.recortado);
        enviarPorFormulario();
        avisar(MENSAJES.manosEnviado);
        return;
      }
      if (!texto) {
        finalizarCiclo(MENSAJES.silencio);
        return;
      }
      const resultado = insertarBorrador(texto);
      finalizarCiclo(resultado.recortado ? MENSAJES.recortado : MENSAJES.listo);
    }

    function cerrarCiclo() {
      if (temporizador !== null) {
        reloj.clearTimeout(temporizador);
        temporizador = null;
      }
      reconocimiento = null;
      grabacion = null;
      grabacionActiva = false;
      subiendo = false;
      subida = null;
      estado.ciclo += 1;
      estado.transcripcion = "";
      marcarEscuchando(false);
    }

    function finalizarCiclo(mensaje) {
      cerrarCiclo();
      avisar(mensaje);
    }

    function detenerDictado() {
      if (!estado.escuchando) return;
      if (grabacionActiva) {
        // Durante la transcripción ya no hay grabación que detener: el clic la cancela
        // (aborta la subida) en lugar de no hacer nada, igual que Escape.
        if (subiendo) cancelarDictado();
        else terminarGrabacion(estado.ciclo);
        return;
      }
      if (!reconocimiento) return;
      avisar(MENSAJES.deteniendo);
      try {
        reconocimiento.stop();
      } catch {
        finalizarCiclo(MENSAJES.error);
      }
    }

    function cancelarDictado() {
      if (!estado.escuchando) return;
      cicloManosLibres = false;
      limpiarSilencio();
      const anterior = reconocimiento;
      estado.ciclo += 1;
      reconocimiento = null;
      soltarGrabacion();
      if (temporizador !== null) {
        reloj.clearTimeout(temporizador);
        temporizador = null;
      }
      estado.transcripcion = "";
      marcarEscuchando(false);
      avisar(MENSAJES.cancelado);
      if (anterior && typeof anterior.abort === "function") anterior.abort();
      if (manosLibres.activo) desactivarManosLibres("manual");
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

    function hayVozEspanola() {
      if (servidorActivo.lectura) return true;
      if (!sintesis) return false;
      const voces = typeof sintesis.getVoices === "function" ? sintesis.getVoices() : [];
      return Boolean(elegirVoz(voces));
    }

    function abrirAvisoManos() {
      if (!vista.avisoManos) {
        activarManosLibres();
        return;
      }
      if (typeof vista.avisoManos.showModal === "function") vista.avisoManos.showModal();
      else vista.avisoManos.open = true;
      if (vista.manosAceptar && typeof vista.manosAceptar.focus === "function") {
        vista.manosAceptar.focus();
      }
    }

    function cerrarAvisoManos() {
      if (!vista.avisoManos || !vista.avisoManos.open) return;
      if (typeof vista.avisoManos.close === "function") vista.avisoManos.close();
      else vista.avisoManos.open = false;
    }

    function prepararManosLibres() {
      const boton = vista.botonManosLibres;
      if (boton) {
        boton.hidden = !soporte.manosLibres;
        if (soporte.manosLibres) {
          boton.setAttribute("type", "button");
          boton.setAttribute("aria-pressed", "false");
          boton.setAttribute("aria-label", "Activar manos libres");
          boton.addEventListener("click", () => {
            if (manosLibres.activo) desactivarManosLibres("manual");
            else intentarActivarManosLibres();
          });
        }
      }
      if (vista.manosAceptar) {
        vista.manosAceptar.addEventListener("click", () => {
          cerrarAvisoManos();
          activarManosLibres();
        });
      }
      if (vista.manosCancelar) {
        vista.manosCancelar.addEventListener("click", () => {
          cerrarAvisoManos();
          avisar(MENSAJES.manosCancelado);
        });
      }
      if (vista.avisoManos) {
        vista.avisoManos.addEventListener("cancel", (evento) => {
          if (evento && evento.preventDefault) evento.preventDefault();
          cerrarAvisoManos();
          avisar(MENSAJES.manosCancelado);
        });
      }
      if (vista.entrada) {
        vista.entrada.addEventListener("input", () => {
          if (manosLibres.activo) desactivarManosLibres("escritura");
        });
      }
      documento.addEventListener("chat:turn-failed", alFallarTurno);
    }

    function intentarActivarManosLibres() {
      if (!soporte.manosLibres) {
        avisar(MENSAJES.manosSinSoporte);
        return;
      }
      if (!hayVozEspanola()) {
        avisar(MENSAJES.sinVoz);
        mostrarNota(MENSAJES.sinVoz);
        return;
      }
      if (turnoLectura) {
        avisar(MENSAJES.manosLeyendo);
        return;
      }
      if (vista.entrada && String(vista.entrada.value || "").trim() !== "") {
        avisar(MENSAJES.borradorNoVacio);
        mostrarNota(MENSAJES.borradorNoVacio);
        return;
      }
      if (
        vista.formulario &&
        typeof vista.formulario.getAttribute === "function" &&
        vista.formulario.getAttribute("aria-busy") === "true"
      ) {
        avisar(MENSAJES.manosOcupado);
        return;
      }
      abrirAvisoManos();
    }

    function activarManosLibres() {
      if (!soporte.manosLibres) return;
      manosLibres.activo = true;
      manosLibres.reanudarTrasLeer = false;
      actualizarBotonManos(true);
      if (documento.documentElement) documento.documentElement.dataset.vozManos = "1";
      avisar(MENSAJES.manosListo);
      iniciarDictado({ manosLibres: true });
    }

    function actualizarBotonManos(activo) {
      const boton = vista.botonManosLibres;
      if (!boton) return;
      boton.setAttribute("aria-pressed", activo ? "true" : "false");
      boton.setAttribute("aria-label", activo ? "Desactivar manos libres" : "Activar manos libres");
      boton.textContent = activo ? "Manos libres activo" : "Manos libres";
    }

    function desactivarManosLibres(motivo) {
      if (!manosLibres.activo) return;
      manosLibres.activo = false;
      manosLibres.reanudarTrasLeer = false;
      cicloManosLibres = false;
      limpiarSilencio();
      actualizarBotonManos(false);
      if (documento.documentElement) delete documento.documentElement.dataset.vozManos;
      if (estado.escuchando) cancelarDictado();
      detenerLectura();
      const mensajes = {
        manual: MENSAJES.manosApagado,
        silencio: MENSAJES.manosSilencio,
        terminar: MENSAJES.manosTerminar,
        escritura: MENSAJES.manosEscritura,
        error: MENSAJES.manosError,
        fallo: MENSAJES.manosFallo,
        oculto: MENSAJES.manosOculto,
        lectura: MENSAJES.manosLectura,
        hilo: MENSAJES.manosHilo,
      };
      avisar(mensajes[motivo] || MENSAJES.manosApagado);
    }

    function reiniciarSilencio(ciclo) {
      if (!manosLibres.activo) return;
      limpiarSilencio();
      temporizadorSilencio = reloj.setTimeout(() => {
        if (!manosLibres.activo || estado.ciclo !== ciclo) return;
        if (grabacionActiva && grabacion && !detectorOperativo()) {
          // Sin detector no se sabe si hubo voz: se transcribe lo grabado en lugar de
          // descartarlo. Si no había nada, la transcripción vacía apaga manos libres.
          temporizadorSilencio = null;
          terminarGrabacion(ciclo);
          return;
        }
        desactivarManosLibres("silencio");
      }, SILENCIO_MANOS_LIBRES_MS);
    }

    function limpiarSilencio() {
      if (temporizadorSilencio !== null) {
        reloj.clearTimeout(temporizadorSilencio);
        temporizadorSilencio = null;
      }
    }

    function enviarPorFormulario() {
      const formulario = vista.formulario;
      if (!formulario) return;
      formulario.dataset.vozOrigen = "manos-libres";
      if (typeof formulario.requestSubmit === "function") formulario.requestSubmit();
      else if (typeof formulario.submit === "function") formulario.submit();
    }

    function alFallarTurno(evento) {
      if (!manosLibres.activo) return;
      const detalle = (evento && evento.detail) || {};
      if (detalle.handsFree === true) desactivarManosLibres("fallo");
    }

    function alCompletarTurno(evento) {
      const detalle = (evento && evento.detail) || {};
      const turno = detalle.turno;
      if (!turno) return;
      if (typeof turno.className === "string" && turno.className.includes("turno-user")) return;
      const hiloActual = vista.hilo && vista.hilo.value ? String(vista.hilo.value) : "";
      const hiloTurno =
        detalle.threadId === undefined || detalle.threadId === null
          ? ""
          : String(detalle.threadId);
      if (manosLibres.activo) {
        if (detalle.handsFree !== true) {
          // Un turno ajeno deja el modo sin escucha ni temporizador: se apaga.
          desactivarManosLibres("fallo");
          return;
        }
        if (hiloActual && hiloTurno && hiloActual !== hiloTurno) {
          desactivarManosLibres("hilo");
          return;
        }
        const leido = leerTurno(turno, { automatico: true, manosLibres: true });
        manosLibres.reanudarTrasLeer = leido;
        if (!leido) {
          if (!hayVozEspanola()) desactivarManosLibres("error");
          else if (manosLibres.activo) comenzarDictado({ manosLibres: true });
        }
        return;
      }
      if (!preferencias.autoLectura) return;
      if (documento.visibilityState === "hidden") return;
      if (estado.escuchando) return;
      if (hiloActual && hiloTurno && hiloActual !== hiloTurno) return;
      if (turno.dataset && turno.dataset.vozAutoLeido === "1") return;
      if (turnoLectura === turno) return;
      // Se marca al arrancar la primera locución: si el navegador bloquea el
      // audio o faltan voces, el turno queda reintentable.
      leerTurno(turno, {
        automatico: true,
        alEmpezar: () => {
          if (turno.dataset) turno.dataset.vozAutoLeido = "1";
        },
      });
    }

    function alCambiarVisibilidad() {
      if (documento.visibilityState !== "hidden") return;
      if (manosLibres.activo) {
        desactivarManosLibres("oculto");
        return;
      }
      detenerLectura();
    }

    function alClicTurno(evento) {
      const objetivo = evento && evento.target;
      if (!objetivo || typeof objetivo.closest !== "function") return;
      const boton = objetivo.closest("[data-voz-accion]");
      if (!boton) return;
      const turno = boton.closest(".turno");
      if (!turno) return;
      const accion = boton.dataset ? boton.dataset.vozAccion : "";
      if (accion === "escuchar") leerTurno(turno);
      else if (accion === "pausar") pausarLectura();
      else if (accion === "reanudar") reanudarLectura();
      else if (accion === "detener") detenerLectura();
    }

    function leerTurno(turno, opciones = {}) {
      if (!turno || !soporte.sintesis) return false;
      if (opciones.automatico && !opciones.manosLibres && !preferencias.autoLectura) return false;
      const mensaje = turno.dataset ? String(turno.dataset.vozMensaje || "") : "";
      if (servidorActivo.lectura && mensaje) return leerConServidor(turno, mensaje, opciones);
      return leerConNavegador(turno, opciones);
    }

    function leerConServidor(turno, mensaje, opciones) {
      if (!opciones.automatico && turno.dataset) delete turno.dataset.vozAutoLeido;
      const alEmpezar = typeof opciones.alEmpezar === "function" ? opciones.alEmpezar : null;
      detenerLectura();
      turnoLectura = turno;
      segmentosLectura = [];
      indiceLectura = 0;
      cicloLectura += 1;
      const ciclo = cicloLectura;
      avisar(MENSAJES.preparandoLectura);
      actualizarControles(turno, "cargando");
      let pendiente;
      try {
        pendiente = Promise.resolve(servidor.sintetizar(mensaje));
      } catch (error) {
        pendiente = Promise.reject(error);
      }
      pendiente.then(
        (audio) => {
          if (ciclo !== cicloLectura) return;
          reproduccion = servidor.reproducir(audio, {
            alEmpezar: () => {
              if (ciclo !== cicloLectura) return;
              estado.leyendo = true;
              if (alEmpezar) alEmpezar();
              avisar(MENSAJES.leyendo);
              actualizarControles(turnoLectura, "leyendo");
            },
            alTerminar: () => {
              if (ciclo !== cicloLectura) return;
              reproduccion = null;
              terminarLectura();
            },
            alFallar: () => {
              if (ciclo !== cicloLectura) return;
              reproduccion = null;
              // El audio llegó pero no suena (autoplay bloqueado tras el await, CSP,
              // decodificación): repetir pagaría otra síntesis que fallaría igual. Se
              // apaga la lectura del servidor y este turno lo lee el navegador.
              servidorActivo.lectura = false;
              calcularSoporte();
              if (leerConRespaldo(turno, opciones)) return;
              const eraManos = manosLibres.activo;
              manosLibres.reanudarTrasLeer = false;
              terminarLectura();
              avisar(MENSAJES.lecturaError);
              if (eraManos) desactivarManosLibres("error");
            },
          });
        },
        (error) => {
          if (ciclo !== cicloLectura) return;
          // Un 404 o un 422 son de este turno (no existe, no tiene texto que leer o el
          // id no vale); cualquier otro fallo apaga la lectura del servidor el resto de
          // la sesión. Se lee con la voz del navegador.
          if (!esFalloDelTurno(error)) {
            servidorActivo.lectura = false;
            calcularSoporte();
          }
          if (leerConRespaldo(turno, opciones)) return;
          terminarLectura();
          avisar(MENSAJES.lecturaError);
        },
      );
      return true;
    }

    function esFalloDelTurno(error) {
      return Boolean(error && (error.status === 404 || error.status === 422));
    }

    // Deja el turno sin lectura en curso y lo intenta con la voz del navegador.
    function leerConRespaldo(turno, opciones) {
      cicloLectura += 1;
      turnoLectura = null;
      estado.leyendo = false;
      actualizarControles(turno, "detenido");
      return leerConNavegador(turno, opciones);
    }

    function leerConNavegador(turno, opciones = {}) {
      if (!turno || !navegador.sintesis) return false;
      const voces = typeof sintesis.getVoices === "function" ? sintesis.getVoices() : [];
      const voz = elegirVoz(voces);
      // Chrome llena la lista de voces de forma asíncrona: con la lista vacía
      // se intenta la locución con lang es-CO para no dar un falso negativo.
      if (!voz && Array.isArray(voces) && voces.length > 0) {
        avisar(MENSAJES.sinVoz);
        mostrarNota(MENSAJES.sinVoz);
        return false;
      }
      const segmentos = construirSegmentos(turno);
      if (segmentos.length === 0) return false;
      if (!opciones.automatico && turno.dataset) delete turno.dataset.vozAutoLeido;
      const alEmpezar = typeof opciones.alEmpezar === "function" ? opciones.alEmpezar : null;
      detenerLectura();
      turnoLectura = turno;
      segmentosLectura = segmentos;
      indiceLectura = 0;
      cicloLectura += 1;
      hablarSegmento(cicloLectura, voz, alEmpezar);
      return true;
    }

    function hablarSegmento(ciclo, voz, alEmpezar) {
      if (ciclo !== cicloLectura || !turnoLectura) return;
      const texto = segmentosLectura[indiceLectura];
      if (!texto) {
        terminarLectura();
        return;
      }
      const locucion = new Utterance(texto);
      locucion.lang = (voz && voz.lang) || "es-CO";
      locucion.voice = voz || null;
      locucion.onstart = () => {
        if (ciclo !== cicloLectura) return;
        estado.leyendo = true;
        if (indiceLectura === 0 && alEmpezar) alEmpezar();
        avisar(MENSAJES.leyendo);
        actualizarControles(turnoLectura, "leyendo");
      };
      locucion.onend = () => {
        if (ciclo !== cicloLectura) return;
        indiceLectura += 1;
        if (indiceLectura < segmentosLectura.length) hablarSegmento(ciclo, voz, alEmpezar);
        else terminarLectura();
      };
      locucion.onerror = () => {
        if (ciclo !== cicloLectura) return;
        const eraManos = manosLibres.activo;
        manosLibres.reanudarTrasLeer = false;
        terminarLectura();
        avisar(MENSAJES.lecturaError);
        if (eraManos) desactivarManosLibres("error");
      };
      sintesis.speak(locucion);
    }

    function pausarLectura() {
      if (!turnoLectura || !estado.leyendo) return;
      if (reproduccion) reproduccion.pausar();
      else if (sintesis && typeof sintesis.pause === "function") sintesis.pause();
      estado.leyendo = false;
      const turno = turnoLectura;
      actualizarControles(turno, "pausado");
      avisar(MENSAJES.pausa);
    }

    function reanudarLectura() {
      if (!turnoLectura) return;
      if (reproduccion) reproduccion.reanudar();
      else if (sintesis && typeof sintesis.resume === "function") sintesis.resume();
      estado.leyendo = true;
      actualizarControles(turnoLectura, "leyendo");
      avisar(MENSAJES.leyendo);
    }

    function detenerLectura() {
      if (!turnoLectura) return;
      manosLibres.reanudarTrasLeer = false;
      const manosActivas = manosLibres.activo;
      const turno = turnoLectura;
      cicloLectura += 1;
      turnoLectura = null;
      segmentosLectura = [];
      indiceLectura = 0;
      estado.leyendo = false;
      if (reproduccion) {
        const anterior = reproduccion;
        reproduccion = null;
        anterior.detener();
      } else if (sintesis && typeof sintesis.cancel === "function") {
        sintesis.cancel();
      }
      actualizarControles(turno, "detenido");
      avisar("");
      if (manosActivas) desactivarManosLibres("lectura");
    }

    function terminarLectura() {
      const turno = turnoLectura;
      cicloLectura += 1;
      turnoLectura = null;
      segmentosLectura = [];
      indiceLectura = 0;
      estado.leyendo = false;
      if (turno) actualizarControles(turno, "detenido");
      avisar("");
      const borradorVacio =
        !vista.entrada || String(vista.entrada.value || "").trim() === "";
      if (
        manosLibres.activo &&
        manosLibres.reanudarTrasLeer &&
        borradorVacio &&
        !estado.escuchando &&
        documento.visibilityState !== "hidden"
      ) {
        manosLibres.reanudarTrasLeer = false;
        comenzarDictado({ manosLibres: true });
      } else if (documento.visibilityState === "hidden") {
        manosLibres.reanudarTrasLeer = false;
      }
    }

    function actualizarControles(turno, modo) {
      if (!turno || typeof turno.querySelector !== "function") return;
      // El foco se decide antes de ocultar botones: si estaba dentro del turno,
      // pasa a uno visible en lugar de caer al body.
      const activo = documento.activeElement;
      const teniaFoco = Boolean(
        activo && typeof turno.contains === "function" && turno.contains(activo),
      );
      const buscar = (accion) => turno.querySelector(`[data-voz-accion="${accion}"]`);
      const escuchar = buscar("escuchar");
      if (escuchar && typeof escuchar.setAttribute === "function") {
        escuchar.setAttribute("aria-pressed", modo === "detenido" ? "false" : "true");
      }
      const pausar = buscar("pausar");
      // «cargando»: el audio del servidor aún no llega; solo se puede detener.
      if (pausar) pausar.hidden = modo !== "leyendo";
      const reanudar = buscar("reanudar");
      if (reanudar) reanudar.hidden = modo !== "pausado";
      const detener = buscar("detener");
      if (detener) detener.hidden = modo === "detenido";
      if (!teniaFoco) return;
      const destino = modo === "pausado" ? reanudar : modo === "detenido" ? escuchar : null;
      if (destino && typeof destino.focus === "function") destino.focus();
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
      leerTurno,
      pausarLectura,
      reanudarLectura,
      detenerLectura,
      manejarTurnoCompleto: alCompletarTurno,
      manosLibres,
      activarManosLibres,
      desactivarManosLibres,
      destruir() {
        detenerLectura();
        soltarGrabacion();
        documento.removeEventListener("keydown", alPulsarTecla);
        documento.removeEventListener("click", alClicTurno);
        documento.removeEventListener("chat:turn-complete", alCompletarTurno);
        documento.removeEventListener("chat:turn-failed", alFallarTurno);
        documento.removeEventListener("visibilitychange", alCambiarVisibilidad);
      },
    };
  }

  // --- Adaptador de la voz del servidor (spec 047) ----------------------------
  // Envuelve MediaRecorder, el detector de voz (Web Audio), fetch y <audio>. El
  // controlador solo ve promesas: grabar → {detener, cancelar}, transcribir(audio)
  // → texto, sintetizar(id) → audio y reproducir(audio) → {pausar, reanudar,
  // detener}. Los errores HTTP llevan `status` y `reason` para decidir el respaldo.

  function errorHttp(status, cuerpo) {
    const reason = cuerpo && typeof cuerpo.reason === "string" ? cuerpo.reason : "";
    const error = new Error(reason || `http_${status}`);
    error.status = status;
    error.reason = reason;
    return error;
  }

  function tipoDeGrabacion(MediaRecorder) {
    if (!MediaRecorder || typeof MediaRecorder.isTypeSupported !== "function") return "";
    const tipos = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"];
    return tipos.find((tipo) => MediaRecorder.isTypeSupported(tipo)) || "";
  }

  function extensionDe(tipo) {
    if (tipo.startsWith("audio/ogg")) return "ogg";
    if (tipo.startsWith("audio/mp4")) return "mp4";
    return "webm";
  }

  function crearServidorVoz(win, ajustes = {}) {
    const fetchFn = typeof win.fetch === "function" ? (...args) => win.fetch(...args) : null;
    const MediaRecorder = win.MediaRecorder || null;
    const dispositivos = win.navigator && win.navigator.mediaDevices;
    const AudioCtx = win.AudioContext || win.webkitAudioContext || null;
    const URLApi = win.URL || null;
    const AudioEl = win.Audio || null;
    const csrf = typeof ajustes.csrf === "function" ? ajustes.csrf : () => "";
    const ahora = () => (win.performance ? win.performance.now() : Date.now());

    async function grabar(llamadas = {}) {
      const flujo = await dispositivos.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
      const tipo = tipoDeGrabacion(MediaRecorder);
      const ajustesGrabadora = { audioBitsPerSecond: BITS_GRABACION };
      if (tipo) ajustesGrabadora.mimeType = tipo;
      let grabadora;
      try {
        grabadora = new MediaRecorder(flujo, ajustesGrabadora);
      } catch (error) {
        flujo.getTracks().forEach((pista) => pista.stop());
        throw error;
      }
      const partes = [];
      grabadora.ondataavailable = (evento) => {
        if (evento.data && evento.data.size) partes.push(evento.data);
      };
      try {
        grabadora.start(250);
      } catch (error) {
        // Sin grabación no se arranca nada más: se suelta el micrófono y se avisa.
        flujo.getTracks().forEach((pista) => pista.stop());
        throw error;
      }
      // Detector de voz por energía: sin Web Audio solo valen el clic y el límite. Se
      // arranca después de start() para no dejar un intervalo ni un contexto vivos si
      // la grabadora no llega a empezar.
      let contexto = null;
      let intervalo = null;
      let huboVoz = null;
      let detectorCorriendo = false;
      if (AudioCtx) {
        try {
          contexto = new AudioCtx();
          // Tras el await del micrófono el contexto puede nacer suspendido: se
          // reanuda y, mientras no corra, el detector no opina (voz desconocida).
          if (contexto.state === "suspended" && typeof contexto.resume === "function") {
            contexto.resume().catch(() => {});
          }
          const analizador = contexto.createAnalyser();
          analizador.fftSize = 1024;
          contexto.createMediaStreamSource(flujo).connect(analizador);
          const muestras = new Float32Array(analizador.fftSize);
          let inicio = null;
          let ultimaVoz = 0;
          intervalo = win.setInterval(() => {
            if (contexto.state !== "running") return;
            detectorCorriendo = true;
            if (inicio === null) {
              inicio = ahora();
              huboVoz = false;
            }
            analizador.getFloatTimeDomainData(muestras);
            let suma = 0;
            for (const muestra of muestras) suma += muestra * muestra;
            const nivel = Math.sqrt(suma / muestras.length);
            const momento = ahora();
            if (nivel > UMBRAL_VOZ) {
              huboVoz = true;
              ultimaVoz = momento;
              if (llamadas.alVoz) llamadas.alVoz();
            } else if (huboVoz && momento - ultimaVoz > PAUSA_FIN_DICTADO_MS) {
              if (llamadas.alPausa) llamadas.alPausa();
            } else if (!huboVoz && momento - inicio > SIN_VOZ_DICTADO_MS) {
              if (llamadas.alSinVoz) llamadas.alSinVoz();
            }
          }, 100);
        } catch {
          // Cada contexto abierto ocupa recursos de audio y el navegador limita cuántos
          // conviven: el que no sirve se cierra.
          if (intervalo !== null) win.clearInterval(intervalo);
          intervalo = null;
          if (contexto && typeof contexto.close === "function") {
            contexto.close().catch(() => {});
          }
          contexto = null;
          huboVoz = null;
        }
      }
      let liberado = false;
      function liberar() {
        if (liberado) return;
        liberado = true;
        if (intervalo !== null) win.clearInterval(intervalo);
        flujo.getTracks().forEach((pista) => pista.stop());
        if (contexto && typeof contexto.close === "function") {
          contexto.close().catch(() => {});
        }
      }
      return {
        // ¿El detector mide la voz? Falso sin Web Audio o con el contexto sin arrancar.
        detectorActivo: () => detectorCorriendo && contexto !== null && contexto.state === "running",
        detener() {
          return new Promise((resolver) => {
            const entregar = () => {
              liberar();
              const tipoFinal = grabadora.mimeType || tipo || "audio/webm";
              resolver({ audio: new win.Blob(partes, { type: tipoFinal }), voz: huboVoz });
            };
            if (grabadora.state === "inactive") {
              entregar();
              return;
            }
            grabadora.onstop = entregar;
            grabadora.stop();
          });
        },
        cancelar() {
          grabadora.onstop = null;
          try {
            if (grabadora.state !== "inactive") grabadora.stop();
          } catch {
            // ya estaba detenida
          }
          partes.length = 0;
          liberar();
        },
      };
    }

    const plazoMs = Number(ajustes.plazoMs) > 0 ? Number(ajustes.plazoMs) : PLAZO_SERVIDOR_MS;
    const Abortador = typeof win.AbortController === "function" ? win.AbortController : null;

    // Un AbortController por petición: lo aborta el plazo o la señal de quien llama
    // (cancelar el dictado). Un plazo vencido sale como TimeoutError, sin `status`.
    async function enviar(ruta, cuerpo, opciones = {}) {
      const externa = opciones.signal || null;
      const control = Abortador ? new Abortador() : null;
      let vencido = false;
      const abortar = () => {
        if (control) control.abort();
      };
      const plazo = control
        ? win.setTimeout(() => {
            vencido = true;
            abortar();
          }, plazoMs)
        : null;
      if (externa) {
        if (externa.aborted) abortar();
        else if (typeof externa.addEventListener === "function") {
          externa.addEventListener("abort", abortar, { once: true });
        }
      }
      let respuesta;
      try {
        respuesta = await fetchFn(ruta, {
          method: "POST",
          body: cuerpo,
          credentials: "same-origin",
          headers: { "X-CSRF-Token": csrf() },
          signal: control ? control.signal : undefined,
        });
      } catch (error) {
        if (vencido) {
          const plazoVencido = new Error("timeout");
          plazoVencido.name = "TimeoutError";
          throw plazoVencido;
        }
        throw error;
      } finally {
        if (plazo !== null) win.clearTimeout(plazo);
        if (externa && typeof externa.removeEventListener === "function") {
          externa.removeEventListener("abort", abortar);
        }
      }
      if (!respuesta.ok) {
        let detalle = {};
        try {
          detalle = await respuesta.json();
        } catch {
          detalle = {};
        }
        throw errorHttp(respuesta.status, detalle);
      }
      return respuesta;
    }

    async function transcribir(audio, opciones = {}) {
      const datos = new win.FormData();
      const tipo = String(audio.type || "audio/webm");
      // El CSRF va solo en la cabecera: el servidor lo comprueba antes de leer el cuerpo.
      datos.append("audio", audio, `dictado.${extensionDe(tipo)}`);
      const respuesta = await enviar("/api/voz/transcribir", datos, opciones);
      const cuerpo = await respuesta.json();
      return String((cuerpo && cuerpo.texto) || "");
    }

    async function sintetizar(mensaje) {
      const datos = new win.URLSearchParams();
      datos.append("message_id", mensaje);
      datos.append("csrf_token", csrf());
      const respuesta = await enviar("/api/voz/hablar", datos);
      return respuesta.blob();
    }

    function reproducir(audio, llamadas = {}) {
      const url = URLApi.createObjectURL(audio);
      const elemento = new AudioEl(url);
      let liberado = false;
      const liberar = () => {
        if (liberado) return;
        liberado = true;
        URLApi.revokeObjectURL(url);
      };
      elemento.onplaying = () => {
        if (llamadas.alEmpezar) llamadas.alEmpezar();
      };
      elemento.onended = () => {
        liberar();
        if (llamadas.alTerminar) llamadas.alTerminar();
      };
      elemento.onerror = () => {
        liberar();
        if (llamadas.alFallar) llamadas.alFallar();
      };
      const inicio = elemento.play();
      if (inicio && typeof inicio.catch === "function") {
        inicio.catch(() => {
          liberar();
          if (llamadas.alFallar) llamadas.alFallar();
        });
      }
      return {
        pausar() {
          elemento.pause();
        },
        reanudar() {
          const reanudacion = elemento.play();
          if (reanudacion && typeof reanudacion.catch === "function") reanudacion.catch(() => {});
        },
        detener() {
          elemento.onended = null;
          elemento.onerror = null;
          elemento.onplaying = null;
          elemento.pause();
          liberar();
        },
      };
    }

    return {
      dictado: Boolean(
        fetchFn &&
          MediaRecorder &&
          win.FormData &&
          win.Blob &&
          dispositivos &&
          typeof dispositivos.getUserMedia === "function",
      ),
      lectura: Boolean(
        fetchFn &&
          AudioEl &&
          win.URLSearchParams &&
          URLApi &&
          typeof URLApi.createObjectURL === "function",
      ),
      maxMs: Number(ajustes.maxMs) > 0 ? Number(ajustes.maxMs) : MAX_GRABACION_MS,
      grabar,
      transcribir,
      sintetizar,
      reproducir,
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
    const formulario = document.querySelector("form[data-chat-stream]");
    const servidor =
      raiz.dataset && raiz.dataset.vozServidor === "1"
        ? crearServidorVoz(win, {
            csrf: () => {
              const campo = formulario && formulario.querySelector('input[name="csrf_token"]');
              return campo ? String(campo.value || "") : "";
            },
            maxMs: (Number(raiz.dataset.vozMaxSegundos) || 60) * 1000,
            plazoMs: (Number(raiz.dataset.vozPlazoSegundos) || 0) * 1000,
          })
        : null;
    const controlador = createVoiceController({
      vista: {
        raiz,
        botonDictar: buscar("[data-voz-dictar]"),
        etiquetaDictar: buscar("[data-voz-dictar-texto]"),
        estado: buscar("[data-voz-estado]"),
        nota: buscar("[data-voz-nota]"),
        autoLectura: buscar("[data-voz-autolectura]"),
        autoLecturaCampo: buscar("[data-voz-autolectura-campo]"),
        hilo: document.querySelector("[data-thread-id]"),
        entrada: document.getElementById("pregunta"),
        formulario,
        aviso: document.querySelector("[data-voz-aviso]"),
        avisoAceptar: document.querySelector("[data-voz-aviso-aceptar]"),
        avisoCancelar: document.querySelector("[data-voz-aviso-cancelar]"),
        botonManosLibres: buscar("[data-voz-manos-libres]"),
        avisoManos: document.querySelector("[data-voz-manos-aviso]"),
        manosAceptar: document.querySelector("[data-voz-manos-aceptar]"),
        manosCancelar: document.querySelector("[data-voz-manos-cancelar]"),
      },
      documento: document,
      almacen,
      reconocimiento: Reconocimiento,
      sintesis,
      Utterance,
      servidor,
      segura: Boolean(win.isSecureContext),
      streaming:
        typeof win.fetch === "function" &&
        typeof win.ReadableStream === "function" &&
        typeof win.TextDecoder === "function",
    });
    controlador.mount();
    const opciones = buscar("[data-voz-opciones]");
    if (opciones) opciones.hidden = !controlador.soporte.sintesis;
    raiz._controladorVoz = controlador;
  }

  const api = {
    MAX_BORRADOR,
    MAX_GRABACION_MS,
    SILENCIO_MANOS_LIBRES_MS,
    MENSAJES,
    createVoiceController,
    crearServidorVoz,
    normalizarCantidades,
    recortarTexto,
    construirSegmentos,
    limpiarTextoHablado,
    elegirVoz,
  };
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
