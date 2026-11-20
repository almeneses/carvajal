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

  /** Texto y segmentos hablables: tarjetas y citas salen de datos visibles. */
  function construirSegmentos(turno) {
    if (!turno) return [];
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
    for (const nodoTitulo of listaDe(turno, '.citas [data-campo="title"]')) {
      const titulo = textoVisible(nodoTitulo);
      if (titulo) segmentos.push(`Según ${titulo}.`);
    }
    return segmentos;
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
    soporte.manosLibres = soporte.dictado && soporte.sintesis && soporte.streaming;
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
        if (anterior && typeof anterior.abort === "function") anterior.abort();
      }
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
      if (ciclo !== estado.ciclo || !reconocimiento) return;
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
      estado.ciclo += 1;
      estado.transcripcion = "";
      marcarEscuchando(false);
    }

    function finalizarCiclo(mensaje) {
      cerrarCiclo();
      avisar(mensaje);
    }

    function detenerDictado() {
      if (!estado.escuchando || !reconocimiento) return;
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
        if (detalle.handsFree !== true) return;
        if (hiloActual && hiloTurno && hiloActual !== hiloTurno) {
          desactivarManosLibres("hilo");
          return;
        }
        const leido = leerTurno(turno, { automatico: true, manosLibres: true });
        manosLibres.reanudarTrasLeer = leido;
        if (!leido) {
          if (!hayVozEspanola()) desactivarManosLibres("error");
          else comenzarDictado({ manosLibres: true });
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
      if (typeof sintesis.pause === "function") sintesis.pause();
      estado.leyendo = false;
      const turno = turnoLectura;
      actualizarControles(turno, "pausado");
      avisar(MENSAJES.pausa);
    }

    function reanudarLectura() {
      if (!turnoLectura) return;
      if (typeof sintesis.resume === "function") sintesis.resume();
      estado.leyendo = true;
      actualizarControles(turnoLectura, "leyendo");
      avisar(MENSAJES.leyendo);
    }

    function detenerLectura() {
      if (!turnoLectura) return;
      const manosActivas = manosLibres.activo;
      const turno = turnoLectura;
      cicloLectura += 1;
      turnoLectura = null;
      segmentosLectura = [];
      indiceLectura = 0;
      estado.leyendo = false;
      if (typeof sintesis.cancel === "function") sintesis.cancel();
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
      if (manosLibres.activo && manosLibres.reanudarTrasLeer) {
        manosLibres.reanudarTrasLeer = false;
        comenzarDictado({ manosLibres: true });
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
        documento.removeEventListener("keydown", alPulsarTecla);
        documento.removeEventListener("click", alClicTurno);
        documento.removeEventListener("chat:turn-complete", alCompletarTurno);
        documento.removeEventListener("chat:turn-failed", alFallarTurno);
        documento.removeEventListener("visibilitychange", alCambiarVisibilidad);
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
        autoLecturaCampo: buscar("[data-voz-autolectura-campo]"),
        hilo: document.querySelector("[data-thread-id]"),
        entrada: document.getElementById("pregunta"),
        formulario: document.querySelector("form[data-chat-stream]"),
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
      segura: Boolean(win.isSecureContext),
      streaming:
        typeof win.fetch === "function" &&
        typeof win.ReadableStream === "function" &&
        typeof win.TextDecoder === "function",
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
