"""RAG del corpus: recuperación, prompt del sistema y generación de respuesta."""

import json
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

from carvajal.config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    GOLD_DIR,
    LLM_PROVIDER,
    QUESTIONS_FILE,
)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBEDDINGS_MODEL = "nomic-embed-text"
LLM_MODEL = "llama3.2"
MAX_SYSTEM_PROMPT_CHARS = 100_000

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
        body = Document(
            page_content=_strip_front_matter(document.page_content),
            metadata=metadata,
        )
        for chunk in splitter.split_documents([body]):
            chunk.metadata = {**metadata, "source": metadata.get("source", "")}
            chunks.append(chunk)
    vectorstore = FAISS.from_texts(
        texts=[chunk.page_content for chunk in chunks],
        embedding=OllamaEmbeddings(model=EMBEDDINGS_MODEL),
        metadatas=[chunk.metadata for chunk in chunks],
    )
    return vectorstore.as_retriever(search_kwargs={"k": 5})


def build_system_prompt(documents: list[Document]) -> str:
    """Consolida el corpus limpio en el prompt de sistema (la 'memoria' del asistente).

    Args:
        documents: Documentos cargados desde ``data/gold/``.

    Returns:
        Instrucciones de sistema con el corpus consolidado como contexto permanente.
    """

    sections: list[str] = []
    for document in documents:
        slug = str(document.metadata.get("slug", "documento"))
        body = _strip_front_matter(document.page_content)
        sections.append(f"### Fuente: {slug}\n\n{body}")

    corpus = "\n\n".join(sections)
    if len(corpus) > MAX_SYSTEM_PROMPT_CHARS:
        corpus = f"{corpus[:MAX_SYSTEM_PROMPT_CHARS]}\n\n[corpus truncado por longitud]"

    return f"{DEFAULT_SYSTEM_PROMPT}\n\n=== CORPUS CARVAJAL ===\n\n{corpus}"


def _strip_front_matter(content: str) -> str:
    """Quita el *front matter* YAML del Markdown Gold."""

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return content.strip()


def build_llm() -> Runnable:
    """Construye el LLM configurado mediante variables de entorno.

    Returns:
        ``ChatOllama`` con ``LLM_PROVIDER=ollama`` (por defecto),
        ``ChatOpenAI`` apuntando a DeepSeek con ``LLM_PROVIDER=deepseek`` o
        ``ChatAnthropic`` con ``LLM_PROVIDER=claude``.
    """

    if LLM_PROVIDER == "deepseek":
        return ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0,
        )
    if LLM_PROVIDER == "claude":
        return ChatAnthropic(model=CLAUDE_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0)
    return ChatOllama(model=LLM_MODEL, temperature=0)


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
        llm: LLM invocable (por defecto: el LLM configurado por ``LLM_PROVIDER``).

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
    response = (llm if llm is not None else build_llm()).invoke(messages)
    return str(response.content)


def load_questions(path: Path = QUESTIONS_FILE) -> list[str]:
    """Carga las preguntas de prueba desde un archivo JSON.

    Args:
        path: Archivo JSON con una lista de preguntas.

    Returns:
        Lista de preguntas de evaluación.
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(question) for question in data]


def evaluate(
    questions: list[str],
    retriever: Runnable,
    system_prompt: str | None = None,
    llm: Runnable | None = None,
) -> list[dict[str, str]]:
    """Ejecuta el pipeline RAG sobre cada pregunta de prueba.

    Args:
        questions: Preguntas de evaluación.
        retriever: Retriever sobre el corpus.
        system_prompt: Instrucciones de sistema con el corpus consolidado.
        llm: LLM invocable (por defecto: el configurado por ``LLM_PROVIDER``).

    Returns:
        Resultados con la pregunta y la respuesta generada.
    """

    results: list[dict[str, str]] = []
    for question in questions:
        answer = ask(question, retriever, system_prompt=system_prompt, llm=llm)
        results.append({"question": question, "answer": answer})
    return results


def render_results_markdown(results: list[dict[str, str]]) -> str:
    """Renderiza los resultados de la evaluación como Markdown.

    Args:
        results: Preguntas y respuestas generadas.

    Returns:
        Documento Markdown con la tabla de resultados.
    """

    lines = ["# Resultados de la evaluación", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"## {index}. {result['question']}")
        lines.append("")
        lines.append(result["answer"])
        lines.append("")
    return "\n".join(lines)
