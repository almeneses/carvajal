"""Contrato de datos comerciales compartido por el chat y el Cotizador.

El contrato separa tres cosas que antes vivían mezcladas en los mismos diccionarios:

- **Producto**: la referencia vendible del catálogo (SKU, país, nombre, unidades por caja).
- **Oferta**: un precio publicado o de demostración para un producto, con su presentación,
  moneda, impuestos, fuente y fecha. Un SKU admite varias ofertas.
- **Sede**: un punto de venta del directorio, con identificador estable, ciudad, dirección,
  teléfono, fuente, fecha y precisión de geocodificación.

La disponibilidad es un dato aparte de la oferta y de la sede. Una afirmación de existencias
solo es ``verificado`` cuando trae evidencia admisible; sin evidencia queda ``por_confirmar``.
Los contactos del directorio nunca son ofertas ni prometen existencias.

El modelo es inmutable y estricto (``extra="forbid"``): una fila con campos de más o de menos
falla en vez de perderse en silencio.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "Availability",
    "AvailabilityClaim",
    "AvailabilityEvidence",
    "AvailabilityStatus",
    "Basis",
    "CityDirectory",
    "CommercialRecord",
    "ContractDocument",
    "Country",
    "Currency",
    "DeliveryTerms",
    "EvidenceKind",
    "GeoPoint",
    "MapView",
    "NormalizedPrice",
    "Offer",
    "Origin",
    "ParityReport",
    "Presentation",
    "PriceBasis",
    "Product",
    "RecordType",
    "Seller",
    "SellerKind",
    "Site",
    "SiteLink",
    "SourceRef",
    "SourceType",
    "StockEvidence",
    "TaxStatus",
]

Country = Literal["CO", "MX"]
Currency = Literal["COP", "MXN"]
Basis = Literal["unidad", "paquete", "caja"]
TaxStatus = Literal["incluido", "no_incluido", "no_indicado"]
SourceType = Literal["oficial", "vendedor", "directorio", "demo"]
SellerKind = Literal["distribuidor", "tienda_en_linea", "fabricante"]
Origin = Literal["publicada", "demo"]
GeocodePrecision = Literal["direccion", "via", "ciudad", "desconocida"]
MapMode = Literal["marcador", "zona", "sin_mapa"]
AvailabilityStatus = Literal["verificado", "por_confirmar", "no_disponible", "desconocido"]
AvailabilityClaim = Literal["disponible", "insuficiente", "agotado", "desconocido"]
EvidenceKind = Literal[
    "inventario_vendedor",
    "confirmacion_vendedor",
    "integracion_pos",
    "pedido_confirmado",
    "simulacion",
]
RecordType = Literal[
    "contacto_directorio",
    "oferta_publicada",
    "oferta_demo",
    "stock_verificado",
]

_CURRENCY_BY_COUNTRY: dict[str, str] = {"CO": "COP", "MX": "MXN"}
# Solo estas evidencias prueban existencias vigentes. Un pedido pasado no prueba stock actual.
_STOCK_EVIDENCE = frozenset({"inventario_vendedor", "confirmacion_vendedor", "integracion_pos"})
_MAX_EVIDENCE_AGE_DAYS = 7
_ZOOM = {"direccion": 17, "via": 15, "ciudad": 11}


class _Frozen(BaseModel):
    """Base común: inmutable, sin campos extra y con vacíos como ``None``."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _vacios_a_none(cls, data: object) -> object:
        if isinstance(data, dict):
            return {key: (None if value == "" else value) for key, value in data.items()}
        return data


class SourceRef(_Frozen):
    """Procedencia de una fila: quién la publica, cuándo y dónde verificarla."""

    type: SourceType
    name: str = Field(min_length=1)
    url: str | None = None
    captured_at: date
    valid_until: date | None = None
    note: str = ""

    @field_validator("url")
    @classmethod
    def _url_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("url debe ser http(s) o quedar vacía")
        return value

    @field_validator("note", mode="before")
    @classmethod
    def _nota_texto(cls, value: object) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _demo_sin_url(self) -> Self:
        if self.type == "demo" and self.url is not None:
            raise ValueError("una fuente demo no puede citar una URL como si fuera publicada")
        if self.valid_until is not None and self.valid_until < self.captured_at:
            raise ValueError("valid_until no puede ser anterior a captured_at")
        return self


class Product(_Frozen):
    """Referencia vendible. No contiene precio ni existencias."""

    sku: str = Field(min_length=1)
    country: Country
    name: str = Field(min_length=1)
    family: str = Field(min_length=1)
    units_per_box: int = Field(gt=0)
    origin: Literal["real", "demo"]
    source: SourceRef

    @model_validator(mode="after")
    def _demo_sin_ficha(self) -> Self:
        if self.origin == "demo" and self.source.type != "demo":
            raise ValueError("un producto demo exige fuente demo")
        if self.origin == "real" and self.source.type == "demo":
            raise ValueError("un producto real no puede tener fuente demo")
        return self


class Seller(_Frozen):
    """Quién vende o publica: un distribuidor, una tienda o el fabricante."""

    seller_id: str = Field(pattern=r"^[A-Z]{2}-VEND-[A-Z0-9-]+$")
    name: str = Field(min_length=1)
    country: Country
    kind: SellerKind
    source: SourceRef


class MapView(BaseModel):
    """Lo que un consumidor puede pintar según la precisión de la ubicación."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool
    mode: MapMode
    latitude: float | None = None
    longitude: float | None = None
    zoom: int = Field(default=1, ge=1, le=20)
    approximate: bool = False
    note: str = ""

    @model_validator(mode="after")
    def _coherente(self) -> Self:
        if self.mode == "sin_mapa":
            if (
                self.available
                or self.latitude is not None
                or self.longitude is not None
                or self.approximate
            ):
                raise ValueError(
                    "sin_mapa no lleva coordenadas, no se marca disponible ni aproximado"
                )
        elif not self.available or self.latitude is None or self.longitude is None:
            raise ValueError("un mapa visible exige coordenadas y available=True")
        return self


class GeoPoint(_Frozen):
    """Coordenada con la precisión declarada por el geocodificador.

    ``direccion`` es un punto de la dirección; ``via`` llega a la vía y puede caer en otro
    número; ``ciudad`` es un centroide y nunca debe presentarse como la sede exacta.
    """

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    precision: GeocodePrecision
    geocoder: str = Field(min_length=1)
    geocoded_at: date

    @model_validator(mode="after")
    def _coordenada_completa(self) -> Self:
        has_lat = self.latitude is not None
        has_lon = self.longitude is not None
        if has_lat != has_lon:
            raise ValueError("latitud y longitud van juntas")
        if self.precision == "desconocida" and has_lat:
            raise ValueError("precisión desconocida no puede traer coordenadas")
        if self.precision != "desconocida" and not has_lat:
            raise ValueError(f"precisión {self.precision} exige coordenadas")
        return self

    @property
    def map_view(self) -> MapView:
        """Vista de mapa coherente con la precisión; el centroide no es un marcador exacto."""

        if self.precision == "desconocida":
            return MapView(
                available=False,
                mode="sin_mapa",
                approximate=False,
                note="No hay coordenadas: se muestran ciudad, dirección y teléfono.",
            )
        assert self.latitude is not None and self.longitude is not None
        if self.precision == "ciudad":
            return MapView(
                available=True,
                mode="zona",
                latitude=self.latitude,
                longitude=self.longitude,
                zoom=_ZOOM["ciudad"],
                approximate=True,
                note=(
                    "Solo se conoce la ciudad: el mapa muestra una zona aproximada, "
                    "no la sede exacta."
                ),
            )
        if self.precision == "via":
            return MapView(
                available=True,
                mode="marcador",
                latitude=self.latitude,
                longitude=self.longitude,
                zoom=_ZOOM["via"],
                approximate=True,
                note="Geocodificado a la vía; el número puede no ser exacto.",
            )
        return MapView(
            available=True,
            mode="marcador",
            latitude=self.latitude,
            longitude=self.longitude,
            zoom=_ZOOM["direccion"],
            approximate=False,
            note="Coordenada de la dirección publicada.",
        )


class Site(_Frozen):
    """Sede del directorio con identificador estable e independiente del orden del archivo."""

    site_id: str = Field(pattern=r"^[A-Z]{2}-SEDE-[A-Z0-9-]+$")
    legacy_provider_id: str | None = Field(
        default=None, description="Identificador anterior de la demo (p01…p29)"
    )
    seller_id: str = Field(pattern=r"^[A-Z]{2}-VEND-[A-Z0-9-]+$")
    country: Country
    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    address: str | None = None
    phone: str = Field(min_length=1)
    source: SourceRef
    location: GeoPoint | None = None

    @property
    def map_view(self) -> MapView:
        """Mapa de la sede o ``sin_mapa`` si nunca se geocodificó."""

        if self.location is None:
            return MapView(
                available=False,
                mode="sin_mapa",
                approximate=False,
                note="Sede sin geocodificación registrada.",
            )
        return self.location.map_view


class Presentation(_Frozen):
    """Presentación comercial que se cobra: una unidad, un paquete o una caja."""

    basis: Basis
    units_in_basis: int = Field(gt=0)

    @model_validator(mode="after")
    def _unidad_es_una(self) -> Self:
        if self.basis == "unidad" and self.units_in_basis != 1:
            raise ValueError("la presentación por unidad exige units_in_basis=1")
        return self

    @property
    def label(self) -> str:
        """Etiqueta legible, por ejemplo ``paquete de 50``."""

        return f"{self.basis} de {self.units_in_basis}"


class PriceBasis(_Frozen):
    """Importe de una presentación concreta, publicada o derivada.

    ``Offer.presentation`` es la presentación comercial que cobra el vendedor; ``normalized``
    es una vista adicional a caja cuando la caja contiene un número entero de presentaciones.
    """

    basis: Basis
    units_in_basis: int = Field(gt=0)
    amount: Decimal = Field(gt=0)


class NormalizedPrice(_Frozen):
    """Vista a caja de una oferta; el esquema no admite otra base."""

    basis: Literal["caja"] = "caja"
    units_in_basis: int = Field(gt=0)
    amount: Decimal = Field(gt=0)


class DeliveryTerms(_Frozen):
    """Condiciones logísticas de la oferta, opcionales y marcadas como simuladas.

    El contrato de producto/oferta/sede no las exige, pero el Cotizador ya las usa. Se
    conservan para que una migración no pierda plazo ni costo de envío.
    """

    lead_days_min: int = Field(default=0, ge=0)
    lead_days_max: int = Field(default=0, ge=0)
    ship_cost: Decimal | None = Field(default=None, ge=0)
    simulated: bool = False
    checked_at: date | None = None
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _nota_texto(cls, value: object) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _rango(self) -> Self:
        if self.lead_days_max < self.lead_days_min:
            raise ValueError("lead_days_max no puede ser menor que lead_days_min")
        return self


class AvailabilityEvidence(_Frozen):
    """Prueba concreta de existencias, con quién la registró y de dónde salió."""

    kind: EvidenceKind
    recorded_by: str = Field(min_length=1, description="Persona o rol que la registró")
    recorded_at: date
    reference: str = Field(min_length=1, description="Correo, acta, folio o consulta")
    source_url: str | None = None
    units: int | None = Field(default=None, ge=0)
    valid_until: date | None = None
    note: str = ""

    @field_validator("source_url")
    @classmethod
    def _url_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("source_url debe ser http(s) o quedar vacía")
        return value

    @field_validator("note", mode="before")
    @classmethod
    def _nota_texto(cls, value: object) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _vigencia_coherente(self) -> Self:
        if self.valid_until is not None and self.valid_until < self.recorded_at:
            raise ValueError("valid_until no puede ser anterior a recorded_at")
        return self

    @property
    def proves_stock(self) -> bool:
        """La evidencia prueba existencias vigentes; una simulación o un pedido pasado no."""

        return self.kind in _STOCK_EVIDENCE


class Availability(_Frozen):
    """Existencia de un SKU en una sede, separada del precio y de la ficha del directorio.

    ``declared_status`` conserva lo que dijo la fuente; ``status`` es lo que el contrato
    permite afirmar. Sin evidencia admisible, ``status`` nunca es ``verificado``.
    """

    status: AvailabilityStatus
    declared_status: AvailabilityClaim | None = None
    units: int | None = Field(default=None, ge=0)
    evidence: tuple[AvailabilityEvidence, ...] = ()
    simulated: bool = False
    checked_at: date | None = None
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _nota_texto(cls, value: object) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _evidencia_obligatoria(self) -> Self:
        if self.status in {"verificado", "no_disponible"}:
            if self.simulated:
                raise ValueError("una simulación no puede afirmar disponibilidad ni agotado")
            if not any(item.proves_stock for item in self.evidence):
                raise ValueError(
                    "verificado/no_disponible exige evidencia de inventario, confirmación "
                    "del vendedor o integración POS"
                )
        if self.status == "verificado" and self.declared_status == "agotado":
            raise ValueError("verificado no puede declarar agotado")
        if self.status == "no_disponible" and self.declared_status not in {
            None,
            "agotado",
            "desconocido",
        }:
            raise ValueError("no_disponible contradice declared_status")
        if self.simulated and self.status in {"verificado", "no_disponible"}:
            raise ValueError("lo simulado se queda en por_confirmar o desconocido")
        return self

    @classmethod
    def unknown(cls, *, note: str = "") -> Self:
        """No hay afirmación de existencias."""

        return cls(status="desconocido", note=note)

    @classmethod
    def from_claim(
        cls,
        declared: AvailabilityClaim | None,
        *,
        units: int | None = None,
        simulated: bool = False,
        checked_at: date | None = None,
        evidence: tuple[AvailabilityEvidence, ...] = (),
        note: str = "",
    ) -> Self:
        """Convierte una afirmación en el estado que el contrato permite sostener.

        Una afirmación sin evidencia admisible queda ``por_confirmar``; si la fuente no
        afirmó nada, queda ``desconocido``. Lo simulado nunca asciende a ``verificado``.
        """

        if declared is None or declared == "desconocido":
            status: AvailabilityStatus = "desconocido"
        elif simulated:
            status = "por_confirmar"
        elif any(item.proves_stock for item in evidence):
            status = "no_disponible" if declared == "agotado" else "verificado"
        else:
            status = "por_confirmar"
        return cls(
            status=status,
            declared_status=declared,
            units=units,
            evidence=evidence,
            simulated=simulated,
            checked_at=checked_at,
            note=note,
        )

    @classmethod
    def verified(
        cls,
        evidence: tuple[AvailabilityEvidence, ...],
        *,
        units: int | None = None,
        declared: AvailabilityClaim = "disponible",
        checked_at: date | None = None,
        note: str = "",
    ) -> Self:
        """Disponibilidad con evidencia; el validador rechaza una lista vacía."""

        if not any(item.proves_stock for item in evidence):
            raise ValueError("verified exige evidencia que pruebe existencias")
        return cls(
            status="verificado",
            declared_status=declared,
            units=units,
            evidence=evidence,
            checked_at=checked_at or max(item.recorded_at for item in evidence),
            note=note,
        )

    def effective(self, as_of: date, *, max_age_days: int = _MAX_EVIDENCE_AGE_DAYS) -> Self:
        """Degrada a ``por_confirmar`` la evidencia vencida o demasiado vieja."""

        if self.status not in {"verificado", "no_disponible"}:
            return self
        latest = max(item.recorded_at for item in self.evidence)
        expired = any(
            item.valid_until is not None and item.valid_until < as_of for item in self.evidence
        )
        if expired or (as_of - latest).days > max_age_days:
            return self.model_validate(
                {
                    **self.model_dump(),
                    "status": "por_confirmar",
                    "note": (
                        f"La evidencia más reciente es del {latest.isoformat()} o ya venció; "
                        "hay que confirmar antes de prometer existencias."
                    ),
                }
            )
        return self


class StockEvidence(_Frozen):
    """Evidencia dirigida a un SKU de una sede concreta.

    La misma evidencia no se comparte entre sedes: el inventario de una sede no prueba el
    de otra, aunque tengan el mismo vendedor.
    """

    sku: str = Field(min_length=1)
    country: Country
    site_id: str = Field(pattern=r"^[A-Z]{2}-SEDE-[A-Z0-9-]+$")
    declared_status: AvailabilityClaim = "desconocido"
    evidence: AvailabilityEvidence


class Offer(_Frozen):
    """Precio de un producto en una presentación concreta, publicado por un vendedor.

    ``presentation`` y ``amount`` son la presentación que cobra el vendedor (la que publica
    la fuente o la que simula la demo). ``normalized`` es una vista extra a caja, solo cuando
    la caja contiene un número entero de esas presentaciones. Varias ofertas pueden compartir
    SKU y país. ``site_id`` solo se llena cuando la oferta se puede atribuir a una sede del
    directorio; una tienda en línea puede no tener sede.
    """

    offer_id: str = Field(pattern=r"^[A-Z]{2}-OFERTA-[A-Z0-9-]+$")
    sku: str = Field(min_length=1)
    country: Country
    seller_id: str = Field(pattern=r"^[A-Z]{2}-VEND-[A-Z0-9-]+$")
    site_id: str | None = Field(default=None, pattern=r"^[A-Z]{2}-SEDE-[A-Z0-9-]+$")
    city: str | None = None
    presentation: Presentation
    amount: Decimal = Field(gt=0, description="Importe de una presentación completa")
    currency: Currency
    tax_status: TaxStatus
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1, description="IVA declarado")
    tax_simulated: bool = Field(
        default=False, description="La demo declaró IVA sin fuente publicada"
    )
    origin: Origin
    source: SourceRef
    normalized: NormalizedPrice | None = None
    availability: Availability
    delivery: DeliveryTerms | None = None
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _nota_texto(cls, value: object) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _moneda_y_origen(self) -> Self:
        expected = _CURRENCY_BY_COUNTRY[self.country]
        if self.currency != expected:
            raise ValueError(
                f"una oferta de {self.country} va en {expected}, no en {self.currency}"
            )
        if self.origin == "demo" and self.source.type != "demo":
            raise ValueError("una oferta demo exige fuente demo")
        if self.origin == "publicada" and self.source.type == "demo":
            raise ValueError("una oferta publicada no puede tener fuente demo")
        if self.origin == "demo" and self.availability.status in {"verificado", "no_disponible"}:
            raise ValueError("una oferta demo no puede afirmar existencias verificadas")
        if self.tax_simulated and self.origin != "demo":
            raise ValueError("solo una oferta demo puede marcar el IVA como simulado")
        return self

    @model_validator(mode="after")
    def _normalizacion_coherente(self) -> Self:
        if self.normalized is None:
            return self
        if self.normalized.units_in_basis % self.presentation.units_in_basis:
            raise ValueError(
                "la caja normalizada no contiene un número entero de las presentaciones"
            )
        factor = self.normalized.units_in_basis // self.presentation.units_in_basis
        expected = self.amount * factor
        if self.normalized.amount != expected:
            raise ValueError(
                f"importe normalizado {self.normalized.amount} no coincide con "
                f"{self.amount} * {factor} = {expected}"
            )
        return self

    @property
    def published(self) -> bool:
        """El precio lo publica una fuente real (no la demo)."""

        return self.origin == "publicada"


class CommercialRecord(BaseModel):
    """Registro que consumen chat y Cotizador con un discriminador estable.

    Los cuatro tipos comparten los mismos campos. Un consumidor decide con ``record_type``
    y nunca tiene que adivinar si un precio es una oferta, si un contacto es una sede que
    vende el SKU o si una existencia está verificada.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    record_type: RecordType
    record_id: str = Field(min_length=1)
    country: Country
    sku: str | None = None
    seller: Seller
    site: Site | None = None
    offer: Offer | None = None
    availability: Availability | None = None
    source: SourceRef
    captured_at: date
    map: MapView

    @model_validator(mode="after")
    def _coherente(self) -> Self:
        if self.record_type == "contacto_directorio":
            if self.site is None:
                raise ValueError("un contacto del directorio exige sede")
            if self.sku is not None or self.offer is not None or self.availability is not None:
                raise ValueError("un contacto del directorio no lleva SKU, oferta ni existencias")
            if self.source != self.site.source or self.captured_at != self.site.source.captured_at:
                raise ValueError("la procedencia del contacto debe ser la de su sede")
            if self.map != self.site.map_view:
                raise ValueError("el mapa del contacto debe derivarse de su sede")
            return self

        if self.record_type in {"oferta_publicada", "oferta_demo"}:
            if self.offer is None or self.availability is None:
                raise ValueError("una oferta exige oferta y disponibilidad")
            if self.sku != self.offer.sku or self.country != self.offer.country:
                raise ValueError("SKU/país del registro no coinciden con la oferta")
            if self.seller.seller_id != self.offer.seller_id:
                raise ValueError("el vendedor del registro no coincide con la oferta")
            if (
                self.source != self.offer.source
                or self.captured_at != self.offer.source.captured_at
            ):
                raise ValueError("la procedencia del registro debe ser la de la oferta")
            expected = "oferta_demo" if self.offer.origin == "demo" else "oferta_publicada"
            if self.record_type != expected:
                raise ValueError(f"el tipo de registro debe ser {expected}")
            if self.offer.site_id is not None and (
                self.site is None or self.site.site_id != self.offer.site_id
            ):
                raise ValueError("la oferta cita una sede que no está en el registro")
            if self.site is not None and self.map != self.site.map_view:
                raise ValueError("el mapa de la oferta debe derivarse de su sede")
            return self

        if self.record_type == "stock_verificado":
            if self.site is None or self.sku is None or self.availability is None:
                raise ValueError("stock verificado exige sede, SKU y disponibilidad")
            if self.availability.status != "verificado":
                raise ValueError("stock_verificado exige availability.status=verificado")
            if self.seller.seller_id != self.site.seller_id:
                raise ValueError("el vendedor del stock debe ser el de la sede")
            if self.map != self.site.map_view:
                raise ValueError("el mapa del stock debe derivarse de su sede")
            if self.offer is not None:
                if self.offer.sku != self.sku or self.offer.country != self.country:
                    raise ValueError("la oferta del stock no corresponde al SKU")
                if self.offer.site_id not in {None, self.site.site_id}:
                    raise ValueError("la oferta del stock pertenece a otra sede")
            return self

        raise ValueError(f"record_type desconocido: {self.record_type}")


class SiteLink(BaseModel):
    """Vínculo entre una sede y un SKU, con la fuerza de la relación."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str
    sku: str
    relationship: Literal["oferta_publicada", "oferta_demo", "stock_verificado"]
    offer_ids: tuple[str, ...] = ()
    availability: Availability


class CityDirectory(BaseModel):
    """Consulta de sedes por ciudad, independiente de precio y cantidad.

    Siempre incluye un ``contacto_directorio`` por sede, aunque no haya ofertas ni stock.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    city: str
    cities: tuple[str, ...]
    records: tuple[CommercialRecord, ...]
    counts: dict[str, int] = Field(default_factory=dict)

    def of_type(self, record_type: RecordType) -> tuple[CommercialRecord, ...]:
        """Registros de un tipo, en el orden estable de la consulta."""

        return tuple(item for item in self.records if item.record_type == record_type)


class ContractDocument(BaseModel):
    """Documento completo del contrato para esquema, ejemplos e intercambio.

    Un consumidor puede recibir solo ``records``; las entidades van completas para que la
    contraparte pueda resolver cualquier referencia sin otra consulta.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    query: dict[str, Any] | None = Field(
        default=None, description="Consulta que produjo el documento, si aplica"
    )
    products: tuple[Product, ...] = ()
    sellers: tuple[Seller, ...] = ()
    sites: tuple[Site, ...] = ()
    offers: tuple[Offer, ...] = ()
    evidence: tuple[StockEvidence, ...] = ()
    records: tuple[CommercialRecord, ...] = ()
    directory: "CityDirectory | None" = None
    parity: "ParityReport | None" = None


class ParityReport(BaseModel):
    """Comparación de la lectura de precios de México antes de retirarla."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    country: Literal["MX"] = "MX"
    audited_references: int
    inline_offers: int
    contract_offers: int
    missing_skus: tuple[str, ...] = ()
    extra_skus: tuple[str, ...] = ()
    duplicate_skus: tuple[str, ...] = ()
    currency_mismatches: tuple[str, ...] = ()
    presentation_mismatches: tuple[str, ...] = ()
    amount_mismatches: tuple[str, ...] = ()
    tax_mismatches: tuple[str, ...] = ()
    source_mismatches: tuple[str, ...] = ()
    date_mismatches: tuple[str, ...] = ()
    valid_until_mismatches: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """La lectura inline se puede retirar sin perder cobertura, moneda ni presentación."""

        return self.audited_references == self.inline_offers == self.contract_offers and not (
            self.missing_skus
            or self.extra_skus
            or self.duplicate_skus
            or self.currency_mismatches
            or self.presentation_mismatches
            or self.amount_mismatches
            or self.tax_mismatches
            or self.source_mismatches
            or self.date_mismatches
            or self.valid_until_mismatches
        )


ContractDocument.model_rebuild()


def evidence_age_limit() -> timedelta:
    """Antigüedad máxima admitida por defecto para una evidencia de existencias."""

    return timedelta(days=_MAX_EVIDENCE_AGE_DAYS)
