"""Gatekeeper del modo ``tools`` y compuerta de afirmaciones (spec 046).

Vacío a propósito: los módulos se importan por su ruta (``gatekeeper.model``,
``gatekeeper.decide``…) para que importar el registro de herramientas no arrastre el grafo
y no haya ciclos ``registry → gatekeeper → toolagent`` (research R1).
"""
