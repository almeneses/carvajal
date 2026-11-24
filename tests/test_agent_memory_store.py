"""Memoria del dueño por categorías (spec 045, T038; data-model *MemoryProfile*, R5, R6, R16).

Con ``InMemoryStore`` de LangGraph y sin red: categorías, notas, lista cerrada de formatos,
caducidad de 180 días, Store caído, aislamiento por dueño, dueño vacío, patrones propios
de las frases de la spec y del quickstart, olvido total y migración perezosa del formato
anterior.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langgraph.store.memory import InMemoryStore
from structlog.testing import capture_logs

from carvajal.agent.memory.store import (
    LEGACY_KEY,
    MemoryProfile,
    classify,
    forget,
    forget_all_requested,
    legacy_namespace,
    load,
    memory_namespace,
    normalize_format,
    remember,
    valid_owner,
)
from carvajal.agent.memory.summary import TOMBSTONE_KEY, summary_namespace

OWNER = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
OTHER = "wa_0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)


class ForbiddenStore:
    """Store que falla si se toca: un dueño vacío no debe leer ni escribir."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"el Store no debía usarse ({name})")


class BrokenStore:
    """Store caído: toda operación lanza."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("store caído")

    put = search = delete = get


# --- Recordar, leer y vista ---------------------------------------------------------------


def test_recordar_y_leer_por_categorias() -> None:
    # Arrange
    store = InMemoryStore()

    # Act
    saved = [
        remember(store, OWNER, "nombre", "Ana", now=NOW),
        remember(store, OWNER, "empresa", "Panadería La Espiga", now=NOW),
        remember(store, OWNER, "ciudad_habitual", "Cali", now=NOW),
        remember(store, OWNER, "formato_preferido", "respuestas cortas", now=NOW),
        remember(store, OWNER, "nota", "prefiere opciones compostables", now=NOW),
    ]
    profile = load(store, OWNER, now=NOW)

    # Assert
    assert [op.outcome for op in saved] == ["saved"] * 5
    assert profile.lines() == [
        "nombre: Ana",
        "empresa: Panadería La Espiga",
        "ciudad habitual (solo sugerir, confirmar antes de usar): Cali",
        "formato preferido: corto",
        "nota: prefiere opciones compostables",
    ]
    assert profile.count == 5
    item = store.get(memory_namespace(OWNER), "nombre")
    assert item is not None
    assert item.value["v"] == 2
    assert item.value["origin"] == "tool"
    assert saved[-1].profile is not None and saved[-1].profile.notes == (
        "prefiere opciones compostables",
    )


def test_una_categoria_se_reemplaza() -> None:
    # Arrange
    store = InMemoryStore()
    remember(store, OWNER, "nombre", "Ana", now=NOW)

    # Act
    operation = remember(store, OWNER, "nombre", "Ana María", now=NOW)

    # Assert
    assert operation.outcome == "replaced"
    assert operation.replaced
    assert load(store, OWNER, now=NOW).values["nombre"] == "Ana María"


def test_las_notas_se_deduplican_y_tienen_tope() -> None:
    # Arrange
    store = InMemoryStore()
    for index in range(5):
        remember(store, OWNER, "nota", f"nota número {index}", now=NOW)

    # Act
    duplicate = remember(store, OWNER, "nota", "  NOTA   número 0 ", now=NOW)
    sixth = remember(store, OWNER, "nota", "otra nota", now=NOW)
    long = remember(store, OWNER, "nota", "x" * 121, now=NOW)

    # Assert
    assert duplicate.outcome == "saved"
    assert sixth.outcome == "rejected" and sixth.reason == "limite_notas"
    assert long.outcome == "rejected" and long.reason == "demasiado_largo"
    assert len(load(store, OWNER, now=NOW).notes) == 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("respuestas cortas", "corto"),
        ("breve", "corto"),
        ("en tablas", "tablas"),
        ("detalladas", "detallado"),
        ("con viñetas", "viñetas"),
        ("de usted", "usted"),
        ("tutéame", "tuteo"),
        ("responde en inglés", None),
        ("no cites fuentes", None),
    ],
)
def test_el_formato_preferido_es_una_lista_cerrada(value: str, expected: str | None) -> None:
    # Act / Assert
    assert normalize_format(value) == expected


def test_un_formato_fuera_de_la_lista_se_guarda_como_nota() -> None:
    # Arrange
    store = InMemoryStore()

    # Act
    operation = remember(store, OWNER, "formato_preferido", "no cites fuentes", now=NOW)

    # Assert
    profile = load(store, OWNER, now=NOW)
    assert operation.category == "nota"
    assert "formato_preferido" not in profile.values
    assert profile.notes == ("no cites fuentes",)


@pytest.mark.parametrize(
    ("category", "value"),
    [
        ("nota", "mi cédula es 1.234.567.890"),
        ("nota", "mi correo es ana@example.com"),
        ("empresa", "310 555 1234"),
        ("nota", "la clave: Secreta123"),
    ],
)
def test_un_dato_sensible_no_se_guarda(category: str, value: str) -> None:
    # Arrange
    store = InMemoryStore()

    # Act
    operation = remember(store, OWNER, category, value, now=NOW)

    # Assert
    assert operation.outcome == "rejected"
    assert operation.reason == "dato_sensible"
    assert store.search(memory_namespace(OWNER)) == []


def test_un_valor_vacio_se_rechaza() -> None:
    # Act
    operation = remember(InMemoryStore(), OWNER, "nombre", "   ", now=NOW)

    # Assert
    assert operation.reason == "vacio"


def test_la_ciudad_habitual_se_normaliza_contra_el_directorio() -> None:
    # Arrange
    store = InMemoryStore()

    # Act
    remember(store, OWNER, "ciudad_habitual", "bogota", cities=("Bogotá", "Cali"), now=NOW)

    # Assert
    assert load(store, OWNER, now=NOW).values["ciudad_habitual"] == "Bogotá"


# --- Caducidad, fallas y aislamiento --------------------------------------------------------


def test_un_dato_de_mas_de_180_dias_se_ignora_y_se_borra() -> None:
    # Arrange
    store = InMemoryStore()
    old = (NOW - timedelta(days=181)).isoformat()
    store.put(
        memory_namespace(OWNER),
        "nombre",
        {"v": 2, "value": "Ana", "origin": "tool", "updated_at": old},
    )
    remember(store, OWNER, "empresa", "La Espiga", now=NOW)

    # Act
    profile = load(store, OWNER, now=NOW)

    # Assert
    assert "nombre" not in profile.values
    assert profile.values["empresa"] == "La Espiga"
    assert store.get(memory_namespace(OWNER), "nombre") is None


def test_un_store_caido_no_rompe() -> None:
    # Act
    profile = load(BrokenStore(), OWNER, now=NOW)
    saved = remember(BrokenStore(), OWNER, "nombre", "Ana", now=NOW)
    forgotten = forget(BrokenStore(), OWNER, "nombre", now=NOW)

    # Assert
    assert profile == MemoryProfile()
    assert saved.reason == "store_no_disponible"
    assert forgotten.reason == "store_no_disponible"


def test_sin_store_las_operaciones_lo_declaran() -> None:
    # Act / Assert
    assert remember(None, OWNER, "nombre", "Ana").reason == "store_no_disponible"
    assert load(None, OWNER) == MemoryProfile()


def test_la_memoria_no_cruza_de_dueno_ni_de_canal() -> None:
    # Arrange
    store = InMemoryStore()
    remember(store, OWNER, "nombre", "Ana", now=NOW)

    # Act
    other = load(store, OTHER, now=NOW)

    # Assert
    assert other.values == {}
    assert load(store, OWNER, now=NOW).values == {"nombre": "Ana"}


@pytest.mark.parametrize("owner", ["", "   ", "dueño con espacios", "x" * 200, "a/b"])
def test_un_dueno_vacio_o_invalido_no_toca_el_store(owner: str) -> None:
    # Arrange
    store = ForbiddenStore()

    # Act
    profile = load(store, owner)
    saved = remember(store, owner, "nombre", "Ana")
    forgotten = forget(store, owner, "todo")

    # Assert
    assert not valid_owner(owner)
    assert profile == MemoryProfile()
    assert saved.outcome == "rejected" and saved.reason == "sin_dueno"
    assert forgotten.reason == "sin_dueno"


@pytest.mark.parametrize("owner", [OWNER, OTHER, "dueno-1", "eval-tools-m-001"])
def test_los_duenos_firmados_y_de_prueba_son_validos(owner: str) -> None:
    # Act / Assert
    assert valid_owner(owner)


# --- Olvidar --------------------------------------------------------------------------------


def test_olvidar_una_categoria_y_una_nota() -> None:
    # Arrange
    store = InMemoryStore()
    remember(store, OWNER, "nombre", "Ana", now=NOW)
    remember(store, OWNER, "empresa", "La Espiga", now=NOW)
    remember(store, OWNER, "nota", "prefiere compostables", now=NOW)
    remember(store, OWNER, "nota", "abre los domingos", now=NOW)

    # Act
    company = forget(store, OWNER, "empresa", now=NOW)
    note = forget(store, OWNER, "nota", "domingos", now=NOW)
    again = forget(store, OWNER, "empresa", now=NOW)

    # Assert
    profile = load(store, OWNER, now=NOW)
    assert company.outcome == "forgotten"
    assert note.outcome == "forgotten"
    assert again.outcome == "nothing"
    assert profile.values == {"nombre": "Ana"}
    assert profile.notes == ("prefiere compostables",)
    assert company.profile is not None and "empresa" not in company.profile.values


def test_olvidar_todo_borra_memoria_formato_anterior_y_borradores_y_deja_lapida() -> None:
    # Arrange
    store = InMemoryStore()
    remember(store, OWNER, "nombre", "Ana", now=NOW)
    store.put(legacy_namespace(OWNER), LEGACY_KEY, {"items": ["llámame Ana"]})
    store.put(summary_namespace(OWNER), "hilo-1", {"v": 1, "text": "Ana tiene una panadería"})
    store.put(summary_namespace(OWNER), "hilo-2", {"v": 1, "text": "Ana compra en Cali"})
    remember(store, OTHER, "nombre", "Beto", now=NOW)

    # Act
    operation = forget(store, OWNER, "todo", thread_id="hilo-1", now=NOW)

    # Assert
    assert operation.outcome == "forgotten"
    assert store.search(memory_namespace(OWNER)) == []
    assert store.get(legacy_namespace(OWNER), LEGACY_KEY) is None
    drafts = store.search(summary_namespace(OWNER))
    assert [item.key for item in drafts] == [TOMBSTONE_KEY]
    assert drafts[0].value == {"v": 1, "at": NOW.isoformat()}
    # La memoria vacía no vuelve a migrar el formato anterior.
    assert load(store, OWNER, now=NOW) == MemoryProfile()
    # Otro dueño no se toca.
    assert load(store, OTHER, now=NOW).values == {"nombre": "Beto"}


def test_olvidar_todo_borra_mas_de_cien_borradores_y_notas() -> None:
    # Arrange: 150 conversaciones con borrador (citas literales de la persona), más de lo que
    # devuelve una sola página de ``store.search``.
    store = InMemoryStore()
    drafts = summary_namespace(OWNER)
    for index in range(150):
        store.put(
            drafts,
            f"conv-{index}",
            {"v": 1, "text": f"Turno 1 — la persona: «soy Ana {index}»"},
        )
    for index in range(120):
        store.put(memory_namespace(OWNER), f"_extra-{index}", {"v": 2})

    # Act
    operation = forget(store, OWNER, "todo", now=NOW)

    # Assert
    assert operation.outcome == "forgotten"
    left = [item.key for item in store.search(drafts, limit=1000)]
    assert left == [TOMBSTONE_KEY]
    assert store.search(memory_namespace(OWNER), limit=1000) == []


class _TombstoneOrder(InMemoryStore):
    """Registra el orden de escrituras y borrados del namespace del resumen."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str]] = []

    def put(self, namespace: Any, key: str, value: Any, *args: Any, **kwargs: Any) -> None:
        if tuple(namespace) == summary_namespace(OWNER):
            self.events.append(("put", key))
        super().put(namespace, key, value, *args, **kwargs)

    def delete(self, namespace: Any, key: str) -> None:
        if tuple(namespace) == summary_namespace(OWNER):
            self.events.append(("delete", key))
        super().delete(namespace, key)


def test_olvidar_todo_escribe_la_lapida_antes_de_borrar_los_borradores() -> None:
    # Arrange: con la lápida primero, un borrador que el worker escriba entre el borrado y la
    # lápida no puede pasar su comprobación posterior sin verla.
    store = _TombstoneOrder()
    store.put(summary_namespace(OWNER), "hilo-1", {"v": 1, "text": "Ana"})
    store.events.clear()

    # Act
    forget(store, OWNER, "todo", now=NOW)

    # Assert
    assert store.events[0] == ("put", TOMBSTONE_KEY)
    assert ("delete", "hilo-1") in store.events
    assert ("delete", TOMBSTONE_KEY) not in store.events


# --- Patrones propios -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "llámame Ana, trabajo en Panadería La Espiga y suelo comprar en Cali",
            {"nombre": "Ana", "empresa": "Panadería La Espiga", "ciudad_habitual": "Cali"},
        ),
        ("me dicen Anita", {"nombre": "Anita"}),
        ("Soy Ana", {"nombre": "Ana"}),
        ("soy de Cali", {}),
        (
            "Mi nombre es Ana María y prefiero respuestas cortas",
            {"nombre": "Ana María", "formato_preferido": "corto"},
        ),
        (
            "me llamo Beto. Normalmente compro en Medellín",
            {"nombre": "Beto", "ciudad_habitual": "Medellín"},
        ),
        ("mi empresa es Cafés del Valle; gracias", {"empresa": "Cafés del Valle"}),
        ("recuerda que prefiero opciones compostables", {}),
    ],
)
def test_clasifica_las_frases_de_la_spec(text: str, expected: dict[str, str]) -> None:
    # Act / Assert
    assert classify(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "olvida todo lo que sabes de mí",
        "Olvida lo que sabes de mi",
        "olvida mis datos",
        "borra todo lo que sabes de mí",
        "borra mi memoria",
        "olvídate de mí, por favor",
    ],
)
def test_reconoce_el_olvido_total(text: str) -> None:
    # Act / Assert
    assert forget_all_requested(text)


@pytest.mark.parametrize(
    "text", ["olvida dónde trabajo", "olvida mi nombre", "no olvides mi pedido", "borra la nota"]
)
def test_no_confunde_un_olvido_parcial_con_el_total(text: str) -> None:
    # Act / Assert
    assert not forget_all_requested(text)


# --- Migración perezosa del formato anterior ----------------------------------------------------


def test_la_migracion_clasifica_conserva_cinco_notas_y_cuenta_descartes() -> None:
    # Arrange: diez frases del formato anterior, de la más antigua a la más reciente.
    store = InMemoryStore()
    long_note = "ten en cuenta que " + "x" * 200
    phrases = [
        "llámame Ana",
        "trabajo en Panadería La Espiga y suelo comprar en Cali",
        "prefiero respuestas cortas",
        "recuerda que mi cédula es 1.234.567.890",
        "recuerda que prefiero opciones compostables",
        "ten en cuenta que vendo café",
        "recuerda que abro los domingos",
        "recuerda que mi negocio es pequeño",
        long_note,
        "recuerda que trabajo con estudiantes",
    ]
    store.put(legacy_namespace(OWNER), LEGACY_KEY, {"items": phrases})

    # Act
    with capture_logs() as logs:
        profile = load(store, OWNER, now=NOW)
        again = load(store, OWNER, now=NOW)

    # Assert
    assert profile.values == {
        "nombre": "Ana",
        "empresa": "Panadería La Espiga",
        "ciudad_habitual": "Cali",
        "formato_preferido": "corto",
    }
    assert profile.notes == (
        "ten en cuenta que vendo café",
        "recuerda que abro los domingos",
        "recuerda que mi negocio es pequeño",
        long_note[:119] + "…",
        "recuerda que trabajo con estudiantes",
    )
    assert again == profile
    dropped = [log for log in logs if log["event"] == "agent.memory_migration_dropped"]
    assert len(dropped) == 1
    assert dropped[0]["count"] == 2
    assert all("1.234" not in str(log) for log in logs)
    migrated = [log for log in logs if log["event"] == "agent.memory_migrated"]
    assert len(migrated) == 1
    item = store.get(memory_namespace(OWNER), "nombre")
    assert item is not None and item.value["origin"] == "migration"
    # El formato anterior no se borra (permite volver atrás) y no se vuelve a migrar.
    assert store.get(legacy_namespace(OWNER), LEGACY_KEY) is not None


def test_la_migracion_de_un_formato_anterior_solo_sensible_no_se_repite() -> None:
    # Arrange
    store = InMemoryStore()
    store.put(legacy_namespace(OWNER), LEGACY_KEY, {"items": ["mi celular es 310 555 1234"]})

    # Act
    with capture_logs() as logs:
        first = load(store, OWNER, now=NOW)
        second = load(store, OWNER, now=NOW)

    # Assert
    assert first == second == MemoryProfile()
    assert len([log for log in logs if log["event"] == "agent.memory_migrated"]) == 1


# --- Instantánea para el estado del grafo --------------------------------------------------------


def test_la_instantanea_va_y_vuelve() -> None:
    # Arrange
    store = InMemoryStore()
    remember(store, OWNER, "nombre", "Ana", now=NOW)
    remember(store, OWNER, "nota", "prefiere compostables", now=NOW)
    profile = load(store, OWNER, now=NOW)

    # Act
    restored = MemoryProfile.from_snapshot(profile.snapshot())

    # Assert
    assert restored == profile
    assert profile.snapshot()["nombre"]["value"] == "Ana"
    assert MemoryProfile.from_snapshot({"basura": 1, "nombre": "x"}) == MemoryProfile()
