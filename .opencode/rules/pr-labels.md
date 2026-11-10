# Etiquetas de PR (obligatorio)

Toda PR abierta por un agente debe llevar al menos una etiqueta adecuada antes
de considerarse entregada. No esperes a que el owner la pida.

1. Consulta el catálogo vigente con `gh label list` y elige la etiqueta más
   específica. Combina varias si el cambio cubre áreas distintas:
   - API o lógica de servidor → `enhancement` + `backend`
   - corrección de datos o curaduría → `bug` + `data`
   - CI, entorno o despliegue → `chore` + `infra`
   - sólo pruebas → `test`
   - sólo documentación → `documentation`
2. Aplica las etiquetas justo después de crear o actualizar la PR:
   `gh pr edit <número> --add-label "enhancement,backend"`
3. **No inventes etiquetas** si existe una razonablemente equivalente. Crear
   una nueva sólo si ninguna encaja y el hueco es estructural; justifícalo en
   la PR.
4. Revisa las etiquetas antes de dar la PR por terminada: una PR sin etiquetas,
   o con etiquetas obsoletas, está incompleta.

La regla completa vive en [`AGENTS.md`](../../AGENTS.md#etiquetas-de-pr-obligatorio-para-agentes).
