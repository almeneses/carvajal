"""Errores propios de la capa de aplicación.

Viven fuera de los casos de uso para que los adaptadores puedan traducir fallos
de infraestructura sin depender de un módulo de aplicación concreto.
"""

__all__ = ["LLMError"]


class LLMError(RuntimeError):
    """El proveedor de LLM no devolvió una respuesta utilizable."""
