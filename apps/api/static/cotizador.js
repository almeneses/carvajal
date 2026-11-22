/* Cotizador: recibe el turno por SSE y lo pinta dentro del hilo.
   Si el navegador no soporta streaming, usa el POST normal (mismo contrato). */
(function () {
  "use strict";
  const form = document.querySelector("[data-coti-form]");
  if (!form) return;
  const hilo = document.getElementById("hilo");
  const estado = document.getElementById("coti-estado");
  const boton = form.querySelector("[data-coti-enviar]");
  const campo = form.querySelector("textarea");
  const hiloInput = form.querySelector("[data-coti-thread]");
  const SEPARADOR = /\r?\n\r?\n/;
  const AVISO = "No se pudo completar la cotización.";
  const soportaStreaming =
    typeof window.fetch === "function" &&
    typeof window.ReadableStream === "function" &&
    typeof window.TextDecoder === "function";
  let enCurso = false;

  function lista() {
    const actual = hilo.querySelector("ol.hilo");
    if (actual) return actual;
    hilo.innerHTML = "";
    const nueva = document.createElement("ol");
    nueva.className = "hilo";
    hilo.appendChild(nueva);
    return nueva;
  }

  function alFinal() {
    const ultimo = hilo.lastElementChild;
    if (ultimo && typeof ultimo.scrollIntoView === "function") {
      ultimo.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  }

  function turno(rol, texto) {
    const item = document.createElement("li");
    item.className = `turno turno-${rol}`;
    const avatar = document.createElement("span");
    avatar.className = "turno-avatar";
    avatar.setAttribute("aria-hidden", "true");
    const etiqueta = document.createElement("p");
    etiqueta.className = "turno-rol";
    etiqueta.textContent = rol === "user" ? "Tú" : "Cotizador";
    const burbuja = document.createElement("div");
    burbuja.className = "burbuja";
    const cuerpo = document.createElement("div");
    cuerpo.className = "texto texto-formateado";
    cuerpo.textContent = texto;
    burbuja.appendChild(cuerpo);
    item.append(avatar, etiqueta, burbuja);
    return { item, cuerpo };
  }

  function bloques(item) {
    let contenedor = item.querySelector(".coti-stream");
    if (!contenedor) {
      contenedor = document.createElement("div");
      contenedor.className = "coti-stream";
      item.appendChild(contenedor);
    }
    return contenedor;
  }

  function pintar(datos) {
    const usuario = turno("user", datos.texto);
    const asistente = turno("assistant", "");
    lista().append(usuario.item, asistente.item);
    alFinal();
    return { usuario: usuario.item, asistente };
  }

  async function normal(texto, provisional) {
    const respuesta = await fetch("/cotizador", {
      method: "POST",
      body: datos(texto),
      headers: { "HX-Request": "true" },
      credentials: "same-origin",
    });
    const html = await respuesta.text();
    provisional.usuario.remove();
    provisional.asistente.item.remove();
    lista().insertAdjacentHTML("beforeend", html);
    return respuesta.ok;
  }

  function datos(texto) {
    const formulario = new FormData(form);
    formulario.set("question", texto);
    return formulario;
  }

  async function enviar(texto) {
    if (enCurso) return;
    enCurso = true;
    boton.disabled = true;
    estado.textContent = "Cotizando…";
    const provisional = pintar({ texto });
    try {
      if (!soportaStreaming) {
        const ok = await normal(texto, provisional);
        terminar(ok);
        return;
      }
      const respuesta = await fetch("/cotizador/stream", {
        method: "POST",
        body: datos(texto),
        headers: { Accept: "text/event-stream" },
        credentials: "same-origin",
      });
      const tipo = respuesta.headers.get("content-type") || "";
      if (!respuesta.body || !tipo.includes("text/event-stream")) {
        const ok = await normal(texto, provisional);
        terminar(ok);
        return;
      }
      if (!respuesta.ok) {
        await leer(respuesta, (nombre, evento) => {
          if (nombre === "error") {
            estado.textContent = String(evento.message || AVISO);
          }
        });
        provisional.usuario.remove();
        provisional.asistente.item.remove();
        terminar(false);
        return;
      }
      let mostrado = false;
      await leer(respuesta, (nombre, evento) => {
        if (nombre === "token") {
          mostrado = true;
          provisional.asistente.cuerpo.textContent += String(evento.text ?? "");
          alFinal();
        } else if (nombre === "products") {
          mostrado = true;
          bloques(provisional.asistente.item).innerHTML = String(evento.html ?? "");
          alFinal();
        } else if (nombre === "done") {
          const hiloId = evento.thread_id || evento.conversation_id;
          if (hiloId && hiloInput) hiloInput.value = String(hiloId);
          terminar(true);
        } else if (nombre === "error") {
          if (mostrado) {
            marcarIncompleta(provisional.asistente.item);
          } else {
            provisional.usuario.remove();
            provisional.asistente.item.remove();
          }
          estado.textContent = String(evento.message || AVISO);
          terminar(false);
        }
      });
    } catch (error) {
      marcarIncompleta(provisional.asistente.item);
      estado.textContent = "No se pudo cotizar. Revisa la conexión e inténtalo de nuevo.";
    } finally {
      enCurso = false;
      boton.disabled = false;
    }
  }

  function terminar(ok) {
    if (ok) {
      campo.value = "";
      estado.textContent = "";
    } else if (!estado.textContent || estado.textContent === "Cotizando…") {
      estado.textContent = AVISO;
    }
    alFinal();
    campo.focus();
  }

  function marcarIncompleta(item) {
    if (item.querySelector(".estado-incompleto")) return;
    const aviso = document.createElement("span");
    aviso.className = "estado-incompleto";
    aviso.setAttribute("role", "status");
    aviso.textContent = "respuesta incompleta";
    item.querySelector(".turno-rol")?.appendChild(aviso);
  }

  async function leer(respuesta, alEvento) {
    const lector = respuesta.body.getReader();
    const decodificador = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      buffer += decodificador.decode(value, { stream: true });
      buffer = vaciar(buffer, alEvento);
    }
    buffer += decodificador.decode();
    vaciar(buffer, alEvento);
  }

  function vaciar(buffer, alEvento) {
    for (;;) {
      const corte = SEPARADOR.exec(buffer);
      if (!corte) return buffer;
      const bloque = buffer.slice(0, corte.index);
      buffer = buffer.slice(corte.index + corte[0].length);
      const evento = interpretar(bloque);
      if (evento) alEvento(evento.nombre, evento.datos);
    }
  }

  function interpretar(bloque) {
    if (!bloque.trim()) return null;
    let nombre = "message";
    const datosLinea = [];
    for (const linea of bloque.split(/\r?\n/)) {
      if (linea.startsWith("event:")) nombre = linea.slice(6).trim();
      else if (linea.startsWith("data:")) datosLinea.push(linea.slice(5).trimStart());
    }
    if (datosLinea.length === 0) return null;
    try {
      return { nombre, datos: JSON.parse(datosLinea.join("\n")) };
    } catch {
      return null;
    }
  }

  form.addEventListener("submit", (evento) => {
    evento.preventDefault();
    const texto = campo.value.trim();
    if (texto) enviar(texto);
  });

  campo.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter" && !evento.shiftKey && !evento.isComposing) {
      evento.preventDefault();
      form.requestSubmit();
    }
  });

  document.addEventListener("click", (evento) => {
    const sugerencia = evento.target.closest("[data-sugerencia]");
    if (!sugerencia || enCurso) return;
    const texto = sugerencia.textContent.trim();
    campo.value = texto;
    enviar(texto);
  });
})();
