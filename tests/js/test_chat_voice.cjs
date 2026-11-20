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
});
