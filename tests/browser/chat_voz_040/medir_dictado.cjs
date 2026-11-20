"use strict";
const fs = require("node:fs");
const { chromium } = require("playwright-core");
const URL_CHAT = process.env.CHAT_URL || "http://127.0.0.1:8130/chat";
const path = require("node:path");
const SALIDA = process.env.SALIDA || path.join(process.cwd(), "dictado.jsonl");
const PREGUNTAS = require(path.join(__dirname, "preguntas_sc001.json"));

(async () => {
  fs.writeFileSync(SALIDA, "");
  const navegador = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: false,
    args: ["--no-sandbox", "--enable-speech-dispatcher"],
  });
  const contexto = await navegador.newContext({ locale: "es-CO" });
  await contexto.grantPermissions(["microphone"], { origin: new URL(URL_CHAT).origin });
  await contexto.addInitScript(() => {
    const Orig = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Orig) return;
    window.__med = { eventos: [], visible: null };
    function Wrapped() {
      const r = new Orig();
      window.__med.eventos = [];
      window.__med.visible = null;
      for (const tipo of ["start", "audiostart", "speechstart", "speechend", "result", "error", "end"]) {
        r.addEventListener(tipo, (e) => {
          let final = null;
          try { final = e.results && e.results[0] && e.results[0][0] ? e.results[0][0].transcript : null; } catch {}
          window.__med.eventos.push({ tipo, t: performance.now(), final, error: e.error || null });
        });
      }
      return r;
    }
    window.SpeechRecognition = Wrapped;
    window.webkitSpeechRecognition = Wrapped;
    window.__med.vigilar = () => {
      const input = document.getElementById("pregunta");
      const arranque = performance.now();
      const bucle = () => {
        if (input.value.trim()) { window.__med.visible = performance.now(); return; }
        if (performance.now() - arranque > 20000) return;
        requestAnimationFrame(bucle);
      };
      requestAnimationFrame(bucle);
    };
  });
  const pagina = await contexto.newPage();
  pagina.on("pageerror", (e) => fs.appendFileSync(SALIDA, JSON.stringify({ pageerror: e.message }) + "\n"));
  await pagina.goto(URL_CHAT, { waitUntil: "networkidle" });

  await pagina.evaluate((etiqueta) => { window.__vozEtiqueta = etiqueta; }, process.env.VOZ || "");
  await pagina.evaluate(() => {
    const caja = document.createElement("div");
    caja.id = "guia";
    caja.style.cssText =
      "position:fixed;left:20px;right:20px;bottom:20px;z-index:99999;background:#fff;border:3px solid #0b6;border-radius:12px;padding:16px;font-family:system-ui;box-shadow:0 8px 30px #0003";
    caja.innerHTML =
      '<div id="g-num" style="font-weight:700;color:#0b6"></div>' +
      '<div id="g-preg" style="font-size:1.3rem;margin:8px 0 12px"></div>' +
      '<button id="g-btn" style="font-size:1.1rem;padding:10px 18px">Dictar</button>' +
      '<span id="g-est" style="margin-left:12px;color:#555"></span>';
    document.body.append(caja);
    const boton = document.getElementById("g-btn");
    boton.dataset.modo = "dictar";
    boton.addEventListener("click", () => {
      if (boton.dataset.modo === "dictar") {
        boton.disabled = true;
        document.getElementById("pregunta").value = "";
        window.__med.vigilar();
        document.querySelector("[data-voz-dictar]").click();
        setTimeout(() => {
          const dialogo = document.querySelector("[data-voz-aviso]");
          if (dialogo && dialogo.open) {
            const aceptar = document.querySelector("[data-voz-aviso-aceptar]");
            if (aceptar) aceptar.click();
          }
        }, 250);
        window.__guiaSiguiente = false;
      } else {
        window.__guiaSiguiente = true;
      }
    });
  });

  for (let i = 0; i < PREGUNTAS.length; i += 1) {
    const p = PREGUNTAS[i];
    await pagina.evaluate(({ num, total, pregunta }) => {
      document.getElementById("g-num").textContent = `${window.__vozEtiqueta || ""}Pregunta ${num} de ${total}`;
      document.getElementById("g-preg").textContent = pregunta;
      const b = document.getElementById("g-btn");
      b.textContent = "Dictar";
      b.disabled = false;
      b.dataset.modo = "dictar";
      document.getElementById("g-est").textContent = "Léela y pulsa Dictar.";
      window.__guiaSiguiente = false;
    }, { num: i + 1, total: PREGUNTAS.length, pregunta: p.question });

    // Esperar el clic en Dictar.
    await pagina.waitForFunction(() => {
      const b = document.getElementById("g-btn");
      return b && b.disabled === true;
    }, null, { timeout: 300000 });

    // Esperar la transcripción en el campo o un error.
    try {
      await pagina.waitForFunction(
        () => document.getElementById("pregunta").value.trim().length > 0,
        null,
        { timeout: 20000 },
      );
    } catch {}

    const datos = await pagina.evaluate(() => ({
      texto: document.getElementById("pregunta").value,
      eventos: window.__med.eventos,
      visible: window.__med.visible,
    }));
    const finHabla = [...datos.eventos].reverse().find((e) => e.tipo === "speechend");
    const error = datos.eventos.find((e) => e.tipo === "error");
    const intento = {
      id: p.id,
      pregunta: p.question,
      ruta_esperada: p.route,
      texto: datos.texto,
      speechend: finHabla ? finHabla.t : null,
      visible: datos.visible,
      latencia_ms: finHabla && datos.visible ? Math.round(datos.visible - finHabla.t) : null,
      error: error ? error.error : null,
      eventos: datos.eventos.map((e) => ({ tipo: e.tipo, t: Math.round(e.t), final: e.final })),
    };
    fs.appendFileSync(SALIDA, JSON.stringify(intento) + "\n");
    await pagina.evaluate(({ texto, ok }) => {
      const b = document.getElementById("g-btn");
      b.textContent = "Siguiente";
      b.dataset.modo = "siguiente";
      b.disabled = false;
      document.getElementById("g-est").textContent = ok
        ? `Transcrito: «${texto}»`
        : "Sin transcripción o sin marca de fin de habla; se cuenta como fallo.";
      window.__guiaSiguiente = false;
    }, { texto: datos.texto || "(vacío)", ok: Boolean(datos.texto && finHabla) });

    await pagina.waitForFunction(() => window.__guiaSiguiente === true, null, { timeout: 300000 });
    await pagina.evaluate(() => {
      document.getElementById("pregunta").value = "";
    });
  }

  await pagina.evaluate(() => {
    document.getElementById("g-num").textContent = "Terminado";
    document.getElementById("g-preg").textContent =
      "Gracias. Ya puedes cerrar esta ventana; los resultados están guardados.";
    document.getElementById("g-btn").style.display = "none";
    document.getElementById("g-est").textContent = "";
  });
  fs.appendFileSync(SALIDA, JSON.stringify({ fin: new Date().toISOString() }) + "\n");
  await new Promise((r) => setTimeout(r, 5000));
  await navegador.close();
})().catch((e) => { console.error(e); process.exitCode = 1; });
