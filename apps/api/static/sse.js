// Lector SSE común (spec 044).
//
// Chat y FAQ reciben eventos con el mismo formato de cable; este archivo es la
// única implementación del bucle de lectura. El consumidor solo decide qué
// hacer con cada evento: el lector no conoce nombres, negocio ni DOM.
//
// Casos que cubre: bloques partidos entre lecturas, varios `data:` unidos con
// salto de línea, finales de línea `\n` y `\r\n`, bloques vacíos o con JSON
// inválido (se ignoran) y un último bloque sin la línea en blanco de cierre.
(() => {
  "use strict";

  const SEPARADOR = /\r?\n\r?\n/;

  function interpretar(bloque) {
    if (!bloque.trim()) return null;
    let nombre = "message";
    const datos = [];
    for (const linea of bloque.split(/\r?\n/)) {
      if (linea.startsWith("event:")) nombre = linea.slice(6).trim();
      else if (linea.startsWith("data:")) datos.push(linea.slice(5).trimStart());
    }
    if (datos.length === 0) return null;
    try {
      return { nombre, datos: JSON.parse(datos.join("\n")) };
    } catch {
      return null;
    }
  }

  function vaciarBloques(buffer, alEvento) {
    for (;;) {
      const corte = SEPARADOR.exec(buffer);
      if (!corte) return buffer;
      const bloque = buffer.slice(0, corte.index);
      buffer = buffer.slice(corte.index + corte[0].length);
      const evento = interpretar(bloque);
      if (evento) alEvento(evento);
    }
  }

  async function leerEventos(response, alEvento) {
    const cuerpo = response && response.body;
    if (!cuerpo || typeof cuerpo.getReader !== "function") {
      throw new Error("La respuesta no tiene un flujo SSE legible.");
    }
    const lector = cuerpo.getReader();
    const decodificador = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      buffer += decodificador.decode(value, { stream: true });
      buffer = vaciarBloques(buffer, alEvento);
    }
    buffer += decodificador.decode();
    buffer = vaciarBloques(buffer, alEvento);
    const ultimo = interpretar(buffer);
    if (ultimo) alEvento(ultimo);
  }

  const api = { interpretar, leerEventos };
  if (typeof window !== "undefined") window.CarvajalSSE = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
