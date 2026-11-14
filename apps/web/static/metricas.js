// Pestaña Métricas de la SPA: consulta /api/metricas y pinta el estado del
// índice, los conteos del corpus y el último resumen de evaluación. No usa
// innerHTML: todo valor llega como texto del servidor.
(() => {
  "use strict";

  const API = String(window.CARVAJAL_API || "http://localhost:8000").replace(/\/$/, "");
  const boton = document.getElementById("metricas-actualizar");
  const estado = document.getElementById("metricas-estado");
  const listas = {
    index: document.getElementById("metricas-indice-lista"),
    corpus: document.getElementById("metricas-corpus-lista"),
    retrieval: document.getElementById("metricas-retrieval-lista"),
  };

  let enCurso = false;

  window.CarvajalMetricas = { activar };

  if (boton) boton.addEventListener("click", () => void cargar());

  function activar() {
    return cargar();
  }

  async function cargar() {
    if (enCurso) return;
    enCurso = true;
    avisar("Consultando el índice…");
    try {
      const response = await fetch(`${API}/api/metricas`, { credentials: "include" });
      const datos = await response.json().catch(() => ({}));
      pintar(datos);
      avisar(
        response.ok
          ? `Actualizado a las ${hora()}`
          : "El índice o la base no responden; se muestra el último estado disponible.",
        !response.ok,
      );
    } catch {
      avisar(`No se pudo consultar la API en ${API}.`, true);
    } finally {
      enCurso = false;
    }
  }

  function avisar(texto, error = false) {
    if (!estado) return;
    estado.textContent = texto;
    estado.classList.toggle("error", Boolean(error));
  }

  function pintar(datos) {
    const payload = datos && typeof datos === "object" ? datos : {};
    pintarLista(listas.index, filasIndice(payload.index));
    pintarLista(listas.corpus, filasCorpus(payload.corpus));
    pintarLista(listas.retrieval, filasRetrieval(payload.retrieval));
  }

  function filasIndice(indice) {
    const datos = indice && typeof indice === "object" ? indice : {};
    const filas = [
      ["Estado", texto(datos.status)],
      ["Colección", texto(datos.collection)],
    ];
    if (datos.status === "ok") {
      filas.push(
        ["Puntos", numero(datos.points)],
        ["Vectores indexados", numero(datos.indexed_vectors)],
        ["Segmentos", numero(datos.segments)],
        ["Dimensión", numero(datos.dimensions)],
        ["Distancia", texto(datos.distance)],
        ["Optimizador", texto(datos.optimizer_status)],
      );
    }
    return filas;
  }

  function filasCorpus(corpus) {
    const datos = corpus && typeof corpus === "object" ? corpus : {};
    if (datos.status === "unavailable") {
      return [["Estado", "Base de datos no disponible"]];
    }
    return [
      ["Documentos", numero(datos.documents)],
      ["Fragmentos", numero(datos.chunks)],
    ];
  }

  function filasRetrieval(retrieval) {
    if (!retrieval || typeof retrieval !== "object") {
      return [["Estado", "Sin evaluación todavía"]];
    }
    const k = numero(retrieval.k);
    return [
      [`Recall@${k}`, fraccion(retrieval.recall_at_k)],
      [`nDCG@${k}`, fraccion(retrieval.ndcg_at_k)],
      ["Consultas etiquetadas", numero(retrieval.labeled_queries)],
      ["Consultas totales", numero(retrieval.queries)],
      ["Abstenciones", numero(retrieval.abstentions)],
      ["Colección", texto(retrieval.collection)],
      ["Generado", fecha(retrieval.generated_at)],
    ];
  }

  function pintarLista(lista, filas) {
    if (!lista) return;
    lista.replaceChildren();
    for (const [etiqueta, valor] of filas) {
      const fila = document.createElement("div");
      fila.className = "metrica-fila";
      const dt = document.createElement("dt");
      dt.textContent = etiqueta;
      const dd = document.createElement("dd");
      dd.textContent = valor;
      fila.append(dt, dd);
      lista.append(fila);
    }
  }

  function numero(valor) {
    const n = Number(valor);
    return Number.isFinite(n) ? n.toLocaleString("es-CO") : "—";
  }

  function fraccion(valor) {
    const n = Number(valor);
    return Number.isFinite(n) ? n.toFixed(4).replace(".", ",") : "—";
  }

  function fecha(valor) {
    if (!valor) return "—";
    const d = new Date(valor);
    if (Number.isNaN(d.getTime())) return texto(valor);
    return d.toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" });
  }

  function texto(valor) {
    if (valor === null || valor === undefined || valor === "") return "—";
    return String(valor);
  }

  function hora() {
    return new Date().toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" });
  }
})();
