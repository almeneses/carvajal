"""Pruebas del módulo RAG del corpus."""

from pathlib import Path

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from carvajal.rag import (
    DEFAULT_SYSTEM_PROMPT,
    ask,
    build_llm,
    build_system_prompt,
    evaluate,
    load_gold_documents,
    load_questions,
    render_results_markdown,
)


def test_load_gold_documents_devuelve_corpus() -> None:
    """Carga los entregables Gold disponibles en ``data/gold``."""

    documents = load_gold_documents()

    assert len(documents) > 0
    assert all(isinstance(document, Document) for document in documents)


def test_build_system_prompt_restringe_respuesta() -> None:
    """El prompt del sistema define restricciones de respuesta sobre el corpus."""

    prompt = build_system_prompt([])

    assert DEFAULT_SYSTEM_PROMPT in prompt
    assert "CORPUS CARVAJAL" in prompt
    assert "no lo sabes" in prompt.lower()


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


def test_build_llm_devuelve_ollama_cuando_se_configura(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con ``LLM_PROVIDER=ollama`` el LLM es Ollama."""

    monkeypatch.setattr("carvajal.rag.LLM_PROVIDER", "ollama")

    assert isinstance(build_llm(), ChatOllama)


def test_build_llm_devuelve_claude_cuando_se_configura(monkeypatch: pytest.MonkeyPatch) -> None:
    """El proveedor Claude (vía API) se selecciona con ``LLM_PROVIDER=claude``."""

    monkeypatch.setattr("carvajal.rag.LLM_PROVIDER", "claude")
    monkeypatch.setattr("carvajal.rag.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("carvajal.rag.CLAUDE_MODEL", "claude-sonnet-4-5")

    assert isinstance(build_llm(), ChatAnthropic)


def test_build_llm_devuelve_deepseek_cuando_se_configura(monkeypatch: pytest.MonkeyPatch) -> None:
    """El proveedor DeepSeek (API compatible con OpenAI) se selecciona con ``LLM_PROVIDER``."""

    monkeypatch.setattr("carvajal.rag.LLM_PROVIDER", "deepseek")
    monkeypatch.setattr("carvajal.rag.DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("carvajal.rag.DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr("carvajal.rag.DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    assert isinstance(build_llm(), ChatOpenAI)


def test_build_system_prompt_consolida_corpus() -> None:
    """El prompt de sistema incluye el texto limpio del corpus consolidado."""

    documents = load_gold_documents()
    prompt = build_system_prompt(documents)

    assert DEFAULT_SYSTEM_PROMPT in prompt
    assert "=== CORPUS CARVAJAL ===" in prompt
    assert "### Fuente:" in prompt
    assert "Carvajal" in prompt
    assert "captured_at:" not in prompt


def test_load_questions_carga_preguntas(tmp_path: Path) -> None:
    """Carga las preguntas de prueba desde un archivo JSON."""

    questions_file = tmp_path / "questions.json"
    questions_file.write_text('["¿Pregunta uno?", "¿Pregunta dos?"]', encoding="utf-8")

    questions = load_questions(questions_file)

    assert questions == ["¿Pregunta uno?", "¿Pregunta dos?"]


def test_evaluate_genera_resultados_por_pregunta() -> None:
    """Ejecuta cada pregunta y conserva pregunta y respuesta."""

    def fake_retriever(query: str) -> list[Document]:
        return [Document(page_content="Contexto de prueba.", metadata={"source": "test.md"})]

    def fake_llm(messages: list) -> AIMessage:
        return AIMessage(content="Respuesta de prueba")

    results = evaluate(
        ["¿Pregunta uno?", "¿Pregunta dos?"],
        RunnableLambda(fake_retriever),
        system_prompt="Sos el asistente.",
        llm=RunnableLambda(fake_llm),
    )

    assert len(results) == 2
    assert results[0]["question"] == "¿Pregunta uno?"
    assert results[0]["answer"] == "Respuesta de prueba"


def test_render_results_markdown_incluye_preguntas_y_respuestas() -> None:
    """Renderiza los resultados como secciones Markdown numeradas."""

    markdown = render_results_markdown([{"question": "¿Pregunta uno?", "answer": "Respuesta uno"}])

    assert "# Resultados de la evaluación" in markdown
    assert "## 1. ¿Pregunta uno?" in markdown
    assert "Respuesta uno" in markdown
