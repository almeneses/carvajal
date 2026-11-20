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
        this._emitir("end", {});
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
  return {
    raiz: crearElemento("section"),
    botonDictar: crearElemento("button", { hidden: true }),
    etiquetaDictar: crearElemento("span"),
    estado: crearElemento("p"),
    nota: crearElemento("p", { hidden: true }),
    ...extra,
  };
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
  const sandbox = cargarVoz({ documento, almacen, reconocimiento, sintesis });
  const api = sandbox.window.CarvajalVoz;
  const controlador = api.createVoiceController({
    vista,
    documento: documento || crearDocumento(),
    almacen: almacen || sandbox.localStorage,
    reconocimiento: reconocimiento || sandbox.SpeechRecognition || null,
    sintesis: sintesis === false ? null : sintesis || sandbox.speechSynthesis,
    Utterance: sandbox.SpeechSynthesisUtterance,
    reloj,
    segura,
    streaming,
  });
  controlador.mount();
  return { sandbox, api, controlador, vista, reloj };
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
