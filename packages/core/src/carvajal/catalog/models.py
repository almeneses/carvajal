"""Modelos del catálogo curado de empaques (specs 036 y 039).

Cada fila conserva su procedencia (``source_url`` y ``captured_at``): el asistente
cita la ficha de la que salió el dato y presenta los precios como referencia fechada.
Las celdas vacías del CSV llegan como ``None``.

Spec 039: un precio deja de ser un atributo único de la referencia y pasa a ser una
**oferta por fuente** (``ReferencePrice``): quién lo publica, cuándo y cómo trata el IVA.
El precio inline de ``referencias.csv`` (tienda de México) sigue siendo válido y se
expone como la oferta oficial de esa referencia.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ADAPTED_PREFIX",
    "Distributor",
    "Family",
    "MaterialRule",
    "PriceSource",
    "Reference",
    "ReferencePrice",
    "TaxStatus",
]

Family = Literal["vaso", "tapa", "contenedor", "charola", "cubierto", "envase", "plato"]
Country = Literal["MX", "CO"]
Currency = Literal["MXN", "COP"]
TaxStatus = Literal["incluido", "no_incluido", "no_indicado"]
# ``sistema``: precio calculado por el proyecto (conversión desde México); ver ``conversion``.
PriceSource = Literal["oficial", "tercero", "sistema"]
# SKU de las referencias de México copiadas a Colombia (``carvajal.catalog.conversion``).
ADAPTED_PREFIX = "CO-MX-"
_CURRENCY_BY_COUNTRY: dict[str, str] = {"MX": "MXN", "CO": "COP"}


class _Row(BaseModel):
    """Base común: inmutable, sin campos extra y con vacíos como ``None``."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _vacios_a_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: (None if value == "" else value) for key, value in data.items()}
        return data


class _Sourced(_Row):
    """Procedencia obligatoria de toda fila (Principio III)."""

    source_url: str
    captured_at: date

    @field_validator("source_url")
    @classmethod
    def _url_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("source_url debe ser una URL http(s)")
        return value


_OZ = re.compile(r"\b(\d+(?:[.,]\d+)?)[_ ]?oz\b", re.IGNORECASE)
_MM = re.compile(r"\b(\d+(?:[.,]\d+)?)Mm\b")
_BY = re.compile(r"\b(\d+)X(\d+)\b")


class Reference(_Sourced):
    """Referencia vendible de Carvajal Empaques."""

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    family: Family
    use: str = Field(min_length=1, description="Usos separados por «|»")
    capacity_oz: float | None = Field(default=None, gt=0)
    diameter_mm: float | None = Field(default=None, gt=0)
    material: str = Field(min_length=1)
    brand: str | None = None
    units_per_pack: int | None = Field(default=None, gt=0)
    packs_per_box: int | None = Field(default=None, gt=0)
    units_per_box: int = Field(gt=0)
    temp_min_c: float | None = Field(default=None, description="Temperatura declarada en la ficha")
    temp_max_c: float | None = None
    price: Decimal | None = Field(default=None, ge=0, description="Precio por caja")
    currency: Currency | None = None
    tax_included: bool | None = None
    price_valid_until: date | None = None
    country: Country

    @field_validator("material")
    @classmethod
    def _material_mayusculas(cls, value: str) -> str:
        return value.upper()

    @field_validator("name")
    @classmethod
    def _nombre_legible(cls, value: str) -> str:
        """Medidas del catálogo de México en forma legible: «8_Oz 80Mm … 20X50» → «8 oz 80 mm
        … 20x50». Los códigos de modelo («066_1P») no se tocan."""

        value = _OZ.sub(r"\1 oz", value)
        value = _MM.sub(r"\1 mm", value)
        return _BY.sub(r"\1x\2", value)

    @property
    def uses(self) -> tuple[str, ...]:
        """Usos normalizados de la referencia."""

        return tuple(part.strip() for part in self.use.split("|") if part.strip())

    @property
    def is_adapted(self) -> bool:
        """Referencia copiada del catálogo de México, sin venta publicada en Colombia."""

        return self.country == "CO" and self.sku.startswith(ADAPTED_PREFIX)

    @model_validator(mode="after")
    def _precio_coherente(self) -> Self:
        if self.price is not None and (self.currency is None or self.tax_included is None):
            raise ValueError("un precio exige moneda (currency) y nota de impuestos (tax_included)")
        if self.price is None and self.currency is not None:
            raise ValueError("currency sin price: deja ambos vacíos o completa el precio")
        if self.units_per_pack and self.packs_per_box:
            expected = self.units_per_pack * self.packs_per_box
            if expected != self.units_per_box:
                raise ValueError(
                    f"units_per_box={self.units_per_box} no coincide con "
                    f"units_per_pack * packs_per_box = {expected}"
                )
        return self

    @model_validator(mode="after")
    def _temperatura_coherente(self) -> Self:
        if (self.temp_min_c is None) != (self.temp_max_c is None):
            raise ValueError("la temperatura de la ficha exige mínimo y máximo")
        if (
            self.temp_min_c is not None
            and self.temp_max_c is not None
            and self.temp_min_c >= self.temp_max_c
        ):
            raise ValueError("temp_min_c debe ser menor que temp_max_c")
        return self

    @model_validator(mode="after")
    def _empaque_completo(self) -> Self:
        if (self.units_per_pack is None) != (self.packs_per_box is None):
            raise ValueError(
                "units_per_pack y packs_per_box van juntos: "
                "deja solo units_per_box o completa el paquete y la caja"
            )
        return self


class MaterialRule(_Sourced):
    """Regla técnica de la guía de materiales."""

    material: str = Field(min_length=1)
    temp_min_c: float
    temp_max_c: float
    microwave: bool
    refrigeration: bool
    freezing: bool
    hot_content: bool
    recycling_code: str | None = None
    applications: str | None = None

    @field_validator("material")
    @classmethod
    def _material_mayusculas(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _rango_valido(self) -> Self:
        if self.temp_min_c >= self.temp_max_c:
            raise ValueError("temp_min_c debe ser menor que temp_max_c")
        return self


class Distributor(_Sourced):
    """Distribuidor publicado en el directorio de Carvajal Empaques."""

    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    address: str | None = None
    phone: str | None = None


class ReferencePrice(_Sourced):
    """Precio de referencia de una referencia, publicado por una fuente concreta (spec 039).

    ``amount`` es siempre por caja (``units_per_box`` de la referencia): la normalización
    desde unidad o paquete ocurre antes, en ``carvajal catalog-prices``. Los precios publicados
    no se estiman ni se promedian entre fuentes (FR-006). Un precio convertido desde México
    lleva ``source_type="sistema"`` y nunca se muestra como publicado en Colombia.
    """

    sku: str = Field(min_length=1)
    country: Country
    amount: Decimal = Field(ge=0, description="Precio por caja")
    currency: Currency
    tax_status: TaxStatus
    source_type: PriceSource
    source_name: str = Field(min_length=1)
    source_city: str | None = Field(default=None, description="Ciudad del distribuidor, si aplica")
    valid_until: date | None = None

    @model_validator(mode="after")
    def _moneda_del_pais(self) -> Self:
        expected = _CURRENCY_BY_COUNTRY[self.country]
        if self.currency != expected:
            raise ValueError(
                f"un precio de {self.country} va en {expected}; no se convierte de {self.currency}"
            )
        return self

    @property
    def tax_included(self) -> bool | None:
        """``True``/``False`` si la fuente lo declara; ``None`` si no lo indica."""

        return {"incluido": True, "no_incluido": False}.get(self.tax_status)
