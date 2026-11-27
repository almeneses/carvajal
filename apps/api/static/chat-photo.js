// Foto de un alimento en el compositor (spec 051; vista previa y comodidades, spec 053).
//
// El botón solo existe si el servidor tiene el clasificador disponible. Al elegir una foto
// (botón, arrastrar y soltar en cualquier parte de la conversación, o pegar) se muestra su
// miniatura, su nombre y el aviso del servicio externo; el texto pasa a ser opcional. La miniatura es un `blob:` local: la CSP
// del chat lo admite en img-src y la foto no sale de la página para mostrarse.
// chat-stream.js la envía con `window.CarvajalFoto.tomar()`, pinta la burbuja con
// `vista()` y la limpia al enviar.
(() => {
  "use strict";

  const raiz = document.querySelector("[data-foto]");
  if (!raiz) return;
  const form = raiz.closest("form");
  const input = raiz.querySelector("[data-foto-input]");
  const ficha = raiz.querySelector("[data-foto-ficha]");
  const nombre = raiz.querySelector("[data-foto-nombre]");
  const peso = raiz.querySelector("[data-foto-peso]");
  const miniatura = raiz.querySelector("[data-foto-miniatura]");
  const quitar = raiz.querySelector("[data-foto-quitar]");
  const aviso = form ? form.querySelector("[data-foto-aviso]") : null;
  // La zona para soltar cubre la conversación entera (historial y compositor), no solo el
  // compositor (ensayo del 2026-11-26).
  const soltar = document.querySelector("[data-foto-soltar]");
  const texto = form ? form.querySelector('[name="question"]') : null;
  const estado = document.getElementById("chat-stream-estado");
  if (!form || !input || !ficha || !nombre || !texto) return;

  const MAX_BYTES = 10 * 1024 * 1024;
  const TIPOS = new Set(["image/jpeg", "image/png"]);
  const PLACEHOLDER = texto.getAttribute("placeholder") || "";
  let archivo = null;
  let urlMiniatura = "";

  function avisar(mensaje) {
    if (!estado) return;
    estado.textContent = mensaje;
    estado.classList.add("error");
  }

  // Aviso cortés (sin error): al soltar una imagen, quien no ve la ficha oye que se adjuntó.
  function anunciar(mensaje) {
    if (!estado) return;
    estado.classList.remove("error");
    estado.textContent = mensaje;
  }

  function crearUrl(foto) {
    try {
      return foto && URL.createObjectURL ? URL.createObjectURL(foto) : "";
    } catch {
      return "";
    }
  }

  function soltarUrl() {
    if (urlMiniatura && URL.revokeObjectURL) URL.revokeObjectURL(urlMiniatura);
    urlMiniatura = "";
  }

  function megas(bytes) {
    const valor = bytes / (1024 * 1024);
    return valor >= 0.1 ? `${valor.toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function pintar() {
    const hay = Boolean(archivo);
    ficha.hidden = !hay;
    nombre.textContent = hay ? archivo.name || "foto" : "";
    if (peso) peso.textContent = hay && archivo.size ? megas(archivo.size) : "";
    soltarUrl();
    if (miniatura) {
      urlMiniatura = hay ? crearUrl(archivo) : "";
      if (urlMiniatura) miniatura.src = urlMiniatura;
      else miniatura.removeAttribute("src");
      miniatura.hidden = !urlMiniatura;
    }
    if (aviso) aviso.hidden = !hay;
    // Con foto, el texto es opcional: sin `required` el navegador deja enviar.
    texto.required = !hay;
    texto.setAttribute("placeholder", hay ? "Añade un detalle (opcional)" : PLACEHOLDER);
    raiz.dataset.fotoAdjunta = hay ? "1" : "0";
    // En el teléfono, con la bienvenida, el compositor queda bajo el pliegue: sin esto la
    // ficha aparece fuera de la vista y parece que la foto no se adjuntó. Se desplaza el
    // compositor entero, no la ficha: con la ficha a la vista, el campo y «Enviar» quedaban
    // tapados bajo la cabecera (ensayo del 2026-11-26, hilo nuevo a 320×640).
    if (hay && typeof form.scrollIntoView === "function") form.scrollIntoView({ block: "nearest" });
  }

  // Las mismas validaciones para el botón, arrastrar y pegar (FR-021).
  function elegir(nuevo) {
    if (!nuevo) return false;
    if (!TIPOS.has(nuevo.type)) {
      avisar("La foto debe ser JPEG o PNG.");
      input.value = "";
      return false;
    }
    if (nuevo.size > MAX_BYTES) {
      avisar("La foto supera los 10 MB.");
      input.value = "";
      return false;
    }
    archivo = nuevo;
    pintar();
    return true;
  }

  function limpiar() {
    archivo = null;
    input.value = "";
    pintar();
  }

  function imagenDe(lista) {
    for (const item of Array.from(lista || [])) {
      const file = item && typeof item.getAsFile === "function" ? item.getAsFile() : item;
      if (file && typeof file.type === "string" && file.type.startsWith("image/")) return file;
    }
    return null;
  }

  function tieneArchivos(event) {
    const tipos = event.dataTransfer && event.dataTransfer.types;
    return Boolean(tipos && Array.from(tipos).includes("Files"));
  }

  // ¿Lo que se arrastra trae una imagen? Durante el arrastre el navegador solo deja ver el tipo
  // de cada elemento (`items`), no el archivo. Sin `items` (navegadores viejos) no se puede
  // saber y se ofrece la zona: al soltar se valida igual que con el botón.
  function arrastraImagen(event) {
    if (!tieneArchivos(event)) return false;
    const items = event.dataTransfer && event.dataTransfer.items;
    if (!items || typeof items.length !== "number" || items.length === 0) return true;
    return Array.from(items).some(
      (item) => item && item.kind === "file" && typeof item.type === "string" && item.type.startsWith("image/"),
    );
  }

  let apagarZona = null;

  function marcarArrastre(activo) {
    if (activo) form.dataset.fotoArrastrando = "1";
    else delete form.dataset.fotoArrastrando;
    if (soltar) soltar.hidden = !activo;
    if (apagarZona) clearTimeout(apagarZona);
    apagarZona = null;
    // Si el arrastre sale de la ventana sin «dragleave» (pasa al soltar fuera o con Escape),
    // la zona se apaga sola cuando dejan de llegar «dragover».
    if (activo && typeof setTimeout === "function") {
      apagarZona = setTimeout(() => {
        profundidad = 0;
        marcarArrastre(false);
      }, 1500);
    }
  }

  input.addEventListener("change", () => elegir(input.files && input.files[0]));
  if (quitar) {
    quitar.addEventListener("click", () => {
      limpiar();
      texto.focus();
    });
  }

  // Pegar una imagen en el compositor la adjunta; pegar texto sigue pegando texto.
  texto.addEventListener("paste", (event) => {
    const datos = event.clipboardData;
    const imagen = datos && (imagenDe(datos.files) || imagenDe(datos.items));
    if (!imagen) return;
    event.preventDefault();
    elegir(imagen);
  });

  // Arrastrar y soltar en cualquier parte de la página (computador): la zona «Suelta la imagen
  // para adjuntarla» cubre la conversación. Texto, enlaces o archivos que no son imágenes se
  // ignoran: no se muestra la zona y, si son archivos, el navegador tampoco los abre (se
  // cancela su efecto con «none»), así soltar un PDF no saca a la persona del chat.
  let profundidad = 0;
  document.addEventListener("dragenter", (event) => {
    if (!arrastraImagen(event)) return;
    profundidad += 1;
    marcarArrastre(true);
  });
  document.addEventListener("dragover", (event) => {
    if (!tieneArchivos(event)) return;
    event.preventDefault();
    const imagen = arrastraImagen(event);
    if (event.dataTransfer) event.dataTransfer.dropEffect = imagen ? "copy" : "none";
    if (imagen) marcarArrastre(true);
  });
  document.addEventListener("dragleave", (event) => {
    if (!arrastraImagen(event)) return;
    profundidad = Math.max(0, profundidad - 1);
    if (!profundidad) marcarArrastre(false);
  });
  document.addEventListener("drop", (event) => {
    if (!tieneArchivos(event)) return;
    event.preventDefault();
    profundidad = 0;
    marcarArrastre(false);
    const imagen = imagenDe(event.dataTransfer && event.dataTransfer.files);
    if (!imagen) return;
    if (elegir(imagen)) {
      anunciar(`Foto adjunta: ${imagen.name || "foto"}. Escribe un detalle o envíala.`);
      texto.focus();
    }
  });

  // Sugerencia de la bienvenida (FR-022): abre el selector de la foto.
  for (const sugerencia of document.querySelectorAll?.("[data-foto-sugerencia]") ?? []) {
    sugerencia.hidden = false;
    sugerencia.addEventListener("click", () => input.click());
  }

  raiz.hidden = false;
  pintar();
  window.CarvajalFoto = {
    tomar: () => archivo,
    limpiar,
    // Una URL propia para la burbuja enviada: sobrevive a limpiar el compositor.
    vista: (foto) => crearUrl(foto),
    // Devuelve la foto al compositor cuando el envío falla antes de llegar al servidor.
    restaurar(foto) {
      archivo = foto || null;
      pintar();
    },
  };
})();
