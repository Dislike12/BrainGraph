# BrainGraph Architecture

BrainGraph is now a package-first CLI system with project-local outputs.

Core layers:

- `parser`: scans the repository and extracts files, symbols, imports, routes, APIs, and components
- `database`: stores projects, files, symbols, relations, summaries, embeddings, and diagnostics in SQLite
- `graph_engine`: builds a NetworkX relationship graph, exports `graph.json`, renders `graph.html`, and computes module paths
- `memory`: chunks code and provides Chroma-backed or SQLite lexical retrieval
- `reporting`: writes `BRAIN_REPORT.md`
- `integrations`: generates project-local BrainGraph-first instruction files for Codex, Claude, Cursor, Gemini, and Copilot
- `watcher`: observes repository changes and refreshes `braingraph-out`
- `cli`: the public product surface

Generated output is stored under `braingraph-out/`:

- `memory.db`
- `embeddings.db`
- `graph.json`
- `graph.html`
- `BRAIN_REPORT.md`
- `summaries/`
- `cache/`
- `integrations/`
