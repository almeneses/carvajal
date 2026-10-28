"""Interfaz web de prueba (Streamlit) para el asistente RAG del corpus."""

import streamlit as st

from carvajal.rag import ask, build_retriever, build_system_prompt, load_gold_documents


def render_answer(question: str) -> str:
    """Genera la respuesta RAG para una pregunta.

    Args:
        question: Pregunta del usuario.

    Returns:
        Respuesta generada por el LLM.
    """

    documents = st.session_state.get("documents")
    if documents is None:
        documents = load_gold_documents()
        st.session_state["documents"] = documents

    retriever = st.session_state.get("retriever")
    if retriever is None:
        retriever = build_retriever(documents)
        st.session_state["retriever"] = retriever

    system_prompt = st.session_state.get("system_prompt")
    if system_prompt is None:
        system_prompt = build_system_prompt(documents)
        st.session_state["system_prompt"] = system_prompt

    return ask(question, retriever, system_prompt=system_prompt)


def main() -> None:
    """Punto de entrada de la interfaz Streamlit."""

    st.set_page_config(page_title="Carvajal — Asistente", page_icon="🤖")
    st.title("Carvajal — Asistente Q&A (prueba)")
    st.caption("Arquitectura RAG sobre el corpus web de Carvajal.")

    question = st.text_area(
        "Pregunta",
        placeholder="Escribe tu pregunta sobre el corpus...",
        height=150,
    )
    if st.button("Responder"):
        if not question.strip():
            st.warning("Escribe una pregunta antes de responder.")
            return
        with st.spinner("Buscando en el corpus y consultando el modelo..."):
            answer = render_answer(question.strip())
        st.markdown(answer)


if __name__ == "__main__":
    main()
