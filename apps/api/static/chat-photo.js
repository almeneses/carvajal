// Foto de un alimento en el compositor (spec 051).
//
// El botón solo existe si el servidor tiene el clasificador disponible. Al elegir una foto
// se muestra su nombre y el aviso del servicio externo; el texto pasa a ser opcional.
// chat-stream.js la envía con `window.CarvajalFoto.tomar()` y la limpia al enviar. Sin vista
// previa: la foto nunca se pinta en la página (la CSP no admite blob: en img-src).
(() => {
  "use strict";

  const raiz = document.querySelector("[data-foto]");
  if (!raiz) return;
  const form = raiz.closest("form");
  const input = raiz.querySelector("[data-foto-input]");
  const ficha = raiz.querySelector("[data-foto-ficha]");
  const nombre = raiz.querySelector("[data-foto-nombre]");
  const quitar = raiz.querySelector("[data-foto-quitar]");
  const aviso = form ? form.querySelector("[data-foto-aviso]") : null;
  const texto = form ? form.querySelector('[name="question"]') : null;
  const estado = document.getElementById("chat-stream-estado");
  if (!form || !input || !ficha || !nombre || !texto) return;

  const MAX_BYTES = 10 * 1024 * 1024;
  const TIPOS = new Set(["image/jpeg", "image/png"]);
  const PLACEHOLDER = texto.getAttribute("placeholder") || "";
  let archivo = null;

  function avisar(mensaje) {
    if (!estado) return;
    estado.textContent = mensaje;
    estado.classList.add("error");
  }

  function pintar() {
    const hay = Boolean(archivo);
    ficha.hidden = !hay;
    nombre.textContent = hay ? archivo.name || "foto" : "";
    if (aviso) aviso.hidden = !hay;
    // Con foto, el texto es opcional: sin `required` el navegador deja enviar.
    texto.required = !hay;
    texto.setAttribute("placeholder", hay ? "Añade un detalle (opcional)" : PLACEHOLDER);
    raiz.dataset.fotoAdjunta = hay ? "1" : "0";
  }

  function elegir(nuevo) {
    if (!nuevo) return;
    if (!TIPOS.has(nuevo.type)) {
      avisar("La foto debe ser JPEG o PNG.");
      input.value = "";
      return;
    }
    if (nuevo.size > MAX_BYTES) {
      avisar("La foto supera los 10 MB.");
      input.value = "";
      return;
    }
    archivo = nuevo;
    pintar();
  }

  function limpiar() {
    archivo = null;
    input.value = "";
    pintar();
  }

  input.addEventListener("change", () => elegir(input.files && input.files[0]));
  if (quitar) {
    quitar.addEventListener("click", () => {
      limpiar();
      texto.focus();
    });
  }

  raiz.hidden = false;
  pintar();
  window.CarvajalFoto = {
    tomar: () => archivo,
    limpiar,
    // Devuelve la foto al compositor cuando el envío falla antes de llegar al servidor.
    restaurar(foto) {
      archivo = foto || null;
      pintar();
    },
  };
})();
