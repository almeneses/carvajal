// Pestaña FAQ de la SPA: pide lotes de hasta diez preguntas por SSE y acumula
// las respuestas en memoria. No usa innerHTML: todo el texto del modelo se
// inserta con textContent.
(() => {
  "use strict";

  const API = String(window.CARVAJAL_API || "http://localhost:8000").replace(/\/$/, "");
  const MAX_PREVIOUS = 100;
  const TIMEOUT_MS = 300000;

  const boton = document.getElementById("faq-generar");
  const reiniciar = document.getElementById("faq-reiniciar");
  const descargar = document.getElementById("faq-descargar");
  const estado = document.getElementById("faq-estado");
  const aviso = document.getElementById("faq-aviso");
  const lista = document.getElementById("faq-lista");
  const vacio = document.getElementById("faq-vacio");
  const faltantes = document.getElementById("faq-faltantes");
  const preview = document.getElementById("faq-preview");
  if (!boton || !lista) return;

  let csrf = "";
  let preguntas = [];
  let temasFaltantes = [];
  let enCurso = false;

  document.addEventListener("DOMContentLoaded", iniciar);
  if (document.readyState !== "loading") iniciar();

  function iniciar() {
    if (boton.dataset.listo === "1") return;
    boton.dataset.listo = "1";
    boton.addEventListener("click", () => void generar());
    if (reiniciar) reiniciar.addEventListener("click", limpiar);
    if (descargar) descargar.addEventListener("click", descargarMarkdown);
  }

  async function cargarCsrf() {
    const response = await fetch(`${API}/api/csrf`, { credentials: "include" });
    if (!response.ok) throw new Error("No se pudo preparar el formulario.");
    const datos = await response.json();
    csrf = String(datos.token || "");
  }

  async function solicitar(cuerpo, signal, reintentar = false) {
    const response = await fetch(`${API}/api/faq`, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: cuerpo,
      signal,
    });
    if (response.status === 403 && reintentar) {
      // El token CSRF caduca a las 12 h; se renueva una vez y se reintenta.
      if (signal && signal.aborted) throw abortError();
      try {
        if (response.body) await response.body.cancel();
      } catch {
        // El cuerpo ya no se va a leer; cerrar el stream es best-effort.
      }
      await cargarCsrf();
      if (signal && signal.aborted) throw abortError();
      cuerpo.set("csrf_token", csrf);
      return solicitar(cuerpo, signal, false);
    }
    return response;
  }

  function abortError() {
    const error = new Error("La generación tardó demasiado.");
    error.name = "AbortError";
    return error;
  }

  async function generar() {
    if (enCurso) return;
    enCurso = true;
    boton.disabled = true;
    boton.setAttribute("aria-busy", "true");
    aviso.hidden = true;
    preview.hidden = false;
    preview.textContent = "";
    fijarEstado("Generando hasta 10 preguntas…");
    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), TIMEOUT_MS);
    let cerrado = false;
    try {
      if (!csrf) await cargarCsrf();
      const cuerpo = new URLSearchParams({
        csrf_token: csrf,
        exclude: JSON.stringify(
          preguntas.map((entrada) => entrada.question).slice(-MAX_PREVIOUS),
        ),
      });
      const response = await solicitar(cuerpo, control.signal, true);
      const tipo = response.headers.get("content-type") || "";
      if (!tipo.includes("text/event-stream")) {
        const datos = await response.json().catch(() => ({}));
        throw new Error(datos.detail || `La API respondió ${response.status}.`);
      }

      let texto = "";
      await leerEventos(response, (evento) => {
        switch (evento.nombre) {
          case "open":
            fijarEstado("Redactando la FAQ…");
            break;
          case "token":
            texto += String(evento.datos.text || "");
            preview.textContent = texto;
            break;
          case "done":
            cerrado = true;
            aplicarLote(evento.datos);
            break;
          case "error":
            cerrado = true;
            control.abort();
            throw new Error(String(evento.datos.message || "No se pudo generar la FAQ."));
          default:
            break;
        }
      });
      if (!cerrado) throw new Error("La generación se interrumpió antes de completarse.");
    } catch (error) {
      fijarEstado(mensajeError(error), true);
    } finally {
      clearTimeout(temporizador);
      preview.hidden = true;
      preview.textContent = "";
      boton.disabled = false;
      boton.removeAttribute("aria-busy");
      enCurso = false;
    }
  }

  function aplicarLote(datos) {
    const entradas = Array.isArray(datos.entries) ? datos.entries : [];
    const vistas = new Set(preguntas.map((entrada) => normalizar(entrada.question)));
    let nuevas = 0;
    for (const entrada of entradas) {
      const pregunta = String(entrada && entrada.question ? entrada.question : "").trim();
      const clave = normalizar(pregunta);
      if (!clave || vistas.has(clave)) continue;
      vistas.add(clave);
      preguntas.push({
        theme: String(entrada.theme || "General").trim() || "General",
        question: pregunta,
        answer: String(entrada.answer || "").trim(),
        source: String(entrada.source || "").trim(),
      });
      nuevas += 1;
    }

    const faltan = Array.isArray(datos.missing_topics) ? datos.missing_topics : [];
    for (const tema of faltan) {
      const texto = String(tema || "").trim();
      if (texto && !temasFaltantes.includes(texto)) temasFaltantes.push(texto);
    }

    const avisos = Array.isArray(datos.warnings) ? datos.warnings.map(String) : [];
    if (datos.truncated) {
      avisos.push("La FAQ se generó con el corpus truncado por longitud.");
    }
    if (datos.exhausted || nuevas === 0) {
      avisos.push(
        "No se encontraron preguntas nuevas en el corpus; prueba a empezar de nuevo.",
      );
    }
    mostrarAviso([...new Set(avisos)].join(" "));
    renderizar();

    if (nuevas > 0) {
      fijarEstado(`${nuevas} preguntas nuevas. Total: ${preguntas.length}.`);
    } else {
      fijarEstado("No hay más preguntas nuevas por ahora.", true);
    }
    actualizarBotones();
  }

  function renderizar() {
    lista.textContent = "";
    const grupos = new Map();
    for (const entrada of preguntas) {
      const tema = entrada.theme || "General";
      if (!grupos.has(tema)) grupos.set(tema, []);
      grupos.get(tema).push(entrada);
    }

    let numero = 0;
    for (const [tema, entradas] of grupos) {
      const grupo = document.createElement("article");
      grupo.className = "faq-grupo";
      const titulo = document.createElement("h2");
      titulo.textContent = tema;
      grupo.append(titulo);

      for (const entrada of entradas) {
        numero += 1;
        const item = document.createElement("div");
        item.className = "faq-entrada";
        const pregunta = document.createElement("h3");
        pregunta.className = "faq-pregunta";
        pregunta.textContent = `${numero}. ${entrada.question}`;
        const respuesta = document.createElement("p");
        respuesta.className = "faq-respuesta";
        respuesta.textContent = entrada.answer || "Sin respuesta en el corpus.";
        const fuente = document.createElement("p");
        fuente.className = "faq-fuente";
        fuente.textContent = entrada.source
          ? `Fuente: ${entrada.source}`
          : "Fuente: no indicada";
        item.append(pregunta, respuesta, fuente);
        grupo.append(item);
      }
      lista.append(grupo);
    }

    vacio.hidden = preguntas.length > 0;
    if (temasFaltantes.length === 0) {
      faltantes.hidden = true;
      faltantes.textContent = "";
    } else {
      faltantes.textContent = "";
      const titulo = document.createElement("h2");
      titulo.textContent = "Temas sin información en el corpus";
      const ul = document.createElement("ul");
      for (const tema of temasFaltantes) {
        const li = document.createElement("li");
        li.textContent = tema;
        ul.append(li);
      }
      faltantes.append(titulo, ul);
      faltantes.hidden = false;
    }
  }

  function actualizarBotones() {
    const hayContenido = preguntas.length > 0 || temasFaltantes.length > 0;
    boton.textContent = hayContenido ? "Generar otras 10" : "Generar 10 preguntas";
    if (reiniciar) reiniciar.hidden = !hayContenido;
    if (descargar) descargar.hidden = preguntas.length === 0;
  }

  function limpiar() {
    if (enCurso) return;
    preguntas = [];
    temasFaltantes = [];
    mostrarAviso("");
    fijarEstado("Listo para generar un lote nuevo.");
    renderizar();
    actualizarBotones();
  }

  function descargarMarkdown() {
    if (preguntas.length === 0) return;
    const lineas = ["# Preguntas frecuentes del corpus Carvajal", ""];
    const grupos = new Map();
    for (const entrada of preguntas) {
      const tema = entrada.theme || "General";
      if (!grupos.has(tema)) grupos.set(tema, []);
      grupos.get(tema).push(entrada);
    }
    for (const [tema, entradas] of grupos) {
      lineas.push(`## ${tema}`, "");
      for (const entrada of entradas) {
        lineas.push(
          `**P:** ${entrada.question}`,
          `**R:** ${entrada.answer || "Sin respuesta en el corpus."}`,
          `_Fuente:_ ${entrada.source || "no indicada"}`,
          "",
        );
      }
    }
    if (temasFaltantes.length > 0) {
      lineas.push("## Temas sin información en el corpus", "");
      for (const tema of temasFaltantes) lineas.push(`- ${tema}`);
      lineas.push("");
    }

    const blob = new Blob([lineas.join("\n")], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.download = "faq-carvajal.md";
    document.body.append(enlace);
    enlace.click();
    enlace.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function fijarEstado(texto, error = false) {
    estado.textContent = texto;
    estado.classList.toggle("error", error);
  }

  function mostrarAviso(texto) {
    aviso.textContent = texto;
    aviso.hidden = !texto;
  }

  function mensajeError(error) {
    if (error && error.name === "AbortError") {
      return "La generación tardó demasiado. Vuelve a intentarlo.";
    }
    if (error && error.message) return String(error.message);
    return "No se pudo generar la FAQ. Revisa la conexión e inténtalo de nuevo.";
  }

  function normalizar(texto) {
    return String(texto || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
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
    vaciarBloques(buffer, alEvento);
  }

  function vaciarBloques(buffer, alEvento) {
    for (;;) {
      const corte = /\r?\n\r?\n/.exec(buffer);
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
