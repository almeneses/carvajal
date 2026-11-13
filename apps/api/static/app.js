// Sustituye a HTMX en este único formulario: envía la pregunta y pinta el parcial.
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
    if (!response.ok || !tipo.includes("text/html")) {
      // Nunca se pinta el cuerpo de un error: podría ser HTML de un proxy.
      zona.innerHTML = aviso;
      return;
    }
    zona.innerHTML = await response.text();
  } catch {
    zona.innerHTML = aviso;
  } finally {
    clearTimeout(temporizador);
    zona.removeAttribute("aria-busy");
    zona.scrollIntoView({ block: "nearest" });
  }
});
