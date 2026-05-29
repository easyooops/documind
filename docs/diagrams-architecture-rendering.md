# Diagrams architecture rendering

PPTX architecture visuals now route to the Python Diagrams package through the
`diagrams_image` method.

The LLM planner returns structured JSON topology instead of executable Python:

- `diagrams_provider`: `aws`, `azure`, `gcp`, `kubernetes`, `generic`, or `mixed`
- `diagrams_direction`: Graphviz rank direction such as `LR` or `TB`
- `diagrams_clusters`: nested logical boundaries for regions, VPCs, subnets,
  VNets, namespaces, and similar groupings
- `diagrams_nodes`: provider, service, label, and cluster assignment
- `diagrams_edges`: source, target, label, color, and style

The renderer maps that topology through an internal whitelist of Diagrams node
classes and then creates a PNG. It does not execute LLM-generated Python code.
When the `diagrams` package or Graphviz `dot` executable is unavailable, it
falls back to a deterministic PNG renderer so HTML preview and PPTX insertion
continue to work.

Operational dependency:

- Install the pinned project dependency: `diagrams==0.25.1`
- Install Graphviz on the host, because Diagrams uses Graphviz for rendering
- On Windows, `npm run install:all` downloads the official portable Graphviz
  ZIP into `.tools/graphviz/` when `dot.exe` is not already on `PATH`.
- At runtime the renderer first checks `PATH`, then `.tools/graphviz/**/dot.exe`.

Security note:

- PyPI currently lists `diagrams 0.25.1` as the latest release.
- pip-audit reports `PYSEC-2024-270` against `diagrams 0.25.1`, but the
  advisory description names `Airflow-Diagrams v2.1.0` and its `unsafe_load`
  CLI path, not mingrammer/diagrams usage in this project.
- `scripts/install-all.mjs` ignores only `PYSEC-2024-270` for this reason.
