// Foto de un alimento en el chat web (spec 051).
//
// Carga chat-photo.js y chat-stream.js en un contexto `vm` con dobles mínimos del DOM y un
// `fetch` falso. Fija el contrato: validación en el cliente, texto opcional con foto, aviso
// visible, envío multipart a /chat/foto con el CSRF en cabecera y la foto devuelta al
// compositor si el envío falla antes de llegar al servidor.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "..", "apps", "api", "static");
const FOTO = fs.readFileSync(path.join(STATIC, "chat-photo.js"), "utf8");
const STREAM = fs.readFileSync(path.join(STATIC, "chat-stream.js"), "utf8");

function crearElemento(etiqueta = "div", selectores = {}) {
  const oyentes = new Map();
  const atributos = new Map();
  const clases = new Set();
  return {
    etiqueta,
    hidden: false,
    required: true,
    value: "",
    textContent: "",
    dataset: {},
    children: [],
    files: null,
    classList: {
      add: (...nombres) => nombres.forEach((nombre) => clases.add(nombre)),
      remove: (...nombres) => nombres.forEach((nombre) => clases.delete(nombre)),
      contains: (nombre) => clases.has(nombre),
      toggle: () => false,
    },
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
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
    append(...hijos) {
      this.children.push(...hijos);
    },
    closest() {
      return this.formulario || null;
    },
    after() {},
    remove() {},
    focus() {},
    querySelector(selector) {
      return selectores[selector] || null;
    },
    querySelectorAll() {
      return [];
    },
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
  };
}

class FormDataFalso {
  constructor() {
    this.partes = [];
  }
  append(nombre, valor, archivo) {
    this.partes.push([nombre, valor, archivo]);
  }
  get(nombre) {
    const parte = this.partes.find(([clave]) => clave === nombre);
    return parte ? parte[1] : null;
  }
}

const esperarTareas = async () => {
  for (let vuelta = 0; vuelta < 6; vuelta++) await new Promise(setImmediate);
};

function montar({ falla = false } = {}) {
  const peticiones = [];
  const codificador = new TextEncoder();
  let control;
  const fetch = (url, opciones) => {
    peticiones.push({ url, opciones });
    if (falla) return Promise.reject(new TypeError("red caída"));
    const body = new ReadableStream({
      start(controlador) {
        control = controlador;
      },
    });
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: { get: () => "text/event-stream" },
      body,
    });
  };

  const pregunta = crearElemento("textarea");
  pregunta.setAttribute("placeholder", "Escribe o dicta tu pregunta");
  const csrf = crearElemento("input");
  csrf.value = "csrf-token";
  const entrada = crearElemento("input");
  const nombre = crearElemento("span");
  const quitar = crearElemento("button");
  const ficha = crearElemento("span");
  ficha.hidden = true;
  const aviso = crearElemento("p");
  aviso.hidden = true;
  const raiz = crearElemento("div", {
    "[data-foto-input]": entrada,
    "[data-foto-ficha]": ficha,
    "[data-foto-nombre]": nombre,
    "[data-foto-quitar]": quitar,
  });
  raiz.hidden = true;
  const formulario = crearElemento("form", {
    '[name="question"]': pregunta,
    'input[name="csrf_token"]': csrf,
    "[data-foto-aviso]": aviso,
  });
  formulario.submit = () => {
    throw new Error("la foto nunca se reenvía por el POST normal");
  };
  raiz.formulario = formulario;
  const estado = crearElemento("p");
  const hilo = crearElemento("section");
  const historial = crearElemento("div");
  const turnos = [];
  const plantilla = {
    content: {
      firstElementChild: {
        cloneNode: () => {
          const texto = crearElemento("div");
          const autor = crearElemento("span");
          const turno = crearElemento("li", { ".texto": texto, "[data-turno-autor]": autor });
          turno.texto = texto;
          turnos.push(turno);
          return turno;
        },
      },
    },
  };
  const porId = { hilo, "chat-stream-estado": estado, "plantilla-turno": plantilla };
  const porSelector = {
    "[data-foto]": raiz,
    "form[data-chat-stream]": formulario,
    ".historial-chat": historial,
  };
  const document = {
    activeElement: null,
    getElementById: (id) => porId[id] || null,
    querySelector: (selector) => porSelector[selector] || null,
    createElement: (etiqueta) => crearElemento(etiqueta),
    addEventListener() {},
  };
  const window = {
    document,
    location: { search: "" },
    fetch,
    FormData: FormDataFalso,
    TextDecoder,
    ReadableStream,
    URLSearchParams,
    AbortController,
    CarvajalTexto: {
      crearFlujo(salida) {
        let acumulado = "";
        return {
          agregar(fragmento) {
            acumulado += fragmento;
          },
          adelantar() {
            salida.textContent = acumulado;
          },
          terminar() {
            salida.textContent = acumulado;
          },
          reemplazar(texto) {
            acumulado = texto;
            salida.textContent = texto;
          },
        };
      },
    },
    setTimeout,
    clearTimeout,
    console,
  };
  window.window = window;
  const contexto = vm.createContext(window);
  vm.runInContext(FOTO, contexto);
  vm.runInContext(STREAM, contexto);

  function elegir(archivo) {
    entrada.files = [archivo];
    entrada.dispatch("change");
  }

  async function enviar(texto = "") {
    pregunta.value = texto;
    formulario.dispatch("submit", { preventDefault() {} });
    await esperarTareas();
  }

  async function evento(nombreEvento, datos) {
    control.enqueue(codificador.encode(`event: ${nombreEvento}\ndata: ${JSON.stringify(datos)}\n\n`));
    await esperarTareas();
  }

  return { window, raiz, pregunta, ficha, aviso, nombre, estado, turnos, peticiones, elegir, enviar, evento, quitar };
}

const JPEG = { name: "ajiaco.jpg", type: "image/jpeg", size: 120_000 };

test("el botón aparece y elegir una foto muestra su nombre y el aviso", () => {
  // Arrange
  const chat = montar();
  assert.equal(chat.raiz.hidden, false);

  // Act
  chat.elegir(JPEG);

  // Assert
  assert.equal(chat.ficha.hidden, false);
  assert.equal(chat.nombre.textContent, "ajiaco.jpg");
  assert.equal(chat.aviso.hidden, false);
  assert.equal(chat.pregunta.required, false);
  assert.equal(chat.window.CarvajalFoto.tomar(), JPEG);
});

test("rechaza en el cliente formatos y tamaños no admitidos", () => {
  // Arrange
  const chat = montar();

  // Act
  chat.elegir({ name: "foto.heic", type: "image/heic", size: 1000 });
  const heic = chat.window.CarvajalFoto.tomar();
  chat.elegir({ name: "enorme.jpg", type: "image/jpeg", size: 11 * 1024 * 1024 });

  // Assert
  assert.equal(heic, null);
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
  assert.match(chat.estado.textContent, /10 MB/);
  assert.equal(chat.pregunta.required, true);
});

test("quitar la foto vuelve a exigir texto", () => {
  // Arrange
  const chat = montar();
  chat.elegir(JPEG);

  // Act
  chat.quitar.dispatch("click");

  // Assert
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
  assert.equal(chat.aviso.hidden, true);
  assert.equal(chat.pregunta.required, true);
});

test("con foto envía multipart a /chat/foto con el CSRF en cabecera y sin texto", async () => {
  // Arrange
  const chat = montar();
  chat.elegir(JPEG);

  // Act
  await chat.enviar("");
  await chat.evento("fallback", { text: "Veo una sopa…", sources: [] });

  // Assert
  assert.equal(chat.peticiones.length, 1);
  const { url, opciones } = chat.peticiones[0];
  assert.equal(url, "/chat/foto");
  assert.equal(opciones.headers["X-CSRF-Token"], "csrf-token");
  assert.equal(opciones.body.get("foto"), JPEG);
  assert.equal(opciones.body.get("texto"), "");
  assert.equal(chat.turnos[0].texto.textContent, "📷 Foto de un alimento");
  assert.equal(chat.turnos[1].texto.textContent, "Veo una sopa…");
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
});

test("si la red falla, devuelve la foto y el texto sin reenviar por POST normal", async () => {
  // Arrange
  const chat = montar({ falla: true });
  chat.elegir(JPEG);

  // Act
  await chat.enviar("para domicilio");

  // Assert
  assert.equal(chat.window.CarvajalFoto.tomar(), JPEG);
  assert.equal(chat.pregunta.value, "para domicilio");
  assert.match(chat.estado.textContent, /No se pudo enviar la foto/);
});

test("sin foto, la pregunta sigue yendo a /chat/stream", async () => {
  // Arrange
  const chat = montar();

  // Act
  await chat.enviar("¿Tienen vasos de 12 oz?");

  // Assert
  assert.equal(chat.peticiones[0].url, "/chat/stream");
});
