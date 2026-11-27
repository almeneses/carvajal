// «Reintentar» en Métricas (metricas.js, spec 055).
//
// Carga metricas.js en un contexto `vm` con dobles mínimos del DOM. Fija el contrato: tras la
// redirección 303, el estado de la fuente reintentada (`data-anunciar`) se vacía y se vuelve a
// escribir para que la región viva lo anuncie; sin aviso no se toca nada.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const FUENTE = path.join(__dirname, "..", "..", "apps", "api", "static", "metricas.js");
const codigo = fs.readFileSync(FUENTE, "utf8");

function cargar(estado, readyState = "complete") {
  const tareas = [];
  const oyentes = new Map();
  const documento = {
    readyState,
    querySelector: (selector) => (selector === "[data-anunciar]" ? estado : null),
    addEventListener(tipo, manejador) {
      oyentes.set(tipo, manejador);
    },
  };
  const ventana = {
    setTimeout(tarea, ms) {
      tareas.push({ tarea, ms });
      return tareas.length;
    },
  };
  vm.runInNewContext(codigo, { document: documento, window: ventana });
  return { tareas, oyentes };
}

test("el aviso del reintento se vacía y se vuelve a escribir para anunciarse", () => {
  // Arrange
  const estado = { textContent: "\n  Se volvió a consultar Qdrant hace un momento.\n" };

  // Act
  const { tareas } = cargar(estado);
  const vaciado = estado.textContent;
  tareas.forEach(({ tarea }) => tarea());

  // Assert
  assert.equal(vaciado, "");
  assert.equal(tareas.length, 1);
  assert.ok(tareas[0].ms > 0);
  assert.equal(estado.textContent, "Se volvió a consultar Qdrant hace un momento.");
});

test("con la página aún cargando espera a DOMContentLoaded", () => {
  // Arrange
  const estado = { textContent: "Espera 9 s para reintentar." };

  // Act
  const { tareas, oyentes } = cargar(estado, "loading");
  const antes = tareas.length;
  oyentes.get("DOMContentLoaded")();

  // Assert
  assert.equal(antes, 0);
  assert.equal(tareas.length, 1);
});

test("sin aviso de reintento no programa nada", () => {
  // Arrange y Act
  const { tareas } = cargar(null);

  // Assert
  assert.equal(tareas.length, 0);
});
