"""Pruebas de las herramientas comerciales de búsqueda y validación (T013-T015)."""

from dataclasses import replace
from datetime import date
from pathlib import Path

from carvajal.agent.commercial import CommercialRequest, check_material, search_packaging
from carvajal.catalog.loader import DEFAULT_DIR, Catalog, load_catalog
from carvajal.catalog.models import MaterialRule, Reference

CATALOGO_REAL = Path(__file__).resolve().parents[1] / DEFAULT_DIR


def _ref(sku: str, material: str, **extra: object) -> Reference:
    datos: dict[str, object] = {
        "sku": sku,
        "name": f"Referencia {sku}",
        "family": "vaso",
        "use": "general",
        "capacity_oz": 12,
        "material": material,
        "units_per_box": 1000,
        "country": "MX",
        "source_url": f"https://ejemplo.test/{sku}",
        "captured_at": date(2026, 10, 23),
    }
    datos.update(extra)
    return Reference.model_validate(datos)


def _regla(material: str, **extra: object) -> MaterialRule:
    datos: dict[str, object] = {
        "material": material,
        "temp_min_c": -20,
        "temp_max_c": 60,
        "microwave": False,
        "refrigeration": True,
        "freezing": False,
        "hot_content": True,
    }
    datos.update(extra)
    return MaterialRule.model_validate(datos)


CATALOGO = Catalog(
    references=(
        _ref("PAPEL-CAL", "PAPEL", use="bebida_caliente"),
        _ref("PAPEL-FRIA", "PAPEL", use="bebida_fria", temp_min_c=0, temp_max_c=90),
        _ref("PAPEL-SIN", "PAPEL"),
        _ref("PET-12", "PET", use="bebida_fria"),
        _ref("PP-12", "PP"),
        _ref("PS-12", "PS", use="bebida_fria", country="CO"),
        _ref(
            "PULPA-8", "PULPA", family="contenedor", capacity_oz=None, temp_min_c=0, temp_max_c=60
        ),
        _ref("EPS-7", "EPS", family="contenedor", capacity_oz=None),
        _ref("AL-1", "ALUMINIO", family="charola", capacity_oz=None),
    ),
    materials={
        "PP": _regla("PP", temp_min_c=0, temp_max_c=100, microwave=True),
        "EPS": _regla("EPS"),
        "PS": _regla("PS", temp_min_c=-30),
        "PET": _regla(
            "PET",
            temp_max_c=40,
            hot_content=False,
            applications="sopas=no_apto|frutas=recomendable",
        ),
        "ALUMINIO": _regla(
            "ALUMINIO", temp_min_c=-30, temp_max_c=250, applications="sopas=no_apto"
        ),
    },
    distributors=(),
)


VACIO = CommercialRequest(
    use=None,
    temperature=None,
    microwave=None,
    capacity_oz=None,
    family=None,
    quantity=None,
    country=None,
    city=None,
)


def _pedido(**cambios: object) -> CommercialRequest:
    return replace(VACIO, **cambios)


def _skus(referencias: list[Reference]) -> list[str]:
    return [referencia.sku for referencia in referencias]


def test_busca_por_familia_capacidad_y_pais() -> None:
    """Un vaso de 12 oz en México excluye contenedores y la referencia de Colombia."""

    encontradas = search_packaging(
        _pedido(family="vaso", capacity_oz=12, country="MX"), catalog=CATALOGO
    )

    assert set(_skus(encontradas)) == {"PAPEL-CAL", "PAPEL-FRIA", "PAPEL-SIN", "PET-12", "PP-12"}


def test_una_bebida_implica_vaso_y_ordena_por_uso_declarado() -> None:
    """Sin familia explícita, una bebida busca vasos y pone primero los del uso pedido."""

    encontradas = search_packaging(_pedido(use="bebida_caliente", country="MX"), catalog=CATALOGO)

    assert {referencia.family for referencia in encontradas} == {"vaso"}
    assert _skus(encontradas)[0] == "PAPEL-CAL"


def test_bebida_caliente_descarta_pet_ps_y_vasos_para_frio() -> None:
    """Escenario US1.1: nada de PET, PS ni vasos declarados para bebida fría."""

    pedido = _pedido(use="bebida_caliente", temperature="caliente")
    candidatas = search_packaging(pedido, catalog=CATALOGO)

    validas, descartadas = check_material(candidatas, pedido, catalog=CATALOGO)

    assert "PAPEL-CAL" in _skus(validas)
    assert "PP-12" in _skus(validas)
    assert {"PET-12", "PS-12", "PAPEL-FRIA", "PAPEL-SIN"} <= {d.sku for d in descartadas}
    assert all(d.reason for d in descartadas)


def test_bebida_caliente_sin_temperatura_tambien_descarta_pet_y_ps() -> None:
    """El uso pedido basta: el router puede no entregar temperatura y la regla igual aplica."""

    pedido = _pedido(use="bebida_caliente")
    candidatas = search_packaging(pedido, catalog=CATALOGO)

    validas, descartadas = check_material(candidatas, pedido, catalog=CATALOGO)

    assert set(_skus(validas)) == {"PAPEL-CAL", "PP-12"}
    assert {"PET-12", "PS-12", "PAPEL-FRIA", "PAPEL-SIN"} <= {d.sku for d in descartadas}


def test_bebida_fria_sin_temperatura_descarta_vasos_solo_calientes() -> None:
    """El uso frío pedido también descarta una ficha declarada solo para caliente."""

    _, descartadas = check_material(
        [CATALOGO.references[0]], _pedido(use="bebida_fria"), catalog=CATALOGO
    )

    assert descartadas[0].sku == "PAPEL-CAL"


def test_papel_sin_temperatura_ni_uso_no_pasa_para_caliente() -> None:
    """Si la guía no cubre el material, la ficha debe respaldar el uso caliente."""

    _, descartadas = check_material(
        [CATALOGO.references[2]], _pedido(temperature="caliente"), catalog=CATALOGO
    )

    assert descartadas[0].sku == "PAPEL-SIN"
    assert "no declara temperatura" in descartadas[0].reason


def test_pulpa_con_temperatura_declarada_pasa_para_comida_caliente() -> None:
    """La pulpa no está en la guía, pero su ficha declara hasta 60 °C."""

    pulpa = next(ref for ref in CATALOGO.references if ref.sku == "PULPA-8")

    validas, _ = check_material([pulpa], _pedido(temperature="caliente"), catalog=CATALOGO)

    assert _skus(validas) == ["PULPA-8"]


def test_microondas_solo_pp() -> None:
    """Escenario US1.2: solo los materiales que la guía declara aptos para microondas."""

    validas, descartadas = check_material(
        list(CATALOGO.references), _pedido(microwave=True), catalog=CATALOGO
    )

    assert _skus(validas) == ["PP-12"]
    assert len(descartadas) == len(CATALOGO.references) - 1


def test_aplicacion_no_apta_de_la_guia() -> None:
    """La guía marca el aluminio como no apto para sopas."""

    aluminio = next(ref for ref in CATALOGO.references if ref.sku == "AL-1")

    validas, descartadas = check_material([aluminio], _pedido(use="sopas"), catalog=CATALOGO)

    assert validas == []
    assert "no apto para sopas" in descartadas[0].reason


def test_sin_candidatas_validas_devuelve_vacio_con_motivos() -> None:
    """Escenario US1.4: ninguna cumple y cada descarte explica por qué."""

    pet = [ref for ref in CATALOGO.references if ref.material == "PET"]

    validas, descartadas = check_material(pet, _pedido(temperature="caliente"), catalog=CATALOGO)

    assert validas == []
    assert [d.sku for d in descartadas] == ["PET-12"]


def test_preferencia_renovable_va_primero() -> None:
    """Escenario US1.5: con preferencia ambiental, papel y pulpa encabezan la lista."""

    pedido = _pedido(temperature="fria", prefers_renewable=True)
    candidatas = [ref for ref in CATALOGO.references if ref.sku in {"PP-12", "PAPEL-FRIA"}]

    validas, _ = check_material(candidatas, pedido, catalog=CATALOGO)

    assert _skus(validas) == ["PAPEL-FRIA", "PP-12"]


def test_catalogo_real_cafe_caliente_12_oz_en_mexico() -> None:
    """Con los datos versionados: vasos de 12 oz para café, sin ningún PET."""

    load_catalog.cache_clear()
    real = load_catalog(str(CATALOGO_REAL))
    pedido = _pedido(use="bebida_caliente", temperature="caliente", capacity_oz=12, country="MX")

    validas, descartadas = check_material(
        search_packaging(pedido, catalog=real), pedido, catalog=real
    )

    assert validas
    assert {ref.material for ref in validas} == {"PAPEL"}
    assert all(ref.family == "vaso" and ref.capacity_oz == 12 for ref in validas)
    assert any(d.sku for d in descartadas)
