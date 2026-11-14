// Dictado del chat: graba con MediaRecorder y pide la transcripción a la API.
// El audio no se reproduce ni se guarda en el navegador; solo se envía una vez.
(() => {
  "use strict";

  const form = document.querySelector("form[data-chat-stream]");
  const boton = document.getElementById("boton-voz");
  if (!form || !boton) return;

  const input = form.querySelector('input[name="question"]');
  const csrf = form.querySelector('input[name="csrf_token"]');
  const estado = document.getElementById("chat-stream-estado");
  const textoBoton = boton.querySelector("[data-voz-texto]");
  if (!input || !estado || !textoBoton) return;

  const soportaVoz =
    typeof navigator.mediaDevices === "object" &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
    typeof window.MediaRecorder === "function";
  if (!soportaVoz) return;

  const MAX_SEGUNDOS = 60;
  const TIPOS = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  let grabadora = null;
  let flujo = null;
  let trozos = [];
  let temporizador = 0;
  let inicio = 0;
  let descartar = false;
  let estadoVoz = "listo";

  boton.hidden = false;

  boton.addEventListener("click", () => {
    if (estadoVoz === "transcribiendo") return;
    if (estadoVoz === "grabando") {
      detener(false);
      return;
    }
    void empezar();
  });

  // Evita enviar una pregunta vacía mientras el micrófono aún está trabajando.
  form.addEventListener(
    "submit",
    (evento) => {
      if (estadoVoz === "listo") return;
      evento.preventDefault();
      evento.stopImmediatePropagation();
      avisar("Termina la grabación o espera la transcripción antes de enviar.", true);
    },
    true,
  );

  document.addEventListener("keydown", (evento) => {
    if (evento.key !== "Escape" || estadoVoz !== "grabando") return;
    detener(true);
  });

  window.addEventListener("pagehide", () => liberar());

  async function empezar() {
    if (form.getAttribute("aria-busy") === "true") {
      avisar("Espera a que termine la respuesta anterior.", true);
      return;
    }
    try {
      flujo = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      const nombre = error && error.name;
      const mensaje =
        nombre === "NotAllowedError" || nombre === "SecurityError"
          ? "El navegador bloqueó el micrófono. Revisa el permiso del sitio."
          : nombre === "NotFoundError"
            ? "No se encontró un micrófono conectado."
            : "No se pudo abrir el micrófono. Revisa que no esté en uso.";
      avisar(mensaje, true);
      return;
    }

    const tipo = tipoPreferido();
    try {
      grabadora = tipo ? new MediaRecorder(flujo, { mimeType: tipo }) : new MediaRecorder(flujo);
    } catch {
      liberar();
      avisar("Tu navegador no puede grabar audio con este formato.", true);
      return;
    }

    trozos = [];
    descartar = false;
    grabadora.ondataavailable = (evento) => {
      if (evento.data && evento.data.size) trozos.push(evento.data);
    };
    grabadora.onstop = () => {
      const tipoFinal = (grabadora && grabadora.mimeType) || tipo || "audio/webm";
      const blob = new Blob(trozos, { type: tipoFinal });
      const cancelado = descartar;
      liberar();
      grabadora = null;
      marcar("listo");
      if (cancelado) {
        avisar("Grabación cancelada.");
        return;
      }
      if (!blob.size) {
        avisar("No se grabó audio. Inténtalo de nuevo.", true);
        return;
      }
      void transcribir(blob);
    };

    grabadora.start();
    inicio = performance.now();
    marcar("grabando");
    avisar("Grabando… pulsa «Detener» al terminar.");
    temporizador = window.setInterval(actualizarTiempo, 1000);
  }

  function detener(cancelar) {
    if (!grabadora || grabadora.state === "inactive") return;
    descartar = cancelar;
    try {
      grabadora.stop();
    } catch {
      liberar();
      grabadora = null;
      marcar("listo");
      avisar("La grabación se interrumpió. Inténtalo de nuevo.", true);
    }
  }

  function actualizarTiempo() {
    if (estadoVoz !== "grabando") return;
    const segundos = Math.min(MAX_SEGUNDOS, Math.floor((performance.now() - inicio) / 1000));
    const minutos = String(Math.floor(segundos / 60)).padStart(2, "0");
    const resto = String(segundos % 60).padStart(2, "0");
    textoBoton.textContent = `Detener · ${minutos}:${resto}`;
    if (segundos >= MAX_SEGUNDOS) detener(false);
  }

  async function transcribir(blob) {
    marcar("transcribiendo");
    avisar("Transcribiendo la pregunta…");
    const datos = new FormData();
    datos.append("audio", blob, nombreArchivo(blob.type));
    datos.append("csrf_token", csrf ? csrf.value : "");
    try {
      const respuesta = await fetch("/api/chat/transcribe", {
        method: "POST",
        body: datos,
      });
      const contenido = await respuesta.json().catch(() => ({}));
      if (!respuesta.ok) {
        avisar(String(contenido.detail || "No se pudo transcribir la grabación."), true);
        return;
      }
      const texto = String(contenido.text || "").trim();
      if (!texto) {
        avisar("No se reconoció voz en la grabación. Inténtalo de nuevo.", true);
        return;
      }
      input.value = texto.slice(0, 500);
      input.focus();
      avisar(
        contenido.truncated
          ? "La transcripción se recortó a 500 caracteres. Revísala antes de enviar."
          : "Revisa la transcripción y pulsa Enviar.",
      );
    } catch {
      avisar("No se pudo conectar con el servicio de voz. Revisa la conexión.", true);
    } finally {
      marcar("listo");
    }
  }

  function marcar(nuevo) {
    estadoVoz = nuevo;
    boton.setAttribute("aria-pressed", nuevo === "grabando" ? "true" : "false");
    boton.setAttribute(
      "aria-label",
      nuevo === "grabando" ? "Detener la grabación" : "Dictar la pregunta por voz",
    );
    boton.disabled = nuevo === "transcribiendo";
    textoBoton.textContent =
      nuevo === "grabando" ? "Detener" : nuevo === "transcribiendo" ? "Transcribiendo…" : "Dictar";
  }

  function liberar() {
    if (temporizador) {
      window.clearInterval(temporizador);
      temporizador = 0;
    }
    if (flujo) {
      for (const pista of flujo.getTracks()) pista.stop();
      flujo = null;
    }
  }

  function tipoPreferido() {
    for (const tipo of TIPOS) {
      if (MediaRecorder.isTypeSupported(tipo)) return tipo;
    }
    return "";
  }

  function nombreArchivo(tipo) {
    if (tipo.includes("mp4")) return "pregunta.m4a";
    if (tipo.includes("ogg")) return "pregunta.ogg";
    return "pregunta.webm";
  }

  function avisar(mensaje, error = false) {
    const nodo = estado.querySelector("[data-estado-texto]") || estado;
    nodo.textContent = mensaje;
    estado.classList.toggle("aviso", error);
    estado.classList.toggle("ayuda", !error);
  }
})();
