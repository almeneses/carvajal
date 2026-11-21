"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const codigo = fs.readFileSync(
  path.join(__dirname, "../../apps/api/static/texto.js"),
  "utf8",
);

function nodo(tag, literal = "") {
  return {
    tag,
    children: [],
    classList: { add() {} },
    get textContent() {
      return literal + this.children.map((hijo) => hijo.textContent).join("");
    },
    set textContent(texto) {
      literal = texto;
      this.children = [];
    },
    append(...hijos) {
      for (const hijo of hijos) {
        this.children.push(...(hijo.tag === "#fragment" ? hijo.children : [hijo]));
      }
    },
    replaceChildren() {
      this.children = [];
    },
  };
}

function montar() {
  const window = { matchMedia: () => ({ matches: true }) };
  const document = {
    readyState: "loading",
    addEventListener() {},
    createElement: (tag) => nodo(tag),
    createTextNode: (texto) => nodo("#text", texto),
    createDocumentFragment: () => nodo("#fragment"),
  };
  vm.runInNewContext(codigo, { window, document });
  const salida = nodo("div");
  return { salida, flujo: window.CarvajalTexto.crearFlujo(salida) };
}

test("el stream pinta bloques formateados antes de terminar y no repinta lo anterior", () => {
  const { salida, flujo } = montar();

  flujo.agregar("**Dato", 2);
  flujo.agregar(" [1]**.\r", 2);
  assert.equal(salida.textContent, "");
  flujo.agregar("\n\r\n- uno [2]\n- dos", 2);

  const [primero] = salida.children;
  assert.equal(primero.children[0].tag, "strong");
  assert.equal(primero.textContent, "Dato.");
  assert.equal(salida.children.length, 1);

  flujo.terminar(2);
  flujo.terminar(2);
  assert.equal(salida.children.length, 2);
  assert.equal(salida.children[0], primero);
  assert.equal(salida.children[1].tag, "ul");
  assert.deepEqual(salida.children[1].children.map((item) => item.textContent), ["uno", "dos"]);
});

test("el fallback descarta el borrador y formatea sin citas ni HTML ejecutable", () => {
  const { salida, flujo } = montar();
  flujo.agregar("Borrador parcial", 1);

  flujo.reemplazar("**Final** [1] con `[1]` y <img src=x>.", 1);
  flujo.terminar(1);

  assert.equal(salida.textContent, "Final con [1] y <img src=x>.");
  assert.deepEqual(salida.children[0].children.map((hijo) => hijo.tag), [
    "strong", "#text", "code", "#text",
  ]);
});
