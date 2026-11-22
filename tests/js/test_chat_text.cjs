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
        if (hijo.tag === "#fragment") this.append(...hijo.children);
        else {
          this.children.push(hijo);
          hijo.padre = this;
        }
      }
    },
    remove() {
      const hermanos = this.padre.children;
      hermanos.splice(hermanos.indexOf(this), 1);
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
  flujo.adelantar(1);

  flujo.reemplazar("**Final** [1] con `[1]` y <img src=x>.", 1);
  flujo.terminar(1);

  assert.equal(salida.textContent, "Final con [1] y <img src=x>.");
  assert.deepEqual(salida.children[0].children.map((hijo) => hijo.tag), [
    "strong", "#text", "code", "#text",
  ]);
});

test("el párrafo activo aparece sin esperar una línea vacía ni mostrar delimitadores incompletos", () => {
  const { salida, flujo } = montar();
  flujo.agregar("**Carva*", 1);
  flujo.adelantar(1);
  assert.equal(salida.textContent, "Carva");
  assert.equal(salida.children[0].children[0].tag, "#text");

  flujo.agregar("* usa [1", 1);
  flujo.adelantar(1);
  assert.equal(salida.textContent.trimEnd(), "Carva usa");
  assert.equal(salida.children[0].children[0].tag, "strong");

  flujo.agregar("] el [catálogo](https://carvajal", 1);
  flujo.adelantar(1);
  assert.equal(salida.textContent, "Carva usa el catálogo");
  assert.equal(salida.children[0].children.some((hijo) => hijo.tag === "a"), false);
  flujo.agregar(".com) [9].", 1);
  flujo.terminar(1);
  assert.equal(salida.textContent, "Carva usa el catálogo [9].");
  assert.equal(salida.children[0].children.find((hijo) => hijo.tag === "a").href, "https://carvajal.com");
});

test("al detener, conserva lo recibido de una vez sin mostrar Markdown incompleto", () => {
  const { salida, flujo } = montar();
  flujo.agregar("Listo.\n\n**Sigue", 1);
  flujo.adelantar(1);
  const [estable] = salida.children;

  flujo.agregar(" aquí [1", 1);
  flujo.terminar(1, true);
  assert.equal(salida.children[0], estable);
  assert.deepEqual(salida.children.map((hijo) => hijo.textContent.trimEnd()), ["Listo.", "Sigue aquí"]);
});

test("los corchetes dentro de una URL o literal de código no son citas", () => {
  const { salida, flujo } = montar();
  flujo.reemplazar("Consulta https://example.com/?ref=[1] y el código `[1]` [1].", 1);
  const enlace = salida.children[0].children.find((hijo) => hijo.tag === "a");
  assert.equal(enlace.href, "https://example.com/?ref=[1]");
  assert.equal(salida.textContent, "Consulta https://example.com/?ref=[1] y el código [1].");
});
