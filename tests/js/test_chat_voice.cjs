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
    AbortController,
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
  servidor = null,
  ventana = null,
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
    servidor,
    ventana,
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

test("T002 el script no requiere dependencias npm y solo llama a la voz del propio sitio", () => {
  // Act
  const fuente = fs.readFileSync(FUENTE, "utf8");

  // Assert: solo APIs del navegador; la única red es la voz del servidor (spec 047),
  // en rutas relativas del propio sitio y nunca a un dominio externo.
  assert.equal(/^\s*import\s/m.test(fuente), false);
  assert.equal(/\brequire\(/.test(fuente), false);
  assert.equal(/https?:\/\//.test(fuente), false);
  const rutas = new Set(fuente.match(/["'`]\/api\/[^"'`]+["'`]/g) || []);
  assert.deepEqual([...rutas].sort(), ['"/api/voz/hablar"', '"/api/voz/transcribir"']);
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

test("T002 no hay lectura automática: una preferencia vieja guardada no hace sonar lo escrito", () => {
  // Arrange: el navegador conserva la clave de la casilla que ya no existe.
  const almacen = crearAlmacen({ carvajal_voz_autolectura: "1" });
  const sintesis = crearSintesis();
  const { controlador, documento } = montar({ almacen, sintesis });

  // Act: termina un turno escrito.
  documento.dispatch("chat:turn-complete", {
    detail: { turno: crearTurnoFalso({ texto: "Respuesta escrita." }), threadId: "hilo-1" },
  });

  // Assert
  assert.equal("autoLectura" in controlador.preferencias, false);
  assert.equal("fijarAutoLectura" in controlador.preferencias, false);
  assert.equal(sintesis.locuciones.length, 0);
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
    dataset: { vozFuentes: JSON.stringify(citas.map((cita) => cita.titulo)) },
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
      return [];
    },
  };
}

test("T012 la lectura manual usa texto, tarjetas y títulos de fuentes sin URLs ni SKU", () => {
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

test("T012 un turno escrito completado no habla y uno hablado sí", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const escrito = crearTurnoFalso({ texto: "Respuesta escrita." });
  const hablado = crearTurnoFalso({ texto: "Respuesta hablada." });

  // Act: fin de stream de una pregunta escrita.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: escrito, threadId: "hilo-1" },
  });

  // Assert: sin audio ni marca de pregunta hablada.
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(escrito.dataset.vozPreguntaHablada, undefined);

  // Act: fin de stream de una pregunta hablada (chat-stream.js manda `spoken`).
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: hablado, threadId: "hilo-1", spoken: true },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(sintesis.locuciones[0].text, "Respuesta hablada.");
  assert.equal(hablado.dataset.vozPreguntaHablada, "1");
});

test("T012 la respuesta hablada solo suena tras done, una vez por turno y si el hilo es visible", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Listo." });

  // Act: evento de un hilo distinto y de un turno de usuario.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno: crearTurnoFalso({ texto: "Otro hilo." }), threadId: "hilo-2", spoken: true },
  });
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "Mío.", rol: "user" }),
      threadId: "hilo-1",
      spoken: true,
    },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 0);

  // Act: el turno visible termina.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", spoken: true },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(sintesis.locuciones[0].text, "Listo.");

  // Act: el mismo turno vuelve a notificarse.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", spoken: true },
  });

  // Assert: no se duplica la lectura automática.
  assert.equal(sintesis.locuciones.length, 1);
});

test("T012 la respuesta hablada no suena con la pestaña oculta ni al cargar el historial", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  montaje.documento.visibilityState = "hidden";

  // Act: no hay evento de historial; solo se monta y se oculta la pestaña.
  montaje.documento.dispatch("chat:turn-complete", {
    detail: {
      turno: crearTurnoFalso({ texto: "No debe sonar." }),
      threadId: "hilo-1",
      spoken: true,
    },
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

test("T017 la plantilla ya no trae la casilla «Leer respuestas en voz alta»", () => {
  // Arrange
  const plantilla = fs.readFileSync(
    path.join(RAIZ, "apps", "api", "templates", "chat.html"),
    "utf8",
  );
  const fuente = fs.readFileSync(FUENTE, "utf8");

  // Act / Assert: ni el control ni su clave de localStorage siguen vivos.
  assert.equal(plantilla.includes("data-voz-autolectura"), false);
  assert.equal(plantilla.includes("Leer respuestas en voz alta"), false);
  assert.equal(fuente.includes("carvajal_voz_autolectura"), false);
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

test("la lectura omite el SKU numérico entre paréntesis que pide el prompt", () => {
  // Arrange
  const { limpiarTextoHablado } = cargarVoz().window.CarvajalVoz;

  // Act
  const limpio = limpiarTextoHablado("Vaso térmico 8 oz (SKU 2864108000) y el SKU de la caja");

  // Assert
  assert.equal(limpio, "Vaso térmico 8 oz y el de la caja");
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
  // El adaptador de streaming vacía el campo al iniciar el envío.
  montaje.vista.entrada.value = "";

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
  montaje.vista.entrada.value = "";
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
  const turno = crearTurnoFalso({ texto: "Respuesta." });
  const evento = { detail: { turno, threadId: "hilo-1", spoken: true } };

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
  const turno = crearTurnoFalso({ texto: "Respuesta." });

  // Act
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", spoken: true },
  });

  // Assert
  assert.equal(turno.dataset.vozAutoLeido, undefined);
});

test("T012 la lectura manual deja el turno disponible para otra lectura automática", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Respuesta." });
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", spoken: true },
  });
  assert.equal(turno.dataset.vozAutoLeido, "1");
  sintesis.terminar();

  // Act: la persona vuelve a escuchar a mano.
  montaje.controlador.leerTurno(turno);

  // Assert: el marcador se limpia y una notificación posterior puede leerlo.
  assert.equal(turno.dataset.vozAutoLeido, undefined);
  sintesis.terminar();
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", spoken: true },
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

test("048 la lectura del navegador prefiere el resumen hablado del servidor", () => {
  // Arrange: el turno trae texto largo, una tarjeta y fuentes, y además su resumen.
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({
    texto: "Respuesta larga que no debe leerse entera [1]. Con muchos detalles.",
    tarjetas: [crearTarjetaFalsa({ nombre: "Vaso papel 12 oz", precio: "7250.00 MXN" })],
    citas: [{ titulo: "Manual de empaques" }],
  });
  turno.dataset.vozResumen =
    "Te muestro tres opciones en pantalla: A, B y C.\n\nSegún el sitio de Carvajal.\n\n¿Cuántas necesitas?";

  // Act
  const ok = montaje.controlador.leerTurno(turno);
  sintesis.terminar();
  sintesis.terminar();

  // Assert: un párrafo por locución, sin el texto completo ni la tarjeta.
  assert.equal(ok, true);
  assert.deepEqual(
    sintesis.locuciones.map((locucion) => locucion.text),
    [
      "Te muestro tres opciones en pantalla: A, B y C.",
      "Según el sitio de Carvajal.",
      "¿Cuántas necesitas?",
    ],
  );
});

test("048 construirSegmentos usa el resumen y, sin él, el DOM como antes", () => {
  // Arrange
  const { construirSegmentos } = cargarVoz().window.CarvajalVoz;
  const conResumen = crearTurnoFalso({ texto: "Texto completo." });
  conResumen.dataset.vozResumen = "Resumen corto.\n\n  \n\nEl detalle completo está en pantalla.";
  const vacio = crearTurnoFalso({ texto: "Texto completo." });
  vacio.dataset.vozResumen = "  ";

  // Act
  const resumidos = construirSegmentos(conResumen);
  const completos = construirSegmentos(vacio);

  // Assert
  // Los arreglos vienen del contexto `vm`: se copian para compararlos.
  assert.deepEqual([...resumidos], ["Resumen corto.", "El detalle completo está en pantalla."]);
  assert.deepEqual([...completos], ["Texto completo."]);
});

test("T017 un atributo de fuentes roto no impide la lectura", () => {
  // Arrange: el turno trae los títulos mal formados (versión vieja o atributo alterado).
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Respuesta legible." });
  turno.dataset.vozFuentes = '["sin cerrar';

  // Act
  const ok = montaje.controlador.leerTurno(turno);

  // Assert: se lee el texto y se omiten los títulos.
  assert.equal(ok, true);
  assert.equal(sintesis.locuciones[0].text, "Respuesta legible.");
  assert.equal(/Según/.test(sintesis.locuciones[0].text), false);
});


// ---------------------------------------------------------------------------
// Spec 047 · voz del servidor con respaldo en la voz del navegador
// ---------------------------------------------------------------------------

function errorHttp(status, reason = "") {
  const error = new Error(reason || `http_${status}`);
  error.status = status;
  error.reason = reason;
  return error;
}

function crearServidorFalso({
  texto = "cotiza 400 vasos de 12 onzas",
  falloGrabar = null,
  falloTranscribir = null,
  falloSintetizar = null,
  voz = true,
  detector = true,
  transcripcionColgada = false,
  sintesisPendiente = false,
} = {}) {
  const registro = {
    grabaciones: 0,
    cancelados: 0,
    transcritos: [],
    opcionesTranscribir: [],
    sintetizados: [],
    opcionesSintetizar: [],
    pendientes: [],
    reproducciones: [],
    desbloqueos: 0,
    liberaciones: 0,
    // Se puede cambiar en mitad de la prueba (el proveedor vuelve o se cae).
    falloSintetizar,
    llamadas: null,
  };
  return {
    dictado: true,
    lectura: true,
    maxMs: 60000,
    registro,
    grabar(llamadas) {
      registro.grabaciones += 1;
      registro.llamadas = llamadas;
      if (falloGrabar) return Promise.reject(falloGrabar);
      return Promise.resolve({
        detectorActivo: () => detector,
        detener: () => Promise.resolve({ audio: { size: 4096, type: "audio/webm" }, voz }),
        cancelar: () => {
          registro.cancelados += 1;
        },
      });
    },
    transcribir(audio, opciones = {}) {
      registro.transcritos.push(audio);
      registro.opcionesTranscribir.push(opciones);
      // Una red colgada: solo termina si se aborta la señal.
      if (transcripcionColgada) return new Promise(() => {});
      return falloTranscribir ? Promise.reject(falloTranscribir) : Promise.resolve(texto);
    },
    sintetizar(mensaje, opciones = {}) {
      registro.sintetizados.push(mensaje);
      registro.opcionesSintetizar.push(opciones);
      if (registro.falloSintetizar) return Promise.reject(registro.falloSintetizar);
      if (sintesisPendiente) {
        return new Promise((resolver, rechazar) => registro.pendientes.push({ resolver, rechazar }));
      }
      return Promise.resolve({ size: 10, mensaje });
    },
    desbloquear() {
      registro.desbloqueos += 1;
    },
    liberar() {
      registro.liberaciones += 1;
    },
    reproducir(audio, llamadas) {
      const reproduccion = {
        audio,
        llamadas,
        pausas: 0,
        reanudaciones: 0,
        detenciones: 0,
        pausar() {
          this.pausas += 1;
        },
        reanudar() {
          this.reanudaciones += 1;
        },
        detener() {
          this.detenciones += 1;
        },
      };
      registro.reproducciones.push(reproduccion);
      return reproduccion;
    },
  };
}

function esperar() {
  return new Promise((resolver) => setImmediate(resolver));
}

async function vaciarPromesas(veces = 5) {
  for (let vez = 0; vez < veces; vez += 1) await esperar();
}

function montarServidor({
  servidor = crearServidorFalso(),
  reconocimiento,
  sintesis,
  documento,
  ventana,
  almacen,
} = {}) {
  const reconocimientoReal = reconocimiento || crearReconocimiento();
  const montaje = montar({
    reconocimiento: reconocimientoReal,
    sintesis: sintesis === false ? false : sintesis || crearSintesis(),
    almacen: almacen || crearAlmacen({ carvajal_voz_aviso_dictado_servidor: "1" }),
    servidor,
    documento,
    ventana,
  });
  montaje.reconocimiento = reconocimientoReal;
  montaje.servidor = servidor;
  return montaje;
}

test("047 el dictado del servidor graba, transcribe y deja el texto para revisar", async () => {
  // Arrange
  const montaje = montarServidor();

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "true");
  assert.match(montaje.vista.estado.textContent, /Escuchando/);
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert: el navegador no transcribe, el texto queda en el borrador y no se envía.
  assert.equal(montaje.servidor.registro.grabaciones, 1);
  assert.equal(montaje.servidor.registro.transcritos.length, 1);
  assert.equal(montaje.reconocimiento.instancias.length, 0);
  assert.equal(montaje.vista.entrada.value, "cotiza 400 vasos de 12 onzas");
  assert.equal(montaje.vista.formulario.enviados, 0);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.listo);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");
});

test("047 el primer dictado del servidor pide su propio aviso aunque se aceptara el del navegador", () => {
  // Arrange
  const almacen = crearAlmacen({ carvajal_voz_aviso_dictado: "1" });
  const servidor = crearServidorFalso();
  const montaje = montar({ almacen, servidor });

  // Act
  clic(montaje.vista.botonDictar);

  // Assert
  assert.equal(montaje.vista.aviso.open, true);
  assert.equal(servidor.registro.grabaciones, 0);
});

test("047 la pausa detectada termina el dictado del servidor sin clic", async () => {
  // Arrange
  const montaje = montarServidor();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  montaje.servidor.registro.llamadas.alPausa();
  montaje.servidor.registro.llamadas.alPausa();
  await vaciarPromesas();

  // Assert: una sola subida aunque el detector avise dos veces.
  assert.equal(montaje.servidor.registro.transcritos.length, 1);
  assert.equal(montaje.vista.entrada.value, "cotiza 400 vasos de 12 onzas");
});

test("047 sin voz detectada no se sube el audio", async () => {
  // Arrange
  const montaje = montarServidor({ servidor: crearServidorFalso({ voz: false }) });
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.transcritos.length, 0);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.silencio);
});

test("047 un 503 del servidor vuelve al dictado del navegador el resto de la sesión", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(503, "cap") });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert: aviso claro y el siguiente dictado usa SpeechRecognition.
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.servidorDictado);
  assert.equal(montaje.controlador.soporte.servidor.dictado, false);
  assert.equal(montaje.vista.botonDictar.hidden, false);
  clic(montaje.vista.botonDictar);
  assert.equal(montaje.reconocimiento.instancias.length, 1);
  assert.equal(servidor.registro.grabaciones, 1);
});

test("047 sin reconocimiento del navegador el fallo del servidor invita a escribir", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(503, "off") });
  const almacen = crearAlmacen({ carvajal_voz_aviso_dictado_servidor: "1" });
  const sandbox = cargarVoz({ reconocimiento: false });
  const vista = crearVista();
  const controlador = sandbox.window.CarvajalVoz.createVoiceController({
    vista,
    documento: crearDocumento(),
    almacen,
    reconocimiento: null,
    sintesis: crearSintesis(),
    Utterance: sandbox.SpeechSynthesisUtterance,
    reloj: crearReloj(),
    segura: true,
    streaming: true,
    servidor,
  });
  controlador.mount();
  assert.equal(vista.botonDictar.hidden, false);

  // Act
  clic(vista.botonDictar);
  await vaciarPromesas();
  clic(vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(vista.estado.textContent, sandbox.window.CarvajalVoz.MENSAJES.servidorSinRespaldo);
  assert.equal(vista.botonDictar.hidden, true);
});

test("047 un permiso denegado no cambia de voz y lo explica", async () => {
  // Arrange
  const denegado = new Error("denegado");
  denegado.name = "NotAllowedError";
  const montaje = montarServidor({ servidor: crearServidorFalso({ falloGrabar: denegado }) });

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.permiso);
  assert.equal(montaje.controlador.soporte.servidor.dictado, true);
  assert.equal(montaje.vista.botonDictar.getAttribute("aria-pressed"), "false");
});

test("047 un 422 del servidor se trata como silencio sin apagar la voz del servidor", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(422, "empty") });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.silencio);
  assert.equal(montaje.controlador.soporte.servidor.dictado, true);
});

test("047 Escape cancela la grabación del servidor y libera el micrófono", async () => {
  // Arrange
  const montaje = montarServidor();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  tecla(montaje.documento, "Escape");
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.cancelados, 1);
  assert.equal(montaje.servidor.registro.transcritos.length, 0);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.cancelado);
});

test("047 el límite de segundos del servidor detiene y transcribe", async () => {
  // Arrange
  const montaje = montarServidor();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  montaje.reloj.avanzar(60000);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.transcritos.length, 1);
  assert.equal(montaje.vista.entrada.value, "cotiza 400 vasos de 12 onzas");
});

/** Respuesta guardada a una pregunta hablada: solo esas tienen nota de voz del servidor. */
function turnoConMensaje(texto, mensaje) {
  const turno = crearTurnoFalso({ texto });
  if (mensaje) {
    turno.dataset.vozMensaje = mensaje;
    turno.dataset.vozPreguntaHablada = "1";
  }
  return turno;
}

test("047 la lectura del servidor pide el audio por id y controla pausa y fin", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarServidor({ sintesis });
  const turno = turnoConMensaje("Hola, soy Carva.", "m-123");

  // Act
  const leido = montaje.controlador.leerTurno(turno);
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alEmpezar();

  // Assert: el servidor recibe el id, nunca el texto, y el navegador no habla. El
  // estado lo anuncia la nota (spec 050), no la línea global.
  assert.equal(leido, true);
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-123"]);
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(turno._botones.pausar.hidden, false);
  assert.equal(montaje.controlador.notaDe(turno).estado, "sonando");
  assert.equal(montaje.vista.estado.textContent, "");

  // Act: pausa, reanuda y termina.
  montaje.controlador.pausarLectura();
  montaje.controlador.reanudarLectura();
  reproduccion.llamadas.alTerminar();

  // Assert
  assert.equal(reproduccion.pausas, 1);
  assert.equal(reproduccion.reanudaciones, 1);
  assert.equal(turno._botones.detener.hidden, true);
  assert.equal(turno._botones.escuchar.getAttribute("aria-pressed"), "false");
});

test("047 detener la lectura del servidor corta el audio", async () => {
  // Arrange
  const montaje = montarServidor();
  const turno = turnoConMensaje("Hola.", "m-1");
  montaje.controlador.leerTurno(turno);
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alEmpezar();

  // Act
  montaje.controlador.detenerLectura();

  // Assert
  assert.equal(reproduccion.detenciones, 1);
  assert.equal(turno._botones.detener.hidden, true);
});

test("047 un 503 en la lectura usa la voz del navegador mientras el servidor sigue en espera", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "circuit_open") });
  const montaje = montarServidor({ servidor, sintesis });
  const turno = turnoConMensaje("Tenemos vasos de papel.", "m-9");

  // Act
  montaje.controlador.leerTurno(turno);
  await vaciarPromesas();
  montaje.controlador.leerTurno(turnoConMensaje("Otra respuesta.", "m-10"));

  // Assert
  assert.deepEqual(servidor.registro.sintetizados, ["m-9"]);
  assert.equal(sintesis.locuciones.length >= 2, true);
  assert.equal(sintesis.locuciones[0].text, "Tenemos vasos de papel.");
  assert.equal(montaje.controlador.soporte.servidor.lectura, false);
});

test("050 un 404 en la lectura deja la nota en error sin cambiar de voz ni apagar el servidor", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(404, "not_found") });
  const montaje = montarServidor({ servidor, sintesis });
  const turno = turnoConMensaje("Hola.", "ajeno");

  // Act
  montaje.controlador.leerTurno(turno);
  await vaciarPromesas();

  // Assert
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.controlador.notaDe(turno).estado, "error");
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
});

test("047 un turno sin id de mensaje se lee con el navegador", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarServidor({ sintesis });

  // Act
  montaje.controlador.leerTurno(turnoConMensaje("Respuesta en curso.", ""));

  // Assert
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(sintesis.locuciones.length, 1);
});

test("047 solo la voz del servidor basta para ofrecer la lectura sin voces del navegador", () => {
  // Arrange
  const servidor = crearServidorFalso();
  const montaje = montar({ sintesis: false, servidor });

  // Assert
  assert.equal(montaje.controlador.soporte.sintesis, true);
  assert.equal(montaje.documento.documentElement.dataset.vozTts, "1");
});

test("047 manos libres con la voz del servidor envía lo dictado y lee la respuesta", async () => {
  // Arrange
  const montaje = montarServidor();
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();
  assert.equal(montaje.controlador.manosLibres.activo, true);

  // Act: habla, pausa y el servidor transcribe.
  montaje.servidor.registro.llamadas.alVoz();
  montaje.servidor.registro.llamadas.alPausa();
  await vaciarPromesas();

  // Assert: se envía sin revisar, como en manos libres del navegador.
  assert.equal(montaje.vista.formulario.enviados, 1);
  montaje.vista.entrada.value = ""; // el stream del chat vacía el borrador al enviar

  // Act: llega la respuesta y se lee con el servidor; al terminar vuelve a escuchar.
  const turno = turnoConMensaje("Listo, son 2 cajas.", "m-77");
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", handsFree: true },
  });
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alEmpezar();
  reproduccion.llamadas.alTerminar();
  await vaciarPromesas();

  // Assert
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-77"]);
  assert.equal(montaje.servidor.registro.grabaciones, 2);
  assert.equal(montaje.controlador.manosLibres.activo, true);
});

test("047 manos libres sigue con el navegador si la transcripción del servidor falla", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(429, "rate_limited") });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();

  // Act
  servidor.registro.llamadas.alPausa();
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, true);
  assert.equal(montaje.reconocimiento.instancias.length, 1);
  assert.equal(montaje.vista.formulario.enviados, 0);
});

test("047 el adaptador del servidor envía CSRF y lanza errores con status y reason", async () => {
  // Arrange
  const peticiones = [];
  const ventana = {
    fetch: (ruta, opciones) => {
      peticiones.push({ ruta, opciones });
      return Promise.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ reason: "off" }),
      });
    },
    URLSearchParams,
    FormData: class {
      constructor() {
        this.campos = [];
      }
      append(...args) {
        this.campos.push(args);
      }
    },
    Blob: class {},
    Audio: class {},
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    navigator: {},
  };
  const sandbox = cargarVoz();
  const servidor = sandbox.window.CarvajalVoz.crearServidorVoz(ventana, { csrf: () => "token-1" });

  // Act
  let capturado = null;
  try {
    await servidor.sintetizar("m-5");
  } catch (error) {
    capturado = error;
  }

  // Assert
  assert.equal(servidor.lectura, true);
  assert.equal(servidor.dictado, false);
  assert.equal(peticiones[0].ruta, "/api/voz/hablar");
  assert.equal(peticiones[0].opciones.headers["X-CSRF-Token"], "token-1");
  assert.equal(String(peticiones[0].opciones.body), "message_id=m-5&csrf_token=token-1");
  assert.equal(capturado.status, 503);
  assert.equal(capturado.reason, "off");
});

// ---------------------------------------------------------------------------
// Spec 047 · arreglos de la revisión: respaldo al reproducir, 422 por turno,
// cancelar la subida, manos libres sin detector y limpieza del micrófono.
// ---------------------------------------------------------------------------

test("050 un audio que no se puede decodificar deja la nota en error y no apaga el servidor", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarServidor({ sintesis });
  const turno = turnoConMensaje("Son 2 cajas de vasos.", "m-1");

  // Act: el audio llega pero el elemento falla (decodificación, CSP sin blob:).
  montaje.controlador.leerTurno(turno);
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alFallar({ name: "MediaError" });
  montaje.controlador.leerTurno(turnoConMensaje("Otra respuesta.", "m-2"));
  await vaciarPromesas();

  // Assert: ni este turno ni el siguiente cambian a la voz del navegador.
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.controlador.notaDe(turno).estado, "error");
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-1", "m-2"]);
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
});

test("050 un 422 en la lectura solo afecta a ese turno y no cambia de voz", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(422, "no_text") });
  const montaje = montarServidor({ servidor, sintesis });
  const turno = turnoConMensaje("Hola.", "m-sin-texto");

  // Act
  montaje.controlador.leerTurno(turno);
  await vaciarPromesas();

  // Assert
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.controlador.notaDe(turno).estado, "error");
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
});

test("047 el clic durante la transcripción la cancela y aborta la subida", async () => {
  // Arrange: la red se cuelga en la subida.
  const servidor = crearServidorFalso({ transcripcionColgada: true });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  clic(montaje.vista.botonDictar); // termina la grabación y empieza a subir
  await vaciarPromesas();
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.transcribiendo);
  const [opciones] = servidor.registro.opcionesTranscribir;

  // Act
  clic(montaje.vista.botonDictar);

  // Assert
  assert.equal(opciones.signal.aborted, true);
  assert.equal(montaje.controlador.estado.escuchando, false);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.cancelado);
});

test("047 manos libres sin detector de voz transcribe lo grabado en vez de descartarlo", async () => {
  // Arrange: sin Web Audio no llegan alVoz ni alPausa.
  const servidor = crearServidorFalso({ detector: false });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();

  // Act
  montaje.reloj.avanzar(10000);
  await vaciarPromesas();

  // Assert: se transcribe y se envía; manos libres sigue activo.
  assert.equal(servidor.registro.transcritos.length, 1);
  assert.equal(servidor.registro.cancelados, 0);
  assert.equal(montaje.vista.formulario.enviados, 1);
  assert.equal(montaje.controlador.manosLibres.activo, true);
});

test("047 manos libres con detector sigue apagándose tras 10 s sin voz", async () => {
  // Arrange
  const montaje = montarServidor();
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();

  // Act
  montaje.reloj.avanzar(10000);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.servidor.registro.transcritos.length, 0);
});

test("047 el paso al reconocimiento del navegador en manos libres queda anunciado y visible", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(503, "unavailable") });
  const montaje = montarServidor({ servidor });
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();

  // Act
  servidor.registro.llamadas.alPausa();
  await vaciarPromesas();

  // Assert
  const mensajes = montaje.api.MENSAJES;
  assert.equal(montaje.vista.nota.hidden, false);
  assert.equal(montaje.vista.nota.textContent, mensajes.servidorDictado);
  assert.equal(montaje.vista.estado.textContent.startsWith(mensajes.servidorDictado), true);
  assert.equal(montaje.vista.estado.textContent.includes(mensajes.escuchando), true);
});

function crearVentanaGrabacion({ fallaStart = false, fallaAnalizador = false } = {}) {
  const registro = { pistasDetenidas: 0, contextos: 0, cerrados: 0, intervalos: 0 };
  const flujo = {
    getTracks: () => [
      {
        stop() {
          registro.pistasDetenidas += 1;
        },
      },
    ],
  };
  class GrabadoraFalsa {
    constructor() {
      this.state = "inactive";
      this.mimeType = "audio/webm";
    }
    start() {
      if (fallaStart) throw new Error("NotSupportedError");
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
    }
    static isTypeSupported() {
      return true;
    }
  }
  class ContextoFalso {
    constructor() {
      registro.contextos += 1;
      this.state = "running";
    }
    createAnalyser() {
      if (fallaAnalizador) throw new Error("InvalidStateError");
      return { fftSize: 0, getFloatTimeDomainData() {} };
    }
    createMediaStreamSource() {
      return { connect() {} };
    }
    close() {
      registro.cerrados += 1;
      return Promise.resolve();
    }
  }
  const ventana = {
    MediaRecorder: GrabadoraFalsa,
    AudioContext: ContextoFalso,
    navigator: { mediaDevices: { getUserMedia: () => Promise.resolve(flujo) } },
    setInterval: () => {
      registro.intervalos += 1;
      return 1;
    },
    clearInterval() {},
    setTimeout,
    clearTimeout,
    fetch: () => Promise.reject(new Error("sin red")),
    FormData: class {},
    Blob: class {},
  };
  return { ventana, registro };
}

test("047 si la grabadora no arranca se suelta el micrófono sin dejar detector vivo", async () => {
  // Arrange
  const { ventana, registro } = crearVentanaGrabacion({ fallaStart: true });
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana);

  // Act
  let capturado = null;
  try {
    await servidor.grabar({});
  } catch (error) {
    capturado = error;
  }

  // Assert
  assert.notEqual(capturado, null);
  assert.equal(registro.pistasDetenidas, 1);
  assert.equal(registro.contextos, 0);
  assert.equal(registro.intervalos, 0);
});

test("047 un AudioContext que no sirve se cierra y la grabación sigue sin detector", async () => {
  // Arrange
  const { ventana, registro } = crearVentanaGrabacion({ fallaAnalizador: true });
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana);

  // Act
  const manejador = await servidor.grabar({});

  // Assert
  assert.equal(registro.contextos, 1);
  assert.equal(registro.cerrados, 1);
  assert.equal(manejador.detectorActivo(), false);
  manejador.cancelar();
});

function crearVentanaFetchColgado() {
  const peticiones = [];
  return {
    peticiones,
    ventana: {
      fetch: (ruta, opciones) => {
        peticiones.push({ ruta, opciones });
        return new Promise((_resolver, rechazar) => {
          opciones.signal.addEventListener("abort", () => {
            const error = new Error("aborted");
            error.name = "AbortError";
            rechazar(error);
          });
        });
      },
      AbortController,
      setTimeout,
      clearTimeout,
      URLSearchParams,
      FormData: class {
        constructor() {
          this.campos = [];
        }
        append(...args) {
          this.campos.push(args);
        }
      },
      Blob: class {},
      Audio: class {},
      URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
      navigator: {},
    },
  };
}

test("047 la transcripción con la red colgada vence por plazo con TimeoutError", async () => {
  // Arrange
  const { ventana, peticiones } = crearVentanaFetchColgado();
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana, {
    csrf: () => "token-1",
    plazoMs: 20,
  });

  // Act
  let capturado = null;
  try {
    await servidor.transcribir({ type: "audio/webm" });
  } catch (error) {
    capturado = error;
  }

  // Assert: CSRF solo por cabecera y error de plazo sin `status`.
  assert.equal(capturado.name, "TimeoutError");
  assert.equal(capturado.status, undefined);
  assert.equal(peticiones[0].opciones.headers["X-CSRF-Token"], "token-1");
  assert.equal(
    peticiones[0].opciones.body.campos.some(([nombre]) => nombre === "csrf_token"),
    false,
  );
});

test("047 cancelar el dictado aborta la subida de la transcripción", async () => {
  // Arrange
  const { ventana, peticiones } = crearVentanaFetchColgado();
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana, { plazoMs: 60000 });
  const control = new AbortController();

  // Act
  const pendiente = servidor.transcribir({ type: "audio/webm" }, { signal: control.signal });
  control.abort();
  let capturado = null;
  try {
    await pendiente;
  } catch (error) {
    capturado = error;
  }

  // Assert
  assert.equal(peticiones[0].opciones.signal.aborted, true);
  assert.equal(capturado.name, "AbortError");
});

// ---------------------------------------------------------------------------
// 048: estados de la voz en <html> para el rediseño (049), solo atributos.
// ---------------------------------------------------------------------------

function conEstilo(montaje) {
  const propiedades = new Map();
  montaje.documento.documentElement.style = {
    setProperty: (nombre, valor) => propiedades.set(nombre, valor),
    removeProperty: (nombre) => propiedades.delete(nombre),
  };
  return propiedades;
}

test("048 el dictado del servidor expone grabando con su progreso y luego transcribiendo", async () => {
  // Arrange
  const servidor = crearServidorFalso({ transcripcionColgada: true });
  const montaje = montarServidor({ servidor });
  const propiedades = conEstilo(montaje);
  const raiz = montaje.documento.documentElement;

  // Act: empieza a grabar y pasan 15 s de 60.
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  montaje.reloj.avanzar(15000);

  // Assert
  assert.equal(raiz.dataset.vozGrabando, "1");
  assert.equal(raiz.dataset.vozTranscribiendo, undefined);
  assert.equal(propiedades.get("--voz-progreso"), "0.250");

  // Act: detiene; la transcripción queda pendiente.
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(raiz.dataset.vozGrabando, undefined);
  assert.equal(propiedades.has("--voz-progreso"), false);
  assert.equal(raiz.dataset.vozTranscribiendo, "1");

  // Act: cancelar con Escape limpia el estado.
  tecla(montaje.documento, "Escape");

  // Assert
  assert.equal(raiz.dataset.vozTranscribiendo, undefined);
  assert.equal(raiz.dataset.vozGrabando, undefined);
});

test("048 al terminar el dictado del servidor no queda ningún estado de grabación", async () => {
  // Arrange
  const montaje = montarServidor();
  const raiz = montaje.documento.documentElement;

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.vista.entrada.value, "cotiza 400 vasos de 12 onzas");
  assert.equal(raiz.dataset.vozGrabando, undefined);
  assert.equal(raiz.dataset.vozTranscribiendo, undefined);
});

test("048 la lectura del servidor marca sintetizando con el id mientras llega el audio", async () => {
  // Arrange
  const montaje = montarServidor();
  const raiz = montaje.documento.documentElement;
  const turno = turnoConMensaje("Hola, soy Carva.", "m-321");

  // Act
  montaje.controlador.leerTurno(turno);

  // Assert: antes de que llegue el audio.
  assert.equal(raiz.dataset.vozSintetizando, "m-321");

  // Act
  await vaciarPromesas();

  // Assert
  assert.equal(raiz.dataset.vozSintetizando, undefined);
});

test("048 detener la lectura mientras se sintetiza no reproduce y limpia el estado al llegar", async () => {
  // Arrange
  const montaje = montarServidor();
  const raiz = montaje.documento.documentElement;
  const turno = turnoConMensaje("Hola.", "m-9");
  montaje.controlador.leerTurno(turno);

  // Act
  montaje.controlador.detenerLectura();

  // Assert: la síntesis sigue en vuelo (llena la caché) y el atributo lo refleja.
  assert.equal(raiz.dataset.vozSintetizando, "m-9");
  assert.equal(montaje.controlador.notaDe(turno).estado, "lista");

  // Act
  await vaciarPromesas();

  // Assert
  assert.equal(raiz.dataset.vozSintetizando, undefined);
  assert.equal(montaje.servidor.registro.reproducciones.length, 0);
});

test("048 el respaldo del navegador queda expuesto cuando el servidor se apaga", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(503, "cap") });
  const montaje = montarServidor({ servidor });
  const raiz = montaje.documento.documentElement;
  assert.equal(raiz.dataset.vozRespaldo, undefined);

  // Act
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(raiz.dataset.vozRespaldo, "1");
});

// ---------------------------------------------------------------------------
// Spec 050 · respuestas como notas de voz: autoplay solo si la persona habló,
// desbloqueo del audio en el gesto, precarga con caché y respaldo solo ante 503.
// ---------------------------------------------------------------------------

function aCamello(nombre) {
  return nombre.replace(/-(\w)/g, (_, letra) => letra.toUpperCase());
}

function coincide(nodo, selector) {
  if (selector.startsWith(".")) {
    return String(nodo.className || "").split(/\s+/).includes(selector.slice(1));
  }
  const atributo = /^\[data-([\w-]+)(?:="([^"]*)")?\]$/.exec(selector);
  if (!atributo) return false;
  const valor = nodo.dataset[aCamello(atributo[1])];
  return atributo[2] === undefined ? valor !== undefined : valor === atributo[2];
}

function buscarEn(raiz, selector, encontrados = []) {
  for (const hijo of raiz.hijos || []) {
    if (coincide(hijo, selector)) encontrados.push(hijo);
    buscarEn(hijo, selector, encontrados);
  }
  return encontrados;
}

// Nodo con árbol mínimo: lo justo para que la nota se inserte, se busque y se pulse.
function crearNodoDom(etiqueta = "div") {
  const propiedades = new Map();
  return crearElemento(etiqueta, {
    className: "",
    hijos: [],
    padre: null,
    style: {
      propiedades,
      setProperty: (nombre, valor) => propiedades.set(nombre, valor),
      removeProperty: (nombre) => propiedades.delete(nombre),
    },
    appendChild(hijo) {
      hijo.padre = this;
      this.hijos.push(hijo);
      return hijo;
    },
    append(...hijos) {
      for (const hijo of hijos) this.appendChild(hijo);
    },
    after(nuevo) {
      const padre = this.padre;
      nuevo.padre = padre;
      padre.hijos.splice(padre.hijos.indexOf(this) + 1, 0, nuevo);
    },
    contains(otro) {
      for (let nodo = otro; nodo; nodo = nodo.padre) if (nodo === this) return true;
      return false;
    },
    closest(selector) {
      for (let nodo = this; nodo; nodo = nodo.padre) if (coincide(nodo, selector)) return nodo;
      return null;
    },
    querySelector(selector) {
      return buscarEn(this, selector)[0] || null;
    },
    querySelectorAll(selector) {
      return buscarEn(this, selector);
    },
  });
}

// El historial pide solo las respuestas a una pregunta hablada; el doble filtra igual.
const SELECTOR_HISTORIAL = ".turno-assistant[data-voz-mensaje][data-voz-pregunta-hablada]";

function crearDocumentoDom(historial = []) {
  const documento = crearDocumento();
  documento.createElement = (etiqueta) => crearNodoDom(etiqueta);
  documento.querySelectorAll = (selector) =>
    selector === SELECTOR_HISTORIAL
      ? historial.filter((turno) => turno.dataset.vozPreguntaHablada === "1")
      : [];
  return documento;
}

function crearTurnoDom({ mensaje = "m-1", texto = "Hola, soy Carva.", hablada = false } = {}) {
  const turno = crearNodoDom("li");
  turno.className = "turno turno-assistant";
  if (mensaje) turno.dataset.vozMensaje = mensaje;
  if (hablada) turno.dataset.vozPreguntaHablada = "1";
  const burbuja = crearNodoDom("div");
  burbuja.className = "burbuja";
  const textoNodo = crearNodoDom("div");
  textoNodo.className = "texto";
  textoNodo.textContent = texto;
  burbuja.appendChild(textoNodo);
  const franja = crearNodoDom("div");
  franja.className = "turno-voz";
  franja.dataset.vozTurno = "";
  const productos = crearNodoDom("div");
  productos.className = "productos";
  turno.append(burbuja, productos, franja);
  return turno;
}

function crearVentanaFalsa() {
  const oyentes = new Map();
  return {
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
    },
    removeEventListener(tipo, manejador) {
      oyentes.set(
        tipo,
        (oyentes.get(tipo) || []).filter((cada) => cada !== manejador),
      );
    },
    dispatch(tipo, evento = {}) {
      for (const manejador of oyentes.get(tipo) || []) manejador(evento);
    },
  };
}

function montarNotas({ servidor = crearServidorFalso(), sintesis, almacen, historial } = {}) {
  const documento = crearDocumentoDom(historial);
  const ventana = crearVentanaFalsa();
  const montaje = montarServidor({ servidor, sintesis, almacen, documento, ventana });
  montaje.ventana = ventana;
  return montaje;
}

function partesDeNota(turno) {
  const raiz = turno.querySelector(".turno-nota");
  const buscar = (clase) => (raiz ? raiz.querySelector(clase) : null);
  return {
    raiz,
    boton: buscar(".turno-nota-boton"),
    barra: buscar(".turno-nota-barra"),
    tiempo: buscar(".turno-nota-tiempo"),
    duracion: buscar(".turno-nota-duracion"),
    estado: buscar(".turno-nota-estado"),
    aviso: buscar(".turno-nota-aviso"),
    reintentar: buscar(".turno-nota-reintentar"),
  };
}

/** Envía el formulario como lo haría el navegador: el evento llega al documento. */
function enviarPregunta(montaje, texto) {
  if (texto !== undefined) montaje.vista.entrada.value = texto;
  montaje.documento.dispatch("submit", { target: montaje.vista.formulario });
}

async function dictarConServidor(montaje) {
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();
}

function completarTurno(montaje, turno, detalle = {}) {
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", handsFree: false, ...detalle },
  });
}

function pulsar(montaje, elemento) {
  montaje.documento.dispatch("click", { target: elemento });
}

/**
 * Respuesta del historial a una pregunta hablada: su nota nace «lista» al montar, sin
 * sonar ni pedir audio. Es la forma de tener una nota quieta para pulsarla.
 */
function montarConNota(opciones = {}, turnoOpciones = {}) {
  const turno = crearTurnoDom({ hablada: true, ...turnoOpciones });
  const montaje = montarNotas({ ...opciones, historial: [turno] });
  return { montaje, turno };
}

test("050 la nota nace tras la burbuja con su contrato accesible y oculta la franja", () => {
  // Arrange: respuesta del historial a una pregunta hablada.
  const turno = crearTurnoDom({ mensaje: "m-1", hablada: true });

  // Act
  montarNotas({ historial: [turno] });

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(turno.hijos[1], nota.raiz);
  assert.equal(nota.raiz.dataset.notaEstado, "lista");
  assert.equal(nota.raiz.dataset.notaMensaje, "m-1");
  assert.equal(nota.raiz.getAttribute("role"), "group");
  assert.equal(nota.boton.getAttribute("type"), "button");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "false");
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
  assert.equal(nota.barra.getAttribute("role"), "progressbar");
  assert.equal(nota.barra.getAttribute("aria-valuenow"), "0");
  assert.equal(nota.tiempo.textContent, "0:00");
  assert.equal(nota.duracion.textContent, "");
  assert.equal(nota.estado.getAttribute("role"), "status");
  assert.equal(nota.estado.getAttribute("aria-live"), "polite");
  assert.equal(nota.reintentar.hidden, true);
  assert.equal(nota.aviso.hidden, true);
  assert.equal(turno.querySelector("[data-voz-turno]").hidden, true);
});

/** Una nota en «sonando» del servidor: se pulsa, llega el audio y empieza. */
async function notaQueSuena(montaje, turno) {
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();
  const reproduccion = montaje.servidor.registro.reproducciones.at(-1);
  reproduccion.llamadas.alEmpezar();
  return reproduccion;
}

/** Activa manos libres y dicta una pregunta que se envía sola. */
async function preguntarConManosLibres(montaje) {
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();
  montaje.servidor.registro.llamadas.alVoz();
  montaje.servidor.registro.llamadas.alPausa();
  await vaciarPromesas();
  montaje.vista.entrada.value = "";
}

test("050 una pregunta escrita no trae nota, ni «Escuchar», ni pide el audio", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-escrita" });
  const franja = turno.querySelector("[data-voz-turno]");
  franja.hidden = true; // chat-stream.js la oculta al crear el turno

  // Act
  enviarPregunta(montaje, "necesito 2000 platos desechables");
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert: sin nota, sin franja, sin síntesis ni desbloqueo (en iOS pausaría la música).
  assert.equal(partesDeNota(turno).raiz, null);
  assert.equal(turno.dataset.vozNota, undefined);
  assert.equal(turno.dataset.vozPreguntaHablada, undefined);
  assert.equal(franja.hidden, true);
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(montaje.servidor.registro.desbloqueos, 0);
});

test("050 una pregunta dictada trae nota y queda marcada como hablada", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-dictada" });
  await dictarConServidor(montaje);

  // Act
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.ok(partesDeNota(turno).raiz);
  assert.equal(turno.dataset.vozNota, "1");
  assert.equal(turno.dataset.vozPreguntaHablada, "1");
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-dictada"]);
});

test("050 pulsar la nota la deja cargando y pulsada mientras llega el audio", () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-escrita" });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert: se desbloquea en el gesto; el nombre del botón no cambia.
  const nota = partesDeNota(turno);
  assert.equal(servidor.registro.desbloqueos, 1);
  assert.equal(nota.raiz.dataset.notaEstado, "cargando");
  assert.equal(nota.boton.getAttribute("aria-busy"), "true");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "true");
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
});

test("050 pulsar la nota de una pregunta escrita pide el audio una vez y lo reproduce", async () => {
  // Arrange
  const { montaje, turno } = montarConNota({}, { mensaje: "m-escrita" });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Assert
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-escrita"]);
  assert.equal(montaje.servidor.registro.reproducciones.length, 1);
});

test("050 una pregunta dictada desbloquea el audio en el gesto y la respuesta suena sola", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-dictada" });
  await dictarConServidor(montaje);
  const desbloqueosAlDictar = montaje.servidor.registro.desbloqueos;

  // Act
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alEmpezar();
  reproduccion.llamadas.alProgreso(4.2, 17.5);
  montaje.reloj.avanzar(0);

  // Assert: sin anuncio «reproduciendo» que tape el audio; el estado va en aria-pressed.
  const nota = partesDeNota(turno);
  assert.equal(desbloqueosAlDictar, 1);
  assert.equal(montaje.servidor.registro.desbloqueos, 2);
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-dictada"]);
  assert.equal(nota.raiz.dataset.notaEstado, "sonando");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "true");
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
  assert.equal(nota.estado.textContent, "");
  assert.equal(nota.raiz.style.propiedades.get("--nota-avance"), "0.240");
  assert.equal(nota.barra.getAttribute("aria-valuenow"), "24");
  assert.equal(nota.barra.getAttribute("aria-valuetext"), "0:04 de 0:17");
  assert.equal(nota.tiempo.textContent, "0:04");
  assert.equal(nota.duracion.textContent, "0:17");
});

test("050 pulsar una nota que suena la pausa sin cambiar el nombre del botón", async () => {
  // Arrange
  const { montaje, turno } = montarConNota({}, { mensaje: "m-pausa" });
  const reproduccion = await notaQueSuena(montaje, turno);

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(reproduccion.pausas, 1);
  assert.equal(nota.raiz.dataset.notaEstado, "pausada");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "false");
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
  assert.equal(nota.estado.textContent, "");
});

test("050 pulsar una nota en pausa la reanuda", async () => {
  // Arrange
  const { montaje, turno } = montarConNota({}, { mensaje: "m-reanuda" });
  const reproduccion = await notaQueSuena(montaje, turno);
  pulsar(montaje, partesDeNota(turno).boton);

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert
  assert.equal(reproduccion.reanudaciones, 1);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "sonando");
  assert.equal(partesDeNota(turno).boton.getAttribute("aria-pressed"), "true");
});

test("050 al terminar la nota vuelve a lista con el avance a cero y conserva la duración", async () => {
  // Arrange
  const { montaje, turno } = montarConNota({}, { mensaje: "m-fin" });
  const reproduccion = await notaQueSuena(montaje, turno);
  reproduccion.llamadas.alProgreso(17.5, 17.5);

  // Act
  reproduccion.llamadas.alTerminar();

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "lista");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "false");
  assert.equal(nota.raiz.style.propiedades.get("--nota-avance"), "0.000");
  assert.equal(nota.duracion.textContent, "0:17");
});

test("050 borrar el dictado y escribir otra pregunta cuenta como escrita", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-reescrita" });
  await dictarConServidor(montaje);

  // Act: vacía el borrador y escribe.
  montaje.vista.entrada.value = "";
  montaje.vista.entrada.dispatch("input", {});
  enviarPregunta(montaje, "Mejor la escribo");
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
});

test("050 una sugerencia sobre un borrador dictado cuenta como escrita", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-sugerencia" });
  await dictarConServidor(montaje);
  const desbloqueosAntes = montaje.servidor.registro.desbloqueos;

  // Act: la sugerencia sustituye el valor y dispara `input` sin vaciar el campo.
  montaje.vista.entrada.value = "¿Qué cajas plegadizas tienen?";
  montaje.vista.entrada.dispatch("input", {});
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert: ni síntesis pagada ni desbloqueo (que en iOS pausaría la música).
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(montaje.servidor.registro.desbloqueos, desbloqueosAntes);
  assert.equal(montaje.servidor.registro.reproducciones.length, 0);
});

test("050 reescribir todo el dictado sin vaciar el campo cuenta como escrita", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-encima" });
  await dictarConServidor(montaje);
  const desbloqueosAntes = montaje.servidor.registro.desbloqueos;

  // Act: seleccionar todo y escribir encima, letra a letra.
  for (const valor of ["Q", "Qu", "Quiero una resma de papel"]) {
    montaje.vista.entrada.value = valor;
    montaje.vista.entrada.dispatch("input", {});
  }
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(montaje.servidor.registro.desbloqueos, desbloqueosAntes);
});

test("050 corregir una palabra del dictado sigue contando como hablada", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-corregida" });
  await dictarConServidor(montaje);

  // Act: «cotiza 400 vasos de 12 onzas» → «cotiza 500 vasos de 12 onzas».
  montaje.vista.entrada.value = "cotiza 500 vasos de 12 onzas";
  montaje.vista.entrada.dispatch("input", {});
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-corregida"]);
  assert.equal(montaje.servidor.registro.reproducciones.length, 1);
});

test("050 una clave vieja de lectura automática no hace sonar una pregunta escrita", async () => {
  // Arrange: el navegador guarda la preferencia de la casilla retirada.
  const almacen = crearAlmacen({
    carvajal_voz_aviso_dictado_servidor: "1",
    carvajal_voz_autolectura: "1",
  });
  const montaje = montarNotas({ almacen });
  const turno = crearTurnoDom({ mensaje: "m-auto" });

  // Act
  enviarPregunta(montaje, "Hola");
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.servidor.registro.desbloqueos, 0);
  assert.deepEqual(montaje.servidor.registro.sintetizados, []);
  assert.equal(partesDeNota(turno).raiz, null);
});

test("050 el autoplay bloqueado deja la nota bloqueada sin cambiar de voz ni apagar el servidor", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montarNotas({ sintesis });
  const turno = crearTurnoDom({ mensaje: "m-bloqueada" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();
  const error = new Error("bloqueado");
  error.name = "NotAllowedError";

  // Act: play() rechazado por la política de autoplay.
  montaje.servidor.registro.reproducciones[0].llamadas.alFallar(error);
  montaje.reloj.avanzar(0);

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "bloqueada");
  assert.equal(nota.aviso.hidden, false);
  assert.equal(nota.aviso.textContent, montaje.api.MENSAJES.notaBloqueada);
  assert.equal(nota.estado.textContent, montaje.api.MENSAJES.notaBloqueada);
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
  assert.equal(montaje.documento.documentElement.dataset.vozRespaldo, undefined);
});

test("050 tocar una nota bloqueada la reproduce desde la caché dentro del gesto", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-bloqueada" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  completarTurno(montaje, turno);
  await vaciarPromesas();
  const error = new Error("bloqueado");
  error.name = "NotAllowedError";
  montaje.servidor.registro.reproducciones[0].llamadas.alFallar(error);

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert: sin otra síntesis y con play() síncrono.
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-bloqueada"]);
  assert.equal(montaje.servidor.registro.reproducciones.length, 2);
});

test("050 repetir una nota no paga otra síntesis", async () => {
  // Arrange
  const { montaje, turno } = montarConNota({}, { mensaje: "m-0" });
  const reproduccion = await notaQueSuena(montaje, turno);
  reproduccion.llamadas.alTerminar();

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-0"]);
  assert.equal(montaje.servidor.registro.reproducciones.length, 2);
});

test("050 la caché olvida la nota más vieja al pasar el límite de seis", async () => {
  // Arrange: se escuchan siete notas, una más que el límite.
  const turnos = Array.from({ length: 7 }, (_, indice) =>
    crearTurnoDom({ mensaje: `m-${indice}`, hablada: true }),
  );
  const montaje = montarNotas({ historial: turnos });
  for (const turno of turnos) {
    pulsar(montaje, partesDeNota(turno).boton);
    await vaciarPromesas();
  }

  // Act
  pulsar(montaje, partesDeNota(turnos[0]).boton);
  await vaciarPromesas();
  pulsar(montaje, partesDeNota(turnos[6]).boton);

  // Assert: m-0 salió de la caché y se pide de nuevo; m-6 sigue en ella.
  const veces = (id) => montaje.servidor.registro.sintetizados.filter((cada) => cada === id).length;
  assert.equal(veces("m-0"), 2);
  assert.equal(veces("m-6"), 1);
});

test("050 una respuesta dictada con la pestaña oculta se precarga sin sonar ni marcar el botón", async () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const montaje = montarNotas({ servidor });
  const turno = crearTurnoDom({ mensaje: "m-vuelo" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  montaje.documento.visibilityState = "hidden";

  // Act
  completarTurno(montaje, turno);

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "cargando");
  assert.equal(nota.boton.getAttribute("aria-busy"), "true");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "false");
  assert.equal(montaje.documento.documentElement.dataset.vozSintetizando, "m-vuelo");
  assert.equal(servidor.registro.reproducciones.length, 0);
});

test("050 la precarga y el clic comparten una sola síntesis en vuelo por mensaje", async () => {
  // Arrange: la respuesta dictada llega con la pestaña oculta y se precarga.
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const montaje = montarNotas({ servidor });
  const turno = crearTurnoDom({ mensaje: "m-vuelo" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  montaje.documento.visibilityState = "hidden";
  completarTurno(montaje, turno);
  montaje.documento.visibilityState = "visible";

  // Act: pulsa antes de que llegue el audio.
  pulsar(montaje, partesDeNota(turno).boton);
  servidor.registro.pendientes[0].resolver({ size: 10 });
  await vaciarPromesas();

  // Assert
  assert.deepEqual(servidor.registro.sintetizados, ["m-vuelo"]);
  assert.equal(servidor.registro.reproducciones.length, 1);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "cargando");
  assert.equal(montaje.documento.documentElement.dataset.vozSintetizando, undefined);
});

test("050 una precarga sin clic deja la nota lista con el audio en caché", async () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-precarga" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  montaje.documento.visibilityState = "hidden";
  completarTurno(montaje, turno);
  await vaciarPromesas();
  montaje.documento.visibilityState = "visible";

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert: el clic reproduce al instante (sin otra síntesis).
  assert.equal(montaje.servidor.registro.sintetizados.length, 1);
  assert.equal(montaje.servidor.registro.reproducciones.length, 1);
});

test("050 una respuesta que llega durante un dictado nuevo se precarga sin sonar", async () => {
  // Arrange: la pregunta fue dictada y la persona ya está dictando otra.
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-durante" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);
  clic(montaje.vista.botonDictar);
  await vaciarPromesas();

  // Act
  completarTurno(montaje, turno);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.controlador.estado.escuchando, true);
  assert.deepEqual(montaje.servidor.registro.sintetizados, ["m-durante"]);
  assert.equal(montaje.servidor.registro.reproducciones.length, 0);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "lista");
});

test("050 pulsar la nota mientras carga cancela la reproducción y la síntesis sigue", () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-cancela" });
  pulsar(montaje, partesDeNota(turno).boton);

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "lista");
  assert.equal(nota.boton.getAttribute("aria-pressed"), "false");
  assert.equal(nota.boton.getAttribute("aria-busy"), null);
  assert.equal(montaje.documento.documentElement.dataset.vozSintetizando, "m-cancela");
});

test("050 una nota cancelada al cargar no suena al llegar el audio y queda en caché", async () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-cancela" });
  pulsar(montaje, partesDeNota(turno).boton);
  pulsar(montaje, partesDeNota(turno).boton);
  servidor.registro.pendientes[0].resolver({ size: 10 });
  await vaciarPromesas();

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert: solo suena con el último clic, sin otra síntesis.
  assert.deepEqual(servidor.registro.sintetizados, ["m-cancela"]);
  assert.equal(servidor.registro.reproducciones.length, 1);
});

test("050 un 503 lee con el navegador dentro de la misma nota", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "circuit_open") });
  const { montaje, turno } = montarConNota({ servidor, sintesis }, { mensaje: "m-a", texto: "Primera respuesta." });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Assert
  assert.equal(sintesis.locuciones[0].text, "Primera respuesta.");
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "sonando");
  assert.equal(montaje.controlador.soporte.servidor.lectura, false);
  assert.equal(montaje.documento.documentElement.dataset.vozRespaldo, "1");
});

test("050 tras un 503 no se vuelve a pedir el audio al servidor antes de 60 s", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "circuit_open") });
  const primero = crearTurnoDom({ mensaje: "m-a", texto: "Primera respuesta.", hablada: true });
  const segundo = crearTurnoDom({ mensaje: "m-b", texto: "Segunda respuesta.", hablada: true });
  const montaje = montarNotas({ servidor, sintesis, historial: [primero, segundo] });
  pulsar(montaje, partesDeNota(primero).boton);
  await vaciarPromesas();
  sintesis.terminar();
  servidor.registro.falloSintetizar = null;

  // Act
  montaje.reloj.avanzar(59000);
  pulsar(montaje, partesDeNota(segundo).boton);
  await vaciarPromesas();

  // Assert
  assert.deepEqual(servidor.registro.sintetizados, ["m-a"]);
  assert.equal(sintesis.locuciones.at(-1).text, "Segunda respuesta.");
});

test("050 a los 60 s de un 503 se vuelve a probar la voz del servidor", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "circuit_open") });
  const primero = crearTurnoDom({ mensaje: "m-a", texto: "Primera respuesta.", hablada: true });
  const tercero = crearTurnoDom({ mensaje: "m-c", texto: "Tercera respuesta.", hablada: true });
  const montaje = montarNotas({ servidor, sintesis, historial: [primero, tercero] });
  pulsar(montaje, partesDeNota(primero).boton);
  await vaciarPromesas();
  sintesis.terminar();
  servidor.registro.falloSintetizar = null;

  // Act
  montaje.reloj.avanzar(60000);
  pulsar(montaje, partesDeNota(tercero).boton);
  await vaciarPromesas();

  // Assert
  assert.deepEqual(servidor.registro.sintetizados, ["m-a", "m-c"]);
  assert.equal(servidor.registro.reproducciones.length, 1);
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
  assert.equal(montaje.documento.documentElement.dataset.vozRespaldo, undefined);
});

/**
 * La voz del servidor queda en espera mientras suena la nota A: se pide C (sin caché),
 * se pasa a A (en caché) y la síntesis de C falla con 503.
 */
async function notaSonandoConServidorEnEspera() {
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const [a, b, c] = ["m-a", "m-b", "m-c"].map((mensaje) =>
    crearTurnoDom({ mensaje, texto: `Respuesta ${mensaje}.`, hablada: true }),
  );
  const montaje = montarNotas({ servidor, sintesis, historial: [a, b, c] });
  pulsar(montaje, partesDeNota(a).boton);
  servidor.registro.pendientes[0].resolver({ size: 10 });
  await vaciarPromesas();
  pulsar(montaje, partesDeNota(c).boton);
  pulsar(montaje, partesDeNota(a).boton);
  const reproduccionA = servidor.registro.reproducciones.at(-1);
  reproduccionA.llamadas.alEmpezar();
  servidor.registro.pendientes[1].rechazar(errorHttp(503, "circuit_open"));
  await vaciarPromesas();
  return { montaje, sintesis, reproduccionA, a, b };
}

test("050 con el servidor en espera tocar otra nota corta la que sonaba antes del respaldo", async () => {
  // Arrange
  const { montaje, sintesis, reproduccionA, a, b } = await notaSonandoConServidorEnEspera();

  // Act
  pulsar(montaje, partesDeNota(b).boton);

  // Assert: una sola voz a la vez.
  assert.equal(reproduccionA.detenciones, 1);
  assert.equal(partesDeNota(a).raiz.dataset.notaEstado, "lista");
  assert.equal(partesDeNota(b).raiz.dataset.notaEstado, "sonando");
  assert.equal(sintesis.locuciones.at(-1).text, "Respuesta m-b.");
});

test("050 con el servidor en espera la pausa llega a la voz del navegador", async () => {
  // Arrange
  const { montaje, sintesis, reproduccionA, b } = await notaSonandoConServidorEnEspera();
  pulsar(montaje, partesDeNota(b).boton);

  // Act
  pulsar(montaje, partesDeNota(b).boton);

  // Assert
  assert.equal(sintesis.paused, true);
  assert.equal(reproduccionA.pausas, 0);
  assert.equal(partesDeNota(b).raiz.dataset.notaEstado, "pausada");
});

test("050 sin voz del navegador un 503 avisa que la voz no está disponible", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "off") });
  const { montaje, turno } = montarConNota({ servidor, sintesis: false }, { mensaje: "m-off" });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "error");
  assert.equal(nota.aviso.textContent, "La voz no está disponible ahora.");
  assert.equal(nota.reintentar.hidden, false);
});

test("050 un 401 o 403 también deja la voz del servidor en espera", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(403, "csrf") });
  const { montaje, turno } = montarConNota({ servidor, sintesis }, { mensaje: "m-403", texto: "Respuesta." });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.controlador.soporte.servidor.lectura, false);
  assert.equal(sintesis.locuciones.length, 1);
});

test("050 un 429 deja la nota en error con reintento y no apaga el servidor", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(429, "rate_limited") });
  const { montaje, turno } = montarConNota({ servidor, sintesis }, { mensaje: "m-429" });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Assert
  const nota = partesDeNota(turno);
  assert.equal(nota.raiz.dataset.notaEstado, "error");
  assert.equal(nota.aviso.textContent, montaje.api.MENSAJES.notaOcupada);
  assert.equal(nota.reintentar.hidden, false);
  assert.equal(nota.boton.getAttribute("aria-label"), "Reproducir la nota de voz");
  assert.equal(sintesis.locuciones.length, 0);
  assert.equal(montaje.controlador.soporte.servidor.lectura, true);
});

test("050 reintentar con el foco en «Reintentar» lo pasa al botón principal y pide el audio", async () => {
  // Arrange
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(429, "rate_limited") });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-429" });
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();
  servidor.registro.falloSintetizar = null;
  const nota = partesDeNota(turno);
  montaje.documento.activeElement = nota.reintentar;

  // Act
  pulsar(montaje, nota.reintentar);
  await vaciarPromesas();

  // Assert: al ocultarse «Reintentar», el foco no cae al body.
  assert.equal(nota.reintentar.hidden, true);
  assert.equal(nota.boton.focused, true);
  assert.deepEqual(servidor.registro.sintetizados, ["m-429", "m-429"]);
  assert.equal(servidor.registro.reproducciones.length, 1);
});

test("050 si el foco estaba en la franja al nacer la nota pasa a su botón", () => {
  // Arrange
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-foco" });
  const franja = turno.querySelector("[data-voz-turno]");
  const escuchar = crearNodoDom("button");
  franja.appendChild(escuchar);
  montaje.documento.activeElement = escuchar;

  // Act: termina un turno hablado (con la franja visible hasta que nace la nota).
  completarTurno(montaje, turno, { spoken: true });

  // Assert
  assert.equal(franja.hidden, true);
  assert.equal(partesDeNota(turno).boton.focused, true);
});

test("050 el primer anuncio de una nota nueva no se escribe en la misma tarea", () => {
  // Arrange: la región viva acaba de entrar en el árbol de accesibilidad.
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-anuncio" });

  // Act
  pulsar(montaje, partesDeNota(turno).boton);

  // Assert
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "cargando");
  assert.equal(partesDeNota(turno).estado.textContent, "");
});

test("050 el primer anuncio de una nota nueva llega en la tarea siguiente", () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-anuncio" });
  pulsar(montaje, partesDeNota(turno).boton);

  // Act
  montaje.reloj.avanzar(0);

  // Assert
  assert.equal(partesDeNota(turno).estado.textContent, montaje.api.MENSAJES.notaCargando);
});

test("050 manos libres usa la nota y la respuesta suena sola", async () => {
  // Arrange
  const montaje = montarNotas();
  await preguntarConManosLibres(montaje);
  const turno = crearTurnoDom({ mensaje: "m-manos" });

  // Act
  completarTurno(montaje, turno, { handsFree: true });
  await vaciarPromesas();
  montaje.servidor.registro.reproducciones[0].llamadas.alEmpezar();

  // Assert
  assert.equal(montaje.servidor.registro.desbloqueos >= 1, true);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "sonando");
});

test("050 en manos libres al terminar la nota se vuelve a escuchar", async () => {
  // Arrange
  const montaje = montarNotas();
  await preguntarConManosLibres(montaje);
  const turno = crearTurnoDom({ mensaje: "m-manos" });
  completarTurno(montaje, turno, { handsFree: true });
  await vaciarPromesas();
  const [reproduccion] = montaje.servidor.registro.reproducciones;
  reproduccion.llamadas.alEmpezar();

  // Act
  reproduccion.llamadas.alTerminar();
  await vaciarPromesas();

  // Assert: el ciclo sigue: otra grabación y la nota queda lista para repetirla.
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "lista");
  assert.equal(montaje.servidor.registro.grabaciones, 2);
  assert.equal(montaje.controlador.manosLibres.activo, true);
});

test("050 en manos libres una nota que no se puede preparar apaga el modo sin culpar al micrófono", async () => {
  // Arrange
  const montaje = montarNotas();
  await preguntarConManosLibres(montaje);
  montaje.servidor.registro.falloSintetizar = errorHttp(422, "invalid");
  const turno = crearTurnoDom({ mensaje: "m-manos-422" });

  // Act
  completarTurno(montaje, turno, { handsFree: true });
  await vaciarPromesas();

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.manosNota);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "error");
});

test("050 en manos libres un audio que no suena apaga el modo con el aviso de la nota", async () => {
  // Arrange
  const montaje = montarNotas();
  await preguntarConManosLibres(montaje);
  const turno = crearTurnoDom({ mensaje: "m-manos-decodificar" });
  completarTurno(montaje, turno, { handsFree: true });
  await vaciarPromesas();
  const error = new Error("no se pudo decodificar");
  error.name = "NotSupportedError";

  // Act
  montaje.servidor.registro.reproducciones[0].llamadas.alFallar(error);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.manosNota);
});

test("050 en manos libres un autoplay bloqueado apaga el modo y deja la nota para tocarla", async () => {
  // Arrange
  const montaje = montarNotas();
  clic(montaje.vista.botonManosLibres);
  if (montaje.vista.avisoManos.open) clic(montaje.vista.manosAceptar);
  await vaciarPromesas();
  montaje.servidor.registro.llamadas.alPausa();
  await vaciarPromesas();
  montaje.vista.entrada.value = "";
  const turno = crearTurnoDom({ mensaje: "m-manos-bloqueo" });
  completarTurno(montaje, turno, { handsFree: true });
  await vaciarPromesas();

  // Act
  const error = new Error("bloqueado");
  error.name = "NotAllowedError";
  montaje.servidor.registro.reproducciones[0].llamadas.alFallar(error);

  // Assert
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.manosBloqueo);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "bloqueada");
  assert.equal(montaje.servidor.registro.grabaciones, 1);
});

test("050 el historial recibe su nota al montar sin pedir ningún audio", () => {
  // Arrange: respuesta a una pregunta hablada (la plantilla la marca).
  const turno = crearTurnoDom({ mensaje: "m-historial", hablada: true });

  // Act
  const montaje = montarNotas({ historial: [turno] });

  // Assert
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "lista");
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
});

test("050 una respuesta del historial a una pregunta escrita no recibe nota", () => {
  // Arrange: sin data-voz-pregunta-hablada, como la deja la plantilla.
  const escrita = crearTurnoDom({ mensaje: "m-escrita" });
  const hablada = crearTurnoDom({ mensaje: "m-hablada", hablada: true });

  // Act
  const montaje = montarNotas({ historial: [escrita, hablada] });

  // Assert
  assert.equal(partesDeNota(escrita).raiz, null);
  assert.equal(escrita.dataset.vozNota, undefined);
  assert.equal(partesDeNota(hablada).raiz.dataset.notaEstado, "lista");
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
});

test("050 salir de la página aborta la síntesis en vuelo y libera el audio", async () => {
  // Arrange
  const servidor = crearServidorFalso({ sintesisPendiente: true });
  const { montaje, turno } = montarConNota({ servidor }, { mensaje: "m-salida" });
  pulsar(montaje, partesDeNota(turno).boton);
  const [{ signal }] = servidor.registro.opcionesSintetizar;

  // Act
  montaje.ventana.dispatch("pagehide", {});
  const abortado = new Error("abortado");
  abortado.name = "AbortError";
  servidor.registro.pendientes[0].rechazar(abortado);
  await vaciarPromesas();

  // Assert
  assert.equal(signal.aborted, true);
  assert.equal(servidor.registro.liberaciones, 1);
  assert.equal(partesDeNota(turno).raiz.dataset.notaEstado, "lista");
  assert.equal(montaje.documento.documentElement.dataset.vozSintetizando, undefined);
  assert.equal(servidor.registro.reproducciones.length, 0);
});

test("050 sin voz del servidor una pregunta dictada se lee sola con el navegador", () => {
  // Arrange
  const sintesis = crearSintesis();
  const reconocimiento = crearReconocimiento();
  const almacen = crearAlmacen({ carvajal_voz_aviso_dictado: "1" });
  const montaje = montar({ sintesis, reconocimiento, almacen });
  clic(montaje.vista.botonDictar);
  reconocimiento.ultima().resultado("vasos de papel");
  reconocimiento.ultima().fin();

  // Act
  enviarPregunta(montaje);
  montaje.controlador.manejarTurnoCompleto({
    detail: { turno: crearTurnoFalso({ texto: "Tenemos vasos." }), threadId: "hilo-1" },
  });

  // Assert
  assert.equal(sintesis.locuciones.length, 1);
  assert.equal(sintesis.locuciones[0].text, "Tenemos vasos.");
});

// --- Adaptador: un solo <audio>, desbloqueo y URL blob: -------------------------

function crearVentanaAudio({ rechazo = null } = {}) {
  const instancias = [];
  const creadas = [];
  const revocadas = [];
  class AudioFalso {
    constructor() {
      this.src = "";
      this.reproducciones = [];
      this.pausas = 0;
      this.currentTime = 0;
      this.duration = Number.NaN;
      instancias.push(this);
    }
    play() {
      this.reproducciones.push(this.src);
      return rechazo ? Promise.reject(rechazo) : Promise.resolve();
    }
    pause() {
      this.pausas += 1;
    }
  }
  let contador = 0;
  return {
    instancias,
    creadas,
    revocadas,
    fetch: () => Promise.reject(new Error("sin red")),
    URLSearchParams,
    Blob: class {
      constructor(partes, opciones) {
        this.partes = partes;
        this.type = opciones && opciones.type;
      }
    },
    Audio: AudioFalso,
    URL: {
      createObjectURL: () => {
        contador += 1;
        const url = `blob:${contador}`;
        creadas.push(url);
        return url;
      },
      revokeObjectURL: (url) => revocadas.push(url),
    },
    navigator: {},
  };
}

test("050 el adaptador desbloquea con un silencio y reutiliza un solo <audio> para las notas", async () => {
  // Arrange
  const ventana = crearVentanaAudio();
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana);

  // Act: el gesto desbloquea con un WAV en silencio servido por blob:.
  servidor.desbloquear();
  const [elemento] = ventana.instancias;
  const silencio = elemento.src;
  await vaciarPromesas();
  servidor.reproducir({ size: 10 }, {});
  servidor.reproducir({ size: 12 }, {});

  // Assert
  assert.equal(ventana.instancias.length, 1);
  assert.equal(silencio, "blob:1");
  assert.deepEqual(elemento.reproducciones, ["blob:1", "blob:2", "blob:3"]);
  assert.equal(elemento.pausas >= 1, true);
  assert.deepEqual(ventana.revocadas, ["blob:1", "blob:2"]);

  // Act: ya desbloqueado, otro gesto no vuelve a tocar el silencio.
  servidor.desbloquear();

  // Assert
  assert.equal(ventana.creadas.length, 3);
});

test("050 el adaptador revoca la URL blob: al terminar, al detener y al salir", () => {
  // Arrange
  const ventana = crearVentanaAudio();
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana);
  let terminadas = 0;
  const avances = [];

  // Act: termina una nota, se detiene otra y la tercera queda al salir.
  servidor.reproducir({ size: 1 }, {
    alTerminar: () => {
      terminadas += 1;
    },
    alProgreso: (actual, total) => avances.push([actual, total]),
  });
  const [elemento] = ventana.instancias;
  elemento.currentTime = 17;
  elemento.duration = 17;
  elemento.onended();
  const segunda = servidor.reproducir({ size: 2 }, {});
  segunda.detener();
  servidor.reproducir({ size: 3 }, {});
  servidor.liberar();

  // Assert
  assert.equal(terminadas, 1);
  assert.deepEqual(avances, [[17, 17]]);
  assert.deepEqual(ventana.revocadas, ventana.creadas);
});

test("050 el adaptador entrega el NotAllowedError del autoplay al controlador", async () => {
  // Arrange
  const rechazo = new Error("autoplay");
  rechazo.name = "NotAllowedError";
  const ventana = crearVentanaAudio({ rechazo });
  const servidor = cargarVoz().window.CarvajalVoz.crearServidorVoz(ventana);
  let recibido = null;

  // Act
  servidor.reproducir({ size: 1 }, {
    alFallar: (error) => {
      recibido = error;
    },
  });
  await vaciarPromesas();

  // Assert
  assert.equal(recibido && recibido.name, "NotAllowedError");
  assert.deepEqual(ventana.revocadas, ventana.creadas);
});

test("050 la respuesta con nota marca la <li> con data-voz-nota para el rediseño", () => {
  // Arrange
  const montaje = montarServidor();
  const turno = turnoConMensaje("Hola, soy Carva.", "m-555");

  // Act
  montaje.controlador.leerTurno(turno);

  // Assert
  assert.equal(turno.dataset.vozNota, "1");
});

test("050 en el respaldo del navegador el avance de la nota crece al terminar cada parte", async () => {
  // Arrange
  const sintesis = crearSintesis();
  const servidor = crearServidorFalso({ falloSintetizar: errorHttp(503, "circuit_open") });
  const turno = crearTurnoDom({
    mensaje: "m-b",
    texto: "Primera parte. Segunda parte.",
    hablada: true,
  });
  turno.dataset.vozResumen = "Primera parte.\n\nSegunda parte.";
  const montaje = montarNotas({ servidor, sintesis, historial: [turno] });
  pulsar(montaje, partesDeNota(turno).boton);
  await vaciarPromesas();

  // Act: termina la primera de dos partes.
  sintesis.terminar();

  // Assert
  assert.equal(sintesis.locuciones.length, 2);
  assert.equal(partesDeNota(turno).barra.getAttribute("aria-valuenow"), "50");
});

// ---------------------------------------------------------------------------
// 052 · Modo de voz: marcas para la escena y el turno «por voz»
// ---------------------------------------------------------------------------

test("052 un borrador dictado marca el envío como hablado; uno escrito no", async () => {
  // Arrange
  const montaje = montarNotas();

  // Act: primero una pregunta escrita.
  enviarPregunta(montaje, "¿Qué vasos tienen?");

  // Assert
  assert.equal(montaje.vista.formulario.dataset.vozHablado, undefined);

  // Act: ahora un dictado enviado tal cual.
  montaje.vista.entrada.value = "";
  await dictarConServidor(montaje);
  enviarPregunta(montaje);

  // Assert: chat-stream.js leerá y borrará la marca para mostrar «por voz».
  assert.equal(montaje.vista.formulario.dataset.vozHablado, "1");

  // Act: si nadie la leyó, el siguiente envío escrito la borra.
  enviarPregunta(montaje, "Ahora la escribo");

  // Assert
  assert.equal(montaje.vista.formulario.dataset.vozHablado, undefined);
});

test("052 leer una respuesta marca data-voz-leyendo y detenerla la quita", () => {
  // Arrange
  const sintesis = crearSintesis();
  const montaje = montar({ sintesis });
  const turno = crearTurnoFalso({ texto: "Leyendo." });

  // Act
  montaje.controlador.leerTurno(turno);

  // Assert
  assert.equal(montaje.documento.documentElement.dataset.vozLeyendo, "1");

  // Act: ocultar la pestaña detiene la lectura.
  montaje.documento.visibilityState = "hidden";
  montaje.documento.dispatch("visibilitychange", {});

  // Assert
  assert.equal(montaje.documento.documentElement.dataset.vozLeyendo, undefined);
});

// ---------------------------------------------------------------------------
// Foto con texto dictado: `spoken` de chat-stream.js manda (la recarga no tendría audio)
// ---------------------------------------------------------------------------

test("foto con un borrador dictado no crea nota ni suena aunque el dictado lo marque", async () => {
  // Arrange: el texto que acompaña a la foto se dictó; /chat/foto no guarda la marca.
  const sintesis = crearSintesis();
  const montaje = montarNotas({ sintesis });
  const turno = crearTurnoDom({ mensaje: "m-foto" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);

  // Act: chat-stream.js avisa el fin del turno con `spoken: false` por la foto.
  completarTurno(montaje, turno, { spoken: false });
  await vaciarPromesas();

  // Assert: ni nota, ni marca hablada, ni síntesis o reproducción automática.
  assert.equal(partesDeNota(turno).raiz, null);
  assert.equal(turno.dataset.vozNota, undefined);
  assert.equal(turno.dataset.vozPreguntaHablada, undefined);
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(montaje.servidor.registro.reproducciones.length, 0);
  assert.equal(sintesis.locuciones.length, 0);
});

test("sin `spoken` en el evento el borrador dictado sigue trayendo la nota", async () => {
  // Arrange: un emisor viejo que no manda `spoken` (respaldo del origen local).
  const montaje = montarNotas();
  const turno = crearTurnoDom({ mensaje: "m-sin-spoken" });
  await dictarConServidor(montaje);
  enviarPregunta(montaje);

  // Act
  montaje.documento.dispatch("chat:turn-complete", {
    detail: { turno, threadId: "hilo-1", handsFree: false },
  });
  await vaciarPromesas();

  // Assert
  assert.ok(partesDeNota(turno).raiz);
  assert.equal(turno.dataset.vozPreguntaHablada, "1");
});

test("una foto en manos libres no suena y apaga el modo con su motivo", async () => {
  // Arrange: manos libres envía lo dictado con una foto adjunta.
  const montaje = montarNotas();
  await preguntarConManosLibres(montaje);
  const grabaciones = montaje.servidor.registro.grabaciones;
  const turno = crearTurnoDom({ mensaje: "m-manos-foto" });

  // Act
  completarTurno(montaje, turno, { handsFree: true, spoken: false });
  await vaciarPromesas();

  // Assert: sin nota ni audio, el modo se apaga y no vuelve a abrir el micrófono.
  assert.equal(partesDeNota(turno).raiz, null);
  assert.equal(turno.dataset.vozPreguntaHablada, undefined);
  assert.equal(montaje.servidor.registro.sintetizados.length, 0);
  assert.equal(montaje.servidor.registro.reproducciones.length, 0);
  assert.equal(montaje.controlador.manosLibres.activo, false);
  assert.equal(montaje.vista.estado.textContent, montaje.api.MENSAJES.manosFoto);
  assert.equal(montaje.servidor.registro.grabaciones, grabaciones);
});

// ---------------------------------------------------------------------------
// «Opciones de voz»: el panel solo guarda Manos libres y nunca se abre vacío
// ---------------------------------------------------------------------------

function crearPanelOpciones() {
  return crearElemento("details", { hidden: true });
}

test("con lectura pero sin dictado el panel «Opciones de voz» queda oculto", () => {
  // Arrange: sin contexto seguro no hay dictado y Manos libres no se ofrece.
  const opciones = crearPanelOpciones();

  // Act
  const montaje = montar({ segura: false, vista: crearVista({ opciones }) });

  // Assert
  assert.equal(montaje.controlador.soporte.sintesis, true);
  assert.equal(montaje.vista.botonManosLibres.hidden, true);
  assert.equal(opciones.hidden, true);
});

test("con dictado, lectura y streaming el panel muestra Manos libres", () => {
  // Arrange
  const opciones = crearPanelOpciones();

  // Act
  const montaje = montar({ vista: crearVista({ opciones }) });

  // Assert
  assert.equal(montaje.vista.botonManosLibres.hidden, false);
  assert.equal(opciones.hidden, false);
});

test("si el dictado se pierde, el panel se oculta junto con Manos libres", async () => {
  // Arrange: solo el servidor dicta; al fallar no hay respaldo del navegador.
  const servidor = crearServidorFalso({ falloTranscribir: errorHttp(503, "off") });
  const sandbox = cargarVoz({ reconocimiento: false });
  const opciones = crearPanelOpciones();
  const vista = crearVista({ opciones });
  const controlador = sandbox.window.CarvajalVoz.createVoiceController({
    vista,
    documento: crearDocumento(),
    almacen: crearAlmacen({ carvajal_voz_aviso_dictado_servidor: "1" }),
    reconocimiento: null,
    sintesis: crearSintesis(),
    Utterance: sandbox.SpeechSynthesisUtterance,
    reloj: crearReloj(),
    segura: true,
    streaming: true,
    servidor,
  });
  controlador.mount();
  assert.equal(opciones.hidden, false);

  // Act
  clic(vista.botonDictar);
  await vaciarPromesas();
  clic(vista.botonDictar);
  await vaciarPromesas();

  // Assert
  assert.equal(vista.botonManosLibres.hidden, true);
  assert.equal(opciones.hidden, true);
});

test("init oculta «Opciones de voz» cuando hay síntesis pero no Manos libres", () => {
  // Arrange: el sandbox no trae fetch/ReadableStream, así que no hay streaming.
  const raiz = crearElemento("section", { hidden: true });
  const opciones = crearElemento("details", { hidden: false });
  const manos = crearElemento("button", { hidden: true });
  const hijos = {
    "[data-voz-dictar]": crearElemento("button", { hidden: true }),
    "[data-voz-dictar-texto]": crearElemento("span"),
    "[data-voz-estado]": crearElemento("p"),
    "[data-voz-nota]": crearElemento("p", { hidden: true }),
    "[data-voz-manos-libres]": manos,
    "[data-voz-opciones]": opciones,
  };
  raiz.querySelector = (selector) => hijos[selector] || null;
  const documento = crearDocumento({
    "[data-voz]": raiz,
    "form[data-chat-stream]": crearFormulario(),
  });

  // Act
  const sandbox = cargarVoz({ documento });

  // Assert
  assert.equal(sandbox.window.CarvajalVoz !== undefined, true);
  assert.equal(raiz._controladorVoz.soporte.sintesis, true);
  assert.equal(manos.hidden, true);
  assert.equal(opciones.hidden, true);
});
