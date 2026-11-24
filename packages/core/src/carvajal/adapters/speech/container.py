"""Duración de un audio por la cabecera de su contenedor, sin decodificarlo (spec 047).

OpenAI cobra la transcripción por duración, y el navegador corta el dictado a
``VOICE_MAX_AUDIO_SECONDS``; un cliente que llama a la API directamente no tiene ese corte.
:func:`audio_duration_s` lee la duración que declaran los contenedores que produce
``MediaRecorder`` para rechazar un audio largo **antes** de llamar a OpenAI:

- **Ogg** (Opus o Vorbis): la mayor posición de granulo de las páginas, menos el
  ``pre-skip`` de Opus, a 48 kHz (Opus) o a la frecuencia de la cabecera (Vorbis).
- **WebM/Matroska**: el mayor instante de bloque (``Cluster/Timecode`` más el desfase del
  ``SimpleBlock``) o la ``Duration`` de ``Info``, en unidades de ``TimecodeScale``. Admite
  elementos de tamaño desconocido, como los que escribe ``MediaRecorder`` en vivo.
- **MP4**: ``mvhd``, ``mehd`` y ``mdhd``, y para MP4 fragmentado el final del último
  fragmento (``tfdt`` más las duraciones de ``trun``).

Es una comprobación contra clientes honestos o mal configurados, no contra uno que falsee
la cabecera: el tope de bytes (``VOICE_MAX_UPLOAD_BYTES``) sigue siendo el límite duro.
Devuelve ``None`` si no reconoce el contenedor o no encuentra la duración; nunca lanza.
"""

import struct
from collections.abc import Callable
from typing import Final

__all__ = ["audio_duration_s"]

_OPUS_RATE: Final = 48_000
_EBML_CONTAINERS: Final = frozenset(
    {
        0x18538067,  # Segment
        0x1549A966,  # Info
        0x1F43B675,  # Cluster
        0xA0,  # BlockGroup
    }
)
_EBML_CLUSTER: Final = 0x1F43B675
_EBML_TIMECODE_SCALE: Final = 0x2AD7B1
_EBML_DURATION: Final = 0x4489
_EBML_CLUSTER_TIMECODE: Final = 0xE7
_EBML_BLOCKS: Final = frozenset({0xA3, 0xA1})  # SimpleBlock y Block
_MP4_CONTAINERS: Final = frozenset({b"moov", b"trak", b"mdia", b"mvex", b"moof", b"traf"})
# Un audio de dictado no tiene más que unos miles de cajas o páginas; el tope evita que un
# cuerpo patológico haga girar el bucle.
_MAX_ELEMENTS: Final = 200_000


def audio_duration_s(audio: bytes, media_type: str) -> float | None:
    """Segundos que declara el contenedor de ``audio``, o ``None`` si no se pueden leer."""

    base = media_type.split(";", 1)[0].strip().lower()
    parser: Callable[[bytes], float | None] | None = {
        "audio/ogg": _ogg_duration,
        "audio/webm": _webm_duration,
        "audio/mp4": _mp4_duration,
    }.get(base)
    if parser is None:
        return None
    try:
        seconds = parser(audio)
    except (IndexError, ValueError, struct.error, OverflowError):
        return None
    if seconds is None or seconds != seconds or seconds < 0:  # NaN o negativo
        return None
    return seconds


# --- Ogg ---------------------------------------------------------------------------------


def _ogg_duration(audio: bytes) -> float | None:
    position = 0
    rate = 0
    pre_skip = 0
    granule_max = -1
    for _ in range(_MAX_ELEMENTS):
        if audio[position : position + 4] != b"OggS" or position + 27 > len(audio):
            break
        granule = struct.unpack_from("<q", audio, position + 6)[0]
        segments = audio[position + 26]
        table_end = position + 27 + segments
        if table_end > len(audio):
            break
        body = sum(audio[position + 27 : table_end])
        payload = audio[table_end : table_end + body]
        if rate == 0 and payload.startswith(b"OpusHead") and len(payload) >= 12:
            rate = _OPUS_RATE
            pre_skip = struct.unpack_from("<H", payload, 10)[0]
        elif rate == 0 and payload.startswith(b"\x01vorbis") and len(payload) >= 16:
            rate = struct.unpack_from("<I", payload, 12)[0]
        if granule >= 0:
            granule_max = max(granule_max, granule)
        position = table_end + body
    if rate <= 0 or granule_max < 0:
        return None
    return max(0, granule_max - pre_skip) / rate


# --- WebM / Matroska ---------------------------------------------------------------------


def _ebml_id(audio: bytes, position: int) -> tuple[int, int] | None:
    first = audio[position]
    for length in range(1, 5):
        if first & (0x80 >> (length - 1)):
            if position + length > len(audio):
                return None
            return int.from_bytes(audio[position : position + length], "big"), length
    return None


def _ebml_size(audio: bytes, position: int) -> tuple[int | None, int] | None:
    """Tamaño EBML: ``(None, largo)`` si es desconocido (todos los bits a uno)."""

    first = audio[position]
    for length in range(1, 9):
        marker = 0x80 >> (length - 1)
        if first & marker:
            if position + length > len(audio):
                return None
            value = first & (marker - 1)
            for byte in audio[position + 1 : position + length]:
                value = (value << 8) | byte
            unknown = value == (1 << (7 * length)) - 1
            return (None if unknown else value), length
    return None


def _webm_duration(audio: bytes) -> float | None:
    position = 0
    scale = 1_000_000  # nanosegundos por unidad (valor por defecto de Matroska)
    declared: float | None = None
    cluster_time: int | None = None
    ticks_max = -1
    for _ in range(_MAX_ELEMENTS):
        if position >= len(audio):
            break
        element = _ebml_id(audio, position)
        if element is None:
            break
        identifier, id_length = element
        size_info = _ebml_size(audio, position + id_length)
        if size_info is None:
            break
        size, size_length = size_info
        data = position + id_length + size_length
        if identifier in _EBML_CONTAINERS:
            # Se entra en el contenedor: sus hijos siguen en orden (también con tamaño
            # desconocido, como los ``Cluster`` de ``MediaRecorder``).
            if identifier == _EBML_CLUSTER:
                cluster_time = None
            position = data
            continue
        if size is None:
            break
        end = data + size
        if end > len(audio):
            break
        value = audio[data:end]
        if identifier == _EBML_TIMECODE_SCALE and value:
            scale = int.from_bytes(value, "big") or scale
        elif identifier == _EBML_DURATION and size in {4, 8}:
            declared = struct.unpack(">f" if size == 4 else ">d", value)[0]
        elif identifier == _EBML_CLUSTER_TIMECODE:
            cluster_time = int.from_bytes(value, "big")
        elif identifier in _EBML_BLOCKS and cluster_time is not None:
            track = _ebml_size(audio, data)
            if track is not None and data + track[1] + 2 <= end:
                offset = struct.unpack_from(">h", audio, data + track[1])[0]
                ticks_max = max(ticks_max, cluster_time + offset)
        position = end
    candidates = [ticks_max * scale] if ticks_max >= 0 else []
    if declared is not None and declared == declared and declared >= 0:
        candidates.append(declared * scale)
    if not candidates:
        return None
    return max(candidates) / 1e9


# --- MP4 ---------------------------------------------------------------------------------


def _full_box_time(box: bytes, *, skip_v0: int, skip_v1: int) -> tuple[int, int]:
    """``(escala, duración)`` de ``mvhd`` o ``mdhd`` según su versión."""

    if box[0] == 1:
        return struct.unpack_from(">IQ", box, 4 + skip_v1)
    return struct.unpack_from(">II", box, 4 + skip_v0)


class _Mp4Scan:
    """Recorre las cajas de un MP4 y anota las duraciones que declaran."""

    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.budget = _MAX_ELEMENTS
        self.movie_scale = 0
        self.track_scale = 0
        self.seconds: list[float] = []
        self.fragment_ends: list[int] = []
        self.base = 0
        self.default_duration = 0

    def duration(self) -> float | None:
        self.walk(0, len(self.audio), 0)
        if self.fragment_ends and self.track_scale:
            self.seconds.append(max(self.fragment_ends) / self.track_scale)
        return max(self.seconds) if self.seconds else None

    def walk(self, start: int, stop: int, depth: int) -> None:
        audio = self.audio
        position = start
        while position + 8 <= stop and self.budget > 0:
            self.budget -= 1
            size, kind = struct.unpack_from(">I4s", audio, position)
            header = 8
            if size == 1:
                if position + 16 > stop:
                    return
                size = struct.unpack_from(">Q", audio, position + 8)[0]
                header = 16
            elif size == 0:
                size = stop - position
            if size < header:
                return
            end = min(position + size, stop)
            if kind in _MP4_CONTAINERS and depth < 8:
                if kind == b"traf":
                    self.base = 0
                    self.default_duration = 0
                self.walk(position + header, end, depth + 1)
            else:
                self.box(kind, audio[position + header : end])
            position += size

    def box(self, kind: bytes, body: bytes) -> None:
        if kind == b"mvhd":
            self.movie_scale, duration = _full_box_time(body, skip_v0=8, skip_v1=16)
            if self.movie_scale:
                self.seconds.append(duration / self.movie_scale)
        elif kind == b"mdhd":
            self.track_scale, duration = _full_box_time(body, skip_v0=8, skip_v1=16)
            if self.track_scale:
                self.seconds.append(duration / self.track_scale)
        elif kind == b"mehd" and self.movie_scale:
            fragment = struct.unpack_from(">Q" if body[0] == 1 else ">I", body, 4)[0]
            self.seconds.append(fragment / self.movie_scale)
        elif kind == b"tfhd":
            flags = int.from_bytes(body[1:4], "big")
            # Versión y banderas, track_ID y, si están, base_data_offset y sample_description.
            offset = 8 + (8 if flags & 0x01 else 0) + (4 if flags & 0x02 else 0)
            if flags & 0x08:
                self.default_duration = struct.unpack_from(">I", body, offset)[0]
        elif kind == b"tfdt":
            self.base = struct.unpack_from(">Q" if body[0] == 1 else ">I", body, 4)[0]
        elif kind == b"trun":
            self.fragment_ends.append(self.base + self._run_length(body))
            self.base = self.fragment_ends[-1]

    def _run_length(self, body: bytes) -> int:
        flags = int.from_bytes(body[1:4], "big")
        count = struct.unpack_from(">I", body, 4)[0]
        if not flags & 0x100:
            return count * self.default_duration
        offset = 8 + (4 if flags & 0x01 else 0) + (4 if flags & 0x04 else 0)
        stride = sum(4 for flag in (0x100, 0x200, 0x400, 0x800) if flags & flag)
        return sum(
            struct.unpack_from(">I", body, offset + index * stride)[0] for index in range(count)
        )


def _mp4_duration(audio: bytes) -> float | None:
    return _Mp4Scan(audio).duration()
