// Panel de hilos y «Nuevo hilo» del chat (hilos.js).
//
// Carga hilos.js en un contexto `vm` con dobles mínimos del DOM. Fija el contrato
// decidido por el owner el 2026-11-26: tras «Nuevo hilo» el panel no se abre encima del
// chat, el foco va al compositor (que se acerca lo justo en un teléfono bajo) y la marca
// «nuevo=1» sale de la URL. `?panel=hilos` sigue abriendo el panel.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const FUENTE = path.join(__dirname, "..", "..", "apps", "api", "static", "hilos.js");
const codigo = fs.readFileSync(FUENTE, "utf8");

function crearElemento() {
  const oyentes = new Map();
  return {
    dataset: {},
    focos: [],
    desplazamientos: [],
    addEventListener(tipo, manejador) {
      if (!oyentes.has(tipo)) oyentes.set(tipo, []);
      oyentes.get(tipo).push(manejador);
    },
    dispatch(tipo, evento = {}) {
      for (const manejador of oyentes.get(tipo) || []) manejador(evento);
    },
    focus(opciones) {
      this.focos.push(opciones || {});
    },
    scrollIntoView(opciones) {
      this.desplazamientos.push(opciones || {});
    },
    removeAttribute() {},
    querySelector() {
      return null;
    },
  };
}

function cargar({ abrir = "0", hiloNuevo = false, url } = {}) {
  const cerrar = crearElemento();
  const panel = crearElemento();
  panel.dataset.abrir = abrir;
  panel.open = false;
  panel.modales = 0;
  panel.showModal = () => {
    panel.open = true;
    panel.modales += 1;
  };
  panel.close = () => {
    panel.open = false;
  };
  panel.querySelector = (selector) => (selector === "[data-cerrar-hilos]" ? cerrar : null);

  const compositor = crearElemento();
  const aviso = crearElemento();
  const clasesBody = new Set();
  const reemplazos = [];
  const document = {
    activeElement: null,
    body: {
      classList: {
        add: (nombre) => clasesBody.add(nombre),
        remove: (nombre) => clasesBody.delete(nombre),
      },
    },
    getElementById: (id) => ({ "panel-hilos": panel, pregunta: compositor })[id] || null,
    querySelector: (selector) => (selector === "[data-hilo-nuevo]" && hiloNuevo ? aviso : null),
    querySelectorAll: () => [],
  };
  const window = {
    location: { href: url || "http://localhost/chat?thread=hilo-nuevo&nuevo=1" },
    history: {
      state: null,
      replaceState: (estado, titulo, destino) => reemplazos.push(destino),
    },
    matchMedia: () => ({ matches: false }),
  };
  vm.runInNewContext(codigo, { window, document, URL, setTimeout });
  return { panel, cerrar, compositor, reemplazos, clasesBody };
}

test("tras «Nuevo hilo» el panel no se abre y el foco va al compositor", () => {
  // Arrange y Act
  const { panel, compositor } = cargar({ hiloNuevo: true });

  // Assert
  assert.equal(panel.modales, 0);
  assert.equal(panel.open, false);
  assert.equal(compositor.focos.length, 1);
  assert.equal(compositor.focos[0].preventScroll, true);
});

test("tras «Nuevo hilo» el compositor se acerca lo justo en un teléfono bajo", () => {
  // Arrange y Act
  const { compositor } = cargar({ hiloNuevo: true });

  // Assert: «nearest» y no «center», para que el aviso de justo encima siga a la vista.
  assert.equal(compositor.desplazamientos.length, 1);
  assert.equal(compositor.desplazamientos[0].block, "nearest");
});

test("«Nuevo hilo» gana aunque la URL también pida el panel", () => {
  // Arrange y Act
  const { panel } = cargar({ hiloNuevo: true, abrir: "1" });

  // Assert
  assert.equal(panel.modales, 0);
});

test("la marca nuevo=1 sale de la URL y se conserva el hilo", () => {
  // Arrange y Act
  const { reemplazos } = cargar({ hiloNuevo: true });

  // Assert
  assert.equal(reemplazos.length, 1);
  assert.equal(reemplazos[0], "/chat?thread=hilo-nuevo");
});

test("?panel=hilos sigue abriendo el panel con el foco en Cerrar", () => {
  // Arrange y Act
  const { panel, cerrar, compositor, reemplazos, clasesBody } = cargar({
    abrir: "1",
    url: "http://localhost/chat?panel=hilos",
  });

  // Assert
  assert.equal(panel.modales, 1);
  assert.equal(cerrar.focos.length, 1);
  assert.equal(compositor.focos.length, 0);
  assert.equal(compositor.desplazamientos.length, 0);
  assert.equal(reemplazos.length, 0);
  assert.ok(clasesBody.has("panel-abierto"));
});

test("sin «Nuevo hilo» ni ?panel=hilos no se mueve el foco ni se abre el panel", () => {
  // Arrange y Act
  const { panel, compositor } = cargar({ url: "http://localhost/chat" });

  // Assert
  assert.equal(panel.modales, 0);
  assert.equal(compositor.focos.length, 0);
  assert.equal(compositor.desplazamientos.length, 0);
});
