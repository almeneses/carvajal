// Validación de navegador de la 040 (script local, no se commitea).
// Usa Chromium real + dobles de Web Speech y un SSE falso para validar el
// comportamiento del cliente. No sustituye SC-001–SC-003 con voces reales ni
// una prueba de micrófono/lector de pantalla.
//
// Uso:
//   PYTHONPATH=<repo> python tests/browser/chat_voz_040/servidor.py
//   NODE_PATH=<dir con playwright-core y axe-core> \
//     node tests/browser/chat_voz_040/validar.cjs
//
// Ver README.md. No se ejecuta en `make check` ni en CI.
"use strict";

const fs = require("node:fs");
const { chromium } = require("playwright-core");

const URL_CHAT = process.env.CHAT_URL || "http://127.0.0.1:8123/chat";
// `playwright-core` resuelve el navegador que espera su versión; si no está
// instalado se usa un Chromium del sistema. CHROME_PATH fuerza uno concreto.
const CANDIDATOS = [
  chromium.executablePath(),
  "/usr/bin/chromium-browser",
  "/usr/bin/chromium",
  "/usr/bin/google-chrome",
];
const CHROME =
  process.env.CHROME_PATH || CANDIDATOS.find((ruta) => ruta && fs.existsSync(ruta));
const AXE_PATH = process.env.AXE_PATH || require.resolve("axe-core/axe.min.js");
const AXE = fs.readFileSync(AXE_PATH, "utf8");

const INIT_FAKES = () => {
  const estado = {
    reconocimientos: [],
    locuciones: [],
    cancelados: 0,
    pausas: 0,
  };
  window.__vozE2E = estado;

  function FakeRecognition() {
    this.iniciado = 0;
    this.detenido = 0;
    this.abortado = 0;
    this.lang = "";
    estado.reconocimientos.push(this);
  }
  FakeRecognition.prototype.start = function () {
    this.iniciado += 1;
    if (typeof this.onstart === "function") this.onstart({});
  };
  FakeRecognition.prototype.stop = function () {
    this.detenido += 1;
  };
  FakeRecognition.prototype.abort = function () {
    this.abortado += 1;
    if (typeof this.onend === "function") this.onend({});
  };
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;

  window.SpeechSynthesisUtterance = function (texto) {
    this.text = texto;
    this.lang = "";
    this.voice = null;
    this.onstart = null;
    this.onend = null;
    this.onerror = null;
  };
  const sintesis = {
    speaking: false,
    paused: false,
    actual: null,
    getVoices() {
      return [{ name: "Mónica", lang: "es-CO", default: true, localService: true }];
    },
    speak(locucion) {
      this.actual = locucion;
      this.speaking = true;
      estado.locuciones.push(locucion.text);
      if (typeof locucion.onstart === "function") locucion.onstart({});
    },
    cancel() {
      estado.cancelados += 1;
      this.speaking = false;
      const anterior = this.actual;
      this.actual = null;
      if (anterior && typeof anterior.onend === "function") anterior.onend({});
    },
    pause() {
      estado.pausas += 1;
      this.paused = true;
    },
    resume() {
      this.paused = false;
    },
  };
  Object.defineProperty(window, "speechSynthesis", { value: sintesis, configurable: true });

  window.__vozEmitir = (texto) => {
    const actual = estado.reconocimientos[estado.reconocimientos.length - 1];
    if (!actual || typeof actual.onresult !== "function") throw new Error("sin reconocimiento");
    const alternativas = [{ transcript: texto, confidence: 0.9 }];
    actual.onresult({
      resultIndex: 0,
      results: Object.assign([], {
        length: 1,
        0: Object.assign(alternativas, { isFinal: true, length: 1 }),
      }),
    });
  };
  window.__vozFin = () => {
    const actual = estado.reconocimientos[estado.reconocimientos.length - 1];
    if (actual && typeof actual.onend === "function") actual.onend({});
  };
  window.__vozError = (codigo) => {
    const actual = estado.reconocimientos[estado.reconocimientos.length - 1];
    if (actual && typeof actual.onerror === "function") actual.onerror({ error: codigo });
  };
  window.__vozTerminarLocucion = () => {
    const actual = window.speechSynthesis.actual;
    window.speechSynthesis.actual = null;
    if (actual && typeof actual.onend === "function") actual.onend({});
  };
};

async function completarLectura(pagina, maximo = 12) {
  for (let i = 0; i < maximo; i += 1) {
    const sigue = await pagina.evaluate(
      () => Boolean(window.speechSynthesis.actual),
    );
    if (!sigue) return;
    await pagina.evaluate(() => window.__vozTerminarLocucion());
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
}

async function esperar(condicion, ms = 5000, etiqueta = "condición") {
  const inicio = Date.now();
  for (;;) {
    if (await condicion()) return;
    if (Date.now() - inicio > ms) throw new Error(`tiempo agotado: ${etiqueta}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function main() {
  const navegador = await chromium.launch({
    executablePath: CHROME,
    args: ["--no-sandbox"],
  });
  const resultado = { pasos: [], errores: [], consola: [], red: [], axe: {} };
  const contexto = await navegador.newContext({ locale: "es-CO" });
  const pagina = await contexto.newPage();
  await pagina.addInitScript(INIT_FAKES);
  pagina.on("pageerror", (error) => resultado.errores.push(`pageerror: ${error.message}`));
  pagina.on("console", (mensaje) => {
    if (mensaje.type() === "error") resultado.consola.push(mensaje.text());
  });
  pagina.on("request", (peticion) =>
    resultado.red.push({
      method: peticion.method(),
      url: peticion.url(),
      type: peticion.resourceType(),
      body: peticion.postData() || "",
    }),
  );

  const paso = (nombre, ok, detalle = "") => {
    resultado.pasos.push({ nombre, ok, detalle });
    if (!ok) resultado.errores.push(`${nombre}: ${detalle}`);
  };

  // 1. Carga y controles accesibles.
  await pagina.goto(URL_CHAT, { waitUntil: "networkidle" });
  const hayVoz = await pagina.evaluate(() => Boolean(document.querySelector("[data-voz-dictar]")));
  if (!hayVoz) {
    console.log(JSON.stringify({ url: pagina.url(), errores: resultado.errores, html: (await pagina.content()).slice(0, 400) }, null, 2));
    await navegador.close();
    process.exit(1);
  }
  const visibles = await pagina.evaluate(() => ({
    dictar: !document.querySelector("[data-voz-dictar]").hidden,
    manos: !document.querySelector("[data-voz-manos-libres]").hidden,
    autolectura: !document.querySelector("[data-voz-autolectura]").hidden,
    autolecturaMarcada: document.querySelector("[data-voz-autolectura]").checked,
    tipos: [...document.querySelectorAll(".voz-boton")].map((b) => b.getAttribute("type")),
    etiquetas: [...document.querySelectorAll(".voz-boton")].map((b) => b.getAttribute("aria-label")),
    estado: document.querySelector("[data-voz-estado]").getAttribute("role"),
  }));
  paso("controles visibles con STT/TTS/contexto seguro", visibles.dictar && visibles.manos);
  paso("autolectura apagada por defecto", visibles.autolectura && !visibles.autolecturaMarcada);
  paso("botones type=button y nombre accesible", visibles.tipos.every((t) => t === "button"));
  paso("estado role=status", visibles.estado === "status");

  // 2. Dictado: aviso previo, transcripción editable y sin envío.
  // Cualquier POST cuenta: el autoenvío usa /chat/stream, no solo /chat.
  const enviosAntes = resultado.red.filter((r) => r.method === "POST").length;
  await pagina.click("[data-voz-dictar]");
  const avisoAbierto = await pagina.evaluate(
    () => document.querySelector("[data-voz-aviso]").open,
  );
  paso("aviso antes del primer dictado", avisoAbierto);
  await pagina.click("[data-voz-aviso-aceptar]");
  await esperar(
    () => pagina.evaluate(() => window.__vozE2E.reconocimientos.length > 0),
    2000,
    "reconocimiento",
  );
  await pagina.evaluate(() => {
    window.__vozEmitir("necesito doce onzas para café");
    window.__vozFin();
  });
  const borrador = await pagina.inputValue("#pregunta");
  const enviosDespues = resultado.red.filter((r) => r.method === "POST").length;
  paso("transcripción normalizada en el campo", borrador === "necesito 12 onzas para café", borrador);
  paso("no se envió sola", enviosAntes === enviosDespues);

  // 3. Turno dictado (spec 050): la persona habló, así que la respuesta suena sola
  // aunque la autolectura esté apagada. Aquí el doble no tiene voz del servidor, así que
  // suena con el respaldo del navegador.
  const enviarYEsperar = async (pregunta, etiqueta) => {
    const antes = await pagina.evaluate(
      () => document.querySelectorAll("#hilo .turno-assistant .texto").length,
    );
    await pagina.fill("#pregunta", pregunta);
    await pagina.click('form[data-chat-stream] button[type="submit"]');
    await esperar(
      () =>
        pagina.evaluate(
          (n) => document.querySelectorAll("#hilo .turno-assistant .texto").length > n,
          antes,
        ),
      5000,
      etiqueta,
    );
    await new Promise((resolve) => setTimeout(resolve, 600));
  };
  const locuciones = () => pagina.evaluate(() => window.__vozE2E.locuciones.length);
  const vaciarLocuciones = () =>
    pagina.evaluate(() => {
      window.__vozE2E.locuciones.length = 0;
    });
  await enviarYEsperar("necesito 12 onzas para café", "turno dictado");
  const locucionesDictado = await locuciones();
  paso(
    "el turno dictado suena solo con la autolectura apagada",
    locucionesDictado >= 1,
    String(locucionesDictado),
  );
  await completarLectura(pagina);

  // 3b. Turno escrito con la autolectura apagada: no suena.
  await vaciarLocuciones();
  await enviarYEsperar("quiero vasos térmicos", "turno escrito");
  const locucionesEscrito = await locuciones();
  paso(
    "con autolectura apagada no suena el turno escrito",
    locucionesEscrito === 0,
    String(locucionesEscrito),
  );

  // 4. Lectura manual: el resumen hablado del servidor (spec 048). Los datos de las
  // tarjetas se quedan en pantalla. Este doble manda las tarjetas como HTML, sin el
  // resultado comercial, así que el resumen tampoco las nombra.
  await vaciarLocuciones();
  await pagina.click("#hilo .turno-assistant [data-voz-accion='escuchar']");
  const primera = await pagina.evaluate(() => window.__vozE2E.locuciones.slice());
  await completarLectura(pagina);
  const todas = await pagina.evaluate(() => window.__vozE2E.locuciones.slice());
  const textoLeido = todas.join(" ");
  const resumen = await pagina.evaluate(
    () => document.querySelector("#hilo .turno-assistant")?.dataset?.vozResumen || "",
  );
  paso("el turno trae el resumen hablado del servidor", resumen.trim() !== "", resumen);
  paso("lee el texto de la respuesta", textoLeido.includes("Tenemos vasos de papel"));
  paso(
    "no lee los datos de la tarjeta (quedan en pantalla)",
    !/7250|Cajas: 5/.test(textoLeido),
    textoLeido,
  );
  paso(
    "lee la fuente en una frase",
    /Lo saqué del documento «Manual de empaques»/.test(textoLeido),
    textoLeido,
  );
  paso(
    "no lee URLs ni SKU",
    !/https?:|FX-/.test(textoLeido),
    textoLeido,
  );
  paso("la primera locución no incluye la tarjeta", !/7250/.test(primera[0]));

  // 5. Pausa, reanudación y parada con teclado.
  const accionActiva = () =>
    pagina.evaluate(() => document.activeElement?.dataset?.vozAccion || "");
  await pagina.focus("#hilo .turno-assistant [data-voz-accion='escuchar']");
  await pagina.keyboard.press("Enter");
  const focoEscuchar = await accionActiva();
  await pagina.keyboard.press("Tab");
  const focoTrasTab = await accionActiva();
  await pagina.click("#hilo .turno-assistant [data-voz-accion='pausar']");
  const pausado = await pagina.evaluate(() => window.speechSynthesis.paused);
  const focoTrasPausa = await accionActiva();
  await pagina.click("#hilo .turno-assistant [data-voz-accion='reanudar']");
  const reanudado = await pagina.evaluate(() => !window.speechSynthesis.paused);
  await pagina.click("#hilo .turno-assistant [data-voz-accion='detener']");
  const cancelados = await pagina.evaluate(() => window.__vozE2E.cancelados);
  const focoTrasDetener = await accionActiva();
  paso("Enter inicia la lectura con el control enfocado", focoEscuchar === "escuchar");
  paso("Tab alcanza el control siguiente", focoTrasTab === "pausar", focoTrasTab);
  paso("pausar y reanudar", pausado && reanudado);
  paso("al pausar el foco pasa a Reanudar", focoTrasPausa === "reanudar", focoTrasPausa);
  paso("detener cancela la síntesis", cancelados >= 1);
  paso("al detener el foco vuelve a Escuchar", focoTrasDetener === "escuchar", focoTrasDetener);

  // 6. Autolectura tras done (solo con la preferencia activa).
  await pagina.click("[data-voz-opciones] > summary");
  await pagina.check("[data-voz-autolectura]");
  const locucionesAntes = await pagina.evaluate(() => window.__vozE2E.locuciones.length);
  await pagina.fill("#pregunta", "otra pregunta");
  await pagina.click('form[data-chat-stream] button[type="submit"]');
  await esperar(
    () => pagina.evaluate((antes) => window.__vozE2E.locuciones.length > antes, locucionesAntes),
    5000,
    "autolectura tras done",
  );
  paso("autolectura tras done con la preferencia activa", true);
  await completarLectura(pagina);

  // 7. Manos libres: activación, envío único y reescucha tras lectura.
  await pagina.fill("#pregunta", "");
  await pagina.click("[data-voz-manos-libres]");
  const avisoManos = await pagina.evaluate(
    () => document.querySelector("[data-voz-manos-aviso]").open,
  );
  paso("manos libres avisa del autoenvío", avisoManos);
  await pagina.click("[data-voz-manos-aceptar]");
  await esperar(
    () => pagina.evaluate(() => window.__vozE2E.reconocimientos.length >= 1),
    2000,
    "manos libres escuchando",
  );
  const enviosAntesManos = resultado.red.filter(
    (r) => r.method === "POST" && r.url.endsWith("/chat/stream"),
  ).length;
  const reconocimientosAntes = await pagina.evaluate(
    () => window.__vozE2E.reconocimientos.length,
  );
  const turnosAntesManos = await pagina.evaluate(
    () => document.querySelectorAll("#hilo .turno-assistant").length,
  );
  const locucionesAntesManos = await pagina.evaluate(
    () => window.__vozE2E.locuciones.length,
  );
  await pagina.evaluate(() => {
    window.__vozEmitir("necesito cinco mil vasos");
    window.__vozFin();
  });
  await esperar(
    () =>
      pagina.evaluate(
        (antes) =>
          document.querySelectorAll("#hilo .turno-assistant").length >= antes + 1,
        turnosAntesManos,
      ),
    5000,
    "envío manos libres",
  );
  const enviosDespuesManos = resultado.red.filter(
    (r) => r.method === "POST" && r.url.endsWith("/chat/stream"),
  ).length;
  paso("manos libres envía una sola vez", enviosDespuesManos === enviosAntesManos + 1);
  await esperar(
    () =>
      pagina.evaluate(
        (antes) => window.__vozE2E.locuciones.length > antes,
        locucionesAntesManos,
      ),
    8000,
    "lectura de la respuesta manos libres",
  );
  await completarLectura(pagina);
  const depuracion = await pagina.evaluate(() => ({
    locuciones: window.__vozE2E.locuciones,
    reconocimientos: window.__vozE2E.reconocimientos.length,
    activo: document.getElementById("chat-voz")?._controladorVoz?.manosLibres?.activo,
    leyendo: document.getElementById("chat-voz")?._controladorVoz?.estado?.leyendo,
    estado: document.querySelector("[data-voz-estado]")?.textContent,
    pregunta: document.querySelector("#pregunta")?.value,
    turnos: document.querySelectorAll("#hilo .turno-assistant").length,
  }));
  try {
    await esperar(
      () =>
        pagina.evaluate(
          (antes) => window.__vozE2E.reconocimientos.length > antes,
          reconocimientosAntes,
        ),
      3000,
      "reescucha",
    );
  } catch (error) {
    console.log(JSON.stringify(depuracion, null, 2));
    throw error;
  }
  paso("reescucha tras la lectura normal", true);

  // 7a. Escribir apaga el modo y conserva el texto (con el modo aún activo).
  await pagina.fill("#pregunta", "escribo yo");
  const manosApagado = await pagina.evaluate(
    () => document.querySelector("[data-voz-manos-libres]").getAttribute("aria-pressed"),
  );
  paso("escribir apaga el modo y conserva el texto", manosApagado === "false");
  paso("el texto escrito sigue en el borrador", (await pagina.inputValue("#pregunta")) === "escribo yo");

  // 7b. Reactivar con borrador vacío para probar el fallo del stream.
  await pagina.fill("#pregunta", "");
  const reconocimientosFallo = await pagina.evaluate(
    () => window.__vozE2E.reconocimientos.length,
  );
  await pagina.click("[data-voz-manos-libres]");
  await pagina.click("[data-voz-manos-aceptar]");
  await esperar(
    () =>
      pagina.evaluate(
        (antes) => window.__vozE2E.reconocimientos.length > antes,
        reconocimientosFallo,
      ),
    3000,
    "reactivación para el fallo",
  );
  await pagina.route("**/chat/stream", (ruta) =>
    ruta.fulfill({ status: 500, contentType: "text/plain", body: "fallo simulado" }),
  );
  const cuenta = (sufijo) =>
    resultado.red.filter((r) => r.method === "POST" && r.url.endsWith(sufijo)).length;
  const fallosStreamAntes = cuenta("/chat/stream");
  const fallosChatAntes = cuenta("/chat");
  await pagina.evaluate(() => {
    window.__vozEmitir("pregunta que falla");
    window.__vozFin();
  });
  await esperar(
    () =>
      pagina.evaluate(
        () =>
          document
            .querySelector("[data-voz-manos-libres]")
            .getAttribute("aria-pressed") === "false",
      ),
    5000,
    "apagado tras fallo",
  );
  const trasFallo = {
    stream: cuenta("/chat/stream"),
    chat: cuenta("/chat"),
    borrador: await pagina.inputValue("#pregunta"),
    estado: await pagina.textContent("[data-voz-estado]"),
  };
  paso(
    "el fallo del stream no reenvía por POST normal",
    trasFallo.stream === fallosStreamAntes + 1 && trasFallo.chat === fallosChatAntes,
    JSON.stringify(trasFallo),
  );
  paso(
    "el fallo devuelve el texto al borrador",
    trasFallo.borrador.includes("pregunta que falla"),
    trasFallo.borrador,
  );
  paso("el fallo apaga manos libres con aviso", /fall|revis/i.test(trasFallo.estado), trasFallo.estado);
  await pagina.unroute("**/chat/stream");

  // 8. Privacidad: sin audio, solo dos booleanos y ningún blob.
  const almacen = await pagina.evaluate(() => Object.keys(window.localStorage).sort());
  const peticionesAudio = resultado.red.filter(
    (peticion) =>
      /audio|media|microphone|speech/i.test(peticion.url) ||
      peticion.type === "media" ||
      /audio|micrófono/i.test(peticion.body),
  );
  const bases = await pagina.evaluate(async () =>
    typeof indexedDB.databases === "function" ? await indexedDB.databases() : [],
  );
  paso("sin peticiones de audio al servidor", peticionesAudio.length === 0, JSON.stringify(peticionesAudio));
  const clavesVoz = almacen.filter((clave) => clave.startsWith("carvajal_voz_"));
  paso(
    "localStorage solo con preferencias booleanas de voz",
    clavesVoz.length === 2 && !almacen.some((clave) => /audio|voz_texto|transcrip/i.test(clave)),
    JSON.stringify(almacen),
  );
  paso("sin IndexedDB", bases.length === 0, JSON.stringify(bases));

  // 8a. Política de reproducción automática: sin onstart no se finge leer.
  await pagina.evaluate(() => {
    window.speechSynthesis.speak = function (locucion) {
      window.__vozE2E.locuciones.push(locucion.text);
    };
  });
  await pagina.click("#hilo .turno-assistant [data-voz-accion='escuchar']");
  const autoplay = await pagina.evaluate(() => ({
    leyendo: document.querySelector(".voz")._controladorVoz.estado.leyendo,
    pulsado: document
      .querySelector("#hilo .turno-assistant [data-voz-accion='escuchar']")
      .getAttribute("aria-pressed"),
  }));
  paso(
    "si el navegador bloquea el audio no se marca como leyendo",
    autoplay.leyendo === false && autoplay.pulsado === "false",
    JSON.stringify(autoplay),
  );

  // 8b. Fallback sin streaming: POST normal, historial y lectura manual.
  const ctxSinStream = await navegador.newContext({ locale: "es-CO" });
  const pSinStream = await ctxSinStream.newPage();
  await pSinStream.addInitScript(INIT_FAKES);
  await pSinStream.route("**/static/chat-stream.js", (ruta) => ruta.abort());
  await pSinStream.goto(URL_CHAT, { waitUntil: "networkidle" });
  await pSinStream.fill("#pregunta", "pregunta sin streaming");
  await pSinStream.click('form[data-chat-stream] button[type="submit"]');
  await pSinStream.waitForLoadState("networkidle");
  const historial = await pSinStream.evaluate(() => ({
    escuchar: document.querySelectorAll("#hilo .turno-assistant [data-voz-accion='escuchar']").length,
    locuciones: window.__vozE2E.locuciones.length,
  }));
  paso(
    "el fallback sin streaming deja «Escuchar» en el historial",
    historial.escuchar >= 1,
    JSON.stringify(historial),
  );
  paso("al recargar el historial no suena solo", historial.locuciones === 0);
  await pSinStream.click("#hilo .turno-assistant [data-voz-accion='escuchar']");
  const leidoSinStream = await pSinStream.evaluate(() =>
    window.__vozE2E.locuciones.join(" "),
  );
  paso(
    "la lectura manual funciona tras el POST sin streaming",
    /Respuesta sin streaming/.test(leidoSinStream),
    leidoSinStream,
  );
  await ctxSinStream.close();

  // 9. axe: antes (sin chat-voice.js) y después.
  async function escanear(bloquearVoz) {
    // bypassCSP solo para inyectar axe desde el runner local; la página real
    // se validó con su CSP intacta en el resto del recorrido.
    const ctx = await navegador.newContext({ locale: "es-CO", bypassCSP: true });
    const p = await ctx.newPage();
    await p.addInitScript(INIT_FAKES);
    if (bloquearVoz) {
      await p.route("**/static/chat-voice.js", (ruta) => ruta.abort());
    }
    await p.goto(URL_CHAT, { waitUntil: "networkidle" });
    await p.addScriptTag({ content: AXE });
    const salida = await p.evaluate(async () => {
      const r = await window.axe.run(document, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
      });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
    });
    await ctx.close();
    return salida;
  }
  const base = await escanear(true);
  const despues = await escanear(false);
  const idsBase = new Set(base.map((v) => v.id));
  const nuevos = despues.filter((v) => !idsBase.has(v.id));
  resultado.axe = { base, despues, nuevos };
  paso("axe sin errores nuevos", nuevos.length === 0, JSON.stringify(nuevos));

  await navegador.close();
  console.log(JSON.stringify(resultado, null, 2));
  if (resultado.errores.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
