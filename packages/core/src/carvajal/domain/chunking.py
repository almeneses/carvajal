"""Parámetros de fragmentación del corpus, compartidos por RAG e importador.

El tamaño y el solapamiento deben ser los mismos al indexar (FAISS) y al
persistir en la base: si divergen, los fragmentos recuperados no coinciden con
los citados. Viven en el dominio para que ningún adaptador los redefina.
"""

CHUNK_SIZE = 1000
"""Caracteres máximos por fragmento."""

CHUNK_OVERLAP = 200
"""Caracteres de solapamiento entre fragmentos contiguos."""
