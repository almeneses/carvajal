"use strict";
const fs = require("node:fs");
const { chromium } = require("playwright-core");
const URL_CHAT = process.env.CHAT_URL || "http://127.0.0.1:8130/chat";
const SALIDA = process.env.SALIDA || require("node:path").join(process.cwd(), "sc003.json");

(async () => {
  const navegador = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: false,
    args: ["--no-sandbox", "--enable-speech-dispatcher"],
  });
  const contexto = await navegador.newContext({ locale: "es-CO" });
  const pagina = await contexto.newPage();
  await pagina.goto(URL_CHAT, { waitUntil: "networkidle" });
  await pagina.evaluate(() => {
    window.__medidas = [];
    window.__click = null;
    window.addEventListener(
      "click",
      (e) => {
        const boton = e.target && e.target.closest && e.target.closest('[data-voz-accion="escuchar"]');
        if (boton) window.__click = performance.now();
      },
      true,
    );
    const original = window.speechSynthesis.speak.bind(window.speechSynthesis);
    window.speechSynthesis.speak = function (locucion) {
      const previo = locucion.onstart;
      locucion.onstart = function (evento) {
        if (window.__click !== null) {
          window.__medidas.push(Math.round(performance.now() - window.__click));
          window.__click = null;
        }
        if (previo) previo.call(this, evento);
      };
      return original(locucion);
    };
  });

  // 20 respuestas por SSE.
  for (let i = 1; i <= 20; i += 1) {
    await pagina.fill("#pregunta", `pregunta de medición ${i}`);
    await pagina.click('form[data-chat-stream] button[type="submit"]');
    await pagina.waitForFunction(
      (n) => document.querySelectorAll("#hilo .turno-assistant").length >= n,
      i,
      { timeout: 15000 },
    );
  }
  const turnos = await pagina.$$("#hilo .turno-assistant");
  const medidas = [];
  let fallos = 0;
  for (let i = 0; i < Math.min(20, turnos.length); i += 1) {
    for (let vez = 0; vez < 2; vez += 1) {
      const antes = await pagina.evaluate(() => window.__medidas.length);
      await turnos[i].$eval('[data-voz-accion="escuchar"]', (b) => b.click());
      try {
        await pagina.waitForFunction(
          (n) => window.__medidas.length > n,
          antes,
          { timeout: 10000 },
        );
        const valor = await pagina.evaluate(() => window.__medidas[window.__medidas.length - 1]);
        medidas.push(valor);
      } catch {
        fallos += 1;
      }
      // Detener para dejar el turno listo para la segunda lectura.
      await pagina.evaluate(() => {
        const boton = document.querySelector(
          '#hilo .turno-assistant [data-voz-accion="detener"]:not([hidden])',
        );
        if (boton) boton.click();
      });
    }
  }
  const ordenadas = [...medidas].sort((a, b) => a - b);
  const p95 = ordenadas.length ? ordenadas[Math.ceil(0.95 * ordenadas.length) - 1] : null;
  const resultado = {
    intentos: 40,
    exitos: medidas.length,
    fallos,
    p95_ms: p95,
    minimo_ms: ordenadas[0] ?? null,
    maximo_ms: ordenadas[ordenadas.length - 1] ?? null,
    umbral_ms: 2000,
    cumple: p95 !== null && p95 < 2000 && fallos === 0,
    medidas_ms: medidas,
    fecha: new Date().toISOString(),
    navegador: await pagina.evaluate(() => navigator.userAgent),
    voz: await pagina.evaluate(() => {
      const v = speechSynthesis.getVoices().find((x) => x.lang.startsWith("es"));
      return v ? `${v.name} (${v.lang})` : null;
    }),
  };
  fs.writeFileSync(SALIDA, JSON.stringify(resultado, null, 2));
  console.log(JSON.stringify(resultado, null, 2));
  await navegador.close();
})().catch((e) => { console.error(e); process.exitCode = 1; });
