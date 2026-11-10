# Etiquetas de PR (obligatorio)

Toda Pull Request debe llevar al menos una etiqueta adecuada antes de
considerarse entregada. En particular, todo agente debe aplicarlas sin esperar
a que el owner las pida. El mínimo es una; si el cambio abarca varias áreas,
combina las etiquetas que correspondan.

1. Consulta el catálogo vigente con `gh label list`: la fuente de verdad son las
   etiquetas existentes. Si el comando falla (sin red o permisos), no inventes
   etiquetas: deja constancia en la PR y avisa al owner.
2. Aplica las etiquetas justo después de crear o actualizar la PR:
   `gh pr edit <número> --add-label "enhancement,backend"`
3. **No inventes etiquetas** si existe una razonablemente equivalente. Crear
   una nueva sólo si ninguna encaja y el hueco es estructural; justifícalo en
   la PR.
4. Revisa las etiquetas antes de dar la PR por terminada: una PR sin etiquetas,
   o con etiquetas obsoletas, está incompleta.

Los ejemplos por área de cambio y el procedimiento completo viven en
[`AGENTS.md` § Etiquetas de PR](../../AGENTS.md#etiquetas-de-pr-obligatorio-para-agentes).
