"""``preguntas_frecuentes``: FAQ de las páginas oficiales, verificada entrada por entrada.

Spec 045, T067; contrato *preguntas_frecuentes* de ``contracts/agent-tools-045.md`` y
research R18. Es la única herramienta que llama a un LLM: reutiliza ``GenerateFaq`` (el
caso de uso de ``POST /api/faq``) con el corpus y el proveedor que trae
``ToolDeps.faq`` (:class:`FaqSource`), y nunca construye un proveedor. Como el texto lo
escribe un LLM, cada entrada se verifica antes de devolverla:

1. su ``_Fuente:_`` debe nombrar un documento de la capa núcleo que alimentó el prompt;
2. el ``fragmento`` es el pasaje literal más corto del documento (párrafos contiguos,
   ≤ :data:`PASSAGE_CHARS` caracteres) que contiene todas las cifras, años, fechas y
   nombres propios de la pregunta y la respuesta, con el mismo criterio de la compuerta
   (``unsupported_terms`` de ``toolagent/evidence.py``); si no hay uno, se descarta.

Cada entrada verificada registra su fuente ``faq:{slug}:{huella}`` con ese pasaje como
``excerpt``, así que la compuerta y ``_tool_backing`` se apoyan en el texto del documento,
nunca en el texto generado.

- ``ok``: ``preguntas: [{n, tema, pregunta, respuesta, fragmento}]``, ``descartadas``,
  ``desde_cache`` y ``aviso``.
- ``empty``: el tema no recupera fragmentos del núcleo o ninguna entrada pasa la
  verificación (con ``evidence_role="corpus"`` el turno se abstiene como con
  ``buscar_corpus``).
- ``error``: sin :class:`FaqSource`, proveedor caído, plazo vencido o ``FAQError`` sin lote
  en la caché para ese tema; con lote en caché (aunque haya caducado) se devuelve ``ok``
  con ``desde_cache: true``.

El módulo no importa el registro: exporta su esquema, :data:`DESCRIPTION`, :func:`status`
y :func:`run`, que ``registry.py`` declara en su ``ToolSpec``.
"""

import hashlib
import re
import threading
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

import structlog
from pydantic import BaseModel, Field, StringConstraints, field_validator

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    FaqSource,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _now_bogota,
    _Sources,
)
from carvajal.agent.toolagent.tools.builtin import _entities_supported, _retrieve
from carvajal.application.ask import _safe_url
from carvajal.application.faq import FaqBatch, FaqEntry, GenerateFaq, PreparedFaq
from carvajal.commercial.book import plain
from carvajal.domain.entities import Document
from carvajal.domain.frontmatter import strip_front_matter

__all__ = [
    "CACHE_TTL",
    "DESCRIPTION",
    "EVIDENCE_ROLE",
    "MAX_FAQ_QUESTIONS",
    "NAME",
    "NOTICE",
    "PASSAGE_CHARS",
    "PreguntasFrecuentesArgs",
    "run",
    "status",
]

logger = structlog.get_logger(__name__)

NAME: Final = "preguntas_frecuentes"
# Papel ante la compuerta (data-model *ToolSpec*): sin entradas verificadas, abstención.
EVIDENCE_ROLE: Final = "corpus"
MAX_FAQ_QUESTIONS: Final = 5
MAX_TOPIC_CHARS: Final = 80
PASSAGE_CHARS: Final = 1500  # tope del pasaje literal que respalda una entrada
CACHE_TTL: Final = timedelta(hours=24)
_FINGERPRINT_CHARS: Final = 8
_DEFAULT_THEME: Final = "General"

# Lo que ve el modelo (≤ 300 caracteres, sin cifras, ciudades ni SKU; research R10).
DESCRIPTION: Final = (
    "Prepara preguntas frecuentes sobre Carvajal con las páginas oficiales, cada una con "
    "su fuente. Úsala para: qué suele preguntarse, una FAQ general o de un tema. No la "
    "uses para: responder una pregunta concreta (usa buscar_corpus, historia_carvajal o "
    "empresas_del_grupo)."
)
NOTICE: Final = "preguntas generadas con las páginas oficiales de Carvajal; cada una cita su fuente"
_OWN_NAME: Final = re.compile(r"\bCarvajal\b", re.IGNORECASE)
_STATUS_TEXT: Final = "Estoy preparando preguntas frecuentes sobre Carvajal…"
_UNAVAILABLE: Final = (
    "No puedo generar preguntas frecuentes en este momento; no hay datos. Puedes buscar "
    "en los documentos con buscar_corpus."
)
_NO_FRAGMENTS: Final = (
    "Las páginas oficiales de Carvajal no tienen fragmentos sobre ese tema; no hay evidencia."
)
_NONE_VERIFIED: Final = (
    "Ninguna pregunta generada pasó la verificación contra las páginas oficiales; no hay evidencia."
)
_OK_MESSAGE: Final = (
    "{count} pregunta(s) frecuente(s) verificadas contra las páginas oficiales de Carvajal; "
    "cada una se cita con su número [n] y su fragmento es el texto literal de la fuente."
)
_CACHE_NOTE: Final = " Vienen de un lote ya verificado (desde_cache)."

# ``_Fuente:_ slug (oficial|tercero)`` de ``FAQ_INSTRUCTIONS``: el slug va antes del tipo.
_SOURCE_SLUG = re.compile(r"[^\s(),;`'\"]+")
# Cabecera de cada documento en el prompt de ``build_faq_prompt``.
_PROMPT_HEADER = "### Fuente: {slug} · "
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CONTENT_WORD = re.compile(r"[a-z0-9ñ]{5,}")


def _collapse(value: object) -> object:
    """Espacios colapsados; vacío o solo espacios cuenta como ausente."""

    if isinstance(value, str):
        return " ".join(value.split()) or None
    return value


class PreguntasFrecuentesArgs(BaseModel):
    """Preguntas frecuentes sobre Carvajal generadas con las páginas oficiales."""

    tema: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TOPIC_CHARS)] | None = (
        Field(
            default=None,
            description=(
                "Área, como «sostenibilidad», «historia» o «empaques»; sin tema, preguntas "
                "generales."
            ),
        )
    )
    cantidad: int = Field(
        default=MAX_FAQ_QUESTIONS,
        ge=1,
        le=MAX_FAQ_QUESTIONS,
        description="Cuántas preguntas, de 1 a 5.",
    )

    _tema = field_validator("tema", mode="before")(_collapse)


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: nunca muestra el tema que escribió el modelo (FR-018)."""

    return _STATUS_TEXT


# --- Lote verificado y caché ----------------------------------------------------------------


@dataclass(frozen=True)
class _Verified:
    """Entrada que pasó la verificación, con la fuente que la respalda (sin ``n``)."""

    theme: str
    question: str
    answer: str
    passage: str
    source: Mapping[str, Any]


@dataclass(frozen=True)
class _CachedBatch:
    """Lote verificado guardado en ``FaqSource.cache`` con la hora en que se generó."""

    created: datetime
    topic: str
    entries: tuple[_Verified, ...]


class _FailedGeneration(Exception):
    """La generación no produjo un lote (proveedor, plazo o ``FAQError``)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Snapshot:
    """``FaqCorpus`` con los documentos ya leídos: una sola lectura por llamada."""

    docs: tuple[Document, ...]

    def documents(self) -> Sequence[Document]:
        return self.docs


def _topic_key(topic: str | None) -> str:
    return plain(topic) if topic else ""


def _cached(faq: FaqSource, key: tuple[str, int], now: datetime) -> _CachedBatch | None:
    """Lote vigente (menos de 24 h) de la misma clave."""

    batch = faq.cache.get(key)
    if isinstance(batch, _CachedBatch) and timedelta(0) <= now - batch.created < CACHE_TTL:
        return batch
    return None


def _fallback(faq: FaqSource, key: tuple[str, int]) -> _CachedBatch | None:
    """Respaldo: el lote de la misma clave o, si no, el más reciente del mismo tema.

    Vale aunque haya caducado (R18): sus entradas ya pasaron la verificación.
    """

    exact = faq.cache.get(key)
    if isinstance(exact, _CachedBatch):
        return exact
    same_topic = [
        batch
        for batch in faq.cache.values()
        if isinstance(batch, _CachedBatch) and batch.topic == key[0]
    ]
    return max(same_topic, key=lambda batch: batch.created, default=None)


# --- Generación con plazo -------------------------------------------------------------------


def _complete(generator: GenerateFaq, prepared: PreparedFaq, timeout_s: float) -> FaqBatch:
    """``GenerateFaq.complete`` en un hilo aparte, con plazo ``timeout_s``.

    El hilo es demonio: si el plazo vence, el turno sigue sin esperar al proveedor (cuyo
    cliente tiene su propio plazo) y la respuesta tardía se descarta.
    """

    outcome: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            outcome["batch"] = generator.complete(prepared)
        except Exception as error:  # se reporta en el hilo del turno
            outcome["error"] = error
        finally:
            done.set()

    threading.Thread(target=target, name="preguntas-frecuentes", daemon=True).start()
    if not done.wait(timeout_s):
        raise _FailedGeneration("plazo_vencido")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["batch"]


def _topic_slugs(
    deps: ToolDeps, context: ToolContext, topic: str, core: Mapping[str, Document]
) -> list[str]:
    """Documentos núcleo de los fragmentos que ``buscar_corpus`` (oficial) recupera.

    Misma recuperación y misma comprobación de nombres propios que ``buscar_corpus`` con
    ``alcance="oficial"``. El índice guarda el slug como ``document_id``; por si guarda
    el identificador, también se acepta ``Document.id``.
    """

    results = _retrieve(deps, topic, CORE_LAYER)
    if not results:
        return []
    texts = [result.chunk.text for result in results]
    # Las páginas núcleo son de Carvajal aunque el fragmento no repita el nombre: exigir
    # «Carvajal» en el texto vaciaba temas legítimos («sostenibilidad de Carvajal»).
    question = _OWN_NAME.sub(" ", context.question or topic)
    if not _entities_supported(question, _OWN_NAME.sub(" ", topic), texts, _book(deps).cities()):
        return []
    by_id = {document.id: slug for slug, document in core.items()}
    slugs: list[str] = []
    for result in results:
        reference = result.chunk.document_id
        slug = reference if reference in core else by_id.get(reference)
        if slug is not None and slug not in slugs:
            slugs.append(slug)
    return slugs


# --- Verificación ---------------------------------------------------------------------------


def _source_slug(source: str) -> str:
    """Slug de ``_Fuente:_ slug (oficial|tercero)``; vacío si no hay."""

    match = _SOURCE_SLUG.search(source)
    return match.group() if match else ""


def _blocks(document: Document) -> list[str]:
    """Párrafos del documento; uno más largo que el tope se parte por oraciones."""

    blocks: list[str] = []
    for raw in _PARAGRAPH_BREAK.split(strip_front_matter(document.markdown)):
        paragraph = raw.strip()
        if not paragraph:
            continue
        if len(paragraph) <= PASSAGE_CHARS:
            blocks.append(paragraph)
            continue
        piece = ""
        for sentence in _SENTENCE_END.split(paragraph):
            candidate = f"{piece} {sentence}".strip()
            if piece and len(candidate) > PASSAGE_CHARS:
                blocks.append(piece)
                candidate = sentence
            piece = candidate[:PASSAGE_CHARS]
        if piece:
            blocks.append(piece)
    return blocks


def _content_words(text: str) -> set[str]:
    return set(_CONTENT_WORD.findall(plain(text)))


def _passage(document: Document, claim: str) -> str | None:
    """Pasaje literal más corto que respalda todas las cifras y nombres de ``claim``.

    Recorre ventanas de párrafos contiguos de menor a mayor tamaño (≤ ``PASSAGE_CHARS``).
    Entre las del tamaño mínimo que respaldan la entrada, elige la que comparte más
    palabras con ella; exige al menos una en común, para no citar un pasaje ajeno.
    """

    from carvajal.agent.toolagent.evidence import unsupported_terms  # evita un ciclo

    blocks = _blocks(document)
    # Si el documento entero no trae una cifra o un nombre, ninguna ventana lo trae.
    if unsupported_terms(claim, allowed=["\n\n".join(blocks)]):
        return None
    words = _content_words(claim)
    for size in range(1, len(blocks) + 1):
        best: tuple[int, str] | None = None
        fits = False
        for start in range(len(blocks) - size + 1):
            window = "\n\n".join(blocks[start : start + size])
            if len(window) > PASSAGE_CHARS:
                continue
            fits = True
            overlap = len(words & _content_words(window))
            if overlap == 0 or (best is not None and overlap <= best[0]):
                continue
            if not unsupported_terms(claim, allowed=[window]):
                best = (overlap, window)
        if best is not None:
            return best[1]
        if not fits:
            return None
    return None


def _source_entry(document: Document, passage: str) -> dict[str, Any]:
    fingerprint = hashlib.sha256(passage.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS]
    return {
        "chunk_id": f"faq:{document.slug}:{fingerprint}",
        "slug": document.slug,
        "section": document.section,
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": passage,
        "url": _safe_url(document.url),
        "title": document.title,
        "source_type": document.source_type,
    }


def _verify(
    entries: Iterable[FaqEntry], documents: Mapping[str, Document], limit: int
) -> tuple[list[_Verified], int]:
    """Entradas verificadas (como mucho ``limit``) y cuántas se descartaron."""

    from carvajal.agent.toolagent.evidence import unsupported_terms  # evita un ciclo

    verified: list[_Verified] = []
    discarded = 0
    for entry in entries:
        if len(verified) >= limit:
            break
        document = documents.get(_source_slug(entry.source))
        passage = (
            _passage(document, f"{entry.question}\n{entry.answer}")
            if document is not None and entry.question and entry.answer
            else None
        )
        if document is None or passage is None:
            discarded += 1
            continue
        # El tema también lo escribe el LLM: si nombra algo que el pasaje no trae, se
        # sustituye por uno neutro en lugar de mostrarlo.
        theme = entry.theme if not unsupported_terms(entry.theme, allowed=[passage]) else ""
        verified.append(
            _Verified(
                theme=theme or _DEFAULT_THEME,
                question=entry.question,
                answer=entry.answer,
                passage=passage,
                source=_source_entry(document, passage),
            )
        )
    return verified, discarded


# --- Resultado ------------------------------------------------------------------------------


def _ok(
    entries: Sequence[_Verified], sources: _Sources, *, discarded: int, from_cache: bool
) -> _Draft:
    numbers = sources.add([dict(entry.source) for entry in entries])
    questions = [
        {
            "n": number,
            "tema": entry.theme,
            "pregunta": entry.question,
            "respuesta": entry.answer,
            "fragmento": entry.passage,
        }
        for number, entry in zip(numbers, entries, strict=True)
    ]
    message = _OK_MESSAGE.format(count=len(questions))
    return _Draft(
        "ok",
        message + (_CACHE_NOTE if from_cache else ""),
        data={
            "preguntas": questions,
            "descartadas": discarded,
            "desde_cache": from_cache,
            "aviso": NOTICE,
        },
    )


def _failed(
    faq: FaqSource, key: tuple[str, int], limit: int, sources: _Sources, reason: str
) -> _Draft:
    """Sin lote nuevo: el de la caché si lo hay (aunque haya caducado); si no, ``error``."""

    batch = _fallback(faq, key)
    if batch is not None and batch.entries:
        return _ok(batch.entries[:limit], sources, discarded=0, from_cache=True)
    return _Draft("error", _UNAVAILABLE, data={"motivo": reason})


def _generate(
    faq: FaqSource,
    documents: tuple[Document, ...],
    topic: str | None,
    limit: int,
    slugs: Collection[str] | None,
) -> tuple[FaqBatch, PreparedFaq]:
    generator = GenerateFaq(_Snapshot(documents), faq.llm, max_prompt_chars=faq.max_prompt_chars)
    prepared = generator.prepare(topic=topic, max_questions=limit, slugs=slugs)
    return _complete(generator, prepared, faq.timeout_s), prepared


def run(
    args: PreguntasFrecuentesArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Genera, verifica y registra un lote de preguntas frecuentes (contrato, pasos 1-5)."""

    faq = deps.faq
    if faq is None:
        return _Draft("error", _UNAVAILABLE, data={"motivo": "sin_fuente"})
    topic, limit = args.tema, args.cantidad
    key = (_topic_key(topic), limit)
    now = _now_bogota(deps)

    fresh = _cached(faq, key, now)
    if fresh is not None:
        return _ok(fresh.entries[:limit], sources, discarded=0, from_cache=True)

    try:
        documents = tuple(faq.corpus.documents())
    except Exception as error:  # base caída: igual que un proveedor caído
        logger.warning("tool_agent.faq_failed", reason="corpus", error=type(error).__name__)
        return _failed(faq, key, limit, sources, "corpus")
    core = {document.slug: document for document in documents if document.layer == CORE_LAYER}

    slugs: list[str] | None = None
    if topic is not None:
        slugs = _topic_slugs(deps, context, topic, core)
        if not slugs:
            return _Draft("empty", _NO_FRAGMENTS)

    try:
        batch, prepared = _generate(faq, documents, topic, limit, slugs)
    except _FailedGeneration as error:
        logger.warning("tool_agent.faq_failed", reason=error.reason)
        return _failed(faq, key, limit, sources, error.reason)
    except Exception as error:  # proveedor caído o ``FAQError``
        logger.warning("tool_agent.faq_failed", reason="proveedor", error=type(error).__name__)
        return _failed(faq, key, limit, sources, "proveedor")

    # Solo cuentan los documentos núcleo que de verdad entraron en el prompt.
    fed = {
        slug: document
        for slug, document in core.items()
        if _PROMPT_HEADER.format(slug=slug) in prepared.system
    }
    verified, discarded = _verify(batch.entries, fed, limit)
    if not verified:
        fallback = _fallback(faq, key)
        if fallback is not None and fallback.entries:
            return _ok(fallback.entries[:limit], sources, discarded=discarded, from_cache=True)
        return _Draft("empty", _NONE_VERIFIED, data={"descartadas": discarded})

    faq.cache[key] = _CachedBatch(created=now, topic=key[0], entries=tuple(verified))
    return _ok(verified, sources, discarded=discarded, from_cache=False)
