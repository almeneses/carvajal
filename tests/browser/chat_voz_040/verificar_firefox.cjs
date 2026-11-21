// Fila Firefox de la matriz de `docs/pruebas-040.md`.
//
// Firefox no implementa SpeechRecognition: el chat debe explicar la limitación,
// ocultar dictado y manos libres, y seguir funcionando por escrito. Este script
// comprueba ese contrato (FR-009) y la accesibilidad con axe. No mide SC-001 ni
// SC-002: sin STT no aplican. Tampoco sustituye SC-003 con voz real.
//
// Uso (con el servidor del arnés en :8123 y playwright-core instalado fuera del
// proyecto, igual que `validar.cjs`):
//
//   NODE_PATH=/tmp/cv040/node_modules \
//   AXE_PATH=/tmp/cv040/node_modules/axe-core/axe.min.js \
//     node tests/browser/chat_voz_040/verificar_firefox.cjs
//
// Variables: CHAT_URL (por defecto http://127.0.0.1:8123/chat), AXE_PATH
// (obligatoria). Sin AXE_PATH el script sale con código 2: la auditoría de
// accesibilidad es parte de la comprobación y no se confunde con una violación.

const fs = require("fs");
const { firefox } = require("playwright-core");

const URL = process.env.CHAT_URL || "http://127.0.0.1:8123/chat";
const AXE = process.env.AXE_PATH;

if (!AXE || !fs.existsSync(AXE)) {
  console.error(
    "AXE_PATH es obligatorio y debe apuntar a axe.min.js de axe-core (código 2).",
  );
  process.exit(2);
}

async function auditarAxe(navegador) {
  // Solo la auditoría usa bypassCSP, como en validar.cjs; el recorrido
  // funcional conserva la CSP real.
  const contexto = await navegador.newContext({ bypassCSP: true });
  const pagina = await contexto.newPage();
  await pagina.goto(URL, { waitUntil: "networkidle" });
  await pagina.addScriptTag({ path: AXE });
  const violaciones = await pagina.evaluate(async () => {
    const resultado = await window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    });
    return resultado.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.length,
    }));
  });
  await contexto.close();
  return { violaciones };
}

(async () => {
  const navegador = await firefox.launch({ headless: true });
  const contexto = await navegador.newContext();
  const pagina = await contexto.newPage();
  await pagina.goto(URL, { waitUntil: "networkidle" });

  const capacidades = await pagina.evaluate(() => ({
    stt: Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    sintesis: Boolean(window.speechSynthesis),
    segura: window.isSecureContext,
    userAgent: navigator.userAgent,
    voces: window.speechSynthesis ? window.speechSynthesis.getVoices().length : 0,
  }));

  const interfaz = await pagina.evaluate(() => {
    const nota = document.querySelector("[data-voz-nota]");
    const dictar = document.querySelector("[data-voz-dictar]");
    const manos = document.querySelector("[data-voz-manos-libres]");
    const visible = (el) => Boolean(el) && !el.hidden && el.offsetParent !== null;
    return {
      dictarVisible: visible(dictar),
      manosLibresVisible: visible(manos),
      notaVisible: visible(nota),
      notaTexto: nota ? nota.textContent.trim() : null,
    };
  });

  await pagina.fill("#pregunta", "¿Cuántas cajas hay en Panamá?");
  await pagina.click("form.composer button[type=submit]");
  await pagina.waitForTimeout(1500);
  const envio = await pagina.evaluate(() => ({
    burbujas: document.querySelectorAll(".mensaje, .turno").length,
    borrador: document.querySelector("#pregunta")?.value ?? null,
  }));

  const peticionesAudio = await pagina.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((e) => e.name)
      .filter((n) => /audio|\/media|blob:/i.test(n)),
  );

  let axe;
  try {
    axe = await auditarAxe(navegador);
  } catch (error) {
    axe = { error: String(error).split("\n")[0] };
  }

  const comprobaciones = {
    "Firefox no expone STT": capacidades.stt === false,
    "Firefox no expone voces TTS": capacidades.voces === 0,
    "el dictado no se ofrece": interfaz.dictarVisible === false,
    "manos libres no se ofrece": interfaz.manosLibresVisible === false,
    "se explica la limitación": interfaz.notaVisible && /no permite dictar/i.test(interfaz.notaTexto || ""),
    "el chat escrito sigue funcionando": envio.burbujas >= 2 && envio.borrador === "",
    "axe sin violaciones nuevas": Array.isArray(axe.violaciones) && axe.violaciones.length === 0,
    "sin peticiones de audio": peticionesAudio.length === 0,
  };
  const fallos = Object.entries(comprobaciones)
    .filter(([, ok]) => !ok)
    .map(([nombre]) => nombre);

  console.log(
    JSON.stringify(
      {
        navegador: `Firefox ${navegador.version()} (Playwright)`,
        version: navegador.version(),
        capacidades,
        interfaz,
        envio,
        peticionesAudio,
        axe,
        comprobaciones,
        fallos,
      },
      null,
      2,
    ),
  );

  await navegador.close();
  process.exit(fallos.length === 0 ? 0 : 1);
})().catch((error) => {
  console.error(String(error));
  process.exit(1);
});
