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
    insertBefore(hijo) {
      this.children.unshift(hijo);
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

function montar({ falla = false, vista = false } = {}) {
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
  const zona = crearElemento("div");
  zona.hidden = true;
  const hilo = crearElemento("section");
  const historial = crearElemento("div");
  const turnos = [];
  const plantilla = {
    content: {
      firstElementChild: {
        cloneNode: () => {
          const texto = crearElemento("div");
          const autor = crearElemento("span");
          const burbuja = crearElemento("div");
          const turno = crearElemento("li", {
            ".texto": texto,
            "[data-turno-autor]": autor,
            ".burbuja": burbuja,
          });
          turno.texto = texto;
          turno.burbuja = burbuja;
          turnos.push(turno);
          return turno;
        },
      },
    },
  };
  const porId = { hilo, "chat-stream-estado": estado, "plantilla-turno": plantilla };
  const porSelector = {
    "[data-foto]": raiz,
    "[data-foto-soltar]": zona,
    "form[data-chat-stream]": formulario,
    ".historial-chat": historial,
  };
  const oyentesDocumento = new Map();
  const document = {
    activeElement: null,
    getElementById: (id) => porId[id] || null,
    querySelector: (selector) => porSelector[selector] || null,
    createElement: (etiqueta) => crearElemento(etiqueta),
    addEventListener(tipo, manejador) {
      if (!oyentesDocumento.has(tipo)) oyentesDocumento.set(tipo, []);
      oyentesDocumento.get(tipo).push(manejador);
    },
  };
  const urls = [];
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
  if (vista) {
    window.URL = {
      createObjectURL: () => {
        urls.push(`blob:foto-${urls.length + 1}`);
        return urls[urls.length - 1];
      },
      revokeObjectURL() {},
    };
  }
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

  function soltar(evento) {
    for (const manejador of oyentesDocumento.get(evento.type) || []) manejador(evento);
  }

  return {
    window, raiz, formulario, pregunta, ficha, aviso, nombre, estado, zona, turnos, peticiones,
    elegir, enviar, evento, quitar, soltar, urls,
  };
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

test("al adjuntar, el compositor entero se desplaza a la vista (campo y Enviar incluidos)", () => {
  // Arrange: ensayo del 2026-11-26, hilo nuevo a 320×640: con la ficha a la vista, el campo
  // y «Enviar» quedaban tapados bajo la cabecera.
  const chat = montar();
  const compositor = [];
  const ficha = [];
  chat.formulario.scrollIntoView = (opciones) => compositor.push(opciones);
  chat.ficha.scrollIntoView = (opciones) => ficha.push(opciones);

  // Act
  chat.elegir(JPEG);
  chat.quitar.dispatch("click");

  // Assert: solo al adjuntar, sin saltar la página entera, y nunca solo la ficha.
  assert.equal(JSON.stringify(compositor), JSON.stringify([{ block: "nearest" }]));
  assert.deepEqual(ficha, []);
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

// --- Spec 053: la foto se ve, Carva mira y se adjunta arrastrando o pegando -----------------

test("con vista local, la burbuja muestra la foto y debajo el texto", async () => {
  // Arrange
  const chat = montar({ vista: true });
  chat.elegir(JPEG);

  // Act
  await chat.enviar("para domicilio");

  // Assert
  const [imagen] = chat.turnos[0].burbuja.children;
  assert.equal(imagen.className, "turno-foto");
  assert.match(imagen.src, /^blob:foto-/);
  assert.equal(imagen.alt, "Foto del alimento que enviaste");
  assert.equal(chat.turnos[0].texto.textContent, "para domicilio");
});

test("mientras analiza la foto, la línea de estado dice «Mirando tu foto…»", async () => {
  // Arrange
  const chat = montar({ vista: true });
  chat.elegir(JPEG);

  // Act
  await chat.enviar("");
  await new Promise((resolve) => setTimeout(resolve, 350));
  const durante = chat.estado.textContent;
  await chat.evento("fallback", { text: "Veo una sopa…", sources: [] });

  // Assert
  assert.equal(durante, "Mirando tu foto…");
  assert.equal(chat.estado.textContent, "");
  assert.equal(chat.turnos[1].dataset.mirando, undefined);
});

test("pegar una imagen la adjunta; pegar texto sigue pegando texto", () => {
  // Arrange
  const chat = montar();
  let evitado = 0;
  const pegar = (datos) =>
    chat.pregunta.dispatch("paste", { clipboardData: datos, preventDefault: () => evitado++ });

  // Act
  pegar({ files: [], items: [] });
  const sinImagen = chat.window.CarvajalFoto.tomar();
  pegar({ files: [JPEG], items: [] });

  // Assert
  assert.equal(sinImagen, null);
  assert.equal(evitado, 1);
  assert.equal(chat.window.CarvajalFoto.tomar(), JPEG);
});

test("soltar una foto la adjunta con las mismas validaciones del botón", () => {
  // Arrange
  const chat = montar();
  const soltar = (files) =>
    chat.soltar({ type: "drop", dataTransfer: { types: ["Files"], files }, preventDefault() {} });

  // Act
  soltar([{ name: "foto.gif", type: "image/gif", size: 10 }]);
  const gif = chat.window.CarvajalFoto.tomar();
  soltar([JPEG]);

  // Assert
  assert.equal(gif, null);
  assert.equal(chat.window.CarvajalFoto.tomar(), JPEG);
});

// --- Ensayo del 2026-11-26: soltar la imagen en cualquier parte de la conversación ----------

function arrastre(tipo, { types = ["Files"], items, files = [] } = {}) {
  const evento = {
    type: tipo,
    dataTransfer: { types, items, files, dropEffect: "" },
    evitado: 0,
    preventDefault() {
      this.evitado += 1;
    },
  };
  return evento;
}

const ITEM_JPEG = { kind: "file", type: "image/jpeg" };
const ITEM_PDF = { kind: "file", type: "application/pdf" };

test("arrastrar una imagen sobre la conversación muestra la zona para soltar y salir la oculta", () => {
  // Arrange
  const chat = montar();

  // Act
  chat.soltar(arrastre("dragenter", { items: [ITEM_JPEG] }));
  const sobre = arrastre("dragover", { items: [ITEM_JPEG] });
  chat.soltar(sobre);
  const visible = chat.zona.hidden;
  chat.soltar(arrastre("dragleave", { items: [ITEM_JPEG] }));

  // Assert
  assert.equal(visible, false);
  assert.equal(sobre.evitado, 1);
  assert.equal(sobre.dataTransfer.dropEffect, "copy");
  assert.equal(chat.formulario.dataset.fotoArrastrando, undefined);
  assert.equal(chat.zona.hidden, true);
});

test("soltar la imagen la adjunta como el botón Foto y lo anuncia sin marcar error", () => {
  // Arrange
  const chat = montar();
  chat.soltar(arrastre("dragenter", { items: [ITEM_JPEG] }));

  // Act
  const soltada = arrastre("drop", { items: [ITEM_JPEG], files: [JPEG] });
  chat.soltar(soltada);

  // Assert
  assert.equal(soltada.evitado, 1);
  assert.equal(chat.window.CarvajalFoto.tomar(), JPEG);
  assert.equal(chat.ficha.hidden, false);
  assert.equal(chat.aviso.hidden, false);
  assert.equal(chat.zona.hidden, true);
  assert.match(chat.estado.textContent, /Foto adjunta: ajiaco\.jpg/);
  assert.equal(chat.estado.classList.contains("error"), false);
});

test("un archivo que no es imagen no muestra la zona ni se adjunta, y el navegador no lo abre", () => {
  // Arrange
  const chat = montar();
  const pdf = { name: "catalogo.pdf", type: "application/pdf", size: 1000 };

  // Act
  chat.soltar(arrastre("dragenter", { items: [ITEM_PDF] }));
  const sobre = arrastre("dragover", { items: [ITEM_PDF] });
  chat.soltar(sobre);
  const zonaVisible = !chat.zona.hidden;
  const soltada = arrastre("drop", { items: [ITEM_PDF], files: [pdf] });
  chat.soltar(soltada);

  // Assert: ignorado en silencio; preventDefault evita que el navegador navegue al PDF.
  assert.equal(zonaVisible, false);
  assert.equal(sobre.evitado, 1);
  assert.equal(sobre.dataTransfer.dropEffect, "none");
  assert.equal(soltada.evitado, 1);
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
  assert.equal(chat.estado.textContent, "");
});

test("arrastrar texto o un enlace no toca la zona ni cancela el comportamiento normal", () => {
  // Arrange
  const chat = montar();
  const tipos = { types: ["text/plain", "text/uri-list"], items: [{ kind: "string", type: "text/plain" }] };

  // Act
  chat.soltar(arrastre("dragenter", tipos));
  const sobre = arrastre("dragover", tipos);
  chat.soltar(sobre);
  const soltada = arrastre("drop", tipos);
  chat.soltar(soltada);

  // Assert
  assert.equal(chat.zona.hidden, true);
  assert.equal(sobre.evitado, 0);
  assert.equal(soltada.evitado, 0);
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
});

test("sin `items` (navegador viejo) ofrece la zona y valida el archivo al soltar", () => {
  // Arrange
  const chat = montar();

  // Act
  chat.soltar(arrastre("dragenter"));
  const visible = !chat.zona.hidden;
  chat.soltar(arrastre("drop", { files: [{ name: "foto.gif", type: "image/gif", size: 10 }] }));

  // Assert
  assert.equal(visible, true);
  assert.equal(chat.window.CarvajalFoto.tomar(), null);
  assert.match(chat.estado.textContent, /JPEG o PNG/);
  assert.equal(chat.zona.hidden, true);
});
