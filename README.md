# koharu-orchestrate

opencode skill — **agent orchestrates Koharu headless server (MCP + HTTP) for end-to-end manga translation.**

## How it works

```
Koharu (ML pipeline)          Agent (brain + translator)
  ┌──────────────┐            ┌──────────────────────┐
  │ Detect       │ ◄─ MCP ── │ Orchestrate workflow  │
  │ OCR          │ ◄─ MCP ── │ Monitor progress      │
  │ Font detect  │ ◄─ MCP ── │ Quality control       │
  │ Inpaint      │ ◄─ MCP ── │ Chapter summarization │
  │ Render       │ ◄─ MCP ── │ Glossary management   │
  └──────────────┘            │ Translate (agent LLM)│
                              └──────────────────────┘
```

The agent takes over Koharu's `llm` engine — translating with glossary + rules + chapter context — while Koharu handles everything visual (detection, OCR, inpainting, compositing).

## Quick start

```bash
# 1. Install Koharu
brew install --cask koharu

# 2. Python environment
python3 -m venv ~/.venvs/koharu-orchestrate
~/.venvs/koharu-orchestrate/bin/pip install httpx ebooklib beautifulsoup4 pillow

# 3. Set path variables
export SKILL_DIR="$HOME/.config/opencode/skills/koharu-orchestrate"
export KOHARU_PY="$HOME/.venvs/koharu-orchestrate/bin/python"
export KOHARU_URL="http://localhost:4000"
export WORK="$HOME/my-manga"
```

## Full workflow (13 steps)

| # | Step | Executor | Interface |
|---|------|----------|-----------|
| 1 | Start Koharu headless | `koharu --headless --port 4000` | CLI |
| 2 | Create project | `koharu.open_project` | MCP |
| 3 | Import pages | `import_epub` / `import_pages` | Python → HTTP |
| 4 | Detect text + bubbles | `koharu.start_pipeline` | MCP → polling |
| 5 | Agent review detections | `GET /scene.json` + `koharu.apply` | HTTP + MCP |
| 6 | OCR + segmentation + font | `koharu.start_pipeline` | MCP → polling |
| 6.5 | Page quality analysis | `analyze.py` | Python |
| 6.6 | Chapter detection | `chapter.py` | Python |
| 7 | **Translate** (agent LLM or DeepSeek API) | — | Agent + `koharu.apply` |
| 8 | Review translations | `GET /scene.json` | HTTP |
| 9 | Inpaint | `koharu.start_pipeline` | MCP |
| 10 | Render | `koharu.start_pipeline` | MCP |
| 11 | Final review | `GET /scene.json` | HTTP |
| 12 | Export | `POST /export` | HTTP |
| 13 | Close project | `koharu.close_project` | MCP |

## Scripts

| Script | Purpose | Dependencies |
|--------|---------|-------------|
| `import_epub.py` | Extract images from manga EPUB | `ebooklib`, `beautifulsoup4`, `Pillow` |
| `import_pages.py` | Batch import images into Koharu via HTTP | `httpx` |
| `glossary.py` | Glossary management (fetch/import/template) | `httpx` |
| `analyze.py` | Page quality analysis & protection | `httpx` |
| `chapter.py` | Chapter boundary detection | `httpx` |
| `call_llm.py` | Batch translation via DeepSeek/OpenAI API | `httpx` |
| `koharu_api.py` | Shared Koharu HTTP API client | `httpx` |

## Translation modes

| Mode | Speed | Quality | Cost |
|------|-------|---------|------|
| **Agent LLM** (default) | ~30-60 min / 100 pages | Best (can follow complex rules) | Free |
| **DeepSeek API** (Option B) | ~3-5 min / 100 pages | Good | ~$0.10-0.50 |

## Glossary sources

1. **Wikipedia fetch** — auto-detect series, search zh.wikipedia.org for official Taiwanese translations
2. **AiNiee import** — import from existing AiNiee `config.json`
3. **Template** — blank skeleton for manual entry

## References

- [`SKILL.md`](SKILL.md) — Full skill guide (loaded by opencode)
- [`references/api-reference.md`](references/api-reference.md) — HTTP API + MCP tool reference
- [`references/pipeline-engines.md`](references/pipeline-engines.md) — ML engine catalog & DAG
- [`references/scene-types.md`](references/scene-types.md) — Scene/Op/Node JSON types
- [`references/translation_rules.md`](references/translation_rules.md) — Translation guidelines
- [`references/glossary_format.md`](references/glossary_format.md) — Glossary `.json` schema
- [`references/workflow-patterns.md`](references/workflow-patterns.md) — Common workflows
