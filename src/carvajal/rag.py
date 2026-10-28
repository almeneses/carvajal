"""RAG del corpus: recuperación, prompt del sistema y generación de respuesta."""

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from carvajal.config import GOLD_DIR

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBEDDINGS_MODEL = "nomic-embed-text"
LLM_MODEL = "llama3.2"

DEFAULT_SYSTEM_PROMPT = (
    "Eres el asistente del corpus Carvajal. Responde ÚNICAMENTE a partir del contexto "
    "proporcionado. Si la respuesta no está en el contexto, di que no lo sabe. No inventes "
    "información y no uses datos externos al corpus. Indica las fuentes consultadas."
)


def load_gold_documents(gold_dir: Path = GOLD_DIR) -> list[Document]:
    """Carga los entregables Gold (Markdown con front matter) como documentos.

    Args:
        gold_dir: Directorio con los Markdown del corpus.

    Returns:
        Lista de documentos listos para embeddear y recuperar.
    """

    documents: list[Document] = []
    if not gold_dir.exists():
        return documents
    for path in sorted(gold_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        documents.append(
            Document(page_content=content, metadata={"source": str(path), "slug": path.stem})
        )
    return documents


def build_retriever(documents: list[Document]) -> BaseRetriever:
    """Construye un retriever FAISS sobre los documentos del corpus.

    Los documentos se dividen en chunks (``RecursiveCharacterTextSplitter``) antes de
    embedirse para recuperar fragmentos acotados al contexto de la pregunta.

    Args:
        documents: Documentos cargados desde ``data/gold/``.

    Returns:
        Retriever con similitud (embeddings de Ollama, modelo ``nomic-embed-text``).
    """

    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks: list[Document] = []
    for document in documents:
        metadata = dict(document.metadata)
        for chunk in splitter.split_documents([document]):
            chunk.metadata = {**metadata, "source": metadata.get("source", "")}
            chunks.append(chunk)
    vectorstore = FAISS.from_texts(
        texts=[chunk.page_content for chunk in chunks],
        embedding=OllamaEmbeddings(model=EMBEDDINGS_MODEL),
        metadatas=[chunk.metadata for chunk in chunks],
    )
    return vectorstore.as_retriever(search_kwargs={"k": 5})


def build_system_prompt(documents: list[Document]) -> str:
    """Construye el prompt de sistema (el 'cerebro') a partir del corpus.

    Args:
        documents: Documentos cargados desde ``data/gold/``.

    Returns:
        Instrucciones de sistema para el asistente.
    """

    return DEFAULT_SYSTEM_PROMPT


def ask(
    question: str,
    retriever: Runnable,
    system_prompt: str | None = None,
    llm: Runnable | None = None,
) -> str:
    """Genera una respuesta RAG a partir de la pregunta y el retriever.

    Args:
        question: Pregunta del usuario.
        retriever: Retriever sobre el corpus.
        system_prompt: Instrucciones de sistema (por defecto: ``DEFAULT_SYSTEM_PROMPT``).
        llm: LLM invocable (por defecto: ``ChatOllama`` con modelo ``llama3.2``).

    Returns:
        Respuesta generada por el LLM.
    """

    retrieved_documents = retriever.invoke(question)
    context = "\n\n".join(document.page_content for document in retrieved_documents)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt or DEFAULT_SYSTEM_PROMPT),
            ("human", "Contexto:\n{context}\n\nPregunta: {question}"),
        ]
    )
    messages = prompt.format_messages(context=context, question=question)
    response = (llm if llm is not None else ChatOllama(model=LLM_MODEL, temperature=0)).invoke(
        messages
    )
    return str(response.content)
