# koharu-orchestrate

opencode skill — **agent 編排 Koharu headless server（MCP + HTTP）端到端翻譯漫畫。**
opencode skill — **agent orchestrates Koharu headless server (MCP + HTTP) for end-to-end manga translation.**

> 中文文件。EN below. 本專案為 **opencode 技能**，非獨立應用程式；翻譯的「大腦」是 LLM agent，Koharu 負責所有視覺/ML 工作。

## How it works / 運作原理

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

Agent 取代 Koharu 的 `llm` engine — 用術語表 + 規則 + 章節脈絡翻譯；Koharu 負責一切視覺處理（偵測、OCR、除字、合成）。Koharu 端只透過 HTTP API 操作，**不修改 Koharu 任何程式碼**。

## Quick start / 快速入門

```bash
# 1. 安裝 Koharu
brew install --cask koharu

# 2. Python 環境
python3 -m venv ~/.venvs/koharu-orchestrate
~/.venvs/koharu-orchestrate/bin/pip install httpx ebooklib beautifulsoup4 pillow

# 3. 設定路徑變數
export SKILL_DIR="$HOME/.config/opencode/skills/koharu-orchestrate"
export KOHARU_PY="$HOME/.venvs/koharu-orchestrate/bin/python"
export KOHARU_URL="http://localhost:4000"
export WORK="$HOME/my-manga"
```

## Full workflow / 完整流程

| # | Step | Executor | Interface |
|---|------|----------|-----------|
| 1 | 啟動 Koharu headless | `koharu --headless --port 4000` | CLI |
| 2 | 建立專案 | `koharu.open_project` | MCP |
| 3 | 匯入頁面 | `import_epub` / `import_pages` | Python → HTTP |
| 4 | 偵測文字+泡泡 | `koharu.start_pipeline` | MCP → polling |
| 5 | Agent 審查偵測 | `GET /scene.json` + `koharu.apply` | HTTP + MCP |
| 6 | OCR + 遮罩 + 字型偵測 | `koharu.start_pipeline` | MCP → polling |
| 7 | 頁面品質分析 | `analyze.py` | Python |
| 8 | 章節偵測 + 摘要 | `chapter.py` | Python |
| 9 | **翻譯**（agent LLM 或 DeepSeek API） | — | Agent + `koharu.apply` |
| 10 | 審查翻譯 | `GET /scene.json` | HTTP |
| 11 | 術語 + 用語一致性稽核 | `verify.py` | Python |
| 12 | 除字（inpaint） | `koharu.start_pipeline` | MCP |
| 13 | 合成（render） | `koharu.start_pipeline` | MCP |
| 14 | 最終審查 | `GET /scene.json` | HTTP |
| 15 | 匯出 | `POST /export` | HTTP |
| 16 | 關閉專案 | `koharu.close_project` | MCP |

## Scripts / 腳本

| Script | Purpose / 用途 | Dependencies |
|--------|---------|-------------|
| `run_volume.py` | **一鍵跑完整卷**：import→detect→OCR→切片→validate→apply→除字(GPU→CPU 自動容錯)→render→QA→export→更新 queue | `httpx` |
| `validate_batch.py` | subagent 翻譯批次稽核：0-based/1-based 錯位偵測、覆蓋率、重複/空白 key | `httpx` |
| `import_epub.py` | 從漫畫 EPUB 提取圖片（含 zipfile 後備 + 卷號模糊比對） | `ebooklib`, `beautifulsoup4`, `Pillow` |
| `import_pages.py` | 批次匯入圖片至 Koharu | `httpx` |
| `glossary.py` | 術語表管理（fetch/import/template） | `httpx` |
| `analyze.py` | 頁面品質分析與保護 | `httpx` |
| `chapter.py` | 章節邊界偵測 | `httpx` |
| `call_llm.py` | DeepSeek/OpenAI API 批次翻譯 | `httpx` |
| `ocr_mistral.py` | Mistral OCR API 重新掃描 | `httpx` |
| `verify.py` | 術語合規 + 重複句一致性稽核 | `httpx` |
| `koharu_api.py` | 共用 Koharu HTTP API client | `httpx` |

## Parallel pipeline / 平行流水線

GPU 除字 + render 期間可平行處理其他卷：

```bash
# 實例 A（GPU, 4000）：卷 N 完整處理
<PFX> -m run_volume --queue q.json --id volume-N

# 實例 B（CPU, 4001）：卷 N+1 先準備原文（dump-only）
<PFX> -m run_volume --queue q.json --id volume-N+1 --port 4001 --dump-only
# → 派 subagent 翻譯卷 N+1 的 slice（純 LLM，不碰 GPU）
# → 卷 N 完成後，卷 N+1 再跑一次（--skip-translate 合併既有 slice）
<PFX> -m run_volume --queue q.json --id volume-N+1 --port 4001 --skip-translate
```

`--dump-only`：只 import/detect/OCR/dump+切片，退出（exit 0，queue 標 `dumped`）。
`--skip-translate`：跳過翻譯、合併既有 slice + 除字 + render。
`--inpaint-only`：只續跑除字（專案保留在 scene.bin）。
`--port`：指定 server 埠，支援多實例並行。

## Translation modes / 翻譯模式

| Mode | Speed | Quality | Cost |
|------|-------|---------|------|
| **Agent LLM**（預設，subagent 切片平行） | ~30-60 min / 100 頁 | 最佳（可套用複雜規則） | 免費 |
| **DeepSeek API**（Option B） | ~3-5 min / 100 頁 | 良好 | ~$0.10-0.50 |

## Glossary sources / 術語表來源

1. **Wikipedia fetch** — 自動偵測作品、查 zh.wikipedia.org 台版官方譯名
2. **AiNiee import** — 從既有 AiNiee `config.json` 匯入
3. **Template** — 空白骨架手寫

## Wording consistency / 用語一致性

三層機制確保全書（及跨次翻譯）角色名、術語、重複台詞一致：

1. **術語表**（`glossary.locked.json`）— 名稱/術語的權威對照，由 `verify.py check` 強制。
2. **翻譯記憶** — `call_llm.py --tm tm.json` 記錄每個 `source → translation`；相同原文在後續頁/章強制沿用，跨次保留。
3. **重複句漂移檢查** — `verify.py` 群組相同原文、標出譯法不一致者，建議以最常見措辭為 canonical 重 render。

## Credits / 致謝

本技能是「編排層」——真正的 ML 能力來自以下開源專案。感謝各位作者與原始工作：

- **Koharu** — 開源漫畫翻譯工具（headless server + 全部 ML 管線）。by [mayocream](https://github.com/mayocream)，<https://github.com/mayocream/koharu>。本技能只是它的 client。
- **comic-text-and-bubble-detector** — 文字/泡泡偵測。by [ogkalu](https://github.com/ogkalu/comic-text-detector)。
- **speech-bubble-segmentation** — 泡泡遮罩。by [mayocream](https://github.com/mayocream)。
- **lama-manga** — 除字 inpaint（本流水線預設，經實測 25+ 卷）。by [mayocream](https://github.com/mayocream)（manga_lama 系列）。
- **PaddleOCR-VL-1.6** — 日文 OCR（本流水線預設）。by [PaddlePaddle](https://github.com/PaddlePaddle)，GGUF 版來自 [PaddlePaddle/PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR-VL)。
- **yuzumarker-font-detection** — 字型/樣式偵測。by [fffonion](https://github.com/fffonion/yuzu-marker)。
- **Koharu Renderer** — 翻譯文字合成進泡泡。
- **FLUX.2 Klein / AOT Inpainting** — 選用 inpaint 引擎（實驗性，見 SKILL.md）。

> 版權與授權屬各原作者；本技能僅聚合其 API。漫畫圖像版權屬原出版社與作者，本工具僅供個人學習研究使用。

## References / 相關文件

- [`SKILL.md`](SKILL.md) — 完整技能指南（opencode 載入）
- [`references/api-reference.md`](references/api-reference.md) — HTTP API + MCP 工具參考
- [`references/pipeline-engines.md`](references/pipeline-engines.md) — ML 引擎目錄與 DAG
- [`references/scene-types.md`](references/scene-types.md) — Scene/Op/Node JSON 型別
- [`references/translation_rules.md`](references/translation_rules.md) — 翻譯規則
- [`references/glossary_format.md`](references/glossary_format.md) — 術語表 `.json` schema
- [`references/workflow-patterns.md`](references/workflow-patterns.md) — 常用工作模式
