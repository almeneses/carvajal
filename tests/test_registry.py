"""Pruebas del registro del corpus y del bloqueo compartido (spec 037, Phase 1)."""

import json
import subprocess
import sys
from pathlib import Path

from carvajal.gold import corpus_lock, merge_index
from carvajal.registry import (
    IndexedDocument,
    corpus_areas,
    load_registry,
    read_area,
)


def _indice(area: Path, entradas: list[dict[str, str | int]]) -> Path:
    """Escribe el ``index.jsonl`` de un área con las entradas indicadas."""

    area.mkdir(parents=True, exist_ok=True)
    indice = area / "index.jsonl"
    indice.write_text(
        "".join(json.dumps(entrada, ensure_ascii=False) + "\n" for entrada in entradas),
        encoding="utf-8",
    )
    return indice


def _bloqueado(ruta: Path) -> bool:
    """Indica si otro proceso puede tomar el bloqueo exclusivo de ``ruta``."""

    guion = (
        "import fcntl, sys\n"
        "with open(sys.argv[1], 'a+') as handle:\n"
        "    try:\n"
        "        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except BlockingIOError:\n"
        "        print('bloqueado')\n"
        "    else:\n"
        "        print('libre')\n"
    )
    salida = subprocess.run(
        [sys.executable, "-c", guion, str(ruta)],
        capture_output=True,
        text=True,
        check=True,
    )
    return salida.stdout.strip() == "bloqueado"


def test_read_area_lee_las_entradas_del_indice(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    _indice(
        area,
        [
            {"slug": "beta", "source_key": "url:https://carvajal.com/beta"},
            {"slug": "alfa"},
        ],
    )

    # Act
    documentos = read_area(area)

    # Assert
    assert [documento.slug for documento in documentos] == ["alfa", "beta"]
    assert documentos[0].area == area


def test_read_area_sin_indice_devuelve_vacio(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "sin-indice"
    area.mkdir()

    # Act
    documentos = read_area(area)

    # Assert
    assert documentos == ()


def test_read_area_descarta_lineas_corruptas(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    area.mkdir()
    (area / "index.jsonl").write_text(
        '{"slug": "bueno"}\nno es json\n{"sin": "slug"}\n', encoding="utf-8"
    )

    # Act
    documentos = read_area(area)

    # Assert
    assert [documento.slug for documento in documentos] == ["bueno"]


def test_load_registry_fusiona_areas_y_prioriza_el_nucleo(tmp_path: Path) -> None:
    # Arrange
    nucleo = tmp_path / "gold"
    extendida = tmp_path / "external" / "alejandro" / "gold"
    _indice(nucleo, [{"slug": "informe", "hash": "hash-nucleo"}])
    _indice(extendida, [{"slug": "informe", "hash": "hash-extendida"}, {"slug": "ext_1"}])

    # Act
    registro = load_registry([nucleo, extendida])

    # Assert
    assert len(registro) == 2
    informe = registro.by_slug("informe")
    assert informe is not None
    assert informe.entry["hash"] == "hash-nucleo"
    assert [documento.slug for documento in registro.shadowed] == ["informe"]


def test_load_registry_sin_areas_devuelve_vacio() -> None:
    # Arrange / Act
    registro = load_registry([])

    # Assert
    assert len(registro) == 0
    assert registro.slugs == frozenset()


def test_busqueda_por_source_key(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    _indice(
        area,
        [
            {"slug": "informe", "source_key": "file:corpus/informe.pdf"},
            {"slug": "historia", "source_key": "url:https://carvajal.com/historia"},
        ],
    )
    registro = load_registry([area])

    # Act
    documento = registro.by_source_key("url:https://carvajal.com/historia")

    # Assert
    assert documento is not None
    assert documento.slug == "historia"
    assert registro.by_source_key("url:https://carvajal.com/otra") is None


def test_busqueda_por_huellas(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    _indice(
        area,
        [
            {
                "slug": "informe",
                "source_key": "file:corpus/informe.pdf",
                "raw_hash": "bytes-1",
                "hash": "texto-1",
            }
        ],
    )
    registro = load_registry([area])

    # Act
    por_bytes = registro.by_raw_hash("bytes-1")
    por_texto = registro.by_content_hash("texto-1")

    # Assert
    assert por_bytes is not None and por_bytes.slug == "informe"
    assert por_texto is not None and por_texto.slug == "informe"
    assert registro.by_raw_hash("desconocida") is None


def test_busqueda_por_huella_prioriza_el_nucleo(tmp_path: Path) -> None:
    # Arrange
    nucleo = tmp_path / "gold"
    extendida = tmp_path / "external" / "alejandro" / "gold"
    _indice(nucleo, [{"slug": "propio", "raw_hash": "mismos-bytes"}])
    _indice(extendida, [{"slug": "ext_1", "raw_hash": "mismos-bytes"}])
    registro = load_registry([nucleo, extendida])

    # Act
    documento = registro.by_raw_hash("mismos-bytes")

    # Assert
    assert documento is not None
    assert documento.slug == "propio"


def test_entradas_sin_fuente_no_participan_en_la_busqueda_por_clave(tmp_path: Path) -> None:
    """El corpus anterior a la 037 no tiene ``source_key`` y no debe colisionar."""

    # Arrange
    area = tmp_path / "gold"
    _indice(area, [{"slug": "legado"}, {"slug": "otro"}])
    registro = load_registry([area])

    # Act
    documento = registro.by_source_key("")

    # Assert
    assert documento is None
    assert registro.slug_owners() == {}


def test_layer_por_defecto_es_core(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    _indice(area, [{"slug": "propio"}, {"slug": "ext_1", "layer": "extended"}])

    # Act
    documentos = {documento.slug: documento for documento in load_registry([area])}

    # Assert
    assert documentos["propio"].layer == "core"
    assert documentos["ext_1"].layer == "extended"


def test_slug_owners_devuelve_los_duenos_del_slug(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    _indice(
        area,
        [
            {"slug": "informe", "source_key": "file:corpus/informe.pdf"},
            {"slug": "legado"},
        ],
    )

    # Act
    duenos = load_registry([area]).slug_owners()

    # Assert
    assert duenos == {"informe": "file:corpus/informe.pdf"}


def test_corpus_areas_incluye_el_nucleo_y_las_extendidas(tmp_path: Path) -> None:
    # Arrange
    nucleo = tmp_path / "gold"
    extendida = tmp_path / "external" / "alejandro" / "gold"
    otra = tmp_path / "external" / "donante" / "gold"
    _indice(nucleo, [])
    _indice(extendida, [])
    _indice(otra, [])
    (tmp_path / "external" / "sin-gold").mkdir(parents=True)

    # Act
    areas = corpus_areas(tmp_path)

    # Assert
    assert areas == (nucleo, extendida, otra)


def test_corpus_areas_sin_indices_devuelve_vacio(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / "gold").mkdir()

    # Act
    areas = corpus_areas(tmp_path)

    # Assert
    assert areas == ()


def test_corpus_lock_crea_el_archivo_de_bloqueo(tmp_path: Path) -> None:
    # Arrange
    datos = tmp_path / "data"

    # Act
    with corpus_lock(datos):
        creado = (datos / ".corpus.lock").is_file()

    # Assert
    assert creado


def test_corpus_lock_excluye_a_otro_proceso(tmp_path: Path) -> None:
    # Arrange
    datos = tmp_path / "data"
    datos.mkdir()
    bloqueo = datos / ".corpus.lock"
    bloqueo.touch()

    # Act
    with corpus_lock(datos):
        durante = _bloqueado(bloqueo)
    despues = _bloqueado(bloqueo)

    # Assert
    assert durante
    assert not despues


def test_corpus_lock_serializa_el_bloqueo_del_indice(tmp_path: Path) -> None:
    """Escribir el índice bajo el bloqueo del corpus no se bloquea a sí mismo."""

    # Arrange
    datos = tmp_path / "data"
    indice = datos / "gold" / "index.jsonl"

    # Act
    with corpus_lock(datos):
        merge_index(indice, [{"slug": "informe"}])

    # Assert
    assert json.loads(indice.read_text(encoding="utf-8").splitlines()[0])["slug"] == "informe"


def test_registry_es_inmutable_ante_cambios_del_indice(tmp_path: Path) -> None:
    # Arrange
    area = tmp_path / "gold"
    indice = _indice(area, [{"slug": "informe"}])
    registro = load_registry([area])

    # Act
    indice.write_text('{"slug": "otro"}\n', encoding="utf-8")

    # Assert
    assert registro.slugs == frozenset({"informe"})
    assert len(registro) == 1


def test_indexed_document_expone_los_campos_del_registro() -> None:
    # Arrange
    documento = IndexedDocument(
        slug="informe",
        area=Path("data/gold"),
        entry={
            "source_key": "file:corpus/informe.pdf",
            "raw_hash": "bytes",
            "hash": "texto",
            "layer": "extended",
        },
    )

    # Act / Assert
    assert documento.source_key == "file:corpus/informe.pdf"
    assert documento.raw_hash == "bytes"
    assert documento.content_hash == "texto"
    assert documento.layer == "extended"
