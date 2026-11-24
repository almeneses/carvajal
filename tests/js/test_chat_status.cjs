// Evento SSE `status` del chat (spec 044, T027, FR-018).
//
// Carga chat-stream.js en un contexto `vm` con dobles mínimos del DOM, un reloj
// manual y un `fetch` que entrega eventos SSE bajo demanda. Sin navegador, sin
// red y sin dependencias npm: fija el contrato de la línea de estado
// (`role=status`), no el aspecto visual.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const FUENTE = path.join(__dirname, "..", "..", "apps", "api", "static", "chat-stream.js");
const codigo = fs.readFileSync(FUENTE, "utf8");

// ---------------------------------------------------------------------------
// Dobles mínimos del DOM: solo lo que chat-stream.js usa.
// ---------------------------------------------------------------------------

function crearElemento(etiqueta = "div", selectores = {}) {
  const oyentes = new Map();
  const atributos = new Map();
  const clases = new Set();
  const elemento = {
    etiqueta,
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    dataset: {},
    children: [],
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
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
    after() {},
    remove() {},
    focus() {},
    querySelector(selector) {
      return selectores[selector] || null;
    },
    querySelectorAll() {
      return [];
    },
  };
  return elemento;
}

// La línea de estado rechaza innerHTML: el texto del servidor solo entra como texto.
function crearEstado() {
  const estado = crearElemento("p");
  estado.setAttribute("role", "status");
  Object.defineProperty(estado, "innerHTML", {
    get() {
      throw new Error("la línea de estado no debe leerse como HTML");
    },
    set() {
      throw new Error("la línea de estado no debe escribirse como HTML");
    },
  });
  return estado;
}

function crearTurno() {
  const texto = crearElemento("div");
  const autor = crearElemento("span");
  return crearElemento("li", { ".texto": texto, "[data-turno-autor]": autor });
}

// Flujo de texto simplificado: el formato real se prueba en test_chat_text.cjs.
function crearFormateador() {
  return {
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
  };
}

// Reloj manual: los temporizadores solo corren cuando la prueba avanza el tiempo.
function crearReloj() {
  let ahora = 0;
  let siguiente = 1;
  const pendientes = new Map();
  return {
    setTimeout(funcion, espera = 0) {
      const id = siguiente++;
      pendientes.set(id, { cuando: ahora + espera, funcion });
      return id;
    },
    clearTimeout(id) {
      pendientes.delete(id);
    },
    avanzar(milisegundos) {
      ahora += milisegundos;
      const vencidos = [...pendientes.entries()]
        .filter(([, tarea]) => tarea.cuando <= ahora)
        .sort(([, a], [, b]) => a.cuando - b.cuando);
      for (const [id, tarea] of vencidos) {
        pendientes.delete(id);
        tarea.funcion();
      }
    },
  };
}

// fetch de /chat/stream con un ReadableStream que la prueba alimenta.
function crearTransporte() {
  const codificador = new TextEncoder();
  let control;
  const transporte = {
    peticiones: 0,
    fetch() {
      transporte.peticiones += 1;
      const body = new ReadableStream({
        start(controlador) {
          control = controlador;
        },
      });
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => "text/event-stream; charset=utf-8" },
        body,
      });
    },
    enviar(evento, datos) {
      control.enqueue(codificador.encode(`event: ${evento}\ndata: ${JSON.stringify(datos)}\n\n`));
    },
    cerrar() {
      control.close();
    },
  };
  return transporte;
}

const esperarTareas = async () => {
  for (let vuelta = 0; vuelta < 5; vuelta++) await new Promise(setImmediate);
};

function montar() {
  const reloj = crearReloj();
  const transporte = crearTransporte();
  const pregunta = crearElemento("textarea");
  const csrf = crearElemento("input");
  csrf.value = "csrf";
  const boton = crearElemento("button");
  const formulario = crearElemento("form", {
    '[name="question"]': pregunta,
    'input[name="csrf_token"]': csrf,
    'button[type="submit"]': boton,
  });
  formulario.submit = () => {
    throw new Error("no debe reenviar por el POST normal");
  };
  const hilo = crearElemento("section");
  const historial = crearElemento("div");
  const estado = crearEstado();
  const turnos = [];
  const plantilla = {
    content: {
      firstElementChild: {
        cloneNode: () => {
          const turno = crearTurno();
          turnos.push(turno);
          return turno;
        },
      },
    },
  };
  const porId = { hilo, "chat-stream-estado": estado, "plantilla-turno": plantilla };
  const porSelector = {
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
    fetch: transporte.fetch,
    TextDecoder,
    ReadableStream,
    URLSearchParams,
    AbortController,
    CarvajalTexto: crearFormateador(),
    setTimeout: reloj.setTimeout,
    clearTimeout: reloj.clearTimeout,
    console,
  };
  window.window = window;
  vm.runInNewContext(codigo, window);

  async function preguntar(texto = "¿Cuánto cuestan 2.000 vasos en Bogotá?") {
    pregunta.value = texto;
    formulario.dispatch("submit", { preventDefault() {} });
    await esperarTareas();
  }

  async function enviar(evento, datos) {
    transporte.enviar(evento, datos);
    await esperarTareas();
  }

  return { estado, reloj, transporte, turnos, preguntar, enviar };
}

// ---------------------------------------------------------------------------
// Pruebas (patrón AAA).
// ---------------------------------------------------------------------------

test("status reemplaza «Pensando…» en la línea de estado accesible", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  chat.reloj.avanzar(300);
  assert.equal(chat.estado.textContent, "Pensando…");

  // Act
  await chat.enviar("status", { text: "Estoy buscando en la información de Carvajal…", step: 1 });

  // Assert
  assert.equal(chat.estado.textContent, "Estoy buscando en la información de Carvajal…");
  assert.equal(chat.estado.getAttribute("role"), "status");
  assert.equal(chat.estado.classList.contains("aviso"), false);
});

test("un status temprano no lo pisa «Pensando…» cuando vence la espera", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  chat.reloj.avanzar(100);

  // Act
  await chat.enviar("status", { text: "Estoy revisando el catálogo…", step: 1 });
  chat.reloj.avanzar(1000);

  // Assert
  assert.equal(chat.estado.textContent, "Estoy revisando el catálogo…");
});

test("cada status nuevo reemplaza al anterior y uno vacío no borra el vigente", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("status", { text: "Estoy revisando qué vasos te sirven…", step: 1 });

  // Act
  await chat.enviar("status", { text: "Estoy calculando cuánto te salen 2.000 vasos…", step: 2 });
  await chat.enviar("status", { text: "", step: 3 });

  // Assert
  assert.equal(chat.estado.textContent, "Estoy calculando cuánto te salen 2.000 vasos…");
});

test("el texto del status se muestra como texto, nunca como HTML", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  const malicioso = "Estoy buscando dónde comprar en <img src=x onerror=alert(1)>…";

  // Act
  await chat.enviar("status", { text: malicioso, step: 1 });

  // Assert
  assert.equal(chat.estado.textContent, malicioso);
});

test("el primer token borra el status y los status posteriores se ignoran", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("status", { text: "Estoy consultando las cifras de Carvajal…", step: 1 });

  // Act
  await chat.enviar("token", { text: "Los ingresos" });
  const trasToken = chat.estado.textContent;
  await chat.enviar("status", { text: "Estoy buscando en la información de Carvajal…", step: 2 });
  chat.reloj.avanzar(1000);

  // Assert
  assert.equal(trasToken, "");
  assert.equal(chat.estado.textContent, "");
});

test("el fallback borra el status", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("status", { text: "Estoy buscando en la información de Carvajal…", step: 1 });

  // Act
  await chat.enviar("fallback", { text: "Respuesta de respaldo.", sources: [] });

  // Assert
  assert.equal(chat.estado.textContent, "");
});

test("el error reemplaza el status por su aviso y no vuelve a mostrarlo", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("status", { text: "Estoy buscando en la información de Carvajal…", step: 1 });

  // Act
  await chat.enviar("error", { message: "No se pudo completar la respuesta." });
  await chat.enviar("status", { text: "Estoy revisando el catálogo…", step: 2 });

  // Assert
  assert.equal(chat.estado.textContent, "No se pudo completar la respuesta.");
  assert.equal(chat.estado.classList.contains("aviso"), true);
});

// Spec 048: el `done` trae el resumen hablado que prepara el servidor.
test("el done guarda el resumen hablado del turno como texto", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("token", { text: "Para café tenemos tres vasos." });
  const resumen = 'Te muestro tres opciones en pantalla: <b>"A"</b>, B y C.\n\n¿Cuántas necesitas?';

  // Act
  await chat.enviar("done", { message_id: "m-1", voz_resumen: resumen });

  // Assert: se guarda tal cual en el dataset (texto, nunca HTML) junto al id.
  const asistente = chat.turnos[1];
  assert.equal(asistente.dataset.vozResumen, resumen);
  assert.equal(asistente.dataset.vozMensaje, "m-1");
});

test("un done sin resumen deja la lectura como antes", async () => {
  // Arrange
  const chat = montar();
  await chat.preguntar();
  await chat.enviar("token", { text: "Hola." });

  // Act
  await chat.enviar("done", { message_id: "m-2", voz_resumen: "   " });

  // Assert
  assert.equal(chat.turnos[1].dataset.vozResumen, undefined);
});
