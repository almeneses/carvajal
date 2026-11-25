"""Preparación de la foto (spec 051, FR-002 y FR-003): enderezar, escalar y quitar metadatos."""

import io

import pytest
from PIL import Image

from carvajal.food.photo import (
    MAX_PHOTO_BYTES,
    MAX_SIDE_PX,
    PhotoRejected,
    prepare_photo,
)


def _jpeg(size: tuple[int, int], *, exif: Image.Exif | None = None) -> bytes:
    out = io.BytesIO()
    image = Image.new("RGB", size, (200, 120, 40))
    if exif is None:
        image.save(out, "JPEG")
    else:
        image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def _camera_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "Fabricante"  # Make
    exif[0x0110] = "Modelo"  # Model
    exif[0x0112] = 6  # Orientation: girar 90° a la derecha
    exif[0x0132] = "2026:11:24 10:00:00"  # DateTime
    return exif


def test_quita_los_metadatos_y_endereza_la_foto() -> None:
    # Arrange
    raw = _jpeg((800, 600), exif=_camera_exif())
    assert Image.open(io.BytesIO(raw)).getexif()  # la original sí los trae

    # Act
    photo = prepare_photo(raw)

    # Assert
    result = Image.open(io.BytesIO(photo.data))
    assert result.format == "JPEG"
    assert not result.getexif()
    assert "icc_profile" not in result.info
    assert (photo.width, photo.height) == (600, 800) == result.size


def test_escala_la_foto_grande_al_lado_maximo() -> None:
    # Act
    photo = prepare_photo(_jpeg((4000, 3000)))

    # Assert
    assert max(photo.width, photo.height) == MAX_SIDE_PX
    assert (photo.width, photo.height) == (1568, 1176)


def test_acepta_png_con_transparencia_y_la_convierte_a_jpeg() -> None:
    # Arrange
    out = io.BytesIO()
    Image.new("RGBA", (600, 600), (0, 0, 0, 0)).save(out, "PNG")

    # Act
    photo = prepare_photo(out.getvalue())

    # Assert
    assert Image.open(io.BytesIO(photo.data)).format == "JPEG"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (_jpeg((200, 600)), "pequena"),
        (_jpeg((6000, 300)), "alargada"),
        (b"no soy una imagen", "ilegible"),
        (b"", "ilegible"),
        (b"\x00" * (MAX_PHOTO_BYTES + 1), "grande"),
    ],
)
def test_rechaza_fotos_que_no_se_pueden_analizar(raw: bytes, reason: str) -> None:
    # Act / Assert
    with pytest.raises(PhotoRejected) as error:
        prepare_photo(raw)
    assert error.value.reason == reason
    assert error.value.message


def test_rechaza_formatos_distintos_de_jpeg_y_png() -> None:
    # Arrange
    out = io.BytesIO()
    Image.new("RGB", (600, 600)).save(out, "GIF")

    # Act / Assert
    with pytest.raises(PhotoRejected) as error:
        prepare_photo(out.getvalue())
    assert error.value.reason == "formato"


def test_el_repr_no_incluye_los_bytes() -> None:
    # Act
    photo = prepare_photo(_jpeg((600, 600)))

    # Assert
    assert repr(photo).startswith("PreparedPhoto(600x600")
    assert "\\x" not in repr(photo)
