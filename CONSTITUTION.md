# CONSTITUTION — Carvajal Corpus

> **Verdad central del proyecto.** Este documento define el propósito, el stack, la
> estructura, las convenciones y el flujo de trabajo de `Carvajal Corpus`. Si algo aquí
> contradice al código, el código manda **y este archivo se actualiza**.

> ⚠️ **Directiva de mantenimiento:** cada vez que se realicen cambios significativos
> (nuevas funcionalidades, cambios arquitectónicos, adición de clases/módulos o ajustes
> de tecnologías), **se debe actualizar este archivo en el mismo commit** para mantener
> la verdad central del proyecto bien documentada.

---

## 0. Identidad del proyecto

| Campo | Valor |
|-------|-------|
| **Nombre** | Carvajal Corpus |
| **Descripción** | Pipeline de captura y curaduría del corpus web de Carvajal para RAG. |
| **Repositorio** | https://github.com/markodavid/carvajal |
| **Autoría** | Marko David Garcia — markodavidgm@gmail.com |
| **Licencia** | MIT |
| **Rama principal** | `main` |
| **Estado** | Captura inicial en curso 🚧 |

## 1. Propósito y no-propósito

### Propósito

Convertir páginas públicas del sitio web de **Carvajal** en un corpus Markdown
citable, reproducible y listo para un pipeline de RAG, respetando `robots.txt` y
sin depender de una API del sitio.

### No-propósito

No es un *crawler* masivo del dominio completo, ni un motor de búsqueda, ni un
sistema que atraviese formularios, sesiones autenticadas o datos personales. Tampoco
es un generador de respuestas: el consumo del corpus (embeddings, recuperación,
agente) pertenece al proyecto del curso, no a este repositorio.

## 2. Alcance

### Dentro del alcance

- Capturar las landing y las páginas de contenido que de ella dependen: misión,
  historia, sostenibilidad, especialidades (empaques, educación, tecnología,
  comunicación, espacios), propiedades e inversiones, inversores, equipo y notas
  institucionales.
- Mantener tres capas: **bronze** (HTML crudo), **silver** (Markdown limpio) y
  **gold** (Markdown + *front matter* + índice JSONL).
- Extraer el cuerpo editorial descartando menús, formularios, modales y pie de página.
- Versionar `data/gold/` como entregable del corpus.
- Ofrecer una CLI y un Makefile como interfaz única del proyecto.

### Fuera del alcance (por ahora)

- Rastrear el `sitemap.xml` completo del dominio (podcast, noticias, especialistas).
- Vídeos, PDFs y transcripciones.
- *Chunking*, embeddings e indexación vectorial.
- Páginas tras autenticación o cualquier contenido no público.

### Fases

| Fase | Estado | Entregable |
|------|--------|------------|
| 1. Captura inicial (landing + secciones) | ✅ Hecha | 15 documentos en `data/gold/` |
| 2. Ampliación de semillas | ⬜ Pendiente | Más URLs del sitemap |
| 3. Enriquecimiento (PDFs, vídeo) | ⬜ Pendiente | Nuevas semillas y extractores |
| 4. Chunking y embeddings | ⬜ Pendiente | Fuera de este repositorio |

## 3. Stack técnico

| Categoría | Tecnología |
|-----------|------------|
| **Lenguaje** | Python 3.13+ |
| **Gestor de paquetes y entorno** | [`uv`](https://docs.astral.sh/uv/) |
| **Build backend** | `uv_build` (src-layout) |
| **Paquete Python** | `carvajal` |
| **HTTP** | `requests` (sesión única, `User-Agent` identificable, `timeout`) |
| **Parseo HTML** | `beautifulsoup4` + `lxml` |
| **Tipado estático** | [`ty`](https://github.com/astral-sh/ty) |
| **Testing** | [`pytest`](https://docs.pytest.org/) |
| **Linting / Formato** | [Ruff](https://docs.astral.sh/ruff/) |
| **Pre-commit** | [pre-commit](https://pre-commit.com) |
| **CI/CD** | [GitHub Actions](.github/workflows/ci.yml) |
| **Dependencias automáticas** | [Dependabot](.github/dependabot.yml) |

> ⚠️ **Regla dura:** toda gestión de dependencias y entornos pasa por `uv`. Está
> **prohibido** `pip` y `requirements.txt`.

> 📝 **Decisión sobre los extractores:** se evaluó y se descartó `trafilatura` como
> extractor principal. Es excelente con artículos de prensa, pero descarta las tarjetas
> y acordeones de las *landings* institucionales —justo el contenido que el RAG necesita—.
> El extractor propio recorre el DOM y conserva encabezados, listas, tablas y enlaces.

## 4. Estructura del repositorio

```text
carvajal/
├── src/carvajal/          # código del paquete (src-layout)
│   ├── config.py            # semillas, capas, política HTTP
│   ├── fetching.py          # cliente HTTP educado
│   ├── extracting.py        # HTML → Markdown
│   ├── gold.py              # front matter + índice JSONL
│   ├── pipeline.py          # orquestación de las tres capas
│   ├── models.py            # modelos del dominio
│   └── cli.py               # capture / status / list
├── data/                    # capas Medallion (raw y processed ignoradas)
│   ├── raw/                 # bronze — HTML crudo
│   ├── processed/           # silver — Markdown limpio
│   └── gold/                # gold — entregable versionado
├── tests/                   # pruebas (pytest, patrón AAA)
├── docs/                    # documentación, ADRs e investigación
├── .github/                 # gobernanza (PR/issue templates, CI, Dependabot, labels)
├── .opencode/rules/         # reglas para agentes (tgrep)
├── AGENTS.md                # contexto para agentes LLM
├── CONSTITUTION.md          # este archivo — verdad central
├── README.md                # puerta de entrada pública
├── CONTRIBUTING.md          # guía de contribución
├── CODE_OF_CONDUCT.md       # código de conducta
├── Makefile                 # automatización de tareas
├── pyproject.toml           # metadatos y dependencias (uv)
├── uv.lock                  # lockfile determinista (versionado)
├── .python-version          # versión de Python fijada
└── .pre-commit-config.yaml  # hooks de calidad
```

## 5. Convenciones de código

- **Estilo:** [PEP 8](https://peps.python.org/pep-0008/) y [PEP 257](https://peps.python.org/pep-0257/),
  impuesto por **Ruff** (`line-length = 100`, comillas dobles).
- **Tipado:** anotaciones en funciones y firmas públicas.
- **Nomenclatura:** `snake_case` (funciones/variables), `PascalCase` (clases),
  `UPPER_CASE` (constantes).
- **Docstrings:** en módulos, clases y funciones públicas.
- **Pruebas:** `pytest` con el patrón **AAA** (*Arrange · Act · Assert*).
- **Idioma:** UI, documentación y commits en español; identificadores de código en inglés.
- **Reglas propias:**
  - La extracción **nunca** depende de la red: `extracting` recibe HTML ya descargado.
  - El *front matter* es la única fuente de metadatos; no se duplica en el cuerpo.
  - `data/gold/` es el contrato con el proyecto consumidor: cambiarlo es un cambio
    incompatible y se documenta en esta constitución.

## 6. Calidad y automatización

| Herramienta | Rol | Comando |
|-------------|-----|---------|
| `uv` | Entornos y dependencias | `uv sync` |
| Ruff | Lint + formato | `make lint` / `make format` |
| `ty` | Verificación de tipos | `make typecheck` |
| `pytest` | Pruebas | `make test` |
| `pre-commit` | Hooks antes de cada commit | `make pre-commit` |
| GitHub Actions | CI (pre-commit + ty + pytest) | `.github/workflows/ci.yml` |

- Ningún commit se salta los hooks (`--no-verify` está prohibido salvo causa justificada y documentada).
- La **CI debe estar en verde** antes de fusionar un Pull Request.
- Toda dependencia nueva entra con `uv add` / `uv add --dev` (nunca editando `uv.lock` a mano).

## 7. Repositorio y Git

- **Remoto (SSH):** `git@github.com:markodavid/carvajal.git`
- **Rama principal:** `main`
- **Estrategia de ramas:** `feat/...`, `fix/...`, `docs/...`, `refactor/...`, `test/...`, `chore/...`; integración vía Pull Request.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:`, `fix:`, `docs:`, `refactor:`, `style:`, `test:`, `chore:`.
- **Identidad git:** Marko David Garcia <markodavidgm@gmail.com> (ver §11).

## 8. Flujo diario (commit + push)

```bash
# 1. Ver cambios
git status

# 2. Verificación completa (lint + formato + tipos + pruebas)
make check

# 3. Agregar y commitear (los hooks se ejecutan solos)
git add -A
git commit -m "tipo: mensaje descriptivo"

# 4. Subir a GitHub
git push
```

## 9. Historial de decisiones técnicas

| Fecha | Decisión | Motivo |
|-------|----------|--------|
| 2026-10-21 | Adoptar `uv` como gestor único de entornos y dependencias | Reproducibilidad; prohibido `pip`/`requirements.txt` |
| 2026-10-21 | Adoptar Ruff (lint + formato) y pre-commit | Calidad automatizada antes de cada commit |
| 2026-10-21 | Adoptar Conventional Commits | Historial legible y automatizable |
| 2026-10-21 | Adoptar licencia MIT y `CONSTITUTION.md` como fuente única de verdad | Claridad legal y operativa |
| 2026-10-21 | Patrón Medallion (bronze/silver/gold) con `data/gold/` versionado | Permite reprocesar sin volver a la web; el entregable queda en Git |
| 2026-10-21 | Descartar `trafilatura` como extractor principal | Pierde tarjetas y acordeones de las *landings* institucionales |
| 2026-10-21 | Extractor propio basado en DOM | Control explícito de qué es contenido y qué es *chrome* |
| 2026-10-21 | Eliminar el pie de página institucional del corpus | Repite el mismo bloque en todas las páginas y contamina la recuperación |
| 2026-10-21 | Semillas explícitas en `config.SEEDS` en lugar de rastrear el sitemap | Alcance acotado al dominio Carvajal; el sitemap queda fuera |
| 2026-10-27 | Capturar las landing y secciones públicas de Carvajal (`config.SEEDS`) | Punto de partida verificable y reproducible del corpus |

## 10. Notas y tareas pendientes

- [ ] Añadir extracción de PDFs (informes, memorias anuales).
- [ ] Añadir transcripciones de vídeo (`yt-dlp` / `youtube-transcript-api`).
- [ ] Enriquecer el *front matter* con `entidades` y `especialidades` detectadas.
- [ ] Evaluar *deduplicación* de *boilerplate* entre documentos por hash de bloque.
- [ ] Definir el contrato de *chunking* con el proyecto consumidor.
- [ ] Registrar decisiones de arquitectura (ADRs) en `docs/decisions/`.

## 11. Configuración local (no trackear)

> Configuración específica de la máquina local. **NO incluir en commits.**

Dentro de la carpeta de proyectos, `~/.gitconfig` aplica:

```ini
[includeIf "gitdir:C:/Users/marko/OneDrive/Documentos/Marko/UAO/Semestre 3/TAIAML/carvajal/"]
    path = C:/Users/marko/.gitconfig-personal
```

`~/.gitconfig-personal`:

```ini
[user]
    name = Marko David Garcia
    email = markodavidgm@gmail.com
[core]
    sshCommand = ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes
```

> Efecto: todo repositorio bajo la carpeta personal firma con la identidad personal y
> usa la llave `id_ed25519`, sin flags por proyecto.
