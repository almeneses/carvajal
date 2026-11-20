// Dobles de voz, reloj y formulario para el chat por voz (spec 040).
//
// Los dobles no sustituyen la prueba con micrófono real: fijan el contrato del
// controlador (avisos, límites, callbacks tardíos y accesibilidad) sin red, sin
// micrófono y sin dependencias npm. Cada aserción usa `node --test`.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const RAIZ = path.join(__dirname, "..", "..");
const FUENTE = path.join(RAIZ, "apps", "api", "static", "chat-voice.js");

// ---------------------------------------------------------------------------
// Dobles mínimos del DOM: solo lo que el controlador usa.
// ---------------------------------------------------------------------------

function crearElemento(etiqueta = "div", props = {}) {
  const oyentes = new Map();
  const atributos = new Map();
  const clases = new Set();
  const elemento = {
    etiqueta,
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    open: false,
    focused: false,
    selectionStart: 0,
    selectionEnd: 0,
    dataset: {},
    classList: {
      add: (...nombres) => nombres.forEach((nombre) => clases.add(nombre)),
      remove: (...nombres) => nombres.forEach((nombre) => clases.delete(nombre)),
      contains: (nombre) => clases.has(nombre),
      toggle: (nombre, forzar) => {
        const activo = forzar === undefined ? !clases.has(nombre) : Boolean(forzar);
        if (activo) clases.add(nombre);
        else clases.delete(nombre);
        return activo;
      },
    },
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
    },
    removeEventListener(tipo, manejador) {
      const lista = oyentes.get(tipo) || [];
      oyentes.set(
        tipo,
        lista.filter((cada) => cada !== manejador),
      );
    },
    dispatch(tipo, evento = {}) {
      for (const manejador of oyentes.get(tipo) || []) manejador(evento);
    },
    setAttribute(nombre, valor) {
      atributos.set(nombre, String(valor));
    },
    getAttribute(nombre) {
      return atributos.has(nombre) ? atributos.get(nombre) : null;
    },
    removeAttribute(nombre) {
      atributos.delete(nombre);
    },
    focus() {
      this.focused = true;
    },
    setSelectionRange(inicio, fin) {
      this.selectionStart = inicio;
      this.selectionEnd = fin;
    },
    scrollIntoView() {},
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      this.dispatch("close");
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    ...props,
  };
  return elemento;
}

function crearFormulario() {
  const formulario = crearElemento("form");
  formulario.enviados = 0;
  formulario.requestSubmit = () => {
    formulario.enviados += 1;
  };
  formulario.submit = () => {
    formulario.enviados += 1;
  };
  return formulario;
}

function crearDocumento(consulta = {}) {
  const oyentes = new Map();
  return {
    documentElement: crearElemento("html"),
    visibilityState: "visible",
    activeElement: null,
    hidden: false,
    getElementById(id) {
      return consulta[id] || null;
    },
    querySelector(selector) {
      return consulta[selector] || null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
    },
    removeEventListener(tipo, manejador) {
      const lista = oyentes.get(tipo) || [];
      oyentes.set(
        tipo,
        lista.filter((cada) => cada !== manejador),
      );
    },
    dispatch(tipo, evento = {}) {
      for (const manejador of oyentes.get(tipo) || []) manejador(evento);
    },
  };
}

function crearAlmacen(inicial = {}, { falla = false } = {}) {
  const datos = new Map(Object.entries(inicial));
  return {
    getItem(clave) {
      if (falla) throw new Error("almacenamiento bloqueado");
      return datos.has(clave) ? datos.get(clave) : null;
    },
    setItem(clave, valor) {
      if (falla) throw new Error("almacenamiento bloqueado");
      datos.set(clave, String(valor));
    },
    removeItem(clave) {
      if (falla) throw new Error("almacenamiento bloqueado");
      datos.delete(clave);
    },
    _datos: datos,
  };
}

function crearReloj() {
  let ahora = 0;
  let siguiente = 1;
  const pendientes = new Map();
  return {
    setTimeout(manejador, ms) {
      const id = siguiente++;
      pendientes.set(id, { manejador, ms, en: ahora + ms });
      return id;
    },
    clearTimeout(id) {
      pendientes.delete(id);
    },
    now() {
      return ahora;
    },
    avanzar(ms) {
      ahora += ms;
      for (const [id, tarea] of [...pendientes].sort((a, b) => a[1].en - b[1].en)) {
        if (tarea.en <= ahora) {
          pendientes.delete(id);
          tarea.manejador();
        }
      }
    },
    pendientes() {
      return [...pendientes.values()];
    },
  };
}

// ---------------------------------------------------------------------------
// Doble de SpeechRecognition: guarda la configuración y permite disparar
// eventos en el orden que la prueba decida, incluso tarde.
// ---------------------------------------------------------------------------

function crearReconocimiento() {
  const instancias = [];
  function Constructor() {
    const oyentes = new Map();
    const instancia = {
      lang: "",
      continuous: true,
      interimResults: false,
      iniciado: 0,
      detenido: 0,
      abortado: 0,
      onresult: null,
      onerror: null,
      onend: null,
      onstart: null,
      addEventListener(tipo, manejador) {
        if (!oyentes.has(tipo)) oyentes.set(tipo, []);
        oyentes.get(tipo).push(manejador);
      },
      start() {
        this.iniciado += 1;
        this._emitir("start", {});
      },
      stop() {
        this.detenido += 1;
      },
      abort() {
        this.abortado += 1;
        this._emitir("end", {});
      },
      _emitir(tipo, evento) {
        for (const manejador of oyentes.get(tipo) || []) manejador(evento);
        const propiedad = this[`on${tipo}`];
        if (typeof propiedad === "function") propiedad(evento);
      },
      resultado(texto, { final = true, indice = 0 } = {}) {
        const alternativas = [{ transcript: texto, confidence: 0.95 }];
        const evento = {
          resultIndex: indice,
          results: Object.assign([], {
            length: indice + 1,
            [indice]: Object.assign(alternativas, { isFinal: final, length: 1 }),
          }),
        };
        this._emitir("result", evento);
      },
      error(codigo) {
        this._emitir("error", { error: codigo });
      },
      fin() {
        this._emitir("end", {});
      },
    };
    instancias.push(instancia);
    return instancia;
  }
  Constructor.instancias = instancias;
  Constructor.ultima = () => instancias[instancias.length - 1] || null;
  return Constructor;
}

// ---------------------------------------------------------------------------
// Doble de síntesis: cada locución conserva texto, voz, idioma y callbacks.
// ---------------------------------------------------------------------------

function crearSintesis({ voces = [{ name: "Mónica", lang: "es-CO", localService: true }] } = {}) {
  const locuciones = [];
  const sintesis = {
    speaking: false,
    pending: false,
    paused: false,
    cancelados: 0,
    current: null,
    dividir: null,
    getVoices() {
      return voces;
    },
    speak(locucion) {
      locuciones.push(locucion);
      this.current = locucion;
      this.speaking = true;
      this.pending = false;
      if (typeof locucion.onstart === "function") locucion.onstart({});
    },
    cancel() {
      this.cancelados += 1;
      this.speaking = false;
      this.pending = false;
      this.paused = false;
      const anterior = this.current;
      this.current = null;
      if (anterior && typeof anterior.onend === "function") anterior.onend({});
    },
    pause() {
      this.paused = true;
    },
    resume() {
      this.paused = false;
    },
    terminar() {
      this.speaking = false;
      const anterior = this.current;
      this.current = null;
      if (anterior && typeof anterior.onend === "function") anterior.onend({});
    },
    locuciones,
  };
  return sintesis;
}

function crearUtterance(texto) {
  return {
    text: texto,
    lang: "",
    voice: null,
    rate: 1,
    pitch: 1,
    onstart: null,
    onend: null,
    onerror: null,
  };
}

// ---------------------------------------------------------------------------
// Carga del script real con un `window` aislado.
// ---------------------------------------------------------------------------

function cargarVoz({
  documento,
  almacen,
  reconocimiento = true,
  sintesis = true,
  ventana = null,
} = {}) {
  const doc = documento || crearDocumento();
  const sandbox = {
    document: doc,
    console,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    performance: { now: () => 0 },
    navigator: { language: "es-CO" },
    isSecureContext: true,
  };
  // El global del contexto y `window` pueden ser objetos distintos: así se
  // prueba un navegador cuyo getter de localStorage lanza al accederse.
  sandbox.window = ventana || sandbox;
  if (reconocimiento) sandbox.SpeechRecognition = reconocimiento === true ? crearReconocimiento() : reconocimiento;
  if (sintesis) {
    sandbox.speechSynthesis = sintesis === true ? crearSintesis() : sintesis;
    sandbox.SpeechSynthesisUtterance = crearUtterance;
  }
  sandbox.localStorage = almacen || crearAlmacen();
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FUENTE, "utf8"), sandbox, { filename: "chat-voice.js" });
  return sandbox;
}

function crearVista(extra = {}) {
  const vista = {
    raiz: crearElemento("section"),
    botonDictar: crearElemento("button", { hidden: true }),
    etiquetaDictar: crearElemento("span"),
    estado: crearElemento("p"),
    nota: crearElemento("p", { hidden: true }),
    entrada: crearElemento("input"),
    formulario: crearElemento("form"),
    aviso: crearElemento("dialog"),
    avisoAceptar: crearElemento("button"),
    avisoCancelar: crearElemento("button"),
    autoLectura: crearElemento("input"),
    hilo: crearElemento("input", { value: "hilo-1" }),
    botonManosLibres: crearElemento("button", { hidden: true }),
    avisoManos: crearElemento("dialog"),
    manosAceptar: crearElemento("button"),
    manosCancelar: crearElemento("button"),
    ...extra,
  };
  vista.formulario.enviados = 0;
  vista.formulario.requestSubmit = () => {
    vista.formulario.enviados += 1;
  };
  return vista;
}

function clic(elemento) {
  elemento.dispatch("click", { preventDefault() {}, target: elemento });
}

function tecla(documento, key, extra = {}) {
  documento.dispatch("keydown", { key, preventDefault() {}, ...extra });
}

function montar({
  documento,
  almacen,
  reconocimiento,
  sintesis,
  segura = true,
  streaming = true,
  vista = crearVista(),
  reloj = crearReloj(),
} = {}) {
  const doc = documento || crearDocumento();
  const sandbox = cargarVoz({ documento: doc, almacen, reconocimiento, sintesis });
  const api = sandbox.window.CarvajalVoz;
  const controlador = api.createVoiceController({
    vista,
    documento: doc,
    almacen: almacen || sandbox.localStorage,
    reconocimiento: reconocimiento || sandbox.SpeechRecognition || null,
    sintesis: sintesis === false ? null : sintesis || sandbox.speechSynthesis,
    Utterance: sandbox.SpeechSynthesisUtterance,
    reloj,
    segura,
    streaming,
  });
  controlador.mount();
  return { sandbox, api, controlador, vista, reloj, documento: doc };
}

// ---------------------------------------------------------------------------
// T002 · base
// ---------------------------------------------------------------------------

test("T002 el script se carga sin DOM y expone la API sin iniciar nada", () => {
  // Act
  const sandbox = cargarVoz();

  // Assert
  assert.equal(typeof sandbox.window.CarvajalVoz, "object");
  assert.equal(typeof sandbox.window.CarvajalVoz.createVoiceController, "function");
  assert.equal(sandbox.document.documentElement.dataset.voz, undefined);
});

test("T002 el script no requiere dependencias npm ni red", () => {
  // Act
  const fuente = fs.readFileSync(FUENTE, "utf8");

  // Assert: solo APIs del navegador; sin importaciones ni fetch de voz.
  assert.equal(/^\s*import\s/m.test(fuente), false);
  assert.equal(/\brequire\(/.test(fuente), false);
  assert.equal(/fetch\(/.test(fuente), false);
});

test("T002 los controles base se muestran solo con JS y sin soporte explican", () => {
  // Arrange
  const vista = crearVista();

  // Act
  const { controlador } = montar({ reconocimiento: false, sintesis: false, vista });

  // Assert
  assert.equal(vista.raiz.hidden, false);
  assert.equal(vista.botonDictar.hidden, true);
  assert.equal(vista.nota.hidden, false);
  assert.match(vista.nota.textContent, /escribir/i);
  assert.equal(controlador.soporte.dictado, false);
});

test("T002 sin contexto seguro el dictado no se ofrece y se explica", () => {
  // Arrange
  const vista = crearVista();

  // Act
  const { controlador } = montar({ segura: false, vista });

  // Assert
  assert.equal(vista.botonDictar.hidden, true);
  assert.equal(controlador.soporte.dictado, false);
  assert.match(vista.nota.textContent, /HTTPS|segura/i);
});

test("T002 con reconocimiento y contexto seguro el micrófono queda disponible", () => {
  // Arrange
  const vista = crearVista();

  // Act
  const { controlador } = montar({ vista });

  // Assert
  assert.equal(vista.raiz.hidden, false);
  assert.equal(vista.botonDictar.hidden, false);
  assert.equal(vista.botonDictar.getAttribute("type"), "button");
  assert.equal(vista.botonDictar.getAttribute("aria-pressed"), "false");
  assert.equal(vista.botonDictar.getAttribute("aria-label"), "Dictar una pregunta");
  assert.equal(vista.etiquetaDictar.textContent, "Dictar");
  assert.equal(vista.estado.textContent, "");
  assert.equal(controlador.soporte.dictado, true);
});

test("T002 la preferencia de lectura existe pero está apagada por defecto", () => {
  // Arrange
  const vista = crearVista({ autoLectura: crearElemento("input") });

  // Act
  const { controlador } = montar({ vista });

  // Assert
  assert.equal(vista.autoLectura.checked, false);
  assert.equal(controlador.preferencias.autoLectura, false);
});

test("T005 init monta los controles con los selectores reales del documento", () => {
  // Arrange: raíz y formulario como los deja la plantilla.
  const raiz = crearElemento("section", { hidden: true });
  const boton = crearElemento("button", { hidden: true });
  const etiqueta = crearElemento("span");
  const estado = crearElemento("p");
  const nota = crearElemento("p", { hidden: true });
  const formulario = crearFormulario();
  const hijos = {
    "[data-voz-dictar]": boton,
    "[data-voz-dictar-texto]": etiqueta,
    "[data-voz-estado]": estado,
    "[data-voz-nota]": nota,
    "[data-voz-autolectura]": null,
  };
  raiz.querySelector = (selector) => hijos[selector] || null;
  const documento = crearDocumento({
    "[data-voz]": raiz,
    "form[data-chat-stream]": formulario,
    "[data-voz-aviso]": null,
    "[data-voz-aviso-aceptar]": null,
    "[data-voz-aviso-cancelar]": null,
  });

  // Act: el script se ejecuta al cargar y encuentra la raíz.
  const sandbox = cargarVoz({ documento });

  // Assert
  assert.ok(sandbox.window.CarvajalVoz);
  assert.equal(raiz.hidden, false);
  assert.equal(boton.hidden, false);
  assert.equal(boton.getAttribute("aria-label"), "Dictar una pregunta");
  assert.equal(etiqueta.textContent, "Dictar");
  assert.equal(documento.documentElement.dataset.voz, "1");
  assert.equal(documento.documentElement.dataset.vozTts, "1");
  assert.ok(raiz._controladorVoz);
  assert.equal(formulario.enviados, 0);
});

test("T005 init monta aunque localStorage lance al accederse", () => {
  // Arrange: navegador con almacenamiento bloqueado (modo privado estricto).
  const raiz = crearElemento("section", { hidden: true });
  const boton = crearElemento("button", { hidden: true });
  const hijos = {
    "[data-voz-dictar]": boton,
    "[data-voz-dictar-texto]": crearElemento("span"),
    "[data-voz-estado]": crearElemento("p"),
    "[data-voz-nota]": crearElemento("p", { hidden: true }),
    "[data-voz-autolectura]": null,
  };
  raiz.querySelector = (selector) => hijos[selector] || null;
  const documento = crearDocumento({
    "[data-voz]": raiz,
    "form[data-chat-stream]": crearFormulario(),
    "[data-voz-aviso]": null,
    "[data-voz-aviso-aceptar]": null,
    "[data-voz-aviso-cancelar]": null,
  });
  const ventana = {
    isSecureContext: true,
    SpeechRecognition: crearReconocimiento(),
    speechSynthesis: crearSintesis(),
    SpeechSynthesisUtterance: crearUtterance,
  };
  Object.defineProperty(ventana, "localStorage", {
    get() {
      throw new Error("SecurityError: almacenamiento bloqueado");
    },
  });

  // Act: cargar el script con ese window no debe abortar el montaje.
  const sandbox = cargarVoz({ documento, ventana });

  // Assert
  assert.ok(sandbox.window.CarvajalVoz);
  assert.equal(raiz.hidden, false);
  assert.equal(boton.hidden, false);
  assert.ok(raiz._controladorVoz);
  raiz._controladorVoz.preferencias.aceptarAviso();
  assert.equal(raiz._controladorVoz.preferencias.avisoAceptado, true);
});

test("T002 sin síntesis la preferencia de lectura no se ofrece", () => {
  // Arrange
  const vista = crearVista({ autoLectura: crearElemento("input") });

  // Act
  montar({ vista, sintesis: false });

  // Assert
  assert.equal(vista.autoLectura.hidden, true);
});

// ---------------------------------------------------------------------------
// T006–T010 · US1: dictado con revisión
// ---------------------------------------------------------------------------

function iniciarDictado(montaje) {
  clic(montaje.vista.botonDictar);
  if (montaje.vista.aviso.open) clic(montaje.vista.avisoAceptar);
  return montaje.reconocimiento.ultima();
}

test("T006 el primer dictado pide confirmación antes de abrir el micrófono", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;

  // Act
  clic(montaje.vista.botonDictar);

  // Assert: aviso visible y ningún reconocimiento iniciado.
  assert.equal(montaje.vista.aviso.open, true);
  assert.equal(reconocimiento.instancias.length, 0);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");

  // Act: continuar.
  clic(montaje.vista.avisoAceptar);
  const instancia = reconocimiento.ultima();

  // Assert: recién ahora escucha, en español y con estado accesible.
  assert.equal(montaje.vista.aviso.open, false);
  assert.equal(instancia.iniciado, 1);
  assert.equal(instancia.lang, "es-CO");
  assert.equal(instancia.continuous, false);
  assert.equal(instancia.interimResults, true);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "true");
  assert.equal(montaje.vista.etiquetaDictar.textContent, "Detener dictado");
  assert.match(montaje.vista.estado.textContent, /Escuchando/);
  assert.equal(montaje.documento.documentElement.dataset.vozEscuchando, "1");
});

test("T006 cancelar el aviso no abre el micrófono ni recuerda la aceptación", () => {
  // Arrange
  const almacen = crearAlmacen();
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento, almacen });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "borrador intacto";

  // Act
  clic(montaje.vista.botonDictar);
  clic(montaje.vista.avisoCancelar);

  // Assert
  assert.equal(montaje.vista.aviso.open, false);
  assert.equal(reconocimiento.instancias.length, 0);
  assert.equal(montaje.vista.entrada.value, "borrador intacto");
  assert.match(montaje.vista.estado.textContent, /cancelado/i);
  assert.equal(almacen.getItem("carvajal_voz_aviso_dictado"), null);

  // Act: el siguiente intento vuelve a avisar.
  clic(montaje.vista.botonDictar);

  // Assert
  assert.equal(montaje.vista.aviso.open, true);
});

test("T006 la aceptación del aviso se recuerda en el navegador", () => {
  // Arrange
  const almacen = crearAlmacen();
  const primero = montar({ almacen, reconocimiento: crearReconocimiento() });
  primero.reconocimiento = primero.sandbox.SpeechRecognition;

  // Act: aceptar en el primer controlador.
  clic(primero.vista.botonDictar);
  clic(primero.vista.avisoAceptar);

  // Assert
  assert.equal(almacen.getItem("carvajal_voz_aviso_dictado"), "1");

  // Act: nueva página con el mismo almacenamiento.
  const reconocimiento = crearReconocimiento();
  const segundo = montar({ almacen, reconocimiento });
  segundo.reconocimiento = reconocimiento;
  clic(segundo.vista.botonDictar);

  // Assert: sin aviso y con micrófono activo.
  assert.equal(segundo.vista.aviso.open, false);
  assert.equal(reconocimiento.instancias.length, 1);
  assert.equal(reconocimiento.ultima().iniciado, 1);
});

test("T006 el aviso no se restaura como dictado al cargar la página", () => {
  // Arrange: aviso aceptado de una visita anterior.
  const almacen = crearAlmacen({ carvajal_voz_aviso_dictado: "1" });
  const reconocimiento = crearReconocimiento();

  // Act
  const montaje = montar({ almacen, reconocimiento });

  // Assert: cargar no inicia reconocimiento.
  assert.equal(reconocimiento.instancias.length, 0);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");
});

test("T006 el permiso denegado se explica, conserva el borrador y deja escribir", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "pregunta previa";
  const instancia = iniciarDictado(montaje);

  // Act
  instancia.error("not-allowed");
  instancia.fin();

  // Assert
  assert.match(montaje.vista.estado.textContent, /permiso/i);
  assert.equal(montaje.vista.nota.hidden, false);
  assert.match(montaje.vista.nota.textContent, /escribir/i);
  assert.equal(montaje.vista.entrada.value, "pregunta previa");
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");
  assert.equal(montaje.vista.etiquetaDictar.textContent, "Dictar");
  assert.equal(montaje.documento.documentElement.dataset.vozEscuchando, undefined);
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T006 el silencio no envía nada, avisa y conserva el borrador", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "borrador";
  const instancia = iniciarDictado(montaje);

  // Act: solo resultados parciales y fin sin texto final.
  instancia.resultado("vasos de", { final: false });
  instancia.fin();

  // Assert
  assert.match(montaje.vista.estado.textContent, /voz|oír|silencio/i);
  assert.equal(montaje.vista.entrada.value, "borrador");
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T006 el micrófono se detiene con stop() a los 60 segundos y aprovecha el final", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  const instancia = iniciarDictado(montaje);

  // Act: antes del límite sigue escuchando.
  montaje.reloj.avanzar(59999);
  assert.equal(instancia.detenido, 0);

  // Act: al límite se pide el final, no se descarta.
  montaje.reloj.avanzar(1);
  assert.equal(instancia.detenido, 1);
  assert.equal(instancia.abortado, 0);
  instancia.resultado("necesito vasos de café");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "necesito vasos de café");
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T006 el texto respeta el tope de 500 sin cortar un par sustituto", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "x".repeat(498);
  montaje.vista.entrada.selectionStart = 498;
  montaje.vista.entrada.selectionEnd = 498;
  const instancia = iniciarDictado(montaje);

  // Act: dos emojis (dos pares sustitutos de 2 unidades cada uno).
  instancia.resultado("😀😀");
  instancia.fin();

  // Assert: entra un solo emoji completo, sin un sustituto suelto.
  const valor = montaje.vista.entrada.value;
  assert.equal(valor.length, 500);
  assert.equal(valor.endsWith("😀"), true);
  assert.equal(Array.from(valor.slice(498)).length, 1);
  assert.match(montaje.vista.estado.textContent, /recort/i);
});

test("T006 con el borrador lleno no se inserta nada y se avisa", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "y".repeat(500);
  const instancia = iniciarDictado(montaje);

  // Act
  instancia.resultado("texto nuevo");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "y".repeat(500));
  assert.match(montaje.vista.estado.textContent, /500|recort/i);
});

test("T010 el dictado se inserta en el cursor y no envía el formulario", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "vasos  tapas";
  montaje.vista.entrada.selectionStart = 6;
  montaje.vista.entrada.selectionEnd = 6;
  const instancia = iniciarDictado(montaje);

  // Act: se inserta en el cursor, no al final del texto.
  instancia.resultado("y");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "vasos y tapas");
  assert.equal(montaje.vista.entrada.selectionStart, 7);
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");
});

test("T010 reemplaza la selección en lugar de duplicarla", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "vasos de 12 oz";
  montaje.vista.entrada.selectionStart = 9;
  montaje.vista.entrada.selectionEnd = 14;
  const instancia = iniciarDictado(montaje);

  // Act
  instancia.resultado("16 onzas");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "vasos de 16 onzas");
});

test("T010 normaliza cantidades inequívocas y deja el texto ambiguo intacto", () => {
  // Arrange
  const { normalizarCantidades } = cargarVoz().window.CarvajalVoz;

  // Act / Assert: junto a unidades y productos comerciales.
  assert.equal(
    normalizarCantidades("necesito doce onzas para café"),
    "necesito 12 onzas para café",
  );
  assert.equal(normalizarCantidades("cinco mil vasos"), "5.000 vasos");
  assert.equal(normalizarCantidades("treinta y dos contenedores"), "32 contenedores");
  assert.equal(normalizarCantidades("mil doscientos platos"), "1.200 platos");
  assert.equal(normalizarCantidades("veintiún tapas"), "21 tapas");

  // Assert: sin unidad comercial no se toca (fechas, años, lenguaje suelto).
  assert.equal(normalizarCantidades("el doce de octubre"), "el doce de octubre");
  assert.equal(normalizarCantidades("dos mil veinticuatro"), "dos mil veinticuatro");
  assert.equal(normalizarCantidades("un vaso"), "un vaso");
});

test("T010 el dictado completo inserta la cantidad normalizada en el borrador", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  const instancia = iniciarDictado(montaje);

  // Act: el reconocimiento entrega números con palabras.
  instancia.resultado("necesito doce onzas de vasos");
  instancia.fin();

  // Assert: se aplica en el punto de inserción, no solo en la función aislada.
  assert.equal(montaje.vista.entrada.value, "necesito 12 onzas de vasos");

  // Act: otra pregunta con cantidad.
  montaje.vista.entrada.value = "";
  montaje.vista.entrada.selectionStart = 0;
  montaje.vista.entrada.selectionEnd = 0;
  const segunda = iniciarDictado(montaje);
  segunda.resultado("necesito cinco mil vasos");
  segunda.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "necesito 5.000 vasos");
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T006 los callbacks tardíos de un ciclo cancelado no escriben ni hablan", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "";
  const primera = iniciarDictado(montaje);

  // Act: cancelar con Escape y abrir un ciclo nuevo.
  tecla(montaje.documento, "Escape");
  assert.equal(primera.abortado, 1);
  assert.equal(montaje.vista.entrada.value, "");
  const segunda = iniciarDictado(montaje);

  // Act: el ciclo viejo responde tarde; el nuevo responde ahora.
  primera.resultado("viejo");
  primera.fin();
  segunda.resultado("nuevo");
  segunda.fin();

  // Assert
  assert.equal(montaje.vista.entrada.value, "nuevo");
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T006 un error de red se explica sin perder el borrador", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;
  montaje.vista.entrada.value = "respaldo";
  const instancia = iniciarDictado(montaje);

  // Act
  instancia.error("network");
  instancia.fin();

  // Assert
  assert.match(montaje.vista.estado.textContent, /dictado|red|conexión|intenta/i);
  assert.equal(montaje.vista.entrada.value, "respaldo");
  assert.equal(montaje.vista.formulario.enviados, 0);
});

// ---------------------------------------------------------------------------
// T012–T017 · US2: lectura de respuestas
// ---------------------------------------------------------------------------

function crearTarjetaFalsa({ nombre, cajas, precio, fecha } = {}) {
  const campos = {
    '[data-campo="producto-nombre"]': nombre,
    '[data-campo="producto-cajas"]': cajas,
    '[data-campo="producto-precio"]': precio,
    '[data-campo="producto-fecha"]': fecha,
  };
  return {
    querySelector(selector) {
      return campos[selector] === undefined || campos[selector] === null
        ? null
        : { textContent: String(campos[selector]) };
    },
  };
}

function crearTurnoFalso({ texto = "", tarjetas = [], citas = [], rol = "assistant" } = {}) {
  const botones = {
    escuchar: crearElemento("button"),
    pausar: crearElemento("button", { hidden: true }),
    reanudar: crearElemento("button", { hidden: true }),
    detener: crearElemento("button", { hidden: true }),
  };
  botones.escuchar.setAttribute("aria-pressed", "false");
  return {
    dataset: {},
    className: `turno turno-${rol}`,
    _botones: botones,
    querySelector(selector) {
      if (selector === ".texto") return { textContent: texto };
      const accion = /^\[data-voz-accion="(\w+)"\]$/.exec(selector);
      if (accion) return botones[accion[1]] || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".productos .producto") return tarjetas;
      if (selector === '.citas [data-campo="title"]') {
        return citas.map((cita) => ({ textContent: cita.titulo }));
      }
      return [];
    },
  };
}

function activarAutoLectura(montaje) {
  montaje.vista.autoLectura.checked = true;
  montaje.vista.autoLectura.dispatch("change", {});
}

test("T012 la lectura manual usa texto, tarjetas y citas visibles sin URLs ni SKU", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({
    texto: "Tenemos vasos de papel [1] para café.",
    tarjetas: [
      crearTarjetaFalsa({
        nombre: "Vaso papel 12 oz bebida caliente",
        cajas: "5",
        precio: "7250.00 MXN",
        fecha: "capturada el 2026-10-23",
      }),
    ],
    citas: [{ titulo: "Manual de empaques" }],
  });

  // Act
  const ok = montaje.controlador.leerTurno(turno);

  // Assert: la síntesis encadena los segmentos en orden.
  assert.equal(ok, true);
  assert.equal(sintesis.locuciones[0].text, "Tenemos vasos de papel para café.");
  sintesis.terminar();
  const tarjeta = sintesis.locuciones[1].text;
  assert.match(tarjeta, /Vaso papel 12 oz bebida caliente/);
  assert.match(tarjeta, /Cajas: 5/);
  assert.match(tarjeta, /Precio: 7250\.00 MXN/);
  assert.match(tarjeta, /capturada el 2026-10-23/);
  sintesis.terminar();
  assert.equal(sintesis.locuciones[2].text, "Según Manual de empaques.");
  const textos = sintesis.locuciones.map((locucion) => locucion.text);
  assert.equal(
    textos.every((texto) => !/https?:|FX-|www\./.test(texto)),
    true,
  );
  assert.equal(sintesis.locuciones[0].lang, "es-CO");
  assert.equal(sintesis.locuciones[0].voice.lang, "es-CO");
  assert.equal(turno._botones.escuchar.getAttribute("aria-pressed"), "true");
  assert.equal(turno._botones.detener.hidden, false);
});

test("T012 una tarjeta sin precio no adquiere precio al leerla", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({
    texto: "Referencias del catálogo.",
    tarjetas: [crearTarjetaFalsa({ nombre: "Vaso PS 12 oz", cajas: "4" })],
  });

  // Act
  montaje.controlador.leerTurno(turno);
  sintesis.terminar();

  // Assert
  const tarjeta = sintesis.locuciones[1].text;
  assert.match(tarjeta, /Vaso PS 12 oz/);
  assert.match(tarjeta, /Cajas: 4/);
  assert.equal(/Precio:/.test(tarjeta), false);
  assert.equal(/MXN|COP/.test(tarjeta), false);
});

test("T012 con la lista de voces vacía se intenta es-CO sin bloquear", () => {
  // Arrange: Chrome aún no cargó getVoices().
  const sintesis = crearSintesis({ voces: [] });
  const montaje = montar({ sintesis });

  // Act
  const ok = montaje.controlador.leerTurno(crearTurnoFalso({ texto: "Hola." }));

  // Assert
  assert.equal(ok, true);
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(sintesis.locuciones[0].lang, "es-CO");
  assert.equal(sintesis.locuciones[0].voice, null);
});

test("T012 sin voz en español se explica y no se reproduce nada", () => {
  // Arrange
  const sintesis = crearSintesis({ voces: [{ name: "Alex", lang: "en-US" }] });
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Hola." });

  // Act
  const ok = montaje.controlador.leerTurno(turno);

  // Assert
  assert.equal(ok, false);
  assert.equal(sintesis.locuciones.length, 0);
  assert.match(montaje.vista.estado.textContent, /voz en español/i);
  assert.equal(montaje.vista.nota.hidden, false);
});

test("T012 con autolectura apagada el turno completado no habla y «Escuchar» sigue", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Respuesta completa." });
  assert.equal(montaje.vista.autoLectura.checked, false);

  // Act: evento de fin de stream con la preferencia apagada.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 0);

  // Act: lectura manual.
  montaje.controlador.leerTurno(turno);

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
});

test("T012 la autolectura solo suena tras done, una vez por turno y si el hilo es visible", () => {
  // Arrange
  const almacen = crearAlmacen();
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis, almacen });
  activarAutoLectura(montaje);
  assert.equal(almacen.getItem("carvajal_voz_autolectura"), "1");
  const turno = crearTurnoFalso({ texto: "Listo." });

  // Act: evento de un hilo distinto y de un turno de usuario.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: crearTurnoFalso({ texto: "Otro hilo." }), threadId: "hilo-2" },
  });
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: crearTurnoFalso({ texto: "Mío.", rol: "user" }), threadId: "hilo-1" },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 0);

  // Act: el turno visible termina.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(sintesis.locuciones[0].text, "Listo.");

  // Act: el mismo turno vuelve a notificarse.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });

  // Assert: no se duplica la lectura automática.
  assert.equal(sintesis.locuciones.length, 1);
});

test("T012 la autolectura no suena con la pestaña oculta ni al cargar el historial", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  activarAutoLectura(montaje);
  montaje.documento.visibilityState = "hidden";

  // Act: no hay evento de historial; solo se monta y se oculta la pestaña.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: crearTurnoFalso({ texto: "No debe sonar." }), threadId: "hilo-1" },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.documento.documentElement.dataset.vozTts, "1");
});

test("T012 pausar, reanudar y detener actualizan la síntesis y los controles", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Primera parte.", citas: [{ titulo: "Fuente" }] });
  montaje.controlador.leerTurno(turno);
  assert.equal(turno._botones.pausar.hidden, false);
  assert.equal(turno._botones.reanudar.hidden, true);

  // Act
  montaje.controlador.pausarLectura();

  // Assert
  assert.equal(sintesis.paused, true);
  assert.equal(turno._botones.pausar.hidden, true);
  assert.equal(turno._botones.reanudar.hidden, false);
  assert.match(montaje.vista.estado.textContent, /pausa/i);

  // Act
  montaje.controlador.reanudarLectura();

  // Assert
  assert.equal(sintesis.paused, false);
  assert.equal(turno._botones.pausar.hidden, false);
  assert.equal(turno._botones.reanudar.hidden, true);

  // Act
  montaje.controlador.detenerLectura();

  // Assert
  assert.equal(sintesis.cancelados, 1);
  assert.equal(turno._botones.escuchar.getAttribute("aria-pressed"), "false");
  assert.equal(turno._botones.detener.hidden, true);
  assert.equal(montaje.controlador.estado.leyendo, false);
});

test("T012 dictar detiene la lectura y un callback tardío no avanza el segmento", () => {
  // Arrange
  const sintesis = crearSintesis();
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ sintesis, reconocimiento });
  montaje.reconocimiento = reconocimiento;
  const turno = crearTurnoFalso({ texto: "Uno.", citas: [{ titulo: "Dos" }] });
  montaje.controlador.leerTurno(turno);
  assert.equal(sintesis.locuciones.length, 1);

  // Act: el micrófono se abre mientras se lee.
  const instancia = iniciarDictado(montaje);

  // Assert: la lectura se cancela y no arranca el segundo segmento.
  assert.equal(sintesis.cancelados, 1);
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(montaje.controlador.estado.leyendo, false);
  assert.ok(instancia.iniciado === 1);
});

test("T012 ocultar la pestaña detiene la lectura sin reanudarla", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Leyendo." });
  montaje.controlador.leerTurno(turno);

  // Act
  montaje.documento.visibilityState = "hidden";
  montaje.documento.dispatch("visibilitychange", {});

  // Assert
  assert.equal(sintesis.cancelados, 1);
  assert.equal(montaje.controlador.estado.leyendo, false);
  assert.equal(turno._botones.detener.hidden, true);
});

test("T012 la preferencia de autolectura se recuerda entre páginas y sigue apagada sin tocar", () => {
  // Arrange
  const almacen = crearAlmacen();
  const primero = montar({ almacen });
  assert.equal(primero.vista.autoLectura.checked, false);

  // Act: activar en la primera página.
  activarAutoLectura(primero);
  const segundo = montar({ almacen });

  // Assert
  assert.equal(segundo.vista.autoLectura.checked, true);
  assert.equal(segundo.controlador.preferencias.autoLectura, true);
});

test("T012 si el navegador bloquea el audio, «Escuchar» sigue disponible", () => {
  // Arrange: la síntesis acepta la locución pero nunca emite start.
  const sintesis = crearSintesis();
  sintesis.speak = function (locucion) {
    this.locuciones.push(locucion);
  };
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Sin reproducción automática." });

  // Act
  const ok = montaje.controlador.leerTurno(turno);

  // Assert: no se marca como leyendo ni se oculta el botón manual.
  assert.equal(ok, true);
  assert.equal(montaje.controlador.estado.leyendo, false);
  assert.equal(turno._botones.escuchar.getAttribute("aria-pressed"), "false");
  assert.equal(turno._botones.detener.hidden, true);
});

test("T017 la lectura prefiere es-CO y cae a otra voz española", () => {
  // Arrange
  const { elegirVoz } = cargarVoz().window.CarvajalVoz;
  const voces = [
    { name: "Alex", lang: "en-US" },
    { name: "Paulina", lang: "es-MX" },
    { name: "Mónica", lang: "es-CO" },
    { name: "Helena", lang: "es-ES" },
  ];

  // Act / Assert
  assert.equal(elegirVoz(voces).name, "Mónica");
  assert.equal(elegirVoz([voces[0], voces[1]]).name, "Paulina");
  assert.equal(elegirVoz([voces[0], voces[3]]).name, "Helena");
  assert.equal(elegirVoz([voces[0]]), null);
});

test("T017 el texto hablado omite SKU, URLs y códigos técnicas aunque estén visibles", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({
    texto:
      "Mira el SKU FX-VASO-PAPEL-12 en https://ejemplo.test/vaso y www.ejemplo.test. Tenemos vasos [1] de papel.",
    citas: [{ titulo: "Manual de empaques" }],
  });

  // Act
  montaje.controlador.leerTurno(turno);

  // Assert: la prosa se conserva y los códigos desaparecen.
  const prosa = sintesis.locuciones[0].text;
  assert.equal(/FX-|https?:|www\.|SKU/i.test(prosa), false, prosa);
  assert.match(prosa, /Tenemos vasos de papel/);
  assert.equal(/\b\[1\]\b/.test(prosa), false);

  // Act / Assert: la utilidad también limpia por sí sola.
  const { limpiarTextoHablado } = cargarVoz().window.CarvajalVoz;
  const limpio = limpiarTextoHablado("SKU: FX-TAPA-9 y https://x.test/a");
  assert.equal(/SKU|FX-|https?:/i.test(limpio), false, limpio);
});

test("T017 la autolectura tiene un control visible y apagado en la plantilla", () => {
  // Arrange: raíz como la deja chat.html, con la casilla y su etiqueta.
  const raiz = crearElemento("section", { hidden: true });
  const casilla = crearElemento("input", { checked: false });
  const campo = crearElemento("label", { hidden: true });
  const boton = crearElemento("button", { hidden: true });
  const hijos = {
    "[data-voz-dictar]": boton,
    "[data-voz-dictar-texto]": crearElemento("span"),
    "[data-voz-estado]": crearElemento("p"),
    "[data-voz-nota]": crearElemento("p", { hidden: true }),
    "[data-voz-autolectura]": casilla,
    "[data-voz-autolectura-campo]": campo,
  };
  raiz.querySelector = (selector) => hijos[selector] || null;
  const documento = crearDocumento({
    "[data-voz]": raiz,
    "form[data-chat-stream]": crearFormulario(),
    "[data-voz-aviso]": null,
    "[data-voz-aviso-aceptar]": null,
    "[data-voz-aviso-cancelar]": null,
  });

  // Act
  cargarVoz({ documento });

  // Assert
  assert.equal(campo.hidden, false);
  assert.equal(casilla.checked, false);
});

test("T016 una tarjeta con varias ofertas lee cada precio con su calificador y fecha", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const oferta = (precio, detalle, fecha) => ({
    querySelector(selector) {
      if (selector === '[data-campo="producto-precio"]') return { textContent: precio };
      if (selector === ".producto-precio-detalle") return { textContent: detalle };
      if (selector === '[data-campo="producto-fecha"]') return { textContent: fecha };
      return null;
    },
  });
  const tarjeta = {
    querySelector(selector) {
      if (selector === '[data-campo="producto-nombre"]') return { textContent: "Vaso PS 12 oz" };
      if (selector === '[data-campo="producto-cajas"]') return { textContent: "4" };
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".producto-oferta") {
        return [
          oferta("54000 COP", "por caja", "capturado el 2026-11-18"),
          oferta("60000 COP", "por caja", "capturado el 2026-11-19"),
        ];
      }
      return [];
    },
  };

  // Act
  montaje.controlador.leerTurno(crearTurnoFalso({ texto: "Dos ofertas.", tarjetas: [tarjeta] }));
  sintesis.terminar();

  // Assert
  const primera = sintesis.locuciones[1].text;
  sintesis.terminar();
  const segunda = sintesis.locuciones[2].text;
  assert.match(primera, /54000 COP, por caja/);
  assert.match(primera, /2026-11-18/);
  assert.match(segunda, /60000 COP, por caja/);
  assert.match(segunda, /2026-11-19/);
});

test("T016 el filtro de códigos también cubre mayúsculas mezcladas", () => {
  // Arrange
  const { limpiarTextoHablado } = cargarVoz().window.CarvajalVoz;

  // Act
  const limpio = limpiarTextoHablado("sku: FX-Vaso-Papel-12 y código 3");

  // Assert
  assert.equal(/FX-Vaso|sku/i.test(limpio), false, limpio);
  assert.match(limpio, /código 3/);
});

// ---------------------------------------------------------------------------
// T018–T021 · US3: conversación manos libres
// ---------------------------------------------------------------------------

function activarManosLibres(montaje) {
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  if (montaje.vista.aviso.open) clic(montaje.vista.avisoAceptar);
  return montaje.reconocimiento.ultima();
}

function montarManos({ almacen, sintesis, reloj, vistas } = {}) {
  const reconocimiento = crearReconocimiento();
  const montaje = montar({
    reconocimiento,
    almacen,
    sintesis: sintesis || crearSintesis(),
    reloj,
    ...vistas,
  });
  montaje.reconocimiento = reconocimiento;
  return montaje;
}

test("T018 manos libres no se activa con borrador previo y conserva el texto", () => {
  // Arrange
  const montaje = montarManos();
  montaje.vista.entrada.value = "texto previo";

  // Act
  clic(montaje.vista.botonManosLibres);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.avisoManos.open, false);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  assert.equal(montaje.vista.entrada.value, "texto previo");
  assert.match(montaje.vista.nota.textContent, /vacía el borrador|borrador/i);
});

test("T019 la activación avisa del autoenvío, es temporal y exige las tres capacidades", () => {
  // Arrange: sin streaming el botón ni se ofrece.
  const sinStreaming = montarManos({ vistas: { streaming: false } });
  assert.equal(sinStreaming.vista.botonManosLibres.hidden, true);
  assert.equal(sinStreaming.controlador.soporte.manosLibres, false);

  // Arrange: con las tres capacidades, el borrador está vacío.
  const almacen = crearAlmacen();
  const montaje = montarManos({ almacen });

  // Act: activar y aceptar el aviso de autoenvío.
  clic(montaje.vista.botonManosLibres);

  // Assert: primero explica que enviará sin revisión.
  assert.equal(montaje.vista.avisoManos.open, true);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  clic(montaje.vista.manosAceptar);
  if (montaje.vista.aviso.open) clic(montaje.vista.avisoAceptar);

  // Assert: activo, visible y con micrófono abierto.
  assert.equal(montaje.controlador.manosLibres.activo, true);
  assert.equal(montaje.vista.botonManosLibres.getAttribute("aria-pressed"), "true");
  assert.equal(montaje.documento.documentElement.dataset.vozManos, "1");
  assert.match(montaje.vista.estado.textContent, /manos libres/i);
  assert.equal(montaje.reconocimiento.ultima().iniciado, 1);
  assert.equal(almacen.getItem("carvajal_voz_manos_libres"), null);

  // Act: recargar la página con el mismo almacenamiento.
  const recarga = montarManos({ almacen });

  // Assert: el modo no se restaura.
  assert.equal(recarga.controlador.manosLibres.activo, false);
});

test("T020 un resultado final se envía una sola vez y conserva el hilo", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act
  instancia.resultado("necesito doce onzas de vasos");
  instancia.fin();

  // Assert: mismo formulario, mismo hilo y una sola petición.
  assert.equal(montaje.vista.formulario.enviados, 1);
  assert.equal(montaje.vista.entrada.value, "necesito 12 onzas de vasos");
  assert.equal(montaje.vista.hilo.value, "hilo-1");
  assert.equal(montaje.vista.formulario.dataset.vozOrigen, "manos-libres");

  // Act: el navegador repite el fin del mismo ciclo.
  instancia.fin();
  instancia.resultado("otra vez", { indice: 1 });

  // Assert: no hay segundo envío.
  assert.equal(montaje.vista.formulario.enviados, 1);
});

test("T018 «terminar» como orden aislada apaga el modo sin enviarse", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act
  instancia.resultado("terminar");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.match(montaje.vista.estado.textContent, /terminar/i);
});

test("T018 diez segundos sin voz apagan el modo y descartan el micrófono", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act: un resultado intermedio reinicia la cuenta.
  montaje.reloj.avanzar(9000);
  instancia.resultado("necesito", { final: false });
  montaje.reloj.avanzar(9000);
  assert.equal(montaje.controlador.manosLibres.activo, true);

  // Act: sin más voz se cumple el silencio.
  montaje.reloj.avanzar(1000);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(instancia.abortado, 1);
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.match(montaje.vista.estado.textContent, /10 segundos|silencio/i);
});

test("T018 escribir desactiva el modo, conserva el borrador e ignora el resultado tardío", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act: la persona escribe con el modo activo.
  montaje.vista.entrada.value = "lo escribo yo";
  montaje.vista.entrada.dispatch("input", {});
  instancia.resultado("texto tardío");
  instancia.fin();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.entrada.value, "lo escribo yo");
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.match(montaje.vista.estado.textContent, /escribiste|escribir/i);
});

test("T018 un error de dictado apaga el modo sin reenviar ni hablar", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  const instancia = activarManosLibres(montaje);

  // Act
  instancia.error("network");
  instancia.fin();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.reconocimiento.instancias.length, 1);
});

test("T018 ocultar la pestaña apaga el modo y la lectura", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  activarManosLibres(montaje);

  // Act
  montaje.documento.visibilityState = "hidden";
  montaje.documento.dispatch("visibilitychange", {});

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(sintesis.cancelados, 0);
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("T018 tras leer la respuesta se vuelve a escuchar solo si la lectura termina normal", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  const instancia = activarManosLibres(montaje);
  instancia.resultado("¿y si son 4500?");
  instancia.fin();
  assert.equal(montaje.vista.formulario.enviados, 1);

  // Act: llega el turno completo del envío manos libres.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "Con 4500 unidades…" }),
      threadId: "hilo-1",
      handsFree: true,
    },
  });

  // Assert: se lee y no se abre el micrófono mientras lee.
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(montaje.reconocimiento.instancias.length, 1);

  // Act: la lectura termina normalmente.
  sintesis.terminar();

  // Assert: vuelve a escuchar en un ciclo nuevo.
  assert.equal(montaje.reconocimiento.instancias.length, 2);
  assert.equal(montaje.reconocimiento.ultima().iniciado, 1);
  assert.equal(montaje.controlador.manosLibres.activo, true);
});

test("T018 detener la lectura a mano apaga el modo sin reabrir el micrófono", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  const instancia = activarManosLibres(montaje);
  instancia.resultado("pregunta");
  instancia.fin();
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "respuesta" }),
      threadId: "hilo-1",
      handsFree: true,
    },
  });

  // Act
  montaje.controlador.detenerLectura();

  // Assert
  assert.equal(sintesis.cancelados, 1);
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.reconocimiento.instancias.length, 1);
});

test("T021 un fallo del stream apaga el modo y no reenvía el turno", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);
  instancia.resultado("pregunta");
  instancia.fin();

  // Act: el adaptador de streaming devuelve el texto y notifica el fallo.
  montaje.vista.entrada.value = "pregunta";
  montaje.documento.dispatch("chat:turn-failed", {
    detail: { handsFree: true, threadId: "hilo-1" },
  });

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.formulario.enviados, 1);
  assert.equal(montaje.vista.entrada.value, "pregunta");
  assert.match(montaje.vista.estado.textContent, /fall|error|revis/i);
});

test("T018 sin voz española las manos libres no se activan", () => {
  // Arrange
  const sintesis = crearSintesis({ voces: [{ name: "Alex", lang: "en-US" }] });
  const montaje = montarManos({ sintesis });

  // Act
  clic(montaje.vista.botonManosLibres);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.avisoManos.open, false);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  assert.match(montaje.vista.nota.textContent, /voz en español/i);
});

test("T018 «quiero terminar» no es la orden aislada y se envía una vez", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act
  instancia.resultado("quiero terminar con la compra");
  instancia.fin();

  // Assert
  assert.equal(montaje.vista.formulario.enviados, 1);
  assert.equal(montaje.controlador.manosLibres.activo, true);
  assert.equal(montaje.vista.entrada.value, "quiero terminar con la compra");
});

test("T019 manos libres no se ofrece sin síntesis", () => {
  // Arrange
  const montaje = montarManos({ vistas: { sintesis: false } });

  // Act / Assert
  assert.equal(montaje.vista.botonManosLibres.hidden, true);
  assert.equal(montaje.controlador.soporte.manosLibres, false);
  clic(montaje.vista.botonManosLibres);
  assert.equal(montaje.controlador.manosLibres.activo, false);
});

test("T018 Escape durante manos libres apaga el modo sin enviar", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);

  // Act
  tecla(montaje.documento, "Escape");
  instancia.resultado("texto tardío");
  instancia.fin();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.equal(montaje.vista.entrada.value, "");
});

test("T018 escribir mientras se espera la respuesta también apaga el modo", () => {
  // Arrange
  const montaje = montarManos();
  const instancia = activarManosLibres(montaje);
  instancia.resultado("primera pregunta");
  instancia.fin();
  assert.equal(montaje.controlador.manosLibres.activo, true);

  // Act: la persona escribe antes de que llegue la respuesta.
  montaje.vista.entrada.value = "mejor lo escribo";
  montaje.vista.entrada.dispatch("input", {});

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.entrada.value, "mejor lo escribo");
  assert.equal(montaje.vista.formulario.enviados, 1);
});

test("T018 un turno completo que no es de manos libres no se lee solo", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  const instancia = activarManosLibres(montaje);
  instancia.resultado("pregunta");
  instancia.fin();

  // Act
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "de un envío manual" }),
      threadId: "hilo-1",
      handsFree: false,
    },
  });

  // Assert: un turno ajeno no deja el modo colgado sin escucha.
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.reconocimiento.instancias.length, 1);
  assert.equal(montaje.controlador.manosLibres.activo, false);
});

test("T018 un turno de otro hilo apaga el modo sin hablar", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  const instancia = activarManosLibres(montaje);
  instancia.resultado("pregunta");
  instancia.fin();

  // Act
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "otro hilo" }),
      threadId: "hilo-2",
      handsFree: true,
    },
  });

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(sintesis.locuciones.length, 0);
  assert.match(montaje.vista.estado.textContent, /hilo/i);
});

test("T018 activar manos libres durante la lectura se rechaza con motivo", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarManos({ sintesis });
  montaje.controlador.leerTurno(crearTurnoFalso({ texto: "leyendo" }));
  assert.equal(montaje.controlador.estado.leyendo, true);

  // Act
  clic(montaje.vista.botonManosLibres);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.avisoManos.open, false);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  assert.match(montaje.vista.estado.textContent, /lectura/i);
});

test("T018 activar manos libres mientras se dicta no deja dos micrófonos", () => {
  // Arrange: dictado manual ya en curso.
  const montaje = montarManos();
  const manual = iniciarDictado(montaje);
  assert.equal(manual.iniciado, 1);

  // Act: activar manos libres con el borrador vacío.
  clic(montaje.vista.botonManosLibres);
  clic(montaje.vista.manosAceptar);

  // Assert: el ciclo anterior se abortó y solo queda el nuevo.
  assert.equal(manual.abortado, 1);
  assert.equal(montaje.reconocimiento.instancias.length, 2);
  assert.equal(montaje.reconocimiento.ultima().iniciado, 1);
  assert.equal(montaje.controlador.manosLibres.activo, true);
  assert.equal(montaje.controlador.estado.escuchando, true);
  assert.equal(/error/i.test(montaje.vista.estado.textContent), false);
});


test("T018 el reloj se invoca con el window del navegador, no como método ajeno", () => {
  // Arrange: un setTimeout que solo acepta `this === window`, como Chrome.
  const documento = crearDocumento();
  const sandbox = cargarVoz({ documento });
  sandbox.setTimeout = function () {
    // El global del navegador tiene `document`; el objeto reloj no.
    if (!this || this.document !== sandbox.document) throw new TypeError("Illegal invocation");
    return 0;
  };
  const vista = crearVista();
  const controlador = sandbox.window.CarvajalVoz.createVoiceController({
    vista,
    documento,
    almacen: sandbox.localStorage,
    reconocimiento: sandbox.SpeechRecognition,
    sintesis: sandbox.speechSynthesis,
    Utterance: sandbox.SpeechSynthesisUtterance,
    segura: true,
    streaming: true,
    // Sin reloj explícito: debe usar el del navegador correctamente.
  });
  controlador.mount();
  vista.entrada.value = "";

  // Act / Assert: activar manos libres arranca el temporizador de silencio.
  assert.doesNotThrow(() => {
    clic(vista.botonManosLibres);
    clic(vista.manosAceptar);
    clic(vista.avisoAceptar);
  });
  assert.equal(controlador.manosLibres.activo, true);

});

test("T002 el script se carga sin window (SSR o runner) sin romper", () => {
  // Arrange: contexto con document pero sin window.
  const sandbox = { document: { querySelector: () => null }, console };
  vm.createContext(sandbox);

  // Act
  assert.doesNotThrow(() => {
    vm.runInContext(fs.readFileSync(FUENTE, "utf8"), sandbox, { filename: "chat-voice.js" });
  });

  // Assert
  assert.equal(typeof sandbox.CarvajalVoz, "object");
});

test("T002 montar dos veces no duplica listeners ni micrófonos", () => {
  // Arrange
  const reconocimiento = crearReconocimiento();
  const montaje = montar({ reconocimiento });
  montaje.reconocimiento = reconocimiento;

  // Act: un remontaje accidental.
  montaje.controlador.mount();
  clic(montaje.vista.botonDictar);
  clic(montaje.vista.avisoAceptar);

  // Assert
  assert.equal(reconocimiento.instancias.length, 1);
  assert.equal(reconocimiento.ultima().iniciado, 1);
});

test("T012 sin voz el turno no queda marcado y se auto-lee cuando aparece", () => {
  // Arrange: primero solo hay voz inglesa.
  const sintesis = crearSintesis({ voces: [{ name: "Alex", lang: "en-US" }] });
  const montaje = montar({ sintesis });
  activarAutoLectura(montaje);
  const turno = crearTurnoFalso({ texto: "Respuesta." });
  const evento = { detail: { turno, threadId: "hilo-1" } };

  // Act: primer intento sin voz española.
  montaje.documento.dispatch("chat:turn-complete", evento);

  // Assert: no habla y el turno queda reintentable.
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(turno.dataset.vozAutoLeido, undefined);

  // Act: el navegador carga las voces y el mismo turno se notifica otra vez.
  sintesis.getVoices = () => [{ name: "Mónica", lang: "es-CO" }];
  montaje.documento.dispatch("chat:turn-complete", evento);

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(turno.dataset.vozAutoLeido, "1");
});

test("T012 si el autoplay bloquea la locución el turno no queda marcado", () => {
  // Arrange: speak no dispara onstart (política del navegador).
  const sintesis = crearSintesis();
  sintesis.speak = function (locucion) {
    this.locuciones.push(locucion);
  };
  const montaje = montar({ sintesis });
  activarAutoLectura(montaje);
  const turno = crearTurnoFalso({ texto: "Respuesta." });

  // Act
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });

  // Assert
  assert.equal(turno.dataset.vozAutoLeido, undefined);
});

test("T012 la lectura manual deja el turno disponible para otra autolectura", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  activarAutoLectura(montaje);
  const turno = crearTurnoFalso({ texto: "Respuesta." });
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });
  assert.equal(turno.dataset.vozAutoLeido, "1");
  sintesis.terminar();

  // Act: la persona vuelve a escuchar a mano.
  montaje.controlador.leerTurno(turno);

  // Assert: el marcador se limpia y una notificación posterior puede leerlo.
  assert.equal(turno.dataset.vozAutoLeido, undefined);
  sintesis.terminar();
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1" },
  });
  assert.equal(sintesis.locuciones.length, 3);
});

test("T019 cancelar el aviso del micrófono no deja manos libres colgado", () => {
  // Arrange: primera visita, sin aviso de dictado aceptado.
  const montaje = montarManos();
  montaje.vista.entrada.value = "";

  // Act: activar y cancelar el aviso de dictado.
  clic(montaje.vista.botonManosLibres);
  clic(montaje.vista.manosAceptar);
  clic(montaje.vista.avisoCancelar);

  // Assert: el modo se apaga y no hay micrófono abierto.
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  assert.equal(
    montaje.vista.botonManosLibres.getAttribute("aria-pressed"),
    "false",
  );
});
