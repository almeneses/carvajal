"""La imagen de producción debe incluir el catálogo curado (ruta comercial)."""

from pathlib import Path

RAIZ = Path(__file__).parent.parent


def test_la_imagen_incluye_el_catalogo_fuera_del_volumen() -> None:
    """`/app/data` es un volumen escribible y oculta lo que traiga la imagen."""

    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY data/catalog ./catalog" in dockerfile
    assert "CATALOG_DIR=/app/catalog/empaques" in dockerfile


def test_el_contexto_de_build_no_excluye_el_catalogo() -> None:
    """.dockerignore excluye `data/*`; el catálogo se re-incluye explícitamente."""

    ignore = (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "!data/catalog" in ignore
    assert "!data/catalog/**" in ignore
