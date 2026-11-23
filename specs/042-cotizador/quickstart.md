# Quickstart — Cotizador (demo)

> **Histórico:** la demo y sus archivos fueron retirados por la [spec 043](../043-chat-comercial-unificado/spec.md). Los comandos que generan `data/demo/` ya no aplican; usa el flujo publicado de la 043.

**Fecha**: 2026-11-21

## Requisitos

- Base PostgreSQL y migraciones aplicadas (`make db`, `alembic upgrade head`) para el
  historial; el motor de cotización no necesita base.
- Conjunto de demo regenerable:

```bash
uv run python scripts/generar_demo_cotizador.py
```

## Levantar

```bash
make api          # interfaz única en http://localhost:8000
```

Abrir `http://localhost:8000/cotizador`.

## Recorrido

1. «Cotízame 20 vasos para bebida caliente» → pide ciudad y conserva producto y cantidad.
2. «En Bogotá» → oferta principal con paquete, sobrantes, total, sede y mapa.
3. «Con domicilio» → zona, envío y ventana en días hábiles.
4. «Mejor 100» → recalcula presentaciones y totales.
5. Probar un producto agotado y una referencia simulada: la tarjeta lo dice.

## Pruebas

```bash
uv run pytest -q tests/test_quotation_engine.py tests/test_quotation_calendar.py \
  tests/test_api_cotizador.py tests/test_demo_cotizador.py tests/test_quotation_eval.py
make check
uv run python scripts/evaluar_cotizador.py   # 13 casos dorados con totales
```

## Qué mirar en la interfaz

- El texto aparece mientras llega (SSE) y las tarjetas se suman al cerrar el turno.
- Los sellos de precio, existencia y producto simulado dentro de cada tarjeta.
- El `#id` y la vigencia de 7 días, y el enlace «Ver la ficha de la oferta».
- El mapa incrustado de la sede de la oferta y el enlace «Abrir el mapa completo».
- La precisión de la sede: «dirección», «vía» o «ciudad».
- Que el subtotal no aparezca como total cuando no hay envío calculado.
- Que el hilo del Cotizador no aparezca en el chat general.
