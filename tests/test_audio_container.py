"""Duración declarada por el contenedor del audio dictado (spec 047).

Los audios se construyen aquí byte a byte (Ogg Opus, WebM con tamaños conocidos y
desconocidos como los de ``MediaRecorder``, MP4 normal y fragmentado): sin ffmpeg ni
archivos binarios en el repositorio.
"""

import struct

import pytest

from carvajal.adapters.speech.container import audio_duration_s

# --- constructores mínimos -----------------------------------------------------------------


def _ogg_page(granule: int, payload: bytes) -> bytes:
    lacing = bytes([255] * (len(payload) // 255) + [len(payload) % 255])
    header = b"OggS" + bytes([0, 0]) + struct.pack("<qIII", granule, 1, 0, 0)
    return header + bytes([len(lacing)]) + lacing + payload


def _ogg_opus(seconds: float, pre_skip: int = 312) -> bytes:
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", pre_skip, 48_000, 0, 0)
    return (
        _ogg_page(0, head)
        + _ogg_page(0, b"OpusTags" + b"\x00" * 8)
        + _ogg_page(int(seconds * 48_000 / 2) + pre_skip, b"\x00" * 300)
        + _ogg_page(int(seconds * 48_000) + pre_skip, b"\x00" * 300)
    )


def _vint_size(value: int | None) -> bytes:
    """Tamaño EBML en 8 bytes; ``None`` es el tamaño desconocido de ``MediaRecorder``."""

    if value is None:
        return b"\x01\xff\xff\xff\xff\xff\xff\xff"
    return (0x01 << 56 | value).to_bytes(8, "big")


def _element(identifier: int, body: bytes, *, unknown: bool = False) -> bytes:
    raw_id = identifier.to_bytes((identifier.bit_length() + 7) // 8, "big")
    return raw_id + _vint_size(None if unknown else len(body)) + body


def _simple_block(offset_ms: int) -> bytes:
    return _element(0xA3, bytes([0x81]) + struct.pack(">h", offset_ms) + b"\x80" + b"\x00" * 20)


def _cluster(start_ms: int, offsets: list[int], *, unknown: bool) -> bytes:
    body = _element(0xE7, start_ms.to_bytes(4, "big"))
    body += b"".join(_simple_block(offset) for offset in offsets)
    return _element(0x1F43B675, body, unknown=unknown)


def _webm(seconds: float, *, unknown: bool, with_duration: bool = False) -> bytes:
    ebml = _element(0x1A45DFA3, _element(0x4282, b"webm"))
    info_body = _element(0x2AD7B1, (1_000_000).to_bytes(3, "big"))
    if with_duration:
        info_body += _element(0x4489, struct.pack(">d", seconds * 1000))
    clusters = b""
    total_ms = int(seconds * 1000)
    for start in range(0, total_ms, 5000):
        last = min(4980, total_ms - start)
        clusters += _cluster(start, [0, last // 2, last], unknown=unknown)
    segment = _element(0x1549A966, info_body) + _element(0x1654AE6B, b"\x00" * 10) + clusters
    return ebml + _element(0x18538067, segment, unknown=unknown)


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def _mp4(seconds: float) -> bytes:
    mvhd = _box(b"mvhd", bytes(4) + struct.pack(">IIII", 0, 0, 1000, int(seconds * 1000)))
    mdhd = _box(b"mdhd", bytes(4) + struct.pack(">IIII", 0, 0, 48_000, int(seconds * 48_000)))
    moov = _box(b"moov", mvhd + _box(b"trak", _box(b"mdia", mdhd)))
    return _box(b"ftyp", b"mp42" + bytes(4)) + moov + _box(b"mdat", b"\x00" * 200)


def _fragmented_mp4(seconds: float, fragment_s: float = 1.0) -> bytes:
    mvhd = _box(b"mvhd", bytes(4) + struct.pack(">IIII", 0, 0, 1000, 0))
    mdhd = _box(b"mdhd", bytes(4) + struct.pack(">IIII", 0, 0, 48_000, 0))
    moov = _box(b"moov", mvhd + _box(b"trak", _box(b"mdia", mdhd)))
    fragments = b""
    samples = int(fragment_s * 48_000 / 1024)
    for index in range(int(seconds / fragment_s)):
        tfhd = _box(b"tfhd", struct.pack(">II", 0x000008, 1) + struct.pack(">I", 1024))
        tfdt = _box(b"tfdt", struct.pack(">II", 0, index * samples * 1024))
        trun = _box(b"trun", struct.pack(">II", 0, samples))
        fragments += _box(b"moof", _box(b"traf", tfhd + tfdt + trun)) + _box(b"mdat", b"\x00")
    return _box(b"ftyp", b"iso5" + bytes(4)) + moov + fragments


# --- pruebas -------------------------------------------------------------------------------


@pytest.mark.parametrize("seconds", [3.0, 75.0])
def test_ogg_opus_declara_su_duracion(seconds: float) -> None:
    # Act
    duration = audio_duration_s(_ogg_opus(seconds), "audio/ogg;codecs=opus")

    # Assert
    assert duration == pytest.approx(seconds, abs=0.01)


@pytest.mark.parametrize("unknown", [False, True])
def test_webm_declara_su_duracion_con_tamanos_conocidos_o_desconocidos(unknown: bool) -> None:
    # Act
    duration = audio_duration_s(_webm(75, unknown=unknown), "audio/webm;codecs=opus")

    # Assert: el último bloque marca el final, como en lo que graba ``MediaRecorder``.
    assert duration == pytest.approx(75, abs=0.05)


def test_webm_usa_la_duracion_de_info_si_es_mayor() -> None:
    # Arrange: la cabecera dice 90 s aunque los bloques lleguen a 10 s.
    audio = _webm(10, unknown=False, with_duration=True)
    audio = audio.replace(struct.pack(">d", 10_000.0), struct.pack(">d", 90_000.0))

    # Act / Assert
    assert audio_duration_s(audio, "audio/webm") == pytest.approx(90)


def test_mp4_declara_su_duracion() -> None:
    # Act / Assert
    assert audio_duration_s(_mp4(75), "audio/mp4") == pytest.approx(75)


def test_mp4_fragmentado_suma_sus_fragmentos() -> None:
    # Act
    duration = audio_duration_s(_fragmented_mp4(70), "audio/mp4")

    # Assert
    assert duration is not None and duration > 65


@pytest.mark.parametrize(
    ("audio", "media_type"),
    [
        (b"\x1a\x45\xdf\xa3" + b"\x00" * 600, "audio/webm"),
        (b"OggS" + b"\x00" * 600, "audio/ogg"),
        (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 600, "audio/mp4"),
        (b"\xff" * 600, "audio/webm"),
        (b"", "audio/ogg"),
        (b"ID3" + b"\x00" * 600, "audio/mpeg"),
    ],
)
def test_sin_duracion_legible_devuelve_none_sin_lanzar(audio: bytes, media_type: str) -> None:
    # Act / Assert
    assert audio_duration_s(audio, media_type) is None


def test_un_audio_truncado_devuelve_lo_que_alcanza_a_leer() -> None:
    # Arrange
    audio = _webm(30, unknown=True)

    # Act
    duration = audio_duration_s(audio[: len(audio) // 2], "audio/webm")

    # Assert: una cota inferior, nunca una excepción.
    assert duration is not None and 0 < duration < 30


def test_un_mp4_con_cajas_de_tamano_absurdo_no_lanza() -> None:
    # Arrange: ``trun`` dice tener 4.000 millones de muestras con duración propia.
    trun = _box(b"trun", struct.pack(">II", 0x000100, 4_000_000_000) + b"\x00" * 16)
    audio = _box(b"ftyp", b"iso5" + bytes(4)) + _box(b"moof", _box(b"traf", trun))

    # Act / Assert
    assert audio_duration_s(audio, "audio/mp4") is None
