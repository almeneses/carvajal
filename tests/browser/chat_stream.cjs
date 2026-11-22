"use strict";

// Usa el servidor de prueba de chat_voz_040; el transporte SSE se controla desde Chrome.
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

async function comprobar(pagina) {
  await pagina.addInitScript(() => {
    const fetchReal = window.fetch.bind(window);
    window.turnosCompletos = 0;
    document.addEventListener("chat:turn-complete", () => window.turnosCompletos++);
    window.fetch = (url, opciones) => {
      if (url !== "/chat/stream") return fetchReal(url, opciones);
      const stream = new ReadableStream({
        start(control) {
          window.enviar = (evento, datos) => {
            control.enqueue(new TextEncoder().encode(
              `event: ${evento}\ndata: ${JSON.stringify(datos)}\n\n`,
            ));
          };
          window.cerrarStream = () => control.close();
          opciones.signal.addEventListener("abort", () => {
            delete window.enviar;
            control.error(opciones.signal.reason);
          }, { once: true });
        },
      });
      return Promise.resolve(new Response(stream, {
        headers: { "Content-Type": "text/event-stream" },
      }));
    };
  });

  await pagina.goto(process.env.CHAT_URL || "http://127.0.0.1:8123/chat");
  const pregunta = pagina.locator("#pregunta");
  const detener = pagina.locator("[data-chat-detener]");
  const alFinal = pagina.locator("[data-chat-final]");
  const historial = pagina.locator(".historial-chat");
  const respuestas = pagina.locator("#hilo .turno-assistant .texto");
  const enviar = (evento, datos) => pagina.evaluate(
    ([nombre, contenido]) => window.enviar(nombre, contenido), [evento, datos],
  );

  const sugerencia = pagina.locator("[data-chat-sugerencia]").first();
  await sugerencia.click();
  assert.equal(await pregunta.inputValue(), await sugerencia.textContent());
  const altura = (await pregunta.boundingBox()).height;
  await pregunta.press("End");
  await pregunta.press("Shift+Enter");
  await pregunta.press("A");
  assert.match(await pregunta.inputValue(), /\na$/i);
  if (await pagina.evaluate(() => CSS.supports("field-sizing: content"))) {
    assert.ok((await pregunta.boundingBox()).height > altura);
  }
  await pregunta.press("Enter");
  await detener.waitFor();
  await pagina.getByRole("status").filter({ hasText: "Pensando…" }).waitFor();
  await enviar("trace", { thread_id: "prueba", route: "rag" });
  assert.equal(await pagina.locator("#chat-traza").isVisible(), false);
  await enviar("meta", { sources: [{ title: "Historia" }] });
  await enviar("token", { text: "**Primera" });
  await respuestas.last().getByText("Primera", { exact: true }).waitFor();
  assert.equal(await pagina.locator("#chat-stream-estado").textContent(), "");
  await enviar("token", { text: "** [1].\n\n" + "Otra línea de la respuesta.\n\n".repeat(42) });
  await respuestas.last().locator("strong").waitFor();
  await pagina.waitForFunction(() => {
    const panel = document.querySelector(".historial-chat");
    return panel.scrollTop > 100 && panel.scrollHeight - panel.clientHeight - panel.scrollTop < 2;
  });
  await historial.evaluate((nodo) => { nodo.scrollTop = 20; });
  await alFinal.waitFor();
  await enviar("token", { text: "**Texto mientras releo" });
  await respuestas.last().getByText("Texto mientras releo", { exact: true }).waitFor();
  assert.equal(await historial.evaluate((nodo) => nodo.scrollTop), 20);
  assert.equal(await pagina.evaluate(() => window.turnosCompletos), 0);
  await alFinal.click();
  await alFinal.waitFor({ state: "hidden" });
  await detener.click();
  await pagina.locator("[data-chat-detenido]:visible").waitFor();
  assert.equal(await pagina.evaluate(() => window.turnosCompletos), 0);
  assert.match(await respuestas.last().textContent(), /Texto mientras releo$/);

  await pregunta.fill("Cancelar antes del texto");
  await pregunta.press("Enter");
  await detener.click();
  await detener.waitFor({ state: "hidden" });
  assert.equal(await pregunta.inputValue(), "Cancelar antes del texto");
  assert.equal(await respuestas.count(), 1);

  await pregunta.fill("Respuesta que sí termina");
  await pregunta.press("Enter");
  await enviar("fallback", { text: "**Texto final** [1].", sources: [{ title: "Historia" }] });
  await enviar("done", { thread_id: "prueba" });
  await pagina.evaluate(() => window.cerrarStream());
  await detener.waitFor({ state: "hidden" });
  assert.equal(await pagina.evaluate(() => window.turnosCompletos), 1);
  assert.equal(await respuestas.last().textContent(), "Texto final.");

  await pregunta.fill("Interrupción de red");
  await pregunta.press("Enter");
  await enviar("token", { text: "Queda **incompleto" });
  await pagina.evaluate(() => window.cerrarStream());
  await detener.waitFor({ state: "hidden" });
  assert.equal(await pagina.evaluate(() => window.turnosCompletos), 1);
  assert.equal(await respuestas.last().textContent(), "Queda incompleto");
  assert.match(await pagina.locator("#chat-stream-estado").textContent(), /interrumpió/);
  assert.ok(await pagina.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const formulario = await pagina.locator("form[data-chat-stream]").boundingBox();
  assert.ok(formulario.y + formulario.height <= pagina.viewportSize().height);
}

async function main() {
  const navegador = await chromium.launch({
    executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  });
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 740 }]) {
      const movil = viewport.width < 500;
      const pagina = await navegador.newPage({ viewport, isMobile: movil, hasTouch: movil });
      const errores = [];
      pagina.on("pageerror", (error) => errores.push(error.message));
      await comprobar(pagina);
      assert.deepEqual(errores, []);
      console.log(`OK: streaming, scroll, detener y compositor en ${viewport.width} px`);
      await pagina.close();
    }
  } finally {
    await navegador.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
