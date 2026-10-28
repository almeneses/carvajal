# Contribución

¡Gracias por tu interés en contribuir a **Carvajal — Corpus Web**! 🙌

## Entorno de desarrollo

```bash
# 1. Sincronizar el entorno (uv es el único gestor permitido)
uv sync

# 2. Preparar el entorno de desarrollo (dependencias + hooks)
make dev
```

## Flujo de trabajo

1. Lee [`CONSTITUTION.md`](CONSTITUTION.md) — es la fuente única de verdad.
2. Crea una rama desde `main` con el prefijo del tipo de cambio:
   `feat/...`, `fix/...`, `docs/...`, `refactor/...`, `test/...`, `chore/...`.
3. Trabaja en el código y las pruebas.
4. Verifica la calidad completa:

   ```bash
   make check
   ```

5. Commits con [Conventional Commits](https://www.conventionalcommits.org/):

   ```bash
   git add -A
   git commit -m "feat: descripción en imperativo"
   ```

6. Abre un Pull Request contra `main` usando la plantilla.

## Estándares

- **Código:** PEP 8 + PEP 257, impuesto por Ruff (`line-length = 100`, comillas dobles).
- **Tipado:** anotaciones en funciones y firmas públicas (ty).
- **Pruebas:** `pytest` con patrón AAA, en `tests/`.
- **Idioma:** UI, documentación y commits en español; identificadores en inglés.

## Regla dura

Toda gestión de dependencias y entornos pasa por `uv`. Está **prohibido** `pip` y
`requirements.txt`. Toda dependencia nueva entra con `uv add` / `uv add --dev`.

## Licencia

Al contribuir, aceptas que tus contribuciones se licencian bajo la [MIT License](LICENSE).
