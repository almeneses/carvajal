"""Modelos del catálogo curado de empaques (spec 036).

Cada fila conserva su procedencia (``source_url`` y ``captured_at``): el asistente
cita la ficha de la que salió el dato y presenta los precios como referencia fechada.
Las celdas vacías del CSV llegan como ``None``.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["Distributor", "Family", "MaterialRule", "Reference"]

Family = Literal["vaso", "tapa", "contenedor", "charola", "cubierto", "envase", "plato"]
Country = Literal["MX", "CO"]
Currency = Literal["MXN", "COP"]


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

    @property
    def uses(self) -> tuple[str, ...]:
        """Usos normalizados de la referencia."""

        return tuple(part.strip() for part in self.use.split("|") if part.strip())

    @model_validator(mode="after")
    def _precio_coherente(self) -> Self:
        if self.price is not None and (self.currency is None or self.tax_included is None):
            raise ValueError("un precio exige moneda (currency) y nota de impuestos (tax_included)")
        if self.price is None and self.currency is not None:
            raise ValueError("currency sin price: deja ambos vacíos o completa el precio")
        if (self.temp_min_c is None) != (self.temp_max_c is None):
            raise ValueError("la temperatura de la ficha exige mínimo y máximo")
        if (
            self.temp_min_c is not None
            and self.temp_max_c is not None
            and self.temp_min_c >= self.temp_max_c
        ):
            raise ValueError("temp_min_c debe ser menor que temp_max_c")
        if self.units_per_pack and self.packs_per_box:
            expected = self.units_per_pack * self.packs_per_box
            if expected != self.units_per_box:
                raise ValueError(
                    f"units_per_box={self.units_per_box} no coincide con "
                    f"units_per_pack * packs_per_box = {expected}"
                )
        return self


class MaterialRule(_Row):
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
