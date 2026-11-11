"""Almacén relacional del corpus: motor, tablas y repositorios SQL."""

from carvajal.adapters.storage.repositories import SqlChunkRepository, SqlDocumentRepository

__all__ = ["SqlChunkRepository", "SqlDocumentRepository"]
