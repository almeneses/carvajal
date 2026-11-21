// Caracterización (spec 044) del lector SSE que hoy vive duplicado en
// `chat-stream.js` y `faq.js`. Carga cada consumidor con dobles mínimos de DOM y
// un flujo sintético para fijar: bloques partidos, varios `data:`, CRLF, cierre,
// último bloque sin separador y las diferencias entre ambos consumidores.
//
// No sustituye la prueba manual del navegador: fija el contrato observable de los
// callbacks (formateo/notificación del chat y acumulación de la FAQ).
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const RAIZ = path.join(__dirname, "..", "..");
const STATIC = path.join(RAIZ, "apps", "api", "static");
const codificador = new TextEncoder();

// ---------------------------------------------------------------------------
// Dobles mínimos
// ---------------------------------------------------------------------------

function crearNodoTexto(textoInicial = "") {
  let directo = textoInicial;
  const hijos = [];
  const clases = new Set();
  return {
    dataset: {},
    classList: {
      add: (...nombres) => nombres.forEach((nombre) => clases.add(nombre)),
      remove: (...nombres) => nombres.forEach((nombre) => clases.delete(nombre)),
      toggle: (nombre, forzar) => {
        const activo = forzar === undefined ? !clases.has(nombre) : Boolean(forzar);
        if (activo) clases.add(nombre);
        else clases.delete(nombre);
        return activo;
      },
      contains: (nombre) => clases.has(nombre),
    },
    get textContent() {
      return directo + hijos.map((hijo) => hijo.textContent).join("");
    },
    set textContent(valor) {
      directo = String(valor);
      hijos.length = 0;
    },
    append(...nodos) {
      hijos.push(...nodos);
    },
    replaceChildren(...nodos) {
      directo = "";
      hijos.length = 0;
      hijos.push(...nodos);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

function crearElemento(etiqueta = "div", props = {}) {
  const oyentes = new Map();
  const atributos = new Map();
  const clases = new Set();
  const texto = crearNodoTexto();
  return {
    etiqueta,
    hidden: false,
    disabled: false,
    value: "",
    open: false,
    dataset: {},
    children: [],
    classList: {
      add: (...nombres) => nombres.forEach((nombre) => clases.add(nombre)),
      remove: (...nombres) => nombres.forEach((nombre) => clases.delete(nombre)),
      toggle: (nombre, forzar) => {
        const activo = forzar === undefined ? !clases.has(nombre) : Boolean(forzar);
        if (activo) clases.add(nombre);
        else clases.delete(nombre);
        return activo;
      },
      contains: (nombre) => clases.has(nombre),
    },
    get textContent() {
      return texto.textContent;
    },
    set textContent(valor) {
      texto.textContent = valor;
    },
    append(...nodos) {
      this.children.push(...nodos);
      texto.append(...nodos);
    },
    replaceChildren(...nodos) {
      this.children = [...nodos];
      texto.replaceChildren(...nodos);
    },
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
    },
    dispatch(tipo, evento = {}) {
      for (const manejador of oyentes.get(tipo) || []) manejador(evento);
    },
    oyentes,
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
    scrollIntoView() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    ...props,
  };
}

function respuestaSse(chunks) {
  let indice = 0;
  return {
    ok: true,
    status: 200,
    headers: { get: () => "text/event-stream; charset=utf-8" },
    body: {
      getReader() {
        return {
          async read() {
            if (indice >= chunks.length) return { value: undefined, done: true };
            return { value: codificador.encode(chunks[indice++]), done: false };
          },
        };
      },
    },
  };
}

function esperar(condicion, descripcion, intentos = 100) {
  return new Promise((resolver, rechazar) => {
    let restantes = intentos;
    const revisar = () => {
      let cumple = false;
      try {
        cumple = condicion();
      } catch (error) {
        rechazar(error);
        return;
      }
      if (cumple) {
        resolver();
        return;
      }
      restantes -= 1;
      if (restantes <= 0) {
        rechazar(new Error(`tiempo agotado: ${descripcion}`));
        return;
      }
      setImmediate(revisar);
    };
    revisar();
  });
}

// ---------------------------------------------------------------------------
// Chat: arnés de `chat-stream.js`
// ---------------------------------------------------------------------------

function crearTurno() {
  const texto = crearNodoTexto();
  const nodo = crearElemento("li");
  nodo.cloneNode = () => crearTurno();
  nodo.remove = () => {};
  nodo.querySelector = (selector) => {
    if (selector === ".texto") return texto;
    if (selector === ".turno-rol") return crearNodoTexto();
    return null;
  };
  nodo.scrollIntoView = () => {};
  return nodo;
}

function cargarChatStream({ fetchFalso }) {
  const form = crearElemento("form");
  const input = crearElemento("input");
  const csrf = crearElemento("input", { value: "token" });
  const threadInput = crearElemento("input", { value: "" });
  const turnIdInput = crearElemento("input", { value: "" });
  const boton = crearElemento("button");
  input.value = "¿Cuándo nació?";
  form.querySelector = (selector) => {
    if (selector === 'input[name="question"]') return input;
    if (selector === 'input[name="csrf_token"]') return csrf;
    if (selector === "[data-thread-id]") return threadInput;
    if (selector === "[data-client-turn-id]") return turnIdInput;
    if (selector === 'button[type="submit"]') return boton;
    return null;
  };
  form.submit = () => {
    form.enviados = (form.enviados || 0) + 1;
  };

  const estado = crearElemento("p");
  const estadoTexto = crearElemento("span");
  const girando = crearElemento("span", { hidden: true });
  estado.querySelector = (selector) => {
    if (selector === "[data-estado-texto]") return estadoTexto;
    if (selector === "[data-girando]") return girando;
    return null;
  };
  const hilo = crearElemento("div");
  let lista = null;
  hilo.querySelector = (selector) => {
    if (selector === "ol.hilo") return lista;
    return null;
  };
  hilo.querySelectorAll = () => [];
  hilo.append = (...nodos) => {
    for (const nodo of nodos) if (nodo && nodo.className === "hilo") lista = nodo;
  };
  const plantillaFuente = { content: { firstElementChild: crearTurno() } };
  const plantillaTurno = { content: { firstElementChild: crearTurno() } };
  plantillaTurno.content.firstElementChild.cloneNode = () => crearTurno();

  const eventos = [];
  const documento = {
    readyState: "complete",
    activeElement: null,
    getElementById(id) {
      if (id === "chat-traza") return null;
      if (id === "hilo") return hilo;
      if (id === "chat-stream-estado") return estado;
      if (id === "plantilla-turno") return plantillaTurno;
      if (id === "plantilla-fuente") return plantillaFuente;
      return null;
    },
    querySelector(selector) {
      if (selector === "form[data-chat-stream]") return form;
      if (selector === "[data-monitoring]") return null;
      return null;
    },
    createElement(etiqueta) {
      const nodo = crearElemento(etiqueta);
      nodo.cloneNode = () => crearTurno();
      return nodo;
    },
    dispatchEvent(evento) {
      eventos.push({ tipo: evento.type, detail: evento.detail });
      return true;
    },
    addEventListener() {},
  };

  const ventana = {
    fetch: fetchFalso,
    TextDecoder,
    ReadableStream: function ReadableStream() {},
    CustomEvent: function CustomEvent(tipo, init) {
      this.type = tipo;
      this.detail = init ? init.detail : undefined;
    },
    crypto: null,
  };

  const lector = fs.readFileSync(path.join(STATIC, "sse.js"), "utf8");
  const fuente = fs.readFileSync(path.join(STATIC, "chat-stream.js"), "utf8");
  const contexto = vm.createContext({
    window: ventana,
    document: documento,
    fetch: ventana.fetch,
    URLSearchParams,
    AbortController,
    TextDecoder,
    setTimeout,
    clearTimeout,
    console,
  });
  vm.runInContext(lector, contexto);
  vm.runInContext(fuente, contexto);

  const enviar = () => {
    for (const manejador of form.oyentes.get("submit") || []) {
      manejador({ preventDefault() {} });
    }
  };
  return { form, input, estado, estadoTexto, eventos, enviar, threadInput };
}

function eventosChat(eventos) {
  return eventos.filter((evento) => evento.tipo === "chat:turn-complete");
}

// ---------------------------------------------------------------------------
// FAQ: arnés de `faq.js`
// ---------------------------------------------------------------------------

function cargarFaq({ chunks }) {
  const contenedor = crearElemento("section", { dataset: { csrf: "token" } });
  const boton = crearElemento("button", { id: "faq-generar" });
  const estado = crearElemento("p");
  const aviso = crearElemento("p", { hidden: true });
  const lista = crearElemento("div");
  const vacio = crearElemento("p", { hidden: false });
  const faltantes = crearElemento("div", { hidden: true });
  const preview = crearElemento("pre", { hidden: true });
  const documento = {
    readyState: "complete",
    querySelector(selector) {
      if (selector === "[data-faq]") return contenedor;
      return null;
    },
    getElementById(id) {
      const mapa = {
        "faq-generar": boton,
        "faq-reiniciar": null,
        "faq-descargar": null,
        "faq-estado": estado,
        "faq-aviso": aviso,
        "faq-lista": lista,
        "faq-vacio": vacio,
        "faq-faltantes": faltantes,
        "faq-preview": preview,
      };
      return mapa[id] || null;
    },
    createElement(etiqueta) {
      return crearElemento(etiqueta);
    },
    addEventListener() {},
  };
  const ventana = {
    fetch: async (url) => {
      if (String(url).includes("/api/csrf")) {
        return { ok: true, json: async () => ({ token: "token" }) };
      }
      return respuestaSse(chunks);
    },
    TextDecoder,
  };
  const lector = fs.readFileSync(path.join(STATIC, "sse.js"), "utf8");
  const fuente = fs.readFileSync(path.join(STATIC, "faq.js"), "utf8");
  const contexto = vm.createContext({
    window: ventana,
    document: documento,
    fetch: ventana.fetch,
    URLSearchParams,
    AbortController,
    TextDecoder,
    setTimeout,
    clearTimeout,
    Blob: class Blob {},
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    console,
  });
  vm.runInContext(lector, contexto);
  vm.runInContext(fuente, contexto);
  return { boton, estado, aviso, lista, faltantes, preview, contenedor };
}

function generarFaq(arnes) {
  for (const manejador of arnes.boton.oyentes.get("click") || []) manejador({});
}

// ---------------------------------------------------------------------------
// Casos del chat
// ---------------------------------------------------------------------------

const DONE = JSON.stringify({
  conversation_id: "conv-1",
  message_id: "msg-1",
  elapsed_ms: 12,
  request_id: "req-1",
});

test("caracterización chat: bloques partidos entre lecturas se ensamblan", async () => {
  // Arrange
  const arnes = cargarChatStream({
    fetchFalso: async () =>
      respuestaSse([
        'event: open\ndata: {"stream": "chat"}\n\nevent: token\ndata: {"te',
        'xt": "Hola "}\n\nevent: token\ndata: {"text": "mundo"}\n\nevent: done\ndata: ',
        `${DONE}\n\n`,
      ]),
  });

  // Act
  arnes.enviar();
  await esperar(() => eventosChat(arnes.eventos).length === 1, "done del chat");

  // Assert: dos tokens ensamblados y el turno notificado una vez.
  const completo = eventosChat(arnes.eventos)[0];
  assert.equal(completo.detail.handsFree, false);
  assert.equal(completo.detail.threadId, "");
  assert.equal(arnes.form.enviados || 0, 0);
});

test("caracterización chat: varias líneas data: se unen con salto de línea", async () => {
  // Arrange
  const arnes = cargarChatStream({
    fetchFalso: async () =>
      respuestaSse([
        'event: token\ndata: {"text":\ndata: "Hola"}\n\n',
        `event: done\ndata: ${DONE}\n\n`,
      ]),
  });

  // Act
  arnes.enviar();
  await esperar(() => eventosChat(arnes.eventos).length === 1, "done del chat");

  // Assert: el JSON partido en dos data: se interpretó en el turno notificado.
  const completo = eventosChat(arnes.eventos)[0];
  assert.equal(completo.detail.turno.querySelector(".texto").textContent, "Hola");
});

test("caracterización chat: separadores CRLF equivalen a LF", async () => {
  // Arrange
  const arnes = cargarChatStream({
    fetchFalso: async () =>
      respuestaSse([
        'event: token\r\ndata: {"text": "Hola"}\r\n\r\n',
        `event: done\r\ndata: ${DONE}\r\n\r\n`,
      ]),
  });

  // Act
  arnes.enviar();
  await esperar(() => eventosChat(arnes.eventos).length === 1, "done del chat");

  // Assert
  assert.equal(eventosChat(arnes.eventos).length, 1);
});

test("caracterización chat: procesa el último bloque sin separador final", async () => {
  // Arrange: el done llega sin la línea en blanco de cierre.
  const arnes = cargarChatStream({
    fetchFalso: async () =>
      respuestaSse(['event: open\ndata: {"stream": "chat"}\n\n', `event: done\ndata: ${DONE}`]),
  });

  // Act
  arnes.enviar();
  await esperar(() => eventosChat(arnes.eventos).length === 1, "done sin separador");

  // Assert: el chat ya procesaba el último bloque; esta es la diferencia con FAQ.
  assert.equal(eventosChat(arnes.eventos).length, 1);
});

test("caracterización chat: cierre sin done avisa interrupción y no reenvía", async () => {
  // Arrange
  const arnes = cargarChatStream({
    fetchFalso: async () =>
      respuestaSse([
        'event: open\ndata: {"stream": "chat"}\n\n',
        'event: token\ndata: {"text": "Carvajal"}\n\n',
      ]),
  });

  // Act
  arnes.enviar();
  await esperar(() => arnes.estadoTexto.textContent.includes("interrumpió"), "aviso");

  // Assert: hubo contenido, así que no se reenvía por el POST normal.
  assert.equal(arnes.form.enviados || 0, 0);
  assert.equal(eventosChat(arnes.eventos).length, 0);
});

test("caracterización chat: fallo de red antes del primer token reenvía sin streaming", async () => {
  // Arrange
  const arnes = cargarChatStream({
    fetchFalso: async () => {
      throw new TypeError("red caída");
    },
  });

  // Act
  arnes.enviar();
  await esperar(() => (arnes.form.enviados || 0) === 1, "fallback al POST normal");

  // Assert
  assert.equal(arnes.input.value, "¿Cuándo nació?");
  assert.equal(arnes.form.enviados, 1);
});

// ---------------------------------------------------------------------------
// Casos de la FAQ
// ---------------------------------------------------------------------------

function lote(n = 1) {
  return JSON.stringify({
    entries: Array.from({ length: n }, (_, indice) => ({
      theme: "Empresa",
      question: `¿Pregunta ${indice + 1}?`,
      answer: "Respuesta.",
      source: "historia",
    })),
    missing_topics: [],
    truncated: false,
    exhausted: false,
    warnings: [],
  });
}

test("caracterización FAQ: ensambla tokens partidos y aplica el lote con done", async () => {
  // Arrange
  const arnes = cargarFaq({
    chunks: [
      'event: open\ndata: {"stream": "faq"}\n\nevent: token\ndata: {"text": "## Emp',
      'resa"}\n\nevent: done\ndata: ',
      `${lote(1)}\n\n`,
    ],
  });

  // Act
  generarFaq(arnes);
  await esperar(() => arnes.lista.children.length > 0, "lote aplicado");

  // Assert
  assert.equal(arnes.lista.children.length, 1);
  assert.match(arnes.estado.textContent, /1 preguntas nuevas/);
});

test("caracterización FAQ: CRLF y varios data: se interpretan igual", async () => {
  // Arrange
  const arnes = cargarFaq({
    chunks: [
      'event: token\r\ndata: {"text":\r\ndata: "## Empresa"}\r\n\r\n',
      `event: done\r\ndata: ${lote(2)}\r\n\r\n`,
    ],
  });

  // Act
  generarFaq(arnes);
  await esperar(() => arnes.lista.children.length > 0, "lote aplicado");

  // Assert
  assert.equal(arnes.lista.children.length, 1);
  assert.match(arnes.estado.textContent, /2 preguntas nuevas/);
});

test("el lector común procesa el último bloque sin separador también en la FAQ", async () => {
  // Arrange: el done llega sin la línea en blanco final.
  const arnes = cargarFaq({
    chunks: ['event: open\ndata: {"stream": "faq"}\n\n', `event: done\ndata: ${lote(1)}`],
  });

  // Act
  generarFaq(arnes);
  await esperar(() => arnes.lista.children.length > 0, "lote del último bloque");

  // Assert: el chat ya lo procesaba; compartir el lector cierra la diferencia.
  assert.equal(arnes.lista.children.length, 1);
  assert.match(arnes.estado.textContent, /1 preguntas nuevas/);
});
