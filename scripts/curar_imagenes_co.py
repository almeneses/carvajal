"""Curación de fotos del catálogo de Colombia: una imagen por SKU para cotizaciones y PDF.

Para cada referencia CO (por defecto, solo las que tienen precio) se toma la foto principal
de su ficha y se guarda reducida en ``data/catalog/empaques/imagenes/{sku}.jpg``, con su
procedencia en ``imagenes.csv``:

- **Fichas oficiales** (``colombia.carvajalempaques.com``): la imagen ``og:image`` de la
  ficha y, si no hay, la primera foto de la galería en ``/wp-content/uploads/``. Es la
  foto de la línea de producto: las tallas de una misma línea comparten foto.
- **Copias de México** (``CO-MX-…``, con ``--desde-mx``): la foto de su ficha en
  ``mexico.carvajalempaques.com``. La referencia de México comparte el mismo archivo.
- **Terceros** (p. ej. EDEXA): solo con ``--incluir-terceros``. Son fotos de otro sitio;
  úsalas solo con permiso de su dueño. Quedan marcadas ``tipo_fuente=tercero``.

Se respeta ``robots.txt`` y el rate limit del proyecto (``carvajal.fetching.WebClient``).
Las imágenes se reducen a ``--max-px`` (600 por defecto) y se guardan como JPEG.

Uso::

    uv run --extra capture python scripts/curar_imagenes_co.py            # con precio
    uv run --extra capture python scripts/curar_imagenes_co.py --todas    # todo CO
    uv run --extra capture python scripts/curar_imagenes_co.py --sku CO-PCOM-J1
    uv run --extra capture python scripts/curar_imagenes_co.py --desde-mx         # copias de México

Revisa las fotos (``imagenes/revision.html``) antes de versionarlas: cada una debe
corresponder a su SKU.
"""

import argparse
import csv
import html
import io
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

from carvajal.catalog.models import ADAPTED_PREFIX
from carvajal.fetching import FetchError, WebClient

BASE = Path("data") / "catalog" / "empaques"
OUT = BASE / "imagenes"
MANIFEST = BASE / "imagenes.csv"
OFFICIAL_HOSTS = {"colombia.carvajalempaques.com", "mexico.carvajalempaques.com"}
FIELDS = ["sku", "archivo", "fuente_url", "imagen_url", "tipo_fuente", "capturada_el"]
SKIP = ("/icons/", "logo", "thumb_", "wiki-empaques", "ico.png")


@dataclass(frozen=True)
class Target:
    sku: str
    name: str
    page: str

    @property
    def official(self) -> bool:
        return urlparse(self.page).netloc in OFFICIAL_HOSTS


def targets(all_refs: bool, only: set[str], from_mx: bool = False) -> list[Target]:
    priced = {row["sku"] for row in csv.DictReader((BASE / "precios.csv").open())}
    found: list[Target] = []
    for row in csv.DictReader((BASE / "referencias.csv").open()):
        if row.get("country") != "CO" or not row.get("source_url"):
            continue
        if only and row["sku"] not in only:
            continue
        if not only and row["sku"].startswith(ADAPTED_PREFIX) != from_mx:
            continue
        if not only and not all_refs and row["sku"] not in priced:
            continue
        found.append(Target(row["sku"], row["name"], row["source_url"]))
    return found


def image_url(page_html: str, page_url: str) -> str | None:
    """Foto principal: ``og:image`` y, si falta, la primera foto de producto."""

    soup = BeautifulSoup(page_html, "html.parser")
    meta = soup.find("meta", attrs={"property": "og:image"})
    if meta and meta.get("content"):
        return urljoin(page_url, str(meta["content"]))
    for img in soup.find_all("img"):
        src = str(img.get("data-src") or img.get("src") or "")
        lowered = src.lower()
        if "/wp-content/uploads/" in lowered and not any(word in lowered for word in SKIP):
            return urljoin(page_url, src)
    return None


def save_jpeg(content: bytes, target: Path, max_px: int) -> None:
    with Image.open(io.BytesIO(content)) as image:
        image.thumbnail((max_px, max_px))
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        image.save(target, "JPEG", quality=85, optimize=True, progressive=True)


def write_review(rows: list[dict[str, str]], names: dict[str, str]) -> Path:
    cards = "\n".join(
        f'<figure><img src="{html.escape(row["archivo"])}" alt="">'
        f"<figcaption><b>{html.escape(row['sku'])}</b><br>{html.escape(names.get(row['sku'], ''))}"
        f"<br><small>{html.escape(row['tipo_fuente'])} · "
        f'<a href="{html.escape(row["fuente_url"])}">ficha</a></small></figcaption></figure>'
        for row in rows
    )
    page = (
        "<!doctype html><meta charset=utf-8><title>Revisión de fotos</title><style>"
        "body{font-family:sans-serif;display:grid;grid-template-columns:repeat(auto-fill,"
        "minmax(200px,1fr));gap:16px;padding:16px}figure{margin:0;border:1px solid #ddd;"
        "padding:8px}img{width:100%;height:180px;object-fit:contain}</style>" + cards
    )
    path = OUT / "revision.html"
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--todas", action="store_true", help="todas las referencias CO")
    parser.add_argument("--sku", action="append", default=[], help="solo estos SKU")
    parser.add_argument("--desde-mx", action="store_true", help="solo las copias de México")
    parser.add_argument("--incluir-terceros", action="store_true", help="fotos de terceros")
    parser.add_argument("--max-px", type=int, default=600)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, str]] = {}
    if MANIFEST.exists():
        existing = {row["sku"]: row for row in csv.DictReader(MANIFEST.open())}
    client = WebClient()
    today = date.today().isoformat()
    names: dict[str, str] = {}
    pages: dict[str, tuple[str, bytes] | None] = {}  # misma ficha → una sola descarga
    for target in targets(args.todas, set(args.sku), args.desde_mx):
        names[target.sku] = target.name
        if not target.official and not args.incluir_terceros:
            print(f"-  {target.sku}: solo tiene ficha de tercero; omitida")
            continue
        if target.page not in pages:
            try:
                page_html = client.fetch(target.page)
                url = image_url(page_html, target.page)
                if url is None:
                    pages[target.page] = None
                else:
                    if not client.can_fetch(url):
                        raise FetchError(f"robots.txt excluye {url}")
                    client._throttle(url)
                    response = client.session.get(url, timeout=client.timeout)
                    response.raise_for_status()
                    pages[target.page] = (url, response.content)
            except (FetchError, requests.RequestException) as error:
                print(f"✗  {target.sku}: {error}")
                pages[target.page] = None
        found = pages[target.page]
        if found is None:
            print(f"✗  {target.sku}: la ficha no tiene foto de producto")
            continue
        url, content = found
        file_name = f"{target.sku}.jpg"
        try:
            save_jpeg(content, OUT / file_name, args.max_px)
        except OSError as error:
            print(f"✗  {target.sku}: imagen ilegible ({error})")
            continue
        existing[target.sku] = {
            "sku": target.sku,
            "archivo": file_name,
            "fuente_url": target.page,
            "imagen_url": url,
            "tipo_fuente": "oficial" if target.official else "tercero",
            "capturada_el": today,
        }
        if target.sku.startswith(ADAPTED_PREFIX):
            # La referencia de México es el mismo producto: comparte el archivo.
            mx_sku = target.sku.removeprefix(ADAPTED_PREFIX)
            existing[mx_sku] = {**existing[target.sku], "sku": mx_sku}
        print(f"✓  {target.sku}: {url}")

    rows = sorted(existing.values(), key=lambda row: row["sku"])
    with MANIFEST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    review = write_review(rows, names)
    print(f"\n{len(rows)} fotos en {MANIFEST}. Revísalas en {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
