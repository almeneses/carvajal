"""Memoria del agente (spec 045): filtro de datos sensibles, memoria del dueño y resumen.

- ``sensitive``: :func:`find_sensitive` y :func:`redact`, deterministas (research R8).
- ``store`` (US2): memoria por categorías en el Store de LangGraph.
- ``summary`` (US1): resumen acumulado de los turnos que salen de la ventana.

Vive fuera de ``toolagent/`` porque la comparten los dos grafos y el atajo.
"""
