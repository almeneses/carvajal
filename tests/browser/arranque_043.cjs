"use strict";

/*
 * Verificación de arranque nuevo y actualización de la spec 043 (T003).
 *
 * Comprueba, contra un stack ya levantado, lo que el runbook promete:
 * vitalidad, readiness, versión y conteos del catálogo, redirecciones de las
 * rutas retiradas y acceso autorizado al histórico.
 *
 * No forma parte de `make check` ni de CI: requiere Chrome y playwright-core.
 * El detalle del arnés está en docs/despliegue-ovh.md.
 *
 * Uso:
 *   BASE_URL=http://127.0.0.1:8080 \
 *   EXPECTED_CATALOG_VERSION=2026-11-18 \
 *   NODE_PATH=/tmp/cv043/node_modules \
 *   node tests/browser/arranque_043.cjs
 */

const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");
const ESPERADO = {
  version: process.env.EXPECTED_CATALOG_VERSION || "2026-11-18",
  products: Number(process.env.EXPECTED_PRODUCTS || 200),
  offers_co: Number(process.env.EXPECTED_OFFERS_CO || 29),
  offers_mx: Number(process.env.EXPECTED_OFFERS_MX || 98),
  sites: Number(process.env.EXPECTED_SITES || 29),
};
const HILO_AJENO = "00000000000000000000000000000000";

const pasos = [];

async function comprobar(nombre, funcion) {
  try {
    const detalle = await funcion();
    pasos.push({ paso: nombre, ok: true, detalle: detalle || "" });
    console.log(`OK: ${nombre}${detalle ? ` — ${detalle}` : ""}`);
  } catch (error) {
    pasos.push({ paso: nombre, ok: false, detalle: error.message });
    console.error(`FALLO: ${nombre} — ${error.message}`);
  }
}

function cabecera(respuesta, nombre) {
  return respuesta.headers()[nombre.toLowerCase()] || "";
}

async function main() {
  const navegador = await chromium.launch({
    executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  });
  try {
    const anonimo = await navegador.newContext();
    const pedir = anonimo.request;

    await comprobar("vitalidad /live", async () => {
      const respuesta = await pedir.get(`${BASE}/live`, { failOnStatusCode: false });
      assert.equal(respuesta.status(), 200);
      assert.deepEqual(await respuesta.json(), { status: "ok" });
    });

    await comprobar("readiness y catálogo en /health", async () => {
      const respuesta = await pedir.get(`${BASE}/health`, { failOnStatusCode: false });
      assert.equal(respuesta.status(), 200);
      const salud = await respuesta.json();
      const catalogo = salud.catalog || {};
      assert.equal(salud.status, "ok");
      assert.equal(salud.index.status, "ok", "Qdrant debe estar indexado");
      assert.ok(salud.documents > 0 && salud.chunks > 0, "el corpus debe estar importado");
      assert.equal(catalogo.status, "ok");
      assert.equal(catalogo.version, ESPERADO.version);
      for (const campo of ["products", "offers_co", "offers_mx", "sites"]) {
        assert.equal(catalogo[campo], ESPERADO[campo], `${campo} inesperado`);
      }
      return `${salud.documents} documentos, ${salud.chunks} fragmentos, ${salud.index.points} puntos, catálogo ${catalogo.version}`;
    });

    const redirecciones = [
      ["/", "/chat"],
      ["/faq", "/chat"],
      ["/cotizador", "/chat"],
    ];
    for (const [ruta, destino] of redirecciones) {
      await comprobar(`redirección GET ${ruta}`, async () => {
        const respuesta = await pedir.get(`${BASE}${ruta}`, {
          maxRedirects: 0,
          failOnStatusCode: false,
        });
        assert.equal(respuesta.status(), 303);
        assert.equal(cabecera(respuesta, "location"), destino);
      });
    }

    for (const ruta of ["/cotizador", "/cotizador/stream", "/cotizador/nuevo"]) {
      await comprobar(`redirección POST ${ruta}`, async () => {
        const respuesta = await pedir.post(`${BASE}${ruta}`, {
          maxRedirects: 0,
          failOnStatusCode: false,
        });
        assert.equal(respuesta.status(), 303);
        assert.equal(cabecera(respuesta, "location"), "/chat");
      });
    }

    await comprobar("ficha de la demo retirada", async () => {
      const respuesta = await pedir.get(`${BASE}/cotizador/ficha/demo`, {
        failOnStatusCode: false,
      });
      assert.equal(respuesta.status(), 404);
    });

    await comprobar("API de FAQ conservada", async () => {
      const respuesta = await pedir.post(`${BASE}/api/faq`, {
        maxRedirects: 0,
        failOnStatusCode: false,
      });
      // Sin CSRF la ruta existe y rechaza: no es un 404 de ruta retirada.
      assert.equal(respuesta.status(), 403);
    });

    await comprobar("chat y cookie de identidad", async () => {
      const pagina = await anonimo.newPage();
      const respuesta = await pagina.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" });
      assert.equal(respuesta.status(), 200);
      const cookies = await anonimo.cookies(BASE);
      assert.ok(
        cookies.some((cookie) => cookie.name === "carvajal_sesion"),
        "el chat debe emitir la cookie de sesión",
      );
      await pagina.close();
    });

    await comprobar("histórico ajeno rechazado en /cotizador", async () => {
      const respuesta = await pedir.get(`${BASE}/cotizador?thread=${HILO_AJENO}`, {
        maxRedirects: 0,
        failOnStatusCode: false,
      });
      assert.equal(respuesta.status(), 404);
    });

    await comprobar("histórico ajeno rechazado en /chat", async () => {
      const respuesta = await pedir.get(`${BASE}/chat?thread=${HILO_AJENO}`, {
        maxRedirects: 0,
        failOnStatusCode: false,
      });
      assert.equal(respuesta.status(), 404);
    });

    await anonimo.close();
  } finally {
    await navegador.close();
  }

  const fallos = pasos.filter((paso) => !paso.ok);
  console.log(
    JSON.stringify(
      { base: BASE, esperado: ESPERADO, pasos, fallos: fallos.length },
      null,
      2,
    ),
  );
  if (fallos.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
