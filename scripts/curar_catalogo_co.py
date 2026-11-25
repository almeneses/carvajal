"""Curación del catálogo de Colombia (spec 039, T013-T014 y T022).

Fuentes, capturadas el 2026-11-18 respetando ``robots.txt``:

- Fichas oficiales de Carvajal Empaques Colombia (``colombia.carvajalempaques.com``): familia,
  tallas, unidades por paquete y por corrugado, material. No publican SKU ni precio.
- Tienda en línea de EDEXA (``tiendaedexa.com``), distribuidor del directorio oficial en
  Bogotá: precio por paquete o caja, con impuestos incluidos («Precios ya incluyen
  impuestos»). EDEXA no publica ``robots.txt`` (404).

Reglas de cruce:

1. Si el producto del distribuidor coincide con una ficha oficial (misma línea, talla y
   material), la referencia toma los datos de la ficha y el distribuidor aporta el precio.
2. Si el producto del distribuidor no está en las fichas, entra como referencia del
   distribuidor en su presentación de venta (paquete o caja), con su página como fuente.
3. Las fichas oficiales sin precio entran sin precio: el asistente declara la ausencia.
4. Lo que no se puede transcribir con certeza (unidades ambiguas, material no publicado,
   productos impresos a la medida, promociones) queda fuera y su motivo va al manifiesto.

El SKU es el código de barras (GTIN-13 con prefijo 7704918 de Carvajal Empaques) cuando el
distribuidor lo publica sin ambigüedad; si no, un código ``CO-…`` estable.

Uso: ``uv run python scripts/curar_catalogo_co.py`` (reescribe las filas CO de
``referencias.csv`` y ``fuentes/precios_co.csv``; luego ``carvajal catalog-prices --country CO``).
"""

# ruff: noqa: E501
import csv
import json
from pathlib import Path

from carvajal.catalog.build import update_manifest, write_references
from carvajal.catalog.loader import load_catalog
from carvajal.catalog.models import ADAPTED_PREFIX

BASE = Path("data") / "catalog" / "empaques"
CAPTURED = "2026-11-18"
OFICIAL = "https://colombia.carvajalempaques.com/producto/{}/"
EDEXA = "https://www.tiendaedexa.com/{}"

LL = "para_llevar_y_delivery"
NEG = "en_tu_negocio"
CC = "comida_caliente"
CF = "comida_fria"
BC = "bebida_caliente"
BF = "bebida_fria"
SO = "sopas"
EV = "fiestas_y_eventos"
SA = "salsas"
PA = "panaderia"
SOS = "empaques_sostenibles"


def ref(
    sku,
    name,
    family,
    uses,
    material,
    upb,
    url,
    *,
    cap=None,
    diam=None,
    brand=None,
    upp=None,
    ppb=None,
):
    """Una fila de ``referencias.csv`` para Colombia, sin precio inline."""

    return {
        "sku": sku,
        "name": name,
        "family": family,
        "use": "|".join(uses),
        "capacity_oz": "" if cap is None else f"{cap:g}",
        "diameter_mm": "" if diam is None else f"{diam:g}",
        "material": material,
        "brand": brand or "",
        "units_per_pack": "" if upp is None else str(upp),
        "packs_per_box": "" if ppb is None else str(ppb),
        "units_per_box": str(upb),
        "temp_min_c": "",
        "temp_max_c": "",
        "price": "",
        "currency": "",
        "tax_included": "",
        "price_valid_until": "",
        "country": "CO",
        "source_url": url,
        "captured_at": CAPTURED,
    }


def oficial(slug):
    return OFICIAL.format(slug)


def edexa(path):
    return EDEXA.format(path)


# fmt: off
REFERENCES = [
    # Vaso térmico Wau (EPS): «ideal para conservar la temperatura … fríos o calientes».
    ref("CO-VTER-04", "Vaso térmico 4 oz Wau", "vaso", [BC, BF, LL], "EPS", 1000,
        oficial("vaso-termico"), cap=4, diam=67.8, brand="Wau", upp=20, ppb=50),
    ref("CO-VTER-06", "Vaso térmico 6 oz Wau", "vaso", [BC, BF, LL], "EPS", 1125,
        oficial("vaso-termico"), cap=6, diam=74.6, brand="Wau"),
    ref("CO-VTER-08", "Vaso térmico 8 oz Wau", "vaso", [BC, BF, LL], "EPS", 2160,
        oficial("vaso-termico"), cap=8, diam=81.4, brand="Wau", upp=20, ppb=108),
    ref("CO-VTER-10", "Vaso térmico 10 oz Wau", "vaso", [BC, BF, LL], "EPS", 960,
        oficial("vaso-termico"), cap=10, diam=83.8, brand="Wau", upp=20, ppb=48),
    # Contenedor térmico Wau (EPS).
    ref("CO-CTER-08", "Contenedor térmico 8 oz Wau", "contenedor", [CC, SO, LL], "EPS", 540,
        oficial("contenedor-termico"), cap=8, diam=94, brand="Wau", upp=20, ppb=27),
    ref("CO-CTER-12", "Contenedor térmico 12 oz Wau", "contenedor", [CC, SO, LL], "EPS", 480,
        oficial("contenedor-termico"), cap=12, diam=104.5, brand="Wau", upp=20, ppb=24),
    ref("CO-CTER-16", "Contenedor térmico 16 oz Wau", "contenedor", [CC, SO, LL], "EPS", 400,
        oficial("contenedor-termico"), cap=16, diam=115, brand="Wau", upp=20, ppb=20),
    ref("CO-CTER-24", "Contenedor térmico 24 oz Wau", "contenedor", [CC, SO, LL], "EPS", 400,
        oficial("contenedor-termico"), cap=24, diam=115, brand="Wau", upp=20, ppb=20),
    ref("CO-CTER-34", "Contenedor térmico 34 oz Wau", "contenedor", [CC, SO, LL], "EPS", 120,
        oficial("contenedor-termico"), cap=34, diam=191, brand="Wau", upp=20, ppb=6),
    # Contenedor térmico con tapa: presentación del distribuidor, no está en las fichas.
    ref("CO-CTT-08", "Contenedor térmico con tapa térmica 8 oz Wau (paquete x 20)",
        "contenedor", [CC, SO, LL], "EPS", 20,
        edexa("portacomidas-y-estuches/498-contenedor-termico-ctapa-termica-8-oz-wau-x-20-und-7707119343082.html"),
        cap=8, brand="Wau"),
    ref("7704918101110", "Contenedor térmico con tapa térmica 12 oz Wau (paquete x 20)",
        "contenedor", [CC, SO, LL], "EPS", 20,
        edexa("vasos/3420-contenedor-termico-ctapa-termica-12-oz-wau-x-20-und-7704918101110.html"),
        cap=12, brand="Wau"),
    ref("CO-CTT-16", "Contenedor térmico con tapa térmica interna 16 oz Wau (paquete x 20)",
        "contenedor", [CC, SO, LL], "EPS", 20,
        edexa("portacomidas-y-estuches/4808-contenedor-termico-ctapa-termica-interna-16-oz-wau-x-20-und.html"),
        cap=16, brand="Wau"),
    # Plato espumado hondo Wau (EPS): «utilízalos para líquidos calientes».
    ref("CO-PHON-05", "Plato espumado hondo 5 oz Wau", "plato", [CC, SO, EV], "EPS", 480,
        oficial("plato-espumado-hondo"), cap=5, diam=120, brand="Wau", upp=20, ppb=24),
    ref("7704918101288", "Plato espumado hondo 12 oz Wau", "plato", [CC, SO, EV], "EPS", 480,
        oficial("plato-espumado-hondo"), cap=12, diam=152, brand="Wau", upp=20, ppb=24),
    ref("CO-PHON-16", "Plato espumado hondo 16 oz Wau", "plato", [CC, SO, EV], "EPS", 480,
        oficial("plato-espumado-hondo"), cap=16, diam=177, brand="Wau", upp=20, ppb=24),
    ref("CO-PHON-25", "Plato espumado hondo 25 oz Wau", "plato", [CC, SO, EV], "EPS", 480,
        oficial("plato-espumado-hondo"), cap=25, diam=199, brand="Wau", upp=20, ppb=24),
    ref("CO-PHON-30", "Plato espumado hondo 30 oz Wau", "plato", [CC, SO, EV], "EPS", 480,
        oficial("plato-espumado-hondo"), cap=30, diam=199, brand="Wau", upp=20, ppb=24),
    # Plato espumado pando Wau (EPS), talla en centímetros.
    *[
        ref(sku, f"Plato espumado pando {label} cm Wau", "plato", [CC, EV, NEG], "EPS", 480,
            oficial("plato-espumado-pando"), diam=diam, brand="Wau", upp=20, ppb=24)
        for sku, label, diam in (
            ("CO-PPAN-12", "12", 121),
            ("CO-PPAN-155", "15,5", 150),
            ("CO-PPAN-18", "18", 176.6),
            ("CO-PPAN-20", "20", 200),
            ("CO-PPAN-23", "23", 222),
            ("CO-PPAN-26", "26", 260),
        )
    ],
    # Plato rígido Wau (PS blanco).
    ref("CO-PRIG-P", "Plato rígido pequeño Wau", "plato", [NEG, EV], "PS", 3040,
        oficial("plato-rigido"), diam=145, brand="Wau", upp=20, ppb=152),
    ref("CO-PRIG-M", "Plato rígido mediano Wau", "plato", [NEG, EV], "PS", 2100,
        oficial("plato-rigido"), diam=182, brand="Wau", upp=20, ppb=105),
    ref("CO-PRIG-G", "Plato rígido grande Wau", "plato", [NEG, EV], "PS", 1500,
        oficial("plato-rigido"), diam=221, brand="Wau", upp=20, ppb=75),
    ref("CO-PLISO", "Plato rígido liso negro Wau Maestro", "plato", [CC, LL], "PS", 200,
        oficial("plato-rigido-liso"), brand="Wau Maestro", upp=50, ppb=4),
    ref("CO-P3DIV", "Plato rígido rectangular negro 3 divisiones Wau Maestro", "plato",
        [CC, LL], "PP", 100, oficial("plato-3-divisiones"), brand="Wau Maestro", upp=50, ppb=2),
    ref("CO-TP3DIV", "Tapa traslúcida para plato rectangular 3 divisiones (caja x 100)", "tapa",
        [LL], "PP", 100,
        edexa("portacomidas-y-estuches/4890-tapa-traslucida-plato-rectangular-3-div-caja-x-100-.html"),
        brand="Wau Maestro"),
    # Portacomidas espumados Wau (EPS), paca de 200.
    *[
        ref(sku, f"Portacomidas espumado {label} Wau (paca x 200)", "contenedor", [LL, CC],
            "EPS", 200, oficial(slug), brand="Wau")
        for sku, label, slug in (
            ("CO-PCOM-C1", "C1", "portacomidas-c1"),
            ("CO-PCOM-P1", "P1", "portacomidas-c1"),
            ("CO-PCOM-J1", "J1", "portacomidas-j1"),
            ("CO-PCOM-J2", "J2", "portacomidas-j2"),
            ("CO-PCOM-P3", "P3", "portacomidas-p3"),
        )
    ],
    # Portacomidas de pulpa de caña Bioform: «resistente en horno microondas».
    ref("7704918105071", "Portacomidas pulpa de caña J1 Bioform", "contenedor", [LL, CC, SOS],
        "PULPA", 250, oficial("portacomidas-pulpa-j1"), brand="Bioform", upp=50, ppb=5),
    ref("CO-PPUL-P3", "Portacomidas pulpa de caña P3 Bioform", "contenedor", [LL, CC, SOS],
        "PULPA", 200, oficial("portacomidas-pulpa-p3"), brand="Bioform", upp=50, ppb=4),
    ref("CO-PPUL-C1", "Portacomidas pulpa de caña C1 Bioform", "contenedor", [LL, CC, SOS],
        "PULPA", 500, oficial("portacomidas-pulpa-c1"), brand="Bioform", upp=50, ppb=10),
    ref("CO-TBP28", "Tapa RPET para bandeja de pulpa 28 oz Bioform", "tapa", [LL], "PET", 500,
        oficial("tapa-bandeja-pulpa-28-oz"), cap=28, brand="Bioform", upp=50, ppb=10),
    # Bowl y contenedor de pulpa de caña Bioform.
    *[
        ref(f"CO-BOWL-{cap}", f"Bowl pulpa de caña {cap} oz Bioform", "contenedor",
            [LL, CC, SOS], "PULPA", 300, oficial("bowl"), cap=cap, diam=diam, brand="Bioform",
            upp=50, ppb=6)
        for cap, diam in ((26, 150), (32, 150), (44, 184))
    ],
    ref("CO-TBOWL32", "Tapa PET para bowl 32 oz (caja x 300)", "tapa", [LL], "PET", 300,
        edexa("portacomidas-y-estuches/4886-tapa-pet-bowl-32-oz-x-300-und.html"), cap=32,
        brand="Bioform"),
    *[
        ref(f"CO-CBIO-{cap:02d}", f"Contenedor pulpa de caña {cap} oz Bioform", "contenedor",
            [SO, LL, SOS], "PULPA", 300, oficial("contenedor"), cap=cap, diam=diam,
            brand="Bioform", upp=25, ppb=12)
        for cap, diam in ((4, 72), (8, 91), (12, 90), (16, 98), (26, 116))
    ],
    # Cazuela y tapa Wau Maestro (PP): «apto para calentar en microondas».
    *[
        ref(f"CO-CAZ-{cap}", f"Cazuela negra {cap} oz Wau Maestro", "contenedor", [CC, SO, LL],
            "PP", 100, oficial("cazuela"), cap=cap, brand="Wau Maestro", upp=25, ppb=4)
        for cap in (12, 16, 26)
    ],
    ref("CO-TCAZ-1216", "Tapa traslúcida para cazuela 12 y 16 oz Wau Maestro", "tapa", [LL],
        "PP", 100, oficial("tapa-cazuela"), brand="Wau Maestro", upp=25, ppb=4),
    ref("CO-TCAZ-26", "Tapa traslúcida para cazuela 26 oz Wau Maestro", "tapa", [LL], "PP",
        100, oficial("tapa-cazuela"), cap=26, brand="Wau Maestro", upp=25, ppb=4),
    # Contenedor rígido Wau (PP traslúcido): «ideales para alimentos calientes».
    *[
        ref(f"CO-CRIG-{cap:02d}", f"Contenedor rígido traslúcido {cap} oz Wau", "contenedor",
            [CC, LL, PA], "PP", 375, oficial("contenedor-rigido"), cap=cap, diam=115,
            brand="Wau", upp=25, ppb=15)
        for cap in (6, 8, 12, 14, 16, 24, 32)
    ],
    # Vasos rígidos Wau (PP): «resistente a altas temperaturas y refrigeración».
    ref("7704918100199", "Vaso rígido traslúcido 9 oz Wau", "vaso", [BF, BC, NEG], "PP", 2750,
        oficial("vaso-rigido-mediano"), cap=9, brand="Wau", upp=50, ppb=55),
    ref("CO-VRM-10", "Vaso rígido traslúcido 10 oz Wau", "vaso", [BF, BC, NEG], "PP", 2500,
        oficial("vaso-rigido-mediano"), cap=10, brand="Wau", upp=50, ppb=50),
    ref("CO-VRM-12", "Vaso rígido traslúcido 12 oz Wau", "vaso", [BF, BC, NEG], "PP", 2500,
        oficial("vaso-rigido-mediano"), cap=12, brand="Wau", upp=50, ppb=50),
    *[
        ref(f"CO-VRG-{cap}", f"Vaso rígido traslúcido {cap} oz Wau", "vaso", [BF, BC, NEG],
            "PP", 925, oficial("vaso-rigido-grande"), cap=cap, brand="Wau", upp=25, ppb=37)
        for cap in (13, 14, 16)
    ],
    # Vasos rígidos pequeños: la ficha mezcla materiales y corrugados; se usa la
    # presentación del distribuidor.
    ref("7704918100083", "Vaso traslúcido 3,5 oz Wau (paquete x 50)", "vaso", [BF, EV], "PS",
        50, edexa("vasos/1653-vaso-traslucido-35-oz-x-50-und-7704918100083.html"), cap=3.5,
        brand="Wau"),
    ref("CO-VBL-035", "Vaso blanco 3,5 oz Wau (paquete x 50)", "vaso", [BF, EV], "PS", 50,
        edexa("vasos/404-vaso-blanco-35-onz-x-50-und.html"), cap=3.5, brand="Wau"),
    ref("CO-VRP-07", "Vaso rígido traslúcido 7 oz Wau (paquete x 50)", "vaso", [BF, EV], "PP",
        50, edexa("vasos/2015-vaso-rigido-traslucido-7-oz-wau-x-50-und-14880.html"), cap=7,
        brand="Wau"),
    ref("7704918100816", "Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau", "tapa", [BF, LL],
        "PS", 4500, oficial("tapa-vaso-rigido-pequeno"), brand="Wau", upp=50, ppb=90),
    # Vaso Gold Wau Maestro (PP traslúcido) y sus tapas y domos (RPET/PET).
    *[
        ref(f"CO-VGOLD-{cap:02d}", f"Vaso Gold traslúcido {cap} oz Wau Maestro", "vaso",
            [BF, LL, NEG], "PP", 1000, oficial("vaso-gold"), cap=cap, diam=94,
            brand="Wau Maestro", upp=50, ppb=20)
        for cap in (9, 12, 14, 16)
    ],
    ref("CO-TGOLD-PL", "Tapa plana RPET para vaso Gold Wau Maestro", "tapa", [BF, LL], "PET",
        1000, oficial("tapa-gold-plana"), diam=98, brand="Wau Maestro", upp=50, ppb=20),
    ref("CO-TGOLD-PI", "Tapa pitillera RPET para vaso Gold Wau Maestro", "tapa", [BF, LL],
        "PET", 1000, oficial("tapa-gold-pitillera"), brand="Wau Maestro", upp=50, ppb=20),
    ref("CO-DGOLD-SP", "Domo sin perforar RPET para vaso Gold Wau Maestro", "tapa", [BF, LL],
        "PET", 1000, oficial("domo-vaso-gold"), diam=98, brand="Wau Maestro", upp=50, ppb=20),
    ref("CO-DGOLD-PE", "Domo perforado PET para vaso Gold Wau Maestro", "tapa", [BF, LL],
        "PET", 1000, oficial("domo-vaso-gold"), diam=98, brand="Wau Maestro", upp=50, ppb=20),
    # Bioform de fibra de caña: vaso, tapa viajera, cajas y bandejas.
    ref("CO-VBIO-16", "Vaso Bioform 16 oz de bagazo de caña", "vaso", [NEG, SOS], "PAPEL",
        1000, oficial("vaso-bioform"), cap=16, diam=89, brand="Bioform", upp=50, ppb=20),
    ref("CO-VCAN-09", "Vaso de cartón blanco 9 oz Cañatek (paquete x 50)", "vaso", [NEG, SOS],
        "PAPEL", 50, edexa("portacomidas-y-estuches/4807-vaso-carton-blanco-9-oz-canatek-x-50-und.html"),
        cap=9, brand="Cañatek"),
    ref("CO-TVIA-07", "Tapa viajera 7 oz para vaso Bioform", "tapa", [LL], "PS", 1000,
        oficial("tapa-viajera-para-vaso-bioform"), brand="Bioform", upp=50, ppb=20),
    ref("CO-TVIA-0912", "Tapa viajera 9 a 12 oz para vaso Bioform", "tapa", [LL], "PS", 1000,
        oficial("tapa-viajera-para-vaso-bioform"), brand="Bioform", upp=50, ppb=20),
    ref("CO-TVIA-0812", "Tapa viajera 8 y 12 oz para vaso Bioform", "tapa", [LL], "PP", 300,
        oficial("tapa-viajera-para-vaso-bioform"), brand="Bioform", upp=25, ppb=12),
    ref("CO-TVIA-16", "Tapa viajera 16 oz para vaso Bioform", "tapa", [LL], "PP", 300,
        oficial("tapa-viajera-para-vaso-bioform"), diam=99.6, brand="Bioform", upp=25, ppb=12),
    ref("CO-TVIA-26", "Tapa viajera 26 oz para vaso Bioform", "tapa", [LL], "PP", 300,
        oficial("tapa-viajera-para-vaso-bioform"), diam=121.2, brand="Bioform", upp=25, ppb=12),
    *[
        ref(f"CO-PCB-{cap}", f"Portacomida caja Bioform {cap} oz de bagazo de caña", "contenedor",
            [LL, SOS], "PULPA", box, oficial("portacomida-caja-bioform"), cap=cap,
            brand="Bioform", upp=25, ppb=box // 25)
        for cap, box in ((29, 400), (45, 300), (67, 150))
    ],
    ref("CO-PCH-C1", "Caja hamburguesa Bioform C1 de bagazo de caña", "contenedor", [LL, SOS],
        "PULPA", 450, oficial("portacomida-caja-hamburguesa"), brand="Bioform", upp=25, ppb=18),
    *[
        ref(f"CO-BBIO-{num:02d}", f"Bandeja Bioform #{num} de fibra de caña", "charola",
            [NEG, SOS], "PAPEL", 300, oficial("bandeja-bioform"), brand="Bioform", upp=20,
            ppb=15)
        for num in (1, 3, 17)
    ],
    # Bandejas espumadas Wau (EPS), paca de 500, y domos PET para bandeja 17.
    *[
        ref(f"CO-BESP-{code}", f"Bandeja espumada {label} Wau (paca x 500)", "charola",
            [NEG, CF], "EPS", 500, oficial(slug), brand="Wau")
        for code, label, slug in (
            ("1", "1", "bandeja-espumada-1"),
            ("3", "3", "bandeja-espumada-3"),
            ("4", "4", "bandeja-espumada-4"),
            ("7", "7", "bandeja-espumada-7"),
            ("8", "8", "bandeja-espumada-8-1"),
            ("81", "8,1", "bandeja-espumada-8-1"),
            ("171", "17,1", "bandeja-espumada-17"),
            ("251", "25/1", "bandeja-espumada-25-1"),
        )
    ],
    ref("CO-DB17-3", "Domo PET 3 cm para bandeja 17 Wau", "tapa", [PA, NEG], "PET", 200,
        oficial("domo-bandeja-17"), brand="Wau", upp=50, ppb=4),
    ref("CO-DB17-6", "Domo PET 6 cm para bandeja 17 Wau", "tapa", [PA, NEG], "PET", 200,
        oficial("domo-bandeja-17"), brand="Wau", upp=50, ppb=4),
    ref("CO-CSAL-006", "Copa salsera 0,6 oz Wau (paquete x 100)", "envase", [SA], "PS", 100,
        edexa("platos/3278-copa-salsera-wau-ps-ts-06-oz-x100.html"), cap=0.6, brand="Wau"),
]
# fmt: on


def price(sku, amount, basis, units, path):
    """Una oferta de EDEXA tal como se publica (por paquete o por caja)."""

    return {
        "sku": sku,
        "country": "CO",
        "amount": str(amount),
        "currency": "COP",
        "basis": basis,
        "units_in_basis": str(units),
        "tax_status": "incluido",
        "source_type": "tercero",
        "source_name": "EDEXA",
        "source_city": "Bogotá",
        "source_url": edexa(path),
        "captured_at": CAPTURED,
        "valid_until": "",
    }


# fmt: off
PRICES = [
    price("7704918100199", 3751, "paquete", 50,
          "vasos/4167-vaso-rigido-9-oz-wau-traslucido-pq-x-50-7704918100199.html"),
    price("CO-VRG-14", 3790, "paquete", 25,
          "vasos/4241-vaso-rigido-traslucido-14-oz-wau-x-25-und.html"),
    price("CO-VRP-07", 2653, "paquete", 50,
          "vasos/2015-vaso-rigido-traslucido-7-oz-wau-x-50-und-14880.html"),
    price("7704918100083", 2794, "paquete", 50,
          "vasos/1653-vaso-traslucido-35-oz-x-50-und-7704918100083.html"),
    price("CO-VBL-035", 2808, "paquete", 50, "vasos/404-vaso-blanco-35-onz-x-50-und.html"),
    price("7704918100816", 4825, "paquete", 50,
          "vasos/3965-tapa-35-70-oz-traslucida-pq-x-50-und-7704918100816.html"),
    price("7704918101288", 3875, "paquete", 20,
          "portacomidas-y-estuches/4547-plato-hondo-espumado-12-cm-blanco-wau-pq-x-20-7704918101288.html"),
    price("CO-PPAN-12", 1748, "paquete", 20,
          "portacomidas-y-estuches/4815-plato-pando-espuma-blanco-12-wau-pq-x-20-7704918103510.html"),
    price("CO-PPAN-155", 2892, "paquete", 20,
          "portacomidas-y-estuches/4534-plato-pando-espumado-blanco-155-wau-x-20-und-770491810257.html"),
    price("CO-PPAN-23", 6687, "paquete", 20,
          "portacomidas-y-estuches/4813-plato-pando-espumado-blanco-23-wau-x-20-und-7704918103510.html"),
    price("CO-PPAN-26", 8808, "paquete", 20,
          "portacomidas-y-estuches/4546-plato-pando-espumadodo-blanco-26-wau-x-20-und-7704918101226.html"),
    price("CO-PRIG-P", 3587, "paquete", 20,
          "platos/3277-plato-rigido-pequeno-wau-blanco-pq-x-20-und.html"),
    price("CO-P3DIV", 107941, "caja", 100,
          "portacomidas-y-estuches/4884-plato-rigido-rectangular-negro-3-div-cj-x-100.html"),
    price("CO-TP3DIV", 77588, "caja", 100,
          "portacomidas-y-estuches/4890-tapa-traslucida-plato-rectangular-3-div-caja-x-100-.html"),
    price("CO-PCOM-C1", 55548, "caja", 200,
          "portacomidas-y-estuches/4885-portacomida-espumado-blanco-c1-wau-x-200-und.html"),
    price("CO-PCOM-J1", 102185, "caja", 200,
          "portacomidas-y-estuches/4698-portacomida-espumado-blanco-j1-wau-x-200-und.html"),
    price("CO-PCOM-J2", 94922, "caja", 200,
          "portacomidas-y-estuches/4699-portacomida-espumado-blanco-j2-wau-x-200-und.html"),
    price("CO-PCOM-P1", 114463, "caja", 200,
          "portacomidas-y-estuches/4814-portacomida-espumado-blanco-p1-wau-x-200und-7704918103510.html"),
    price("CO-PCOM-P3", 114463, "caja", 200,
          "portacomidas-y-estuches/4817-portacomida-espumado-blanco-p3-wau-x-200-und.html"),
    price("7704918105071", 180166, "caja", 250,
          "portacomidas-y-estuches/2145-contenedor-j1-pulpa-23cm-caja-x-250-und-7704918105071.html"),
    price("CO-PPUL-P3", 164845, "caja", 200,
          "portacomidas-y-estuches/2954-contenedor-p3-pulpa-20-cm-caja-x-200-und.html"),
    price("CO-TBP28", 479014, "caja", 500,
          "portacomidas-y-estuches/4823-tapa-pet-bandeja-pulpa-28-oz-x-500-und.html"),
    price("CO-TBOWL32", 112476, "caja", 300,
          "portacomidas-y-estuches/4886-tapa-pet-bowl-32-oz-x-300-und.html"),
    price("CO-CTT-08", 17642, "paquete", 20,
          "portacomidas-y-estuches/498-contenedor-termico-ctapa-termica-8-oz-wau-x-20-und-7707119343082.html"),
    price("7704918101110", 17276, "paquete", 20,
          "vasos/3420-contenedor-termico-ctapa-termica-12-oz-wau-x-20-und-7704918101110.html"),
    price("CO-CTT-16", 20793, "paquete", 20,
          "portacomidas-y-estuches/4808-contenedor-termico-ctapa-termica-interna-16-oz-wau-x-20-und.html"),
    price("CO-VGOLD-14", 19809, "paquete", 50,
          "portacomidas-y-estuches/4820-vaso-gold-reload-14-oz-x-50-und.html"),
    price("CO-VCAN-09", 7263, "paquete", 50,
          "portacomidas-y-estuches/4807-vaso-carton-blanco-9-oz-canatek-x-50-und.html"),
    price("CO-CSAL-006", 3965, "paquete", 100, "platos/3278-copa-salsera-wau-ps-ts-06-oz-x100.html"),
]
# fmt: on

# Lo que se vio en las fuentes y no entra, con su motivo (FR-009).
# fmt: off
DISCARDED = [
    {"source": oficial("vaso-termico"), "item": "Vaso térmico 6 oz",
     "reason": "entra sin paquete: la ficha da 1125 unidades por corrugado, que no es múltiplo de 20"},
    {"source": oficial("vaso-rigido-pequeno"), "item": "Vasos rígidos pequeños 3,3 a 7 oz",
     "reason": "la ficha mezcla materiales (PP/PS) y corrugados (3000/5100/5250); solo entran las presentaciones con precio de EDEXA"},
    {"source": oficial("tapa-vaso-rigido-mediano"), "item": "Tapa vaso rígido mediano",
     "reason": "corrugado ambiguo (3100/2500) y material mixto (PP/PS/RPET)"},
    {"source": oficial("tapa-vaso-rigido-grande"), "item": "Tapa vaso rígido grande",
     "reason": "paquete ambiguo (50/25) y material mixto (PP/PS/RPET)"},
    {"source": oficial("vaso-bioform"), "item": "Vaso Bioform 7, 9 y 12 oz",
     "reason": "paquete y corrugado ambiguos (48/50 y 960/1000)"},
    {"source": oficial("vaso-gold"), "item": "Vaso Gold 22 oz",
     "reason": "paquete y corrugado ambiguos (50/32 y 1000/768)"},
    {"source": oficial("domo-bandeja-25-1"), "item": "Domo bandeja 25-1",
     "reason": "corrugado ambiguo (100/200)"},
    {"source": oficial("contenedor-rigido"), "item": "Contenedor rígido 60 y 80 oz",
     "reason": "la ficha repite las medidas del de 32 oz; no se pueden distinguir"},
    {"source": oficial("plato-rectangular"), "item": "Plato rectangular",
     "reason": "datos incoherentes (diámetro de 1000 mm, corrugado 1000/200)"},
    {"source": oficial("tapa-bioform-pitillera"), "item": "Tapa Bioform pitillera",
     "reason": "el corrugado (25) es menor que el paquete (100)"},
    {"source": oficial("tapa-bioform-plana"), "item": "Tapa Bioform plana",
     "reason": "medidas de bandeja (248 x 326 mm) en una tapa de vaso; dato dudoso"},
    {"source": edexa("portacomidas-y-estuches/4818-vaso-imp-9-oz-lstidos-de-mi-tierra-pq-x-50.html"),
     "item": "Vaso impreso 9 oz", "reason": "impresión a la medida de un cliente, no es catálogo estándar"},
    {"source": edexa("portacomidas-y-estuches/660-estuche-cuadrado-alto-x-250-und-ref-f2a-7701844000500.html"),
     "item": "Estuche cuadrado alto F2A", "reason": "material no publicado y código fuera del prefijo de Carvajal"},
    {"source": edexa("portacomidas-y-estuches/661-estuche-triangular-alto-x-250-und-ref-p6-7701844000548.html"),
     "item": "Estuche triangular alto P6", "reason": "material no publicado y código fuera del prefijo de Carvajal"},
    {"source": edexa("marca/29-carvajal-empaques-sa"), "item": "Productos «OUTLET … PAGUE 2 LLEVE 3»",
     "reason": "promoción por volumen, no es un precio de referencia"},
    {"source": edexa("marca/29-carvajal-empaques-sa"), "item": "Blanqueadores y lavaplatos Berhlan",
     "reason": "otra marca listada en la página de Carvajal"},
]
# fmt: on


def main() -> None:
    path = BASE / "referencias.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        # Las copias de México (``CO-MX-…``) las regenera ``adaptar_catalogo_mx.py``.
        keep = [
            row for row in reader if row["country"] != "CO" or row["sku"].startswith(ADAPTED_PREFIX)
        ]
    rows = keep + REFERENCES
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    # Se reescribe con el mismo serializador de ``catalog-build`` para que el formato
    # (por ejemplo «4.0» en la capacidad) no cambie según qué orden se corrió última.
    load_catalog.cache_clear()
    mexico = [ref for ref in load_catalog(str(BASE)).references if ref.country == "MX"]
    write_references(BASE, mexico)
    load_catalog.cache_clear()

    sources = BASE / "fuentes"
    with (sources / "precios_co.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PRICES[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(PRICES)
    (sources / "descartes_co.json").write_text(
        json.dumps(DISCARDED, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    official = sum(1 for row in REFERENCES if row["source_url"].startswith(OFICIAL[:40]))
    update_manifest(
        BASE,
        "referencias_co",
        {
            "origin": (
                "Fichas oficiales de Carvajal Empaques Colombia, cruzadas con la tienda en línea "
                "de EDEXA (distribuidor del directorio oficial)"
            ),
            "source_urls": [
                "https://colombia.carvajalempaques.com/",
                "https://www.tiendaedexa.com/",
            ],
            "generated_by": "scripts/curar_catalogo_co.py",
            "captured_at": CAPTURED,
            "rows": len(REFERENCES),
            "from_official": official,
            "from_distributor": len(REFERENCES) - official,
            "curated": "transcrito a mano desde las fichas; ver el docstring del script para las reglas de cruce",
            "discarded": "fuentes/descartes_co.json",
            "notes": [
                "Las fichas oficiales no publican SKU: se usa el GTIN de Carvajal (prefijo 7704918) cuando el distribuidor lo publica sin ambigüedad; si no, un código CO-… estable.",
                "Las referencias del distribuidor que no están en las fichas entran en su presentación de venta (paquete o caja) y citan la página del distribuidor.",
                "Las fichas oficiales no declaran temperatura: la validación usa la guía de materiales; para papel y pulpa, el uso declarado.",
                "Donde la ficha dice «PP/PS» (vasos y tapas rígidos pequeños) se usa PS, la regla más estricta de la guía (sin microondas, hasta 60 °C).",
            ],
        },
    )
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    if manifest.pop("pending", None) is not None:  # la T009 de la 036 la resuelve esta spec
        (BASE / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"referencias CO: {len(REFERENCES)} · precios CO: {len(PRICES)} · "
        f"descartes: {len(DISCARDED)}"
    )


if __name__ == "__main__":
    main()
