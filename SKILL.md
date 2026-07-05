---
name: koharu-orchestrate
description: Use when the user wants to translate a manga/comic end-to-end using Koharu as the ML backend (detect, OCR, inpaint, render) and the agent as the translation engine. Triggers on "translate this manga", "用 koharu 翻這本漫畫", "幫我把這本漫畫翻了" and similar.
---

# koharu-orchestrate 技能指南

## 總覽

本技能讓 **coding agent（Claude Code / Codex）** 搭配 **Koharu headless server** 端到端翻譯漫畫：

```
Koharu 負責（ML pipeline）：
  圖片輸入 → 文字泡泡偵測 → OCR 辨識 → Inpaint 除字 → Render 合成

Agent 負責（大腦 + 翻譯）：
  編排流程 → 監控進度 → 審查修正 → 套用術語表逐條翻譯 → QA → 匯出
```

Agent 用自己的 LLM 能力取代 Koharu 的 `llm` engine，確保：
- 翻譯品質可控（套用 `translation_rules.md` + `glossary.locked.json`）
- 術語一致性（人名保留、專有名詞鎖定）
- 每頁可獨立審查和修正

---

## 前置依賴與安裝

### 1. 安裝 Koharu

```bash
# macOS
brew install --cask koharu

# Windows (winget)
winget install koharu

# 或從 Releases 下載：https://github.com/mayocream/koharu/releases
```

### 2. Python venv + 依賴

```bash
python3 -m venv ~/.venvs/koharu-orchestrate
~/.venvs/koharu-orchestrate/bin/pip install httpx ebooklib beautifulsoup4 pillow
```

### 3. 設置路徑變數（每個新終端）

```bash
export SKILL_DIR=~/.config/opencode/skills/koharu-orchestrate
export KOHARU_PY=~/.venvs/koharu-orchestrate/bin/python
export KOHARU_URL=http://localhost:4000
```

### 命令前綴（後文用 `<PFX>` 代替）

```bash
PYTHONPATH="$SKILL_DIR/scripts" "$KOHARU_PY"
```

---

## 快速入門（一鍵端到端）

```bash
# 1. 啟動 Koharu
koharu --headless --port 4000

# 2. 打開另一個終端，建立專案並匯入頁面
koharu.open_project { path: "/path/to/proj", createName: "My Manga" }

# 3. 匯入漫畫頁面（從 EPUB 或目錄）
<PFX> -m import_epub --input book.epub --output ./pages/
<PFX> -m import_pages --server $KOHARU_URL --dir ./pages/

# 4. 執行完整管線（步驟見下）
```

---

## 完整翻譯流程（12 步驟）

### 步驟 1：啟動 Koharu Headless Server

```bash
koharu --headless --port 4000
```

- `--headless`：不啟動 GUI 視窗
- `--port 4000`：HTTP API + MCP 監聽於 `http://localhost:4000`
- 可加 `--debug` 看詳細日誌
- 可加 `--cpu` 強制 CPU 模式（無 GPU 時）

等待 `Koharu ready` 日誌出現後再繼續。

### 步驟 2：建立或開啟專案

MCP 工具 `koharu.open_project`：

```json
{
  "path": "/path/to/your/project.khrproj",
  "createName": "My Manga"
}
```

若 `createName` 有值，則在該路徑新建專案；否則開啟既有專案。

回應：`{ name: "My Manga", path: "/path/to/your/project.khrproj" }`

### 步驟 3：匯入頁面

**選項 A：從 EPUB 提取圖片**

```bash
<PFX> -m import_epub \
  --input /path/to/book.epub \
  --output ./pages/
```

成功後列印提取的圖片數量與路徑。

**選項 B：從目錄匯入既有圖片**

```bash
<PFX> -m import_pages \
  --server $KOHARU_URL \
  --dir ./pages/ \
  --replace
```

- `--replace`：清除 Koharu 中現有頁面再匯入（首次建議加）
- 支援格式：`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`
- 按檔名自然排序匯入（`page_01.png` → `page_02.png`）

### 步驟 4：Detect 階段 — 文字框 + 泡泡偵測

```json
koharu.start_pipeline {
  "steps": ["pp-doclayout-v3", "speech-bubble-segmentation"]
}
```

> `pp-doclayout-v3` 偵測文字區塊並產生 `Text` node；`speech-bubble-segmentation` 產生泡泡遮罩供後續排版用。

回應：`{ jobId: "uuid" }`

**監控進度（polling）：**

```bash
curl -s $KOHARU_URL/api/v1/operations
```

篩選 `operations[].id == jobId`，看 `status`：
- `running` → 繼續等待（sleep 2s）
- `completed` → 下一步
- `failed` → 查看 `error`，重試或跳過

### 步驟 5：Agent 審查偵測結果（可選但建議）

檢查文字框位置是否正確：

```bash
curl -s $KOHARU_URL/api/v1/scene.json | python3 -m json.tool
```

查看每個 page 的 `nodes`，注意：
- `Text` node 是否有遺漏（該被偵測的對話框沒被框到）
- 多餘的 `Text` node（背景文字被誤判）
- `Text` node 的 `transform`（位置/大小）是否合理

修正方式：

```json
// 移除誤判文字框
koharu.apply {
  "op": {
    "type": "RemoveNode",
    "page": "<pageId>",
    "id": "<nodeId>"
  }
}
```

### 步驟 6：OCR + 遮罩 + 字體偵測

```json
koharu.start_pipeline {
  "steps": [
    "comic-text-detector-seg",
    "paddle-ocr-vl-1.6",
    "yuzumarker-font-detection"
  ]
}
```

引擎順序自動由 DAG 解析器決定（`comic-text-detector-seg` 需要 `TextBoxes`，在 Detect 已產生）。

- `comic-text-detector-seg`：文字區域遮罩（用於 Inpaint 除字）
- `paddle-ocr-vl-1.6`：OCR 辨識文字
- `yuzumarker-font-detection`：字體/顏色分析（供 Render 使用）

> `paddle-ocr-vl-1.6` 需要較多 VRAM。若資源有限，可換 `manga-ocr` 或 `mit48px-ocr`。

### 步驟 7：Agent 翻譯（核心步驟）

這是整個流程的核心 — Agent **用自己的 LLM 能力**翻譯。

#### 7a. 讀取 OCR 文字

```bash
curl -s $KOHARU_URL/api/v1/scene.json > scene.json
```

從 `scene.json` 提取每個 Text node 的：
- `page` (page ID)
- `id` (node ID)
- `text` (OCR 辨識結果)
- `confidence` (可選，過濾低信度辨識)

#### 7b. 格式化為標籤區塊

對每一頁，按閱讀順序（從 scene.json 中 node 的排列順序）將文字格式化為：

```
[1] source text from first bubble
[2] source text from second bubble
[3] source text from third bubble
...
```

記錄每個 `[N]` 對應的 `(page_id, node_id)`。

#### 7c. 套用術語表 + 翻譯規則翻譯

翻譯時遵循：

1. **通用規則**：見 `references/translation_rules.md`（逐行對應、保留標記、人名保留/翻譯）
2. **術語表**：見 `$WORK/work/glossary.locked.json`（若不存在則第一次翻譯前需建立）
3. **人物名稱**：依術語表 `characters[].render`：
   - 英文值 → 保留原文
   - 非英文值 → 使用該譯名
4. **專有名詞**：依術語表 `terms[].dst` 決定是否翻譯

#### 7d. 寫回翻譯

對每個 text node，用 `koharu.apply` 寫回 `translation`：

```json
koharu.apply {
  "op": {
    "type": "UpdateNode",
    "page": "<pageId>",
    "id": "<nodeId>",
    "patch": {
      "translation": "譯文內容"
    }
  }
}
```

建議對**同一頁**的所有 node，包成一個 `Batch` op：

```json
koharu.apply {
  "op": {
    "type": "Batch",
    "ops": [
      { "type": "UpdateNode", "page": "...", "id": "...", "patch": { "translation": "..." } },
      { "type": "UpdateNode", "page": "...", "id": "...", "patch": { "translation": "..." } }
    ],
    "label": "translate page 1"
  }
}
```

#### 7e. 翻譯提示詞範例

```
Translate the following manga dialogue into {target_language}.
Follow the glossary for character names and terms.
Preserve all [N] tags.
Return only the translations, one per line, with the [N] prefix.

Glossary:
- Cecil → Cecil (keep source)
- Aria → 亞莉亞

[1] Cecil, wait up!
[2] I found the hidden passage behind the bookshelf.
[3] Aria, be careful!
```

`target_language` 由用戶指定（例如 `"Traditional Chinese"`、`"Korean"`）。

#### 7f. 解析翻譯回應

回應範例：

```
[1] Cecil，等等！
[2] 我在書架後面找到了隱藏的通道。
[3] 亞莉亞，小心！
```

逐行解析 `[N]` 前綴，將譯文對應回 `(page_id, node_id)`，然後 `koharu.apply` 寫回。

注意過濾 thinking block（`<think>...</think>`）、strip 包裹引號。

### 步驟 8：除字（Inpaint）

```json
koharu.start_pipeline {
  "steps": ["lama-manga"]
}
```

用遮罩去除原文文字。若 VRAM 夠可換 `flux2-klein`（品質更好但更慢）或 `aot-inpainting`。

### 步驟 9：合成（Render）

```json
koharu.start_pipeline {
  "steps": ["koharu-renderer"],
  "targetLanguage": "Chinese",
  "defaultFont": "Noto Sans SC"
}
```

- `targetLanguage`：影響斷字、字型選取
- `defaultFont`：字型名稱。可用 `GET /api/v1/fonts` 列出系統可用字型

### 步驟 10：最終審查

```bash
curl -s $KOHARU_URL/api/v1/scene.json | python3 -c "
import json, sys
scene = json.load(sys.stdin)
pages = scene['scene']['pages']
for pid, page in pages.items():
    print(f'Page: {page.get(\"name\", pid)}')
    for nid, node in page.get('nodes', {}).items():
        k = node.get('kind', {})
        if 'Text' in k:
            t = k['Text']
            src = t.get('text', '')[:50]
            dst = t.get('translation', '')[:50]
            print(f'  {nid[:8]}: {src} → {dst}')
"
```

修正不滿意的翻譯或位置後回到步驟 7d 重跑。

### 步驟 11：匯出

```bash
curl -s -X POST $KOHARU_URL/api/v1/projects/current/export \
  -H "Content-Type: application/json" \
  -d '{"format": "rendered"}' \
  -o ./output.zip
```

支援格式：
- `rendered`：合成後的渲染圖片（ZIP）
- `psd`：Photoshop PSD 分層檔案
- `khr`：Koharu 專案存檔
- `inpainted`：僅除字後的圖片

### 步驟 12：關閉專案

```json
koharu.close_project
```

---

## 術語表管理

### 第一次翻譯前：建立術語表

**選項 A — 從 AiNiee config 匯入（已有 AiNiee 術語設定時）**

```bash
<PFX> -m glossary import-ainiee \
  --config ~/Library/Application\ Support/AiNiee/config.json \
  --out ./work/glossary.locked.json
```

**選項 B — 產生空白模板手寫**

```bash
<PFX> -m glossary template \
  --out ./work/glossary.locked.json
```

**選項 C — 直接編寫**

手動建立 `glossary.locked.json`，格式見 `references/glossary_format.md`。

### 翻譯後：驗證術語合規

Agent 在步驟 7 翻譯後可以用 `references/translation_rules.md` 的規則自行 verify（如人名保留檢查、術語一致性檢查）。

---

## 命令速查

```bash
# 路徑變數
export SKILL_DIR=~/.config/opencode/skills/koharu-orchestrate
export KOHARU_PY=~/.venvs/koharu-orchestrate/bin/python
export KOHARU_URL=http://localhost:4000
export WORK=~/my-manga

PFX="PYTHONPATH=$SKILL_DIR/scripts $KOHARU_PY"

# 啟動 Koharu
koharu --headless --port 4000

# EPUB 提取圖片
$PFX -m import_epub --input book.epub --output $WORK/pages/

# 匯入頁面到 Koharu
$PFX -m import_pages --server $KOHARU_URL --dir $WORK/pages/ --replace

# 術語表
$PFX -m glossary import-ainiee --config "<config.json>" --out $WORK/glossary.locked.json
$PFX -m glossary template --out $WORK/glossary.locked.json

# 檢視場景
curl -s $KOHARU_URL/api/v1/scene.json

# 監控管線
curl -s $KOHARU_URL/api/v1/operations

# 匯出
curl -s -X POST $KOHARU_URL/api/v1/projects/current/export \
  -H "Content-Type: application/json" \
  -d '{"format": "rendered"}' -o output.zip
```

---

## 常見問題

**Q: Koharu 啟動報錯？**
A: 確認 GPU 驅動（NVIDIA/CUDA、AMD/ZLUDA、Apple Metal）或加 `--cpu` 跑 CPU 模式。

**Q: Model 下載很慢？**
A: Koharu 首次執行各模型時自動從 Hugging Face 下載。可用 `koharu --download` 預先下載。

**Q: 管線 job 失敗？**
A: `GET /api/v1/operations` 看 `error` 欄位。通常原因：VRAM 不足（換小模型或用 `--cpu`）、模型未下載（等待自動下載完成後重試）。

**Q: OCR 辨識品質不好？**
A: `paddle-ocr-vl-1.6` 最強但最慢。可用 `manga-ocr`（CRNN 模型，輕量但支援日文為主）。可在 `PATCH /api/v1/config` 切換 `pipeline.ocr`。

**Q: 如何只重翻某幾頁的翻譯？**
A: 重新 `koharu.apply UpdateNode` 覆寫 `translation`，然後只跑步驟 9（Render）即可，不必重新 OCR。

---

## 相關文件

- [`references/api-reference.md`](references/api-reference.md) — 完整 HTTP API + MCP 參考
- [`references/pipeline-engines.md`](references/pipeline-engines.md) — 引擎目錄與 DAG
- [`references/scene-types.md`](references/scene-types.md) — Scene/Op/Node JSON 型別
- [`references/translation_rules.md`](references/translation_rules.md) — 翻譯通用規則
- [`references/glossary_format.md`](references/glossary_format.md) — 術語表格式
- [`references/workflow-patterns.md`](references/workflow-patterns.md) — 常用工作模式
