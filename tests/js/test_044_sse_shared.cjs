// Pruebas del lector SSE compartido (spec 044): `apps/api/static/sse.js`.
//
// Fijan el contrato del archivo único que consumen chat y FAQ y comprueban que
// los consumidores ya no llevan su propia copia del bucle de lectura.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const RAIZ = path.join(__dirname, "..", "..");
const STATIC = path.join(RAIZ, "apps", "api", "static");
const TEMPLATES = path.join(RAIZ, "apps", "api", "templates");
const lector = require(path.join(STATIC, "sse.js"));
const codificador = new TextEncoder();

function respuesta(chunks) {
  let indice = 0;
  return {
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

async function leer(chunks) {
  const eventos = [];
  await lector.leerEventos(respuesta(chunks), (evento) => eventos.push(evento));
  return eventos;
}

test("interpretar descarta bloques vacíos, sin data: o con JSON inválido", () => {
  assert.equal(lector.interpretar(""), null);
  assert.equal(lector.interpretar("   "), null);
  assert.equal(lector.interpretar("event: token"), null);
  assert.equal(lector.interpretar("event: token\ndata: no-es-json"), null);
});

test("interpretar usa message por defecto y une varias líneas data:", () => {
  const evento = lector.interpretar('data: {"text":\ndata: "Hola"}');
  assert.deepEqual(evento, { nombre: "message", datos: { text: "Hola" } });
});

test("interpretar acepta event: con y sin espacio", () => {
  assert.equal(lector.interpretar('event:done\ndata: {"x": 1}').nombre, "done");
  assert.equal(lector.interpretar('event:  done\ndata: {"x": 1}').nombre, "done");
});

test("leerEventos ensambla un bloque partido entre lecturas", async () => {
  const eventos = await leer([
    'event: token\ndata: {"te',
    'xt": "Hola"}\n\n',
  ]);
  assert.deepEqual(eventos, [{ nombre: "token", datos: { text: "Hola" } }]);
});

test("leerEventos reconoce un separador CRLF partido entre lecturas", async () => {
  const eventos = await leer([
    'event: token\r\ndata: {"text": "Hola"}\r\n\r',
    "\n",
  ]);
  assert.deepEqual(eventos, [{ nombre: "token", datos: { text: "Hola" } }]);
});

test("leerEventos entrega varios eventos de un solo chunk en orden", async () => {
  const eventos = await leer([
    'event: open\ndata: {"stream": "chat"}\n\nevent: token\ndata: {"text": "a"}\n\n',
  ]);
  assert.deepEqual(
    eventos.map((evento) => evento.nombre),
    ["open", "token"],
  );
});

test("leerEventos procesa el último bloque sin separador final", async () => {
  const eventos = await leer(['event: done\ndata: {"ok": true}']);
  assert.deepEqual(eventos, [{ nombre: "done", datos: { ok: true } }]);
});

test("leerEventos conserva la secuencia cuando el done llega en el último bloque", async () => {
  const eventos = await leer([
    'event: open\ndata: {"stream": "chat"}\n\n',
    'event: token\ndata: {"text": "Hola"}\n\nevent: done\ndata: {"id": "c1"}',
  ]);
  assert.deepEqual(
    eventos.map((evento) => evento.nombre),
    ["open", "token", "done"],
  );
});

test("leerEventos ignora bloques vacíos y eventos inválidos", async () => {
  const eventos = await leer([
    "\n\n",
    "event: token\ndata: roto\n\n",
    'event: token\ndata: {"text": "ok"}\n\n',
  ]);
  assert.deepEqual(eventos, [{ nombre: "token", datos: { text: "ok" } }]);
});

test("leerEventos decodifica un carácter UTF-8 partido entre lecturas", async () => {
  const bytes = codificador.encode('event: token\ndata: {"text": "ñ"}\n\n');
  let indice = 0;
  const trozos = [bytes.slice(0, 28), bytes.slice(28)];
  const respuestaPartida = {
    body: {
      getReader() {
        return {
          async read() {
            if (indice >= trozos.length) return { value: undefined, done: true };
            return { value: trozos[indice++], done: false };
          },
        };
      },
    },
  };
  const eventos = [];
  await lector.leerEventos(respuestaPartida, (evento) => eventos.push(evento));
  assert.deepEqual(eventos, [{ nombre: "token", datos: { text: "ñ" } }]);
});

test("leerEventos rechaza una respuesta sin flujo legible", async () => {
  await assert.rejects(() => lector.leerEventos({ body: null }, () => {}));
});

test("chat-stream.js y faq.js consumen el lector común sin copiarlo", () => {
  for (const archivo of ["chat-stream.js", "faq.js"]) {
    const fuente = fs.readFileSync(path.join(STATIC, archivo), "utf8");
    assert.match(fuente, /window\.CarvajalSSE/);
    assert.match(fuente, /lectorSse\.leerEventos/);
    assert.doesNotMatch(fuente, /function interpretar\(/);
    assert.doesNotMatch(fuente, /function vaciarBloques\(/);
    assert.doesNotMatch(fuente, /function leerEventos\(/);
  }
});

test("las plantillas cargan sse.js antes que su consumidor", () => {
  for (const [plantilla, consumidor] of [
    ["chat.html", "chat-stream.js"],
    ["faq.html", "faq.js"],
  ]) {
    const fuente = fs.readFileSync(path.join(TEMPLATES, plantilla), "utf8");
    const posicionLector = fuente.indexOf("/static/sse.js");
    const posicionConsumidor = fuente.indexOf(`/static/${consumidor}`);
    assert.ok(posicionLector >= 0, `${plantilla} no carga sse.js`);
    assert.ok(posicionConsumidor >= 0, `${plantilla} no carga ${consumidor}`);
    assert.ok(
      posicionLector < posicionConsumidor,
      `${plantilla}: sse.js debe ir antes de ${consumidor}`,
    );
  }
});
