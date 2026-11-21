// Voces TTS de Firefox en modo gráfico (fila Firefox de la matriz 040).
//
// `verificar_firefox.cjs` corre en headless y registra el contrato de FR-009.
// Este script comprueba si Firefox de escritorio expone voces del sistema, que es
// la condición para poder medir SC-003. No mide latencia: si hay voces, la
// medición de SC-003 sigue el protocolo de quickstart.md con 40 intentos.
//
// Uso:
//   NODE_PATH=/tmp/cv040/node_modules \
//     node tests/browser/chat_voz_040/medir_voces_firefox.cjs
//
// Variables: CHAT_URL (por defecto http://127.0.0.1:8123/chat).

const { firefox } = require("playwright-core");

const URL = process.env.CHAT_URL || "http://127.0.0.1:8123/chat";

(async () => {
  const navegador = await firefox.launch({ headless: false });
  const pagina = await navegador.newPage();
  await pagina.goto(URL, { waitUntil: "networkidle" });

  const datos = await pagina.evaluate(async () => {
    const sintesis = window.speechSynthesis;
    if (!sintesis) return { sintesis: false, voces: 0, es: [] };
    await new Promise((ok) => {
      let listo = false;
      const fin = () => {
        if (listo) return;
        listo = true;
        ok();
      };
      sintesis.addEventListener("voiceschanged", fin, { once: true });
      setTimeout(fin, 5000);
    });
    const voces = sintesis.getVoices();
    return {
      sintesis: true,
      voces: voces.length,
      es: voces.filter((v) => /^es/i.test(v.lang)).map((v) => `${v.name} (${v.lang})`),
    };
  });

  const salida = {
    navegador: `Firefox ${navegador.version()} (Playwright, headed)`,
    version: navegador.version(),
    modo: "headed",
    ...datos,
    sc003Medible: datos.voces > 0,
  };
  console.log(JSON.stringify(salida, null, 2));

  await navegador.close();
  process.exit(0);
})().catch((error) => {
  console.error(String(error));
  process.exit(1);
});
