"""Pruebas del módulo RAG del corpus."""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from carvajal.rag import DEFAULT_SYSTEM_PROMPT, ask, build_system_prompt, load_gold_documents


def test_load_gold_documents_devuelve_corpus() -> None:
    """Carga los entregables Gold disponibles en ``data/gold``."""

    documents = load_gold_documents()

    assert len(documents) > 0
    assert all(isinstance(document, Document) for document in documents)


def test_build_system_prompt_restringe_respuesta() -> None:
    """El prompt del sistema define restricciones de respuesta sobre el contexto."""

    prompt = build_system_prompt([])

    assert DEFAULT_SYSTEM_PROMPT in prompt
    assert "contexto" in prompt.lower()


def test_ask_genera_respuesta_con_contexto() -> None:
    """El pipeline RAG recupera contexto y delega la respuesta al LLM."""

    captured_messages: list = []

    def fake_retriever(query: str) -> list[Document]:
        return [Document(page_content="Contexto de prueba.", metadata={"source": "test.md"})]

    def fake_llm(messages: list) -> AIMessage:
        captured_messages.append(messages)
        return AIMessage(content="Respuesta de prueba")

    retriever = RunnableLambda(fake_retriever)
    llm = RunnableLambda(fake_llm)

    answer = ask("pregunta", retriever, system_prompt="Sos el asistente.", llm=llm)

    assert answer == "Respuesta de prueba"
    assert len(captured_messages) == 1
    first_message = captured_messages[0][1]
    assert "Contexto:" in first_message.content
