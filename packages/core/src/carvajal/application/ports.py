"""Puertos de la capa de aplicación: contratos que implementa la infraestructura.

Los casos de uso (``application``) dependen de estos ``Protocol`` y nunca de
``requests``, ``psycopg`` o LangChain. Cada adaptador de infraestructura debe
pasar las pruebas de contrato de ``tests/test_ports.py``.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol, runtime_checkable

from carvajal.domain.entities import Chunk, Document, DocumentVersion

__all__ = [
    "GATE_CLASSES",
    "ChunkRepository",
    "ClaimsJudge",
    "ClaimsQuery",
    "ClaimsVerdict",
    "DocumentRepository",
    "Embedder",
    "Extractor",
    "Fetcher",
    "GateClass",
    "GateScope",
    "JevStatus",
    "JudgeCall",
    "JudgeKind",
    "JudgeUnavailable",
    "LLMProvider",
    "ModelAwareEmbedder",
    "RouteJudge",
    "RouteQuery",
    "RouteVerdict",
    "ScoredChunk",
    "UsageRepository",
    "VectorStore",
]


@dataclass(frozen=True)
class ScoredChunk:
    """Resultado de una búsqueda vectorial: fragmento, puntuación y metadatos.

    ``metadata`` lo rellena el adaptador cuando el índice guarda la procedencia
    (Qdrant): título, URL, tipo de fuente y hash. Los adaptadores que no la
    conservan pueden dejarlo vacío; la cita sigue siendo válida con el chunk.
    """

    chunk: Chunk
    score: float
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class DocumentRepository(Protocol):
    """Almacén de documentos del corpus (núcleo y capa extendida)."""

    def get(self, document_id: str) -> Document | None:
        """Devuelve el documento por ID, o ``None`` si no existe."""

        ...

    def get_by_slug(self, slug: str) -> Document | None:
        """Devuelve el documento por slug, o ``None`` si no existe."""

        ...

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Lista documentos, opcionalmente filtrados por capa y sección.

        ``limit=0`` es válido y devuelve una secuencia vacía. Lanza ``ValueError``
        si ``limit`` u ``offset`` son negativos.
        """

        ...

    def upsert(self, document: Document) -> Document:
        """Inserta o actualiza el documento y lo devuelve.

        El ``id`` es inmutable. Lanza ``ValueError`` si el ``slug`` ya pertenece
        a otro ``id``.
        """

        ...

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        """Devuelve las versiones archivadas, de la más reciente a la más antigua.

        Devuelve una secuencia vacía si el documento no existe o no tiene
        versiones.
        """

        ...

    def delete(self, document_id: str) -> bool:
        """Borra el documento y su historial de versiones; devuelve si existía.

        Los fragmentos se eliminan aparte con ``ChunkRepository.delete_by_document``.
        """

        ...

    def count(self, *, layer: str | None = None) -> int:
        """Cuenta documentos, opcionalmente filtrados por capa."""

        ...


@runtime_checkable
class ChunkRepository(Protocol):
    """Almacén de fragmentos asociados a un documento."""

    def list_by_document(self, document_id: str) -> Sequence[Chunk]:
        """Devuelve los fragmentos del documento ordenados por posición."""

        ...

    def replace(self, document_id: str, chunks: Iterable[Chunk]) -> None:
        """Reemplaza de forma atómica todos los fragmentos del documento."""

        ...

    def delete_by_document(self, document_id: str) -> int:
        """Borra los fragmentos del documento y devuelve cuántos eliminó."""

        ...


@runtime_checkable
class VectorStore(Protocol):
    """Índice vectorial para recuperación semántica."""

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """Inserta o actualiza fragmentos con sus vectores."""

        ...

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        """Devuelve los fragmentos más similares, de mayor a menor puntuación."""

        ...

    def delete_by_document(self, document_id: str) -> None:
        """Elimina del índice todos los fragmentos del documento."""

        ...


@runtime_checkable
class Embedder(Protocol):
    """Generador de vectores para la recuperación semántica."""

    def embed(self, text: str) -> Sequence[float]:
        """Devuelve el vector del texto."""

        ...


@runtime_checkable
class ModelAwareEmbedder(Protocol):
    """Embedder que además declara el modelo que produce sus vectores.

    El índice lo registra en cada punto para no mezclar modelos distintos. Los
    dobles de prueba pueden implementar solo ``Embedder``.
    """

    @property
    def model(self) -> str:
        """Nombre del modelo de embeddings."""


@runtime_checkable
class LLMProvider(Protocol):
    """Proveedor de generación de texto, con y sin streaming."""

    def complete(self, *, system: str, prompt: str) -> str:
        """Devuelve una respuesta completa."""

        ...

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        """Emite la respuesta por fragmentos."""

        ...


@runtime_checkable
class Fetcher(Protocol):
    """Cliente HTTP que consume el pipeline de captura."""

    def can_fetch(self, url: str) -> bool:
        """Indica si la URL se puede descargar."""

        ...

    def fetch(self, url: str) -> str:
        """Devuelve el HTML de la URL."""

        ...


@runtime_checkable
class Extractor(Protocol):
    """Conversor de HTML a Markdown limpio."""

    def extract(self, html: str) -> str:
        """Devuelve el Markdown del HTML."""

        ...


@runtime_checkable
class UsageRepository(Protocol):
    """Contador diario de uso por visitante anónimo."""

    def try_consume(self, owner_key: str, day: date, limit: int) -> bool:
        """Reserva una petición si queda cupo y devuelve si se concedió.

        Debe ser atómico: dos peticiones simultáneas del mismo visitante no
        pueden superar ``limit``. ``limit`` no positivo no concede nada.
        """

        ...

    def has_room(self, owner_key: str, day: date, limit: int) -> bool:
        """Indica si al visitante le queda cupo hoy, sin reservar nada.

        Sirve para no gastar un servicio externo (la foto de la spec 051 va a Jev antes
        de cobrar) cuando el cupo ya se agotó. No es una reserva: ``try_consume`` sigue
        siendo la única operación que concede.
        """

        ...


# --- Juez externo (spec 046: Jev de TypeSafe) ------------------------------------------------
#
# Los tipos viven aquí, sin dependencias: ``agent/gatekeeper/model.py`` los reexporta y el
# registro de herramientas importa ``GateScope`` solo de este módulo (research R1). Ningún
# valor de estos tipos lleva el texto enviado al juez: solo metadatos (FR-021).

GateClass = Literal["herramienta_estructurada", "rag", "mixta", "respuesta_directa"]
GateScope = Literal["direct", "rag", "structured", "all"]
JevStatus = Literal[
    "ok", "off", "timeout", "error", "cap", "circuit_open", "auth", "invalid", "skipped"
]
JudgeKind = Literal["router", "claims", "food", "flow"]
# Orden de las opciones de la pregunta Choice (contrato §2): la más costosa de equivocar,
# la respuesta directa, va al final por el sesgo posicional que advierte la documentación.
GATE_CLASSES: tuple[GateClass, ...] = (
    "herramienta_estructurada",
    "rag",
    "mixta",
    "respuesta_directa",
)


@dataclass(frozen=True)
class JudgeCall:
    """Metadatos de una llamada al juez; nunca el texto enviado ni recibido."""

    kind: JudgeKind
    status: JevStatus
    latency_ms: int
    model: str = ""  # el que devolvió la API; vacío si no hubo respuesta
    input_tokens: int = 0
    output_tokens: int = 0

    def to_value(self) -> dict[str, object]:
        """Forma JSON para el estado del grafo, la traza y el monitoreo."""

        return {
            "kind": self.kind,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class RouteQuery:
    """Carga del gatekeeper: la pregunta ya redactada y, si aplica, una línea de contexto."""

    question: str
    context: str = ""


@dataclass(frozen=True)
class RouteVerdict:
    """Respuesta Choice: clase, confianza y probabilidades (Score solo en la medición)."""

    choice: GateClass
    confidence: float
    probabilities: Mapping[GateClass, float]
    call: JudgeCall
    # Valor esperado del nivel de Score (real en [0, 4]); la API no devuelve un entero.
    clarity: float | None = None
    clarity_confidence: float | None = None


@dataclass(frozen=True)
class ClaimsQuery:
    """Carga de la compuerta: respuesta sin datos de la persona y evidencia mínima.

    ``evidence`` son pares ``(consulta, texto)`` ya acotados; puede incluir
    ``("dicho_por_la_persona", valores estructurados)``. La pregunta no viaja (FR-018).
    """

    answer: str
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ClaimsVerdict:
    """Respuesta Noul: probabilidad de que todas las afirmaciones estén respaldadas."""

    p_supported: float
    call: JudgeCall


class JudgeUnavailable(Exception):
    """El juez no respondió: apagado, tope, circuito, plazo, error o salida inválida.

    Es la única excepción que ``classify`` y ``check`` pueden lanzar; el gatekeeper y la
    compuerta la convierten en el comportamiento sin Jev (FR-006, FR-015). El mensaje es
    solo el estado: nunca lleva el texto enviado ni la clave.
    """

    def __init__(self, status: JevStatus, call: JudgeCall | None = None, reason: str = "") -> None:
        super().__init__(status)
        self.status: JevStatus = status
        self.call = call
        # Motivo sin llamada distinto del estado (``cuota`` de una corrida de evaluación).
        self.reason = reason


@runtime_checkable
class RouteJudge(Protocol):
    """Clasificador del turno para el gatekeeper (pregunta Choice)."""

    def available(self) -> bool:
        """Configurado, encendido, con el circuito cerrado y bajo el tope; sin red."""

        ...

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        """Clase del turno; lanza :class:`JudgeUnavailable` ante cualquier fallo."""

        ...


@runtime_checkable
class ClaimsJudge(Protocol):
    """Juez del respaldo de una respuesta (pregunta Noul)."""

    def available(self) -> bool:
        """Configurado, encendido, con el circuito cerrado y bajo el tope; sin red."""

        ...

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        """Probabilidad de respaldo; lanza :class:`JudgeUnavailable` ante cualquier fallo."""

        ...
