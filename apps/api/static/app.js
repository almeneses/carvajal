// Sustituye a HTMX en el formulario de la portada: envía la pregunta y pinta el parcial.
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-preguntar]");
  if (!form) return;
  event.preventDefault();
  const zona = document.getElementById("respuesta");
  const aviso =
    '<p class="aviso">No se pudo obtener la respuesta. Inténtalo de nuevo en unos segundos.</p>';
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), 30000);
  zona.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      signal: control.signal,
    });
    const tipo = response.headers.get("content-type") ?? "";
    const propio = response.headers.get("x-carvajal-error") === "1";
    // Un error propio (403 de CSRF, 429 de cuota, 503 del proveedor o 500)
    // también se pinta; el HTML ajeno de un proxy se sustituye por el aviso.
    if (tipo.includes("text/html") && (response.ok || propio)) {
      zona.innerHTML = await response.text();
      return;
    }
    zona.innerHTML = aviso;
  } catch {
    zona.innerHTML = aviso;
  } finally {
    clearTimeout(temporizador);
    zona.removeAttribute("aria-busy");
    zona.scrollIntoView({ block: "nearest" });
  }
});
