// Chat por voz (spec 040): dictado y lectura con las APIs del navegador.
//
// Con la voz del servidor (spec 047, `data-voz-servidor="1"`), el dictado graba con
// MediaRecorder y sube el audio a /api/voz/transcribir, y la lectura pide a
// /api/voz/hablar el audio de la respuesta guardada (por su id, nunca por el texto
// de la página). Un fallo del dictado del servidor vuelve al reconocimiento del
// navegador el resto de la sesión. La lectura (spec 050) es una nota de voz solo en las
// respuestas a una pregunta hablada (borrador dictado o manos libres), y suena sola; una
// pregunta escrita no trae audio. Un fallo de reproducción o de un turno nunca cambia de
// voz ni apaga la del servidor;
// solo un 503 o un 401/403 la dan por no disponible, y se vuelve a probar a los 60 s.
// Sin voz del servidor el audio nunca sale del navegador hacia el servidor del
// proyecto. La detección es por capacidad, no por marca ni versión.
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
  // Notas de voz (spec 050): audios guardados en memoria (LRU) y espera antes de volver
  // a probar la voz del servidor tras un 503 o un 401/403.
  const MAX_NOTAS_EN_CACHE = 6;
  const REINTENTO_LECTURA_MS = 60000;
  const CLAVES = {
    aviso: "carvajal_voz_aviso_dictado",
    // El aviso de la voz del servidor dice otra cosa (el audio pasa por el sitio): se
    // acepta aparte, aunque ya se hubiera aceptado el del navegador.
    avisoServidor: "carvajal_voz_aviso_dictado_servidor",
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
    manosNota:
      "Manos libres se apagó: no se pudo reproducir la respuesta. Toca la nota para reintentarla.",
    manosOculto: "Manos libres se apagó al ocultar la pestaña.",
    manosLectura: "Manos libres se apagó al detener la lectura.",
    manosHilo: "Manos libres se apagó porque cambió el hilo visible.",
    transcribiendo: "Transcribiendo el dictado…",
    servidorDictado:
      "La transcripción del servidor no respondió. Dicta de nuevo: se usará el reconocimiento de tu navegador.",
    servidorSinRespaldo: "No se pudo transcribir el dictado. Puedes escribir tu pregunta.",
    manosSinSoporte: "Manos libres necesita dictado, lectura y streaming disponibles.",
    manosOcupado: "Espera a que termine la respuesta en curso para activar manos libres.",
    manosLeyendo: "Espera a que termine la lectura para activar manos libres.",
    manosBloqueo:
      "Manos libres se apagó: el navegador no dejó reproducir la respuesta. Toca reproducir para escucharla.",
    notaCargando: "Preparando la nota de voz…",
    notaBloqueada: "El navegador no dejó reproducirla sola. Toca reproducir para escucharla.",
    notaError: "No se pudo preparar la nota de voz. Inténtalo de nuevo.",
    notaOcupada: "Hay muchas lecturas seguidas. Inténtalo de nuevo en un momento.",
    vozNoDisponible: "La voz no está disponible ahora.",
  };

  // El botón principal de la nota es un botón de alternancia: su nombre no cambia (ARIA
  // APG) y el estado va en aria-pressed (y aria-busy al cargar) y en data-nota-estado.
  const ETIQUETA_NOTA = "Reproducir la nota de voz";
  // La región viva solo anuncia lo que el botón no dice: «sonando» y «pausada» ya los
  // da aria-pressed, y anunciarlos taparía el propio audio con el lector de pantalla.
  const ANUNCIOS_NOTA = {
    cargando: MENSAJES.notaCargando,
    bloqueada: MENSAJES.notaBloqueada,
    error: MENSAJES.notaError,
  };

  /** «m:ss» para el tiempo de la nota; cualquier valor raro cuenta como 0. */
  function formatoTiempo(segundos) {
    const total = Number.isFinite(segundos) && segundos > 0 ? Math.floor(segundos) : 0;
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

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
    return {
      get avisoAceptado() {
        return aviso;
      },
      aceptarAviso() {
        if (aviso) return;
        aviso = true;
        almacen.set(claveAviso, "1");
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
    // `pagehide` llega a window, no al documento: ahí se cortan los audios pendientes.
    const ventana = opciones.ventana || null;
    // Voz del servidor (spec 047): opcional. El dictado se apaga al primer fallo y el
    // resto de la sesión sigue con el navegador; la lectura (spec 050) solo se suspende
    // ante un 503 o un 401/403 y se vuelve a probar pasado REINTENTO_LECTURA_MS.
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
    // Lo que el servidor ofrecía al cargar: si después se apaga algo, la página está
    // usando la voz del navegador como respaldo (data-voz-respaldo).
    const servidorInicial = { dictado: servidorActivo.dictado, lectura: servidorActivo.lectura };

    // --- Estados de la voz en <html> (contrato con el rediseño, 049) ---------------
    // Solo atributos: el CSS los estila y este archivo no conoce clases de diseño.
    function marcarEstadoVoz(nombre, valor) {
      const raiz = documento && documento.documentElement;
      if (!raiz || !raiz.dataset) return;
      if (valor) raiz.dataset[nombre] = String(valor);
      else delete raiz.dataset[nombre];
    }

    // Lectura en curso (rediseño 052): la escena de voz muestra «Carva te responde».
    function fijarLeyendo(valor) {
      estado.leyendo = Boolean(valor);
      marcarEstadoVoz("vozLeyendo", estado.leyendo ? "1" : null);
    }

    function fijarProgresoVoz(valor) {
      const raiz = documento && documento.documentElement;
      const estilo = raiz && raiz.style;
      if (!estilo) return;
      if (valor === null) {
        if (typeof estilo.removeProperty === "function") estilo.removeProperty("--voz-progreso");
      } else if (typeof estilo.setProperty === "function") {
        estilo.setProperty("--voz-progreso", valor.toFixed(3));
      }
    }

    function calcularSoporte() {
      marcarEstadoVoz(
        "vozRespaldo",
        (servidorInicial.dictado && !servidorActivo.dictado) ||
          (servidorInicial.lectura && !servidorActivo.lectura)
          ? "1"
          : null,
      );
      soporte.dictado = navegador.dictado || servidorActivo.dictado;
      // La lectura del servidor solo se suspende un rato: la página la sigue ofreciendo.
      soporte.sintesis = navegador.sintesis || servidorInicial.lectura;
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
    let temporizadorProgreso = null;
    // Notas de voz (spec 050): una por respuesta guardada, el audio de cada mensaje en
    // un LRU pequeño y como mucho una síntesis en vuelo por mensaje.
    const notas = new Map();
    const audios = new Map();
    const pedidos = new Map();
    let lecturaReintentoEn = 0;
    // ¿El borrador actual trae texto dictado? ¿La pregunta enviada salió de la voz?
    // `palabrasDictadas` son las del texto que escribió el dictado en este borrador.
    let borradorDictado = false;
    let palabrasDictadas = new Set();
    let turnoHablado = false;

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
        else {
          // Gesto de la persona: deja listo el reproductor para la respuesta hablada.
          desbloquearAudio();
          iniciarDictado();
        }
      });
    }

    function prepararLectura() {
      documento.addEventListener("chat:turn-complete", alCompletarTurno);
      documento.addEventListener("visibilitychange", alCambiarVisibilidad);
      documento.addEventListener("click", alClicTurno);
      // En captura: corre antes que el envío del stream, que marca el formulario ocupado.
      documento.addEventListener("submit", alEnviar, true);
      if (ventana && typeof ventana.addEventListener === "function") {
        ventana.addEventListener("pagehide", liberarNotas);
      }
      if (vista.entrada) {
        // El dictado escribe el valor sin evento `input`: todo `input` es de la persona
        // (o de una sugerencia). Corregir una palabra mantiene el borrador como dictado;
        // vaciarlo o sustituirlo (una sugerencia, seleccionar todo y escribir) no.
        vista.entrada.addEventListener("input", () => {
          if (borradorDictado && !conservaDictado(vista.entrada.value)) olvidarDictado();
        });
      }
      montarNotasDelHistorial();
    }

    // --- Notas de voz por respuesta (spec 050) ---------------------------------
    // Contrato con el rediseño (049): solo clases .turno-nota-* y atributos; el estado va
    // en data-nota-estado y el avance en --nota-avance. Ver docs/voz-web.md.

    /** En el gesto de la persona (clic o envío): Safari/iOS exigen un play() dentro. */
    function desbloquearAudio() {
      if (!servidorInicial.lectura || typeof servidor.desbloquear !== "function") return;
      try {
        servidor.desbloquear();
      } catch {
        // Sin desbloqueo la nota puede quedar «bloqueada» y se toca para escucharla.
      }
    }

    function palabrasDe(texto) {
      return String(texto || "")
        .toLocaleLowerCase("es")
        .split(/[^\p{L}\p{N}]+/u)
        .filter(Boolean);
    }

    /** ¿El borrador conserva al menos la mitad de las palabras dictadas? */
    function conservaDictado(valor) {
      if (palabrasDictadas.size === 0) return false;
      const actuales = new Set(palabrasDe(valor));
      let conservadas = 0;
      for (const palabra of palabrasDictadas) if (actuales.has(palabra)) conservadas += 1;
      return conservadas * 2 >= palabrasDictadas.size;
    }

    function olvidarDictado() {
      borradorDictado = false;
      palabrasDictadas = new Set();
    }

    function alEnviar(evento) {
      const formulario = vista.formulario;
      if (!formulario || !evento || evento.target !== formulario) return;
      // El stream ignora un envío vacío o mientras otra respuesta sigue en curso.
      const ocupado =
        typeof formulario.getAttribute === "function" &&
        formulario.getAttribute("aria-busy") === "true";
      const vacio = Boolean(vista.entrada) && String(vista.entrada.value || "").trim() === "";
      if (ocupado || vacio) return;
      turnoHablado =
        borradorDictado ||
        Boolean(formulario.dataset && formulario.dataset.vozOrigen === "manos-libres");
      // Rediseño 052: el stream marca el turno «por voz»; lee y borra la marca al enviar.
      // Un envío escrito la borra: una marca vieja nunca pasa a la pregunta siguiente.
      if (formulario.dataset) {
        if (turnoHablado) formulario.dataset.vozHablado = "1";
        else delete formulario.dataset.vozHablado;
      }
      olvidarDictado();
      if (turnoHablado) desbloquearAudio();
    }

    function crearNodo(etiqueta, clase, atributos = {}) {
      const nodo = documento.createElement(etiqueta);
      nodo.className = clase;
      for (const [nombre, valor] of Object.entries(atributos)) nodo.setAttribute(nombre, valor);
      return nodo;
    }

    // Un solo elemento dentro del turno, justo después de la burbuja; sin DOM (pruebas,
    // documentos raros) la nota existe igual y solo guarda su estado.
    function construirNota(turno, mensaje) {
      const nota = {
        turno,
        mensaje,
        estado: "lista",
        segundos: null,
        raiz: null,
        anuncioPendiente: null,
      };
      if (typeof documento.createElement !== "function") return nota;
      try {
        const raiz = crearNodo("div", "turno-nota", {
          role: "group",
          "aria-label": "Nota de voz de la respuesta",
        });
        raiz.dataset.notaEstado = "lista";
        raiz.dataset.notaMensaje = mensaje;
        const boton = crearNodo("button", "turno-nota-boton", {
          type: "button",
          "aria-pressed": "false",
          "aria-label": ETIQUETA_NOTA,
        });
        boton.dataset.notaAccion = "alternar";
        const barra = crearNodo("span", "turno-nota-barra", {
          role: "progressbar",
          "aria-label": "Avance de la nota de voz",
          "aria-valuemin": "0",
          "aria-valuemax": "100",
          "aria-valuenow": "0",
          "aria-valuetext": formatoTiempo(0),
        });
        const tiempo = crearNodo("span", "turno-nota-tiempo");
        tiempo.textContent = formatoTiempo(0);
        const duracion = crearNodo("span", "turno-nota-duracion");
        const estadoTexto = crearNodo("span", "turno-nota-estado", {
          role: "status",
          "aria-live": "polite",
        });
        // Texto visible en «bloqueada» y «error»; el lector de pantalla ya lo oye arriba.
        const aviso = crearNodo("span", "turno-nota-aviso", { "aria-hidden": "true" });
        aviso.hidden = true;
        const reintentar = crearNodo("button", "turno-nota-reintentar", { type: "button" });
        reintentar.dataset.notaAccion = "reintentar";
        reintentar.textContent = "Reintentar";
        reintentar.hidden = true;
        raiz.append(boton, barra, tiempo, duracion, estadoTexto, aviso, reintentar);
        const burbuja = turno.querySelector(".burbuja");
        if (burbuja && typeof burbuja.after === "function") burbuja.after(raiz);
        else turno.appendChild(raiz);
        Object.assign(nota, { raiz, boton, barra, tiempo, duracion, estadoTexto, aviso, reintentar });
        nota.anuncioPendiente = "";
        reloj.setTimeout(() => {
          const texto = nota.anuncioPendiente;
          nota.anuncioPendiente = null;
          if (texto) estadoTexto.textContent = texto;
        }, 0);
        // Con nota, la franja «Escuchar/Pausar/…» sobra: la nota es el único control.
        const franja = turno.querySelector("[data-voz-turno]");
        if (franja) {
          const activo = documento.activeElement;
          const teniaFoco = Boolean(
            activo && typeof franja.contains === "function" && franja.contains(activo),
          );
          franja.hidden = true;
          if (teniaFoco && typeof boton.focus === "function") boton.focus();
        }
      } catch {
        nota.raiz = null;
      }
      return nota;
    }

    // Una región viva recién añadida al árbol de accesibilidad no se anuncia en muchos
    // lectores: el primer texto de una nota nueva espera a la siguiente tarea.
    function anunciarNota(nota, texto) {
      if (nota.anuncioPendiente !== null) {
        nota.anuncioPendiente = texto;
        return;
      }
      if (nota.estadoTexto.textContent !== texto) nota.estadoTexto.textContent = texto;
    }

    function notaDe(turno) {
      return turno ? notas.get(turno) || null : null;
    }

    /**
     * La nota del turno; se crea si la voz del servidor existe, el turno trae su id y la
     * pregunta se habló (data-voz-pregunta-hablada). Una pregunta escrita no trae audio.
     */
    function asegurarNota(turno) {
      if (!turno || !servidorInicial.lectura) return null;
      const existente = notas.get(turno);
      if (existente) return existente;
      if (typeof turno.className === "string" && turno.className.includes("turno-user")) {
        return null;
      }
      if (!turno.dataset || turno.dataset.vozPreguntaHablada !== "1") return null;
      const mensaje = String(turno.dataset.vozMensaje || "");
      if (!mensaje) return null;
      const nota = construirNota(turno, mensaje);
      notas.set(turno, nota);
      // Contrato con el rediseño (049): la <li> marca que tiene nota.
      if (turno.dataset) turno.dataset.vozNota = "1";
      return nota;
    }

    // El historial llega del servidor sin nota: se crea aquí, sin pedir ningún audio, solo
    // en las respuestas a una pregunta hablada (la plantilla las marca).
    const SELECTOR_HISTORIAL = ".turno-assistant[data-voz-mensaje][data-voz-pregunta-hablada]";
    function montarNotasDelHistorial() {
      if (!servidorInicial.lectura || typeof documento.querySelectorAll !== "function") return;
      for (const turno of Array.from(documento.querySelectorAll(SELECTOR_HISTORIAL) || [])) {
        asegurarNota(turno);
      }
    }

    function pintarAvance(nota, avance, actual) {
      const raiz = nota.raiz;
      if (!raiz) return;
      if (raiz.style && typeof raiz.style.setProperty === "function") {
        raiz.style.setProperty("--nota-avance", avance.toFixed(3));
      }
      nota.barra.setAttribute("aria-valuenow", String(Math.round(avance * 100)));
      nota.barra.setAttribute(
        "aria-valuetext",
        nota.segundos
          ? `${formatoTiempo(actual)} de ${formatoTiempo(nota.segundos)}`
          : formatoTiempo(actual),
      );
      nota.tiempo.textContent = formatoTiempo(actual);
      nota.duracion.textContent = nota.segundos ? formatoTiempo(nota.segundos) : "";
    }

    function fijarAvance(nota, actual, total) {
      if (Number.isFinite(total) && total > 0) nota.segundos = total;
      const avance = nota.segundos ? Math.min(1, Math.max(0, (Number(actual) || 0) / nota.segundos)) : 0;
      pintarAvance(nota, avance, Number(actual) || 0);
    }

    function pintarNota(nota, estadoNota, aviso = "") {
      if (!nota) return;
      nota.estado = estadoNota;
      if (estadoNota === "lista") fijarAvance(nota, 0, null);
      const raiz = nota.raiz;
      if (!raiz) return;
      raiz.dataset.notaEstado = estadoNota;
      // Pulsada mientras suena y mientras carga la reproducción pedida (soltarla la
      // cancela); una precarga que nadie pidió reproducir sigue sin pulsar.
      const pulsada =
        estadoNota === "sonando" || (estadoNota === "cargando" && notaDe(turnoLectura) === nota);
      nota.boton.setAttribute("aria-pressed", pulsada ? "true" : "false");
      if (estadoNota === "cargando") nota.boton.setAttribute("aria-busy", "true");
      else nota.boton.removeAttribute("aria-busy");
      const texto = aviso || ANUNCIOS_NOTA[estadoNota] || "";
      anunciarNota(nota, texto);
      const visible = estadoNota === "bloqueada" || estadoNota === "error";
      nota.aviso.hidden = !visible;
      nota.aviso.textContent = visible ? texto : "";
      // Ocultar el botón con foco lo tiraría al body: pasa al botón principal.
      const focoEnReintentar = documento.activeElement === nota.reintentar;
      nota.reintentar.hidden = estadoNota !== "error";
      if (focoEnReintentar && nota.reintentar.hidden && typeof nota.boton.focus === "function") {
        nota.boton.focus();
      }
    }

    function lecturaServidorDisponible() {
      if (servidorActivo.lectura) return true;
      if (!servidorInicial.lectura || reloj.now() < lecturaReintentoEn) return false;
      servidorActivo.lectura = true;
      calcularSoporte();
      return true;
    }

    function marcarLecturaNoDisponible() {
      servidorActivo.lectura = false;
      lecturaReintentoEn = reloj.now() + REINTENTO_LECTURA_MS;
      calcularSoporte();
    }

    // Solo el proveedor caído o apagado (503) o un rechazo de la sesión (401/403) dejan
    // la voz del servidor sin uso un rato; 404, 422, 429, la red o el plazo son del turno.
    function esServidorNoDisponible(error) {
      const status = error && error.status;
      return status === 503 || status === 401 || status === 403;
    }

    function esCancelado(error) {
      return Boolean(error && error.name === "AbortError");
    }

    function mensajeDeFallo(error) {
      if (esServidorNoDisponible(error)) return MENSAJES.vozNoDisponible;
      if (error && error.status === 429) return MENSAJES.notaOcupada;
      return MENSAJES.notaError;
    }

    function audioEnCache(mensaje) {
      if (!audios.has(mensaje)) return null;
      const audio = audios.get(mensaje);
      audios.delete(mensaje);
      audios.set(mensaje, audio);
      return audio;
    }

    function guardarAudio(mensaje, audio) {
      audios.delete(mensaje);
      audios.set(mensaje, audio);
      while (audios.size > MAX_NOTAS_EN_CACHE) audios.delete(audios.keys().next().value);
    }

    function marcarSintetizandoVoz() {
      const ultimo = Array.from(pedidos.keys()).pop();
      marcarEstadoVoz("vozSintetizando", ultimo || null);
    }

    /** El audio de la respuesta: de la caché, del pedido en vuelo o de una síntesis nueva. */
    function obtenerAudio(mensaje) {
      const enCache = audioEnCache(mensaje);
      if (enCache) return Promise.resolve(enCache);
      const vigente = pedidos.get(mensaje);
      if (vigente) return vigente.promesa;
      const control = crearAbortador();
      let pendiente;
      try {
        pendiente = Promise.resolve(
          servidor.sintetizar(mensaje, { signal: control ? control.signal : undefined }),
        );
      } catch (error) {
        pendiente = Promise.reject(error);
      }
      const pedido = { control, promesa: null };
      const soltar = () => {
        if (pedidos.get(mensaje) === pedido) pedidos.delete(mensaje);
        marcarSintetizandoVoz();
      };
      pedido.promesa = pendiente.then(
        (audio) => {
          const vivo = pedidos.get(mensaje) === pedido;
          soltar();
          if (vivo) guardarAudio(mensaje, audio);
          return audio;
        },
        (error) => {
          soltar();
          if (esServidorNoDisponible(error)) marcarLecturaNoDisponible();
          throw error;
        },
      );
      pedidos.set(mensaje, pedido);
      marcarSintetizandoVoz();
      return pedido.promesa;
    }

    /** Pide el audio sin reproducirlo, para que la nota esté lista en pocos segundos. */
    function precargarNota(nota) {
      if (!nota || audios.has(nota.mensaje) || !lecturaServidorDisponible()) return;
      if (nota.estado !== "lista") return;
      pintarNota(nota, "cargando");
      obtenerAudio(nota.mensaje).then(
        () => {
          if (nota.estado === "cargando" && notaDe(turnoLectura) !== nota) pintarNota(nota, "lista");
        },
        (error) => {
          if (notaDe(turnoLectura) === nota) return;
          if (esCancelado(error) || (esServidorNoDisponible(error) && navegador.sintesis)) {
            pintarNota(nota, "lista");
            return;
          }
          pintarNota(nota, "error", mensajeDeFallo(error));
        },
      );
    }

    function alternarNota(nota) {
      const actual = notaDe(turnoLectura) === nota;
      if (actual && nota.estado === "sonando") {
        pausarLectura();
        return;
      }
      if (actual && nota.estado === "pausada") {
        reanudarLectura();
        return;
      }
      if (actual && nota.estado === "cargando") {
        // Cancela la reproducción; la síntesis en vuelo termina y queda en la caché.
        detenerLectura();
        return;
      }
      if (nota.estado === "error") {
        reintentarNota(nota);
        return;
      }
      // Con el audio en caché, el play() ocurre dentro de este gesto; sin él se
      // desbloquea el reproductor antes de esperar la síntesis.
      if (!audios.has(nota.mensaje)) desbloquearAudio();
      reproducirNota(nota);
    }

    function reintentarNota(nota) {
      // Un reintento explícito vuelve a probar el servidor aunque siga en espera.
      lecturaReintentoEn = 0;
      if (!audios.has(nota.mensaje)) desbloquearAudio();
      reproducirNota(nota);
    }

    function soltarTurnoLectura() {
      cicloLectura += 1;
      turnoLectura = null;
      segmentosLectura = [];
      indiceLectura = 0;
      fijarLeyendo(false);
    }

    function reproducirNota(nota, opciones = {}) {
      if (!lecturaServidorDisponible()) {
        // La nota que suena (del servidor, antes de la espera) se corta de verdad: si no,
        // sonarían dos voces y la pausa llegaría a la reproducción vieja.
        detenerLectura();
        return leerNotaConRespaldo(nota, opciones);
      }
      if (!opciones.automatico && nota.turno.dataset) delete nota.turno.dataset.vozAutoLeido;
      detenerLectura();
      turnoLectura = nota.turno;
      segmentosLectura = [];
      indiceLectura = 0;
      cicloLectura += 1;
      const ciclo = cicloLectura;
      avisar("");
      const enCache = audioEnCache(nota.mensaje);
      if (enCache) {
        sonarNota(nota, enCache, ciclo, opciones);
        return true;
      }
      pintarNota(nota, "cargando");
      obtenerAudio(nota.mensaje).then(
        (audio) => {
          if (ciclo === cicloLectura) sonarNota(nota, audio, ciclo, opciones);
        },
        (error) => {
          if (ciclo === cicloLectura) fallarNota(nota, error, opciones);
        },
      );
      return true;
    }

    function sonarNota(nota, audio, ciclo, opciones) {
      const alEmpezar = typeof opciones.alEmpezar === "function" ? opciones.alEmpezar : null;
      pintarNota(nota, "cargando");
      let control;
      try {
        control = servidor.reproducir(audio, {
          alEmpezar: () => {
            if (ciclo !== cicloLectura) return;
            fijarLeyendo(true);
            if (alEmpezar) alEmpezar();
            actualizarControles(nota.turno, "leyendo");
          },
          alProgreso: (actual, total) => {
            if (ciclo === cicloLectura) fijarAvance(nota, actual, total);
          },
          alTerminar: () => {
            if (ciclo !== cicloLectura) return;
            reproduccion = null;
            terminarLectura();
          },
          alFallar: (error) => {
            if (ciclo !== cicloLectura) return;
            reproduccion = null;
            fallarReproduccion(nota, error);
          },
        });
      } catch (error) {
        if (ciclo === cicloLectura) fallarReproduccion(nota, error);
        return;
      }
      if (ciclo === cicloLectura) reproduccion = control;
    }

    // El audio llegó pero no suena. Autoplay bloqueado (NotAllowedError): la nota queda
    // «bloqueada» con su audio en caché y un toque la reproduce. Otro fallo (decodificar,
    // CSP): «error» y el reintento vuelve a pedir el audio. Nunca se cambia de voz.
    function fallarReproduccion(nota, error) {
      const bloqueo = Boolean(error && error.name === "NotAllowedError");
      const eraManos = manosLibres.activo;
      manosLibres.reanudarTrasLeer = false;
      soltarTurnoLectura();
      actualizarControles(nota.turno, "detenido");
      if (bloqueo) pintarNota(nota, "bloqueada");
      else {
        audios.delete(nota.mensaje);
        pintarNota(nota, "error", MENSAJES.notaError);
      }
      if (eraManos) desactivarManosLibres(bloqueo ? "bloqueo" : "nota");
    }

    // La síntesis falló. Con el servidor no disponible (503, 401/403) este turno lo lee
    // la voz del navegador si existe; un 404, 422, 429 o la red dejan la nota en «error».
    function fallarNota(nota, error, opciones) {
      if (esCancelado(error)) {
        soltarTurnoLectura();
        pintarNota(nota, "lista");
        return;
      }
      if (esServidorNoDisponible(error)) {
        leerNotaConRespaldo(nota, opciones);
        return;
      }
      const eraManos = manosLibres.activo;
      manosLibres.reanudarTrasLeer = false;
      soltarTurnoLectura();
      actualizarControles(nota.turno, "detenido");
      pintarNota(nota, "error", mensajeDeFallo(error));
      if (eraManos) desactivarManosLibres("nota");
    }

    function leerNotaConRespaldo(nota, opciones = {}) {
      soltarTurnoLectura();
      actualizarControles(nota.turno, "detenido");
      if (navegador.sintesis && leerConNavegador(nota.turno, opciones)) return true;
      pintarNota(nota, "error", MENSAJES.vozNoDisponible);
      manosLibres.reanudarTrasLeer = false;
      if (manosLibres.activo) desactivarManosLibres("nota");
      return false;
    }

    // Navegación (otro hilo, /chat/new) o destrucción: se cortan las síntesis en vuelo,
    // el audio y su URL blob:, y la caché.
    function liberarNotas() {
      detenerLectura();
      for (const pedido of pedidos.values()) {
        if (pedido.control) pedido.control.abort();
      }
      pedidos.clear();
      marcarSintetizandoVoz();
      audios.clear();
      if (servidor && typeof servidor.liberar === "function") servidor.liberar();
      for (const nota of notas.values()) {
        if (nota.estado === "cargando") pintarNota(nota, "lista");
      }
    }

    function prepararAviso() {
      if (!vista.aviso) return;
      if (vista.avisoAceptar) {
        vista.avisoAceptar.addEventListener("click", () => {
          desbloquearAudio();
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

    // --voz-progreso: de 0 a 1 frente al máximo de segundos mientras se graba.
    function seguirProgreso(ciclo, inicio, maximo) {
      fijarProgresoVoz(Math.min(1, Math.max(0, (reloj.now() - inicio) / maximo)));
      temporizadorProgreso = reloj.setTimeout(() => {
        temporizadorProgreso = null;
        if (estado.ciclo !== ciclo || !grabacionActiva || subiendo) return;
        seguirProgreso(ciclo, inicio, maximo);
      }, 250);
    }

    function pararGrabacionVisible() {
      if (temporizadorProgreso !== null) {
        reloj.clearTimeout(temporizadorProgreso);
        temporizadorProgreso = null;
      }
      fijarProgresoVoz(null);
      marcarEstadoVoz("vozGrabando", null);
    }

    function soltarGrabacion() {
      pararGrabacionVisible();
      marcarEstadoVoz("vozTranscribiendo", null);
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
      marcarEstadoVoz("vozGrabando", "1");
      seguirProgreso(ciclo, reloj.now(), maximo);
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
      pararGrabacionVisible();
      marcarEstadoVoz("vozTranscribiendo", "1");
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
      pararGrabacionVisible();
      marcarEstadoVoz("vozTranscribiendo", null);
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
      // Solo el dictado escribe por aquí: si se envía este borrador, la persona habló.
      borradorDictado = true;
      for (const palabra of palabrasDe(fragmento)) palabrasDictadas.add(palabra);
      return { insertado: true, recortado };
    }

    function hayVozEspanola() {
      if (lecturaServidorDisponible()) return true;
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
            else {
              desbloquearAudio();
              intentarActivarManosLibres();
            }
          });
        }
      }
      if (vista.manosAceptar) {
        vista.manosAceptar.addEventListener("click", () => {
          desbloquearAudio();
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
        nota: MENSAJES.manosNota,
        fallo: MENSAJES.manosFallo,
        oculto: MENSAJES.manosOculto,
        lectura: MENSAJES.manosLectura,
        hilo: MENSAJES.manosHilo,
        bloqueo: MENSAJES.manosBloqueo,
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
      // Un turno fallido no deja «hablado» pendiente para la respuesta siguiente.
      turnoHablado = false;
      if (!manosLibres.activo) return;
      const detalle = (evento && evento.detail) || {};
      if (detalle.handsFree === true) desactivarManosLibres("fallo");
    }

    function alCompletarTurno(evento) {
      const detalle = (evento && evento.detail) || {};
      const turno = detalle.turno;
      if (!turno) return;
      if (typeof turno.className === "string" && turno.className.includes("turno-user")) return;
      // Spec 050: solo una pregunta hablada (manos libres o un borrador dictado) trae audio,
      // y su respuesta suena sola. Escrita, no hay nota ni «Escuchar».
      const hablado = detalle.handsFree === true || detalle.spoken === true || turnoHablado;
      turnoHablado = false;
      if (hablado && turno.dataset) turno.dataset.vozPreguntaHablada = "1";
      const nota = hablado ? asegurarNota(turno) : null;
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
      if (!hablado) return;
      if (hiloActual && hiloTurno && hiloActual !== hiloTurno) return;
      if (turno.dataset && turno.dataset.vozAutoLeido === "1") return;
      if (turnoLectura === turno) return;
      if (documento.visibilityState === "hidden" || estado.escuchando) {
        // No suena ahora (pestaña oculta o un dictado nuevo), pero queda lista al tocarla.
        precargarNota(nota);
        return;
      }
      // Se marca al arrancar la primera locución: si el navegador bloquea el
      // audio o faltan voces, el turno queda reintentable.
      leerTurno(turno, {
        automatico: true,
        hablado,
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
      const accionNota = objetivo.closest("[data-nota-accion]");
      if (accionNota) {
        const nota = notaDe(accionNota.closest(".turno"));
        if (!nota) return;
        if (accionNota.dataset && accionNota.dataset.notaAccion === "reintentar") reintentarNota(nota);
        else alternarNota(nota);
        return;
      }
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
      if (opciones.automatico && !opciones.manosLibres && !opciones.hablado) return false;
      // Con la voz del servidor y el id de la respuesta, la lectura es su nota de voz.
      const nota = asegurarNota(turno);
      if (nota) return reproducirNota(nota, opciones);
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
        fijarLeyendo(true);
        if (indiceLectura === 0 && alEmpezar) alEmpezar();
        avisarLectura(MENSAJES.leyendo);
        actualizarControles(turnoLectura, "leyendo");
        // Respaldo dentro de una nota: el avance es la parte que se está leyendo.
        const nota = notaDe(turnoLectura);
        if (nota && segmentosLectura.length) {
          pintarAvance(nota, indiceLectura / segmentosLectura.length, 0);
        }
      };
      locucion.onend = () => {
        if (ciclo !== cicloLectura) return;
        indiceLectura += 1;
        // Respaldo dentro de una nota: al cerrar cada parte, el avance llega a su final.
        const notaLeida = notaDe(turnoLectura);
        if (notaLeida && segmentosLectura.length) {
          pintarAvance(notaLeida, indiceLectura / segmentosLectura.length, 0);
        }
        if (indiceLectura < segmentosLectura.length) hablarSegmento(ciclo, voz, alEmpezar);
        else terminarLectura();
      };
      locucion.onerror = () => {
        if (ciclo !== cicloLectura) return;
        const eraManos = manosLibres.activo;
        const nota = notaDe(turnoLectura);
        manosLibres.reanudarTrasLeer = false;
        terminarLectura();
        if (nota) pintarNota(nota, "error", MENSAJES.lecturaError);
        else avisar(MENSAJES.lecturaError);
        if (eraManos) desactivarManosLibres(nota ? "nota" : "error");
      };
      sintesis.speak(locucion);
    }

    function pausarLectura() {
      if (!turnoLectura || !estado.leyendo) return;
      if (reproduccion) reproduccion.pausar();
      else if (sintesis && typeof sintesis.pause === "function") sintesis.pause();
      fijarLeyendo(false);
      const turno = turnoLectura;
      actualizarControles(turno, "pausado");
      avisarLectura(MENSAJES.pausa);
    }

    function reanudarLectura() {
      if (!turnoLectura) return;
      if (reproduccion) reproduccion.reanudar();
      else if (sintesis && typeof sintesis.resume === "function") sintesis.resume();
      fijarLeyendo(true);
      actualizarControles(turnoLectura, "leyendo");
      avisarLectura(MENSAJES.leyendo);
    }

    function detenerLectura() {
      if (!turnoLectura) return;
      manosLibres.reanudarTrasLeer = false;
      const manosActivas = manosLibres.activo;
      const turno = turnoLectura;
      cicloLectura += 1;
      turnoLectura = null;
      // Una síntesis en vuelo sigue (para la caché): el atributo refleja los pedidos.
      marcarSintetizandoVoz();
      segmentosLectura = [];
      indiceLectura = 0;
      fijarLeyendo(false);
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
      // Una síntesis en vuelo sigue (para la caché): el atributo refleja los pedidos.
      marcarSintetizandoVoz();
      segmentosLectura = [];
      indiceLectura = 0;
      fijarLeyendo(false);
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
      const nota = notaDe(turno);
      if (nota) {
        const estados = { leyendo: "sonando", pausado: "pausada", cargando: "cargando" };
        pintarNota(nota, estados[modo] || "lista");
        // La franja está oculta: el foco se queda en el botón de la nota.
        if (nota.raiz) return;
      }
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

    // Un turno con nota anuncia su estado en la nota: no se repite en la línea global.
    function avisarLectura(mensaje) {
      if (notaDe(turnoLectura)) return;
      avisar(mensaje);
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
      notaDe,
      manosLibres,
      activarManosLibres,
      desactivarManosLibres,
      destruir() {
        liberarNotas();
        soltarGrabacion();
        documento.removeEventListener("keydown", alPulsarTecla);
        documento.removeEventListener("click", alClicTurno);
        documento.removeEventListener("chat:turn-complete", alCompletarTurno);
        documento.removeEventListener("chat:turn-failed", alFallarTurno);
        documento.removeEventListener("visibilitychange", alCambiarVisibilidad);
        documento.removeEventListener("submit", alEnviar, true);
        if (ventana && typeof ventana.removeEventListener === "function") {
          ventana.removeEventListener("pagehide", liberarNotas);
        }
      },
    };
  }

  // --- Adaptador de la voz del servidor (spec 047) ----------------------------
  // Envuelve MediaRecorder, el detector de voz (Web Audio), fetch y <audio>. El
  // controlador solo ve promesas: grabar → {detener, cancelar}, transcribir(audio)
  // → texto, sintetizar(id, {signal}) → audio y reproducir(audio, llamadas) →
  // {pausar, reanudar, detener}; desbloquear() va dentro del gesto y liberar() al salir.
  // Los errores HTTP llevan `status` y `reason` para decidir el respaldo.

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

    async function sintetizar(mensaje, opciones = {}) {
      const datos = new win.URLSearchParams();
      datos.append("message_id", mensaje);
      datos.append("csrf_token", csrf());
      const respuesta = await enviar("/api/voz/hablar", datos, opciones);
      return respuesta.blob();
    }

    // Un solo <audio> para todas las notas (spec 050). Safari/iOS solo dejan sonar tras
    // un await al elemento que ya hizo play() dentro de un gesto: desbloquear() lo hace
    // con un silencio en el clic o el envío, y las notas reutilizan ese elemento. Como
    // mucho hay una URL blob: viva, la de la nota cargada.
    let elemento = null;
    let urlActual = null;
    let ficha = 0;
    let desbloqueado = false;

    function asegurarElemento() {
      if (!elemento && AudioEl) {
        elemento = new AudioEl();
        elemento.preload = "auto";
      }
      return elemento;
    }

    function limpiarManejadores(audio) {
      audio.onplaying = null;
      audio.onended = null;
      audio.onerror = null;
      audio.ontimeupdate = null;
      audio.onloadedmetadata = null;
      audio.ondurationchange = null;
    }

    function soltarUrl() {
      if (!urlActual) return;
      URLApi.revokeObjectURL(urlActual);
      urlActual = null;
    }

    // WAV de 0,1 s en silencio (8 kHz, 8 bits). Va por blob: porque la CSP del chat
    // permite `media-src 'self' blob:` y no data:.
    function silencio() {
      const muestras = 800;
      const bytes = new Uint8Array(44 + muestras);
      const vista = new DataView(bytes.buffer);
      const escribir = (posicion, texto) => {
        for (let indice = 0; indice < texto.length; indice += 1) {
          bytes[posicion + indice] = texto.charCodeAt(indice);
        }
      };
      escribir(0, "RIFF");
      vista.setUint32(4, 36 + muestras, true);
      escribir(8, "WAVE");
      escribir(12, "fmt ");
      vista.setUint32(16, 16, true);
      vista.setUint16(20, 1, true);
      vista.setUint16(22, 1, true);
      vista.setUint32(24, 8000, true);
      vista.setUint32(28, 8000, true);
      vista.setUint16(32, 1, true);
      vista.setUint16(34, 8, true);
      escribir(36, "data");
      vista.setUint32(40, muestras, true);
      bytes.fill(128, 44);
      return new win.Blob([bytes], { type: "audio/wav" });
    }

    /** Síncrono, dentro del gesto. Con una nota cargada el elemento ya está desbloqueado. */
    function desbloquear() {
      if (desbloqueado || urlActual) return;
      const audio = asegurarElemento();
      if (!audio || !win.Blob) return;
      let url;
      try {
        url = URLApi.createObjectURL(silencio());
      } catch {
        return;
      }
      ficha += 1;
      const propia = ficha;
      limpiarManejadores(audio);
      const soltar = () => URLApi.revokeObjectURL(url);
      audio.src = url;
      let inicio;
      try {
        inicio = audio.play();
      } catch {
        soltar();
        return;
      }
      if (!inicio || typeof inicio.then !== "function") {
        soltar();
        return;
      }
      inicio.then(
        () => {
          desbloqueado = true;
          // Si mientras tanto empezó una nota, el silencio ya no manda.
          if (propia === ficha) audio.pause();
          soltar();
        },
        soltar,
      );
    }

    function reproducir(audioNota, llamadas = {}) {
      const audio = asegurarElemento();
      ficha += 1;
      const propia = ficha;
      const vigente = () => propia === ficha;
      limpiarManejadores(audio);
      try {
        audio.pause();
      } catch {
        // nada sonaba
      }
      soltarUrl();
      urlActual = URLApi.createObjectURL(audioNota);
      const terminar = () => {
        if (!vigente()) return;
        ficha += 1;
        limpiarManejadores(audio);
        soltarUrl();
      };
      const fallar = (error) => {
        if (!vigente()) return;
        terminar();
        if (llamadas.alFallar) llamadas.alFallar(error);
      };
      const progreso = () => {
        if (vigente() && llamadas.alProgreso) {
          llamadas.alProgreso(Number(audio.currentTime) || 0, Number(audio.duration));
        }
      };
      audio.onplaying = () => {
        if (!vigente()) return;
        desbloqueado = true;
        if (llamadas.alEmpezar) llamadas.alEmpezar();
      };
      audio.ontimeupdate = progreso;
      audio.onloadedmetadata = progreso;
      audio.ondurationchange = progreso;
      audio.onended = () => {
        if (!vigente()) return;
        progreso();
        terminar();
        if (llamadas.alTerminar) llamadas.alTerminar();
      };
      audio.onerror = () => {
        const fallo = new Error("media");
        fallo.name = "MediaError";
        fallar(fallo);
      };
      audio.src = urlActual;
      const tocar = () => {
        let inicio;
        try {
          inicio = audio.play();
        } catch (error) {
          fallar(error);
          return;
        }
        if (inicio && typeof inicio.catch === "function") inicio.catch(fallar);
      };
      tocar();
      return {
        pausar() {
          if (vigente()) audio.pause();
        },
        reanudar() {
          if (vigente()) tocar();
        },
        detener() {
          if (!vigente()) return;
          terminar();
          audio.pause();
        },
      };
    }

    /** Al salir de la página: nada suena y no queda ninguna URL blob: viva. */
    function liberar() {
      ficha += 1;
      if (elemento) {
        limpiarManejadores(elemento);
        try {
          elemento.pause();
        } catch {
          // ya estaba en pausa
        }
      }
      soltarUrl();
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
      desbloquear,
      reproducir,
      liberar,
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
      ventana: win,
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
