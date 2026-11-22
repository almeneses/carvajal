// Identidad anónima del visitante: hash persistente en localStorage.
// Se genera una sola vez por navegador y se refleja en una cookie de primera
// parte para que el servidor pueda cargar los hilos en navegaciones completas.
(() => {
  "use strict";

  const CLAVE = "carvajal:visitante";
  const COOKIE = "carvajal_visitante";
  const MAX_AGE = 60 * 60 * 24 * 180;
  const VALIDO = /^[0-9a-f]{64}$/;

  function leerCookie(nombre) {
    for (const parte of document.cookie.split(";")) {
      const [clave, ...resto] = parte.trim().split("=");
      if (clave === nombre) {
        try {
          return decodeURIComponent(resto.join("="));
        } catch {
          return "";
        }
      }
    }
    return "";
  }

  function aleatorio() {
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      const bytes = new Uint8Array(32);
      window.crypto.getRandomValues(bytes);
      return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    }
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID().replaceAll("-", "");
    }
    let texto = "";
    while (texto.length < 64) {
      texto += Math.floor(Math.random() * 16).toString(16);
    }
    return texto.slice(0, 64);
  }

  function guardar(valor) {
    try {
      localStorage.setItem(CLAVE, valor);
    } catch {
      // El modo privado o una política del navegador pueden bloquearlo.
    }
  }

  function leer() {
    try {
      const guardado = String(localStorage.getItem(CLAVE) || "").toLowerCase();
      if (VALIDO.test(guardado)) return guardado;
    } catch {
      // Sin localStorage se intenta reutilizar la cookie de la visita anterior.
    }
    const cookie = leerCookie(COOKIE).toLowerCase();
    return VALIDO.test(cookie) ? cookie : "";
  }

  let visitante = leer();
  if (!visitante) {
    visitante = aleatorio();
    guardar(visitante);
  } else {
    guardar(visitante);
  }

  const seguro = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${COOKIE}=${visitante}; Path=/; Max-Age=${MAX_AGE}; SameSite=Lax${seguro}`;
})();
