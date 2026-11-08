"""Reexportación de compatibilidad del dominio del corpus.

La fuente de verdad es ``carvajal.domain``; este módulo se conserva para que
las importaciones antiguas (``carvajal.models``) sigan funcionando.
"""

from carvajal.domain.entities import CapturedPage
from carvajal.domain.rules import OFFICIAL_DOMAIN

__all__ = ["OFFICIAL_DOMAIN", "CapturedPage"]
