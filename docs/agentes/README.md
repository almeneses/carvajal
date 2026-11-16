# Prompts de arranque para sesiones con IA

Cada archivo es el prompt con el que se inicia una sesión de IA (OpenCode, Claude Code,
Copilot…) para una pista de trabajo de una spec. Existen porque dos IAs que arrancan con
contextos distintos proponen soluciones distintas: el prompt fija qué leer, qué archivos
son de cada pista, el contrato compartido y las reglas del proyecto.

| Archivo | Spec | Pista | Responsable | Tareas |
|---|---|---|---|---|
| [`036-pista-a.md`](036-pista-a.md) | 036 asistente comercial de empaques | A: datos y herramientas | `markodavid` | T003–T015, T021–T027 |
| [`036-pista-b.md`](036-pista-b.md) | 036 asistente comercial de empaques | B: router, grafo e interfaz | `almeneses` | T016–T020, T028–T041 |

Reglas:

- Un prompt se trata como un contrato: se cambia por PR y lo revisa la otra persona.
- El prompt no reemplaza a `CONSTITUTION.md`, `AGENTS.md` ni la spec; solo los ordena para
  la sesión. Si discrepan, mandan ellos y se corrige el prompt.
- Cuando una pista cambia un contrato compartido, actualiza en la misma PR el prompt de la
  otra pista.
