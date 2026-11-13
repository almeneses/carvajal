// Stream SSE del chat (W3.4).
// Degrada al POST normal si falta soporte de streaming o la conexión falla
// antes del primer token; el servidor solo persiste el turno al recibir done.
(() => {
  "use strict";

  const form = document.querySelector("form[data-chat-stream]");
  if (!form) return;

  const soportaStreaming =
    typeof window.fetch === "function" &&
    typeof window.TextDecoder === "function" &&
    typeof window.ReadableStream === "function";
  if (!soportaStreaming) return;

  const input = form.querySelector('input[name="question"]');
  const boton = form.querySelector('button[type="submit"]');
  const hilo = document.getElementById("hilo");
  const estado = document.getElementById("chat-stream-estado");
  const plantillaTurno = document.getElementById("plantilla-turno");
  const plantillaFuente = document.getElementById("plantilla-fuente");
  if (!input || !hilo || !estado || !plantillaTurno || !plantillaFuente) return;

  const AVISO = "No se pudo completar la respuesta. Vuelve a intentarlo.";
  const INCOMPLETA = "La respuesta se interrumpió antes de completarse.";
  const SEPARADOR = /\r?\n\r?\n/;
  let enCurso = false;

  form.addEventListener("submit", (event) => {
    if (!soportaStreaming) return; // el navegador envía el POST de siempre
    event.preventDefault();
    if (enCurso) return;

    const pregunta = input.value.trim();
    if (!pregunta) {
      avisar("Escribe una pregunta antes de enviar.", true);
      input.focus();
      return;
    }
    if (pregunta.length > 500) {
      avisar("La pregunta no puede superar los 500 caracteres.", true);
      input.focus();
      return;
    }
    enCurso = true;
    void responder(pregunta);
  });

  async function responder(pregunta) {
    quitarVacio();
    const turnoUsuario = crearTurno("user", pregunta);
    const turnoAsistente = crearTurno("assistant", "");
    hilo.append(turnoUsuario, turnoAsistente);
    const texto = turnoAsistente.querySelector(".texto");
    input.value = "";
    form.setAttribute("aria-busy", "true");
    if (boton) boton.disabled = true;
    avisar("Generando respuesta…");

    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 120000);
    let cerrado = false;
    let recibioContenido = false;
    try {
      const response = await fetch("/chat/stream", {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({ question: pregunta }),
        signal: control.signal,
      });
      const tipo = response.headers.get("content-type") ?? "";
      if (!response.ok || !response.body || !tipo.includes("text/event-stream")) {
        throw new Error(`respuesta ${response.status}`);
      }

      await leerEventos(response, (evento) => {
        switch (evento.nombre) {
          case "open":
            avisar("Recuperando fuentes…");
            break;
          case "meta":
            pintarFuentes(turnoAsistente, evento.datos.sources);
            avisar("Redactando…");
            break;
          case "token": {
            const fragmento = String(evento.datos.text ?? "");
            texto.textContent += fragmento;
            if (fragmento) recibioContenido = true;
            break;
          }
          case "fallback":
            texto.textContent = String(evento.datos.text ?? "");
            pintarFuentes(turnoAsistente, evento.datos.sources);
            recibioContenido = true;
            avisar("Respuesta completa sin streaming.");
            break;
          case "done":
            cerrado = true;
            avisar("");
            break;
          case "error":
            cerrado = true;
            if (!texto.textContent) {
              texto.textContent = String(evento.datos.message ?? INCOMPLETA);
            }
            avisar(String(evento.datos.message ?? INCOMPLETA), true);
            break;
          default:
            break;
        }
      });

      if (!cerrado) {
        avisar(INCOMPLETA, true);
        if (!texto.textContent) texto.textContent = INCOMPLETA;
      }
    } catch (error) {
      if (!recibioContenido && !cerrado) {
        // Falló antes del primer token y el servidor no persistió el turno:
        // restaura la pregunta y reintenta por el POST normal del formulario.
        input.value = pregunta;
        turnoUsuario.remove();
        turnoAsistente.remove();
        avisar("La conexión falló; enviando la pregunta sin streaming…");
        form.submit(); // HTMLFormElement.submit() no dispara el listener de submit
        return;
      }
      avisar(error && error.name === "AbortError" ? INCOMPLETA : AVISO, true);
      if (!texto.textContent) turnoAsistente.remove();
    } finally {
      clearTimeout(temporizador);
      form.removeAttribute("aria-busy");
      if (boton) boton.disabled = false;
      input.focus();
      turnoAsistente.scrollIntoView({ block: "nearest" });
      enCurso = false;
    }
  }

  function crearTurno(rol, contenido) {
    const turno = plantillaTurno.content.firstElementChild.cloneNode(true);
    turno.classList.add(`turno-${rol}`);
    turno.querySelector(".turno-rol").textContent = rol === "user" ? "Tú" : "Asistente";
    turno.querySelector(".texto").textContent = contenido;
    return turno;
  }

  function quitarVacio() {
    const vacio = hilo.querySelector(".vacio");
    if (vacio) vacio.remove();
  }

  function avisar(mensaje, esError = false) {
    estado.textContent = mensaje;
    estado.classList.toggle("aviso", esError);
    estado.classList.toggle("ayuda", !esError);
  }

  function pintarFuentes(turno, fuentes) {
    if (!Array.isArray(fuentes) || fuentes.length === 0) return;
    const seccion = turno.querySelector(".citas");
    const lista = seccion.querySelector(".fuentes");
    lista.textContent = "";
    for (const fuente of fuentes) {
      const item = plantillaFuente.content.firstElementChild.cloneNode(true);
      const campos = {
        slug: fuente && fuente.slug,
        section: fuente && fuente.section,
        layer: fuente && fuente.layer,
        excerpt: fuente && fuente.excerpt,
        chunk_id: fuente && fuente.chunk_id,
      };
      for (const [campo, valor] of Object.entries(campos)) {
        const nodo = item.querySelector(`[data-campo="${campo}"]`);
        if (nodo) nodo.textContent = valor == null ? "" : String(valor);
      }
      const puntuacion = Number(fuente && fuente.score);
      const nodoScore = item.querySelector('[data-campo="score"]');
      if (nodoScore) nodoScore.textContent = Number.isFinite(puntuacion)
        ? puntuacion.toFixed(3)
        : "";
      lista.append(item);
    }
    seccion.hidden = false;
  }

  async function leerEventos(response, alEvento) {
    const lector = response.body.getReader();
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
})();
