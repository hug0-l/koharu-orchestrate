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
~/.venvs/koharu-orchestrate/bin/pip install -r "$SKILL_DIR/requirements.txt"
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

## 完整翻譯流程（16 步驟）

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
  "steps": ["comic-text-bubble-detector", "speech-bubble-segmentation"]
}
```

> `comic-text-bubble-detector` 同時偵測文字區塊與泡泡輪廓（Koharu 預設引擎），產生 `TextBoxes`；`speech-bubble-segmentation` 產生泡泡遮罩供後續排版用。
>
> 若需要純文字區塊偵測（無泡泡輪廓），可用 `pp-doclayout-v3` 替換。

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
  "removeNode": {
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
    "paddle-ocr-vl-1.5",
    "yuzumarker-font-detection"
  ]
}
```

引擎順序自動由 DAG 解析器決定（`comic-text-detector-seg` 需要 `TextBoxes`，在 Detect 已產生）。

- `comic-text-detector-seg`：文字區域遮罩（用於 Inpaint 除字）
- `paddle-ocr-vl-1.5`：OCR 辨識文字（Koharu 預設 OCR 引擎）
- `yuzumarker-font-detection`：字體/顏色分析（供 Render 使用）

> 若需要更新版的 OCR 可試 `paddle-ocr-vl-1.6`（較新但可能需額外下載）。VRAM 不足時可換 `manga-ocr` 或 `mit48px-ocr`。

##### 使用步驟簡稱

Koharu 也接受管線步驟的高層別名，agent 可寫更簡潔的指令：

```json
koharu.start_pipeline {
  "steps": ["detect", "ocr", "inpaint", "render"]
}
```

支援的別名：`detect`、`ocr`、`inpaint`、`render`。別名會自動解析為對應的預設引擎 ID。翻譯步驟（`llm-translate`）在技能中由 agent 取代，不經由 Koharu 的 `llm` engine。

##### 替代選項：Mistral OCR API

若 Koharu 內建 OCR 品質不足，可用 Mistral OCR API 重新掃描頁面：

```bash
# 重新掃描全部頁面，輸出修正後的 OCR JSON
<PFX> -m ocr_mistral rescan \
  --server $KOHARU_URL \
  --api-key "$MISTRAL_API_KEY" \
  --output /tmp/corrected_ocr.json

# 掃描後自動寫回 Koharu
<PFX> -m ocr_mistral rescan \
  --server $KOHARU_URL \
  --api-key "$MISTRAL_API_KEY" \
  --apply

# 或分兩步：先掃描、再手動套用
<PFX> -m ocr_mistral apply \
  --server $KOHARU_URL \
  --input /tmp/corrected_ocr.json
```

- 使用 Mistral OCR API（需申請 API key，設 `MISTRAL_API_KEY` 環境變數）
- 自動比對 Mistral 回傳的文字區塊與 Koharu 既有 node（IoU 配對）
- 僅更新 OCR 原文，不影響既有翻譯
- 適合內建 OCR 對特殊字體/手寫字辨識不佳時使用

### 步驟 7：分析頁面品質與保護（可選但建議）

OCR 完成後，分析哪些頁面品質不足以翻譯（封面、裝飾頁、低信度 OCR），自動標記跳過：

```bash
# 分析並顯示報告
<PFX> -m analyze --server $KOHARU_URL

# 輸出 JSON 供 agent 進一步處理
<PFX> -m analyze --server $KOHARU_URL --json > analysis.json

# 標記低品質頁面（輸出 protected_pages.json，不修改 Koharu scene 資料）
<PFX> -m analyze --server $KOHARU_URL --apply-protection
```

> `--apply-protection` 不會清除 Koharu 中的 OCR 文字。它輸出 `protected_pages.json`，翻譯步驟會參考此檔案跳過受保護的頁面。若要取消保護，刪除該檔案即可。

保護判斷邏輯：
- **封面頁**：文字 node 數 ≤ 3 且全為短裝飾文字
- **無 OCR 頁**：detect 有框到文字區但 OCR 沒讀到內容
- **低信度頁**：平均 confidence < 0.3
- **裝飾頁**：文字全為符號/點/非語言內容

agent 可先看 report，再用 `--apply-protection` 或手動 `koharu.apply removeNode` 剔除誤判框。

### 步驟 8：偵測章節邊界與生成摘要（可選但建議）

翻譯前先了解漫畫的章節結構，讓 agent 翻譯時有上下文脈絡：

```bash
# 偵測章節邊界
<PFX> -m chapter detect --server $KOHARU_URL

# 輸出 JSON 供 agent 生成摘要
<PFX> -m chapter detect --server $KOHARU_URL --json > chapters.json
```

自動偵測方式：
1. **明確章節標記**：`第1話`、`Chapter 1`、`VOL.1` 等
2. **自然分段**：偵測頁面之間的間隔頁（文字極少的頁面）作為章節邊界
3. **無標記時**：整卷視為一個章節

取得章節結構後，agent 為每個章節生成摘要（供翻譯時參考上下文）：

```
chapters.json → agent 用 LLM 或 DeepSeek API 為每章生成 2-3 句摘要
              → 摘要注入翻譯 prompt 的 system message
              → agent 翻譯時知道這章在講什麼，人物/語氣更一致
```

### 步驟 9：翻譯（核心步驟 — 選 A 或 B）

翻譯階段提供兩種模式，依速度和品質需求選擇。

**選項 A（建議首次使用）：Agent 自身 LLM 翻譯**
**選項 B（大量頁面時更快）：DeepSeek / OpenAI-compatible API 批次翻譯**

---

#### 選項 A：Agent 自身 LLM 翻譯

##### 9a. 讀取 OCR 文字

```bash
curl -s $KOHARU_URL/api/v1/scene.json > scene.json
```

從 `scene.json` 提取每個 Text node 的：
- `page` (page ID)
- `id` (node ID)
- `text` (OCR 辨識結果)
- `confidence` (可選，過濾低信度辨識)

> **受保護頁面**：若步驟 7 產生了 `protected_pages.json`，先讀取 `protected_pages[].page_id`，跳過這些頁面不翻譯。

##### 9b. 格式化為標籤區塊

對每一頁，**依視覺閱讀順序**（不是 scene.json 的 dict 排列順序）將文字格式化：

```
[1] source text from first bubble
[2] source text from second bubble
[3] source text from third bubble
...
```

排序規則（日漫 RTL 為預設）：
- 用 node 的 `transform` 取中心點 `(cx, cy)`
- 先依 `cy` 上到下，再依 `cx`：RTL 從右到左（`cx` 遞減）、LTR 從左到右（`cx` 遞增）

記錄每個 `[N]` 對應的 `(page_id, node_id)`。

##### 9c. 套用術語表 + 章節摘要 + 翻譯規則翻譯

翻譯時遵循：

1. **通用規則**：見 `references/translation_rules.md`（逐行對應、保留標記、人名保留/翻譯）
2. **章節摘要**：步驟 8 產生的摘要注入 system message，讓翻譯有上下文
3. **術語表**：見 `$WORK/work/glossary.locked.json`（若不存在則第一次翻譯前需建立）
4. **人物名稱**：依術語表 `characters[].render`：
   - 英文值 → 保留原文
   - 非英文值 → 使用該譯名
5. **專有名詞**：依術語表 `terms[].dst` 決定是否翻譯
6. **翻譯記憶**（用語一致性）：若已有 `tm.json`，相同原文的台詞（口癖、咒語、固定稱呼）必須沿用舊譯文，不得改寫。選項 B 會自動強制覆寫；選項 A 需 agent 自行查對 `tm.json`。

##### 9d. 寫回翻譯

對每個 text node，用 `koharu.apply` 寫回 `translation`：

```json
koharu.apply {
  "updateNode": {
    "page": "<pageId>",
    "id": "<nodeId>",
    "patch": {
      "data": {
        "text": {
          "translation": "譯文內容"
        }
      }
    },
    "prev": {}
  }
}
```

建議對**同一頁**的所有 node，包成一個 `batch` op：

```json
koharu.apply {
  "batch": {
    "ops": [
      { "updateNode": { "page": "...", "id": "...", "patch": { "data": { "text": { "translation": "..." } } }, "prev": {} } },
      { "updateNode": { "page": "...", "id": "...", "patch": { "data": { "text": { "translation": "..." } } }, "prev": {} } }
    ],
    "label": "translate page 1"
  }
}
```

##### 9e. 翻譯提示詞範例

```
Translate the following manga dialogue into {target_language}.
Follow the glossary for character names and terms.
Preserve all [N] tags.
Return only the translations, one per line, with the [N] prefix.

Chapter context (for reference):
[Chapter 1: Botan gets drunk at a party and Aria takes care of her.]

Glossary:
- Cecil → Cecil (keep source)
- Aria → 亞莉亞

[1] Cecil, wait up!
[2] I found the hidden passage behind the bookshelf.
[3] Aria, be careful!
```

`target_language` 由用戶指定（例如 `"Traditional Chinese"`、`"Korean"`）。

##### 9f. 解析翻譯回應

回應範例：

```
[1] Cecil，等等！
[2] 我在書架後面找到了隱藏的通道。
[3] 亞莉亞，小心！
```

逐行解析 `[N]` 前綴，將譯文對應回 `(page_id, node_id)`，然後 `koharu.apply` 寫回。

注意過濾 thinking block（`<think>...</think>`）、strip 包裹引號。

---

#### 選項 B（大量頁面時更快）：DeepSeek / OpenAI-compatible API 批次翻譯

適用於 50+ 頁面的大量翻譯。一次 API call 翻譯 30-50 段文字，大幅加速。

```bash
# 一次性翻譯全部 OCR 文字 → 輸出 translations.json
call_llm.py translate \
  --server $KOHARU_URL \
  --api-key $DEEPSEEK_API_KEY \
  --model deepseek-chat \
  --backend https://api.deepseek.com/v1 \
  --lang "Traditional Chinese" \
  --glossary $WORK/glossary.locked.json \
  --rules $SKILL_DIR/references/translation_rules.md \
  --chapters $WORK/chapters.json \
  --protected $WORK/protected_pages.json \
  --tm $WORK/tm.json \
  --reading-order rtl \
  --batch-size 40 \
  --output $WORK/translations.json
```

**流程：**
1. `call_llm.py` 從 Koharu 讀取所有 OCR text
2. 依 `--reading-order` 排序（RTL 從右到左、LTR 從左到右），並跳過 `--protected` 標記的頁面
3. 批次格式化為 `[N]` tagged blocks
4. 每批 40 段送 DeepSeek API（自動附帶術語表 + 翻譯規則 + `--chapters` 章節摘要）
5. 解析 API 回應
6. 套用翻譯記憶（`--tm`）：相同原文強制沿用舊譯文；新配對寫回 `tm.json`
7. 輸出 `translations.json`（`page_id, node_id, translation`）

**翻譯記憶（用語一致性）：**
`tm.json` 是 `{ "原文": "譯文" }` 的對應表，跨批次、跨章節、跨次翻譯都保留：
- 同一句台詞重複出現 → 一律使用首次翻譯的措辭，保證全書一致
- 若想更換某個用語，編輯 `tm.json` 後重跑即可（先刪掉要改的條目）
- 沒有 `--tm` 時此機制自動停用

**寫回 Koharu：**

```bash
# agent 將 translations.json 轉換為 batch ops 寫回
# 可用 python 腳本或 koharu.apply
python3 -c "
import json
data = json.load(open('$WORK/translations.json'))
ops = [{'updateNode': {
    'page': d['page_id'], 'id': d['node_id'],
    'patch': {'data': {'text': {'translation': d['translation']}}},
    'prev': {}
}} for d in data if d.get('translation')]
# 批次寫回
import httpx
httpx.post('$KOHARU_URL/api/v1/history/apply',
    json={'batch': {'label': 'batch translate', 'ops': ops}})
print(f'Written {len(ops)} translations')
"
```

**速度對比：**

| 模式 | 100 頁翻譯時間 | 成本 |
|------|---------------|------|
| Agent 自身 LLM | 30-60 min | 免費 |
| DeepSeek API (batch) | 3-5 min | ~$0.10-0.50 |
| 兩者混合 | 先用 API 批次 → agent 審查修正 | 低 + 品質可控 |

### 步驟 10：審查翻譯（選項 B 專用）

若使用選項 B（DeepSeek API），翻譯完成後 agent 應抽樣審查：
- 選 3-5 頁檢視譯文品質
- 發現系統性問題 → 修正 glossary 或 rules → 重新翻譯
- 發現個別問題 → `koharu.apply` 手動修正

### 步驟 11：驗證術語與用語一致性（建議）

翻譯寫回後，用 `verify.py` 稽核整本場景，找出術語表違規與「同句原文譯法不一」：

```bash
# 術語合規 + 重複句一致性報告
<PFX> -m verify check \
  --server $KOHARU_URL \
  --glossary $WORK/glossary.locked.json

# 機器可讀輸出（供 agent 批量修正）
<PFX> -m verify check \
  --server $KOHARU_URL \
  --glossary $WORK/glossary.locked.json --json > verify_report.json
```

檢查三類：
1. **術語違規**：人名未依 `render` 保留/翻譯、術語未用 `dst`、`keep_source` 被改譯
2. **重複句漂移**：相同原文在不同頁被譯成不同措辭 → 建議以最常見譯文為 canonical，`koharu.apply` 覆寫其他頁
3. **未翻譯**：有 OCR 原文但無譯文的 node

修正後跳回步驟 9d（或直接改對應 node）重跑渲染。

### 步驟 12：除字（Inpaint）

```json
koharu.start_pipeline {
  "steps": ["lama-manga"]
}
```

用遮罩去除原文文字。若 VRAM 夠可換 `flux2-klein`（品質更好但更慢）或 `aot-inpainting`。

### 步驟 13：合成（Render）

```json
koharu.start_pipeline {
  "steps": ["koharu-renderer"],
  "targetLanguage": "Chinese",
  "defaultFont": ".PingFang SC"    // macOS 內建中文字型
}
```

- `targetLanguage`：影響斷字、字型選取
- `defaultFont`：字型名稱。**務必先用以下指令確認系統可用字型再設定：**
  ```bash
  curl -s $KOHARU_URL/api/v1/fonts | python3 -c "
  import sys,json
  fonts = json.load(sys.stdin)
  cjk = [f for f in fonts if any(kw in f.get('familyName','') for kw in ['SC','TC','CJK','Song','Ming','Hei','Gothic'])]
  for f in set(cjk): print(f)
  "
  ```
  若不指定 `defaultFont`，Koharu 會自動 fallback 到系統字型。

### 步驟 14：最終審查

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

修正不滿意的翻譯或位置後回到步驟 9d 重跑。

### 步驟 15：匯出

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

### 步驟 16：關閉專案

```json
koharu.close_project
```

---

## 術語表管理

### 第一次翻譯前：建立術語表

**選項 A（推薦）— 從網路爬取官方中譯／台版譯名**

自動偵測作品名稱，產生 Wikipedia 查詢連結，讓 agent 爬取官方譯名：

```bash
# 從目錄名自動偵測作品
<PFX> -m glossary fetch \
  --from-dir "$WORK" \
  --out ./work/glossary.locked.json

# 或直接指定作品名
<PFX> -m glossary fetch \
  --series "上伊那ぼたん、酔へる姿は百合の花" \
  --out ./work/glossary.locked.json
```

產生骨架 `glossary.locked.json` 後，agent 會：
1. `webfetch` Wikipedia 條目（zh.wikipedia.org，`?variant=zh-tw`）
2. 從角色列表提取正式官方中譯名
3. 比對 Bangumi / 出版社官網確認
4. 填入 `characters[]`（日文原名 → 台版官方譯名）
5. 填入 `terms[].dst`（作品名、關鍵術語）
6. 用戶審查確認後鎖定

**選項 B — 從 AiNiee config 匯入（已有 AiNiee 術語設定時）**

```bash
<PFX> -m glossary import-ainiee \
  --config ~/Library/Application\ Support/AiNiee/config.json \
  --out ./work/glossary.locked.json
```

**選項 C — 產生空白模板手寫**

```bash
<PFX> -m glossary template \
  --out ./work/glossary.locked.json
```

**選項 D — 直接編寫**

手動建立 `glossary.locked.json`，格式見 `references/glossary_format.md`。

### 翻譯後：驗證術語合規

Agent 在步驟 9 翻譯後應執行 **步驟 11** 的 `verify.py` 稽核：

```bash
<PFX> -m verify check \
  --server $KOHARU_URL \
  --glossary $WORK/glossary.locked.json \
  --json > verify_report.json
```

`verify.py` 依 `references/translation_rules.md` + `references/glossary_format.md` 自動檢查：
- 人名是否依 `characters[].render` 保留/翻譯（含 alias 出現的場合）
- 術語是否使用 `terms[].dst`、`keep_source` 是否被誤譯
- 相同原文是否在書內各處譯法一致（用語一致性）

任何違規都應修正後才進行 Inpaint/Render。

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

# 術語表（三種方式）
$PFX -m glossary fetch --from-dir "$WORK" --out $WORK/glossary.locked.json
$PFX -m glossary import-ainiee --config "<config.json>" --out $WORK/glossary.locked.json
$PFX -m glossary template --out $WORK/glossary.locked.json

# Mistral OCR 重新掃描
$PFX -m ocr_mistral rescan \
  --server $KOHARU_URL --api-key "$MISTRAL_API_KEY" --apply

# 分析頁面品質（保護封面/低信度頁）
$PFX -m analyze --server $KOHARU_URL
$PFX -m analyze --server $KOHARU_URL --apply-protection

# 偵測章節邊界
$PFX -m chapter detect --server $KOHARU_URL
$PFX -m chapter detect --server $KOHARU_URL --json > chapters.json

# DeepSeek 批次翻譯（含章節摘要、受保護頁、翻譯記憶、閱讀順序）
$PFX -m call_llm translate \
  --server $KOHARU_URL --api-key $DEEPSEEK_API_KEY \
  --lang "Traditional Chinese" \
  --glossary $WORK/glossary.locked.json \
  --chapters $WORK/chapters.json \
  --protected $WORK/protected_pages.json \
  --tm $WORK/tm.json \
  --reading-order rtl \
  --output $WORK/translations.json

# 驗證術語合規 + 用語一致性
$PFX -m verify check \
  --server $KOHARU_URL \
  --glossary $WORK/glossary.locked.json
$PFX -m verify check \
  --server $KOHARU_URL \
  --glossary $WORK/glossary.locked.json --json > verify_report.json

# 檢視場景
curl -s $KOHARU_URL/api/v1/scene.json

# 監控管線（輪詢）
curl -s $KOHARU_URL/api/v1/operations

# 監控管線（SSE 事件串流）
curl -s -N $KOHARU_URL/api/v1/events

# 儲存 API key 到 Koharu 憑證儲存
curl -s -X PUT $KOHARU_URL/api/v1/config/providers/deepseek/secret \
  -H "Content-Type: text/plain" \
  -d "$DEEPSEEK_API_KEY"

# 匯出
curl -s -X POST $KOHARU_URL/api/v1/projects/current/export \
  -H "Content-Type: application/json" \
  -d '{"format": "rendered"}' -o output.zip
```

---

## 實戰筆記（整卷重做心得，2026-08 上伊那ぼたん 08）

### 匯入／清除
- `import_pages --replace` **不可靠**：實測舊頁不會被清掉，會疊出 2 倍頁數。重做時先用 clear-all 腳本清除（`removePage` batch，**欄位是 `prev_page`/`prev_index`（snake_case）**，不是文件舊寫的 `prevPage`），再重新匯入。
- `scene.json` 的 node kind 是小寫：`image`/`text`/`mask`；image `role` 也是小寫 `source`/`inpainted`/`rendered`。

### OCR
- 內建預設 `manga-ocr` 品質差（上一版翻譯爛的根源）。先 `PATCH /api/v1/config` 換 `paddle-ocr-vl-1.6`。低信度（<0.4）節點幾乎都是 SFX/背景字，翻譯時跳過即可。

### Render 溢出（文字超出泡泡）
- 成因：節點 `lockLayoutBox=false` 且 `fontPrediction.fontSizePx` 很大（如 129px）時，renderer 不做縮放直接溢出。
- 修法：render 前對所有文字節點 `patch {data:{text:{lockLayoutBox:true}}}` → 文字自動縮放進框。
- **無法看圖時的驗證法**：
  1. 溢出：render 後比對節點 `spriteTransform` 寬高 vs `transform`，ratio>1.2 即溢出；
  2. 文字可見性：算泡泡框內暗像素比例（<100 亮度）vs 除字底圖，rendered 應遠高於 inpainted。

### 字型
- **務必用 `source: system` 且 `cached: true` 的字型**。Google Font（`cached: false`）未下載時 renderer 整句不畫（log：`no font found`），成品文字會消失。
- 繁中圓體：`Yuanti TC`；繁中黑體：`.PingFang TC`；日式丸ゴ：`Hiragino Maru Gothic ProN`；手寫：`Hannotate TC`。用 `GET /api/v1/fonts` 檢查 `cached`，Google Font 可先 `POST /api/v1/google-fonts/{family}/fetch` 下載。

### 字型／樣式偵測流程（如何落到 render）
字型偵測是**管線第 3 步 `yuzumarker-font-detection`**（依賴第 1 步 detect 產生的 `TextBoxes`）。它把結果寫進每個文字節點的 `fontPrediction`，render 時由 renderer 讀取：

```jsonc
// 節點 kind.text 上的欄位（見 references/scene-types.md）
"fontPrediction": {
  "fontFamily": "Noto Sans",   // 偵測到的字型家族（不一定存在於系統）
  "fontSizePx": 32,            // 原圖文字大小（px）
  "textColor": { "r": 0, "g": 0, "b": 0 }   // 偵測到的文字顏色
},
"style": { "fontSize": 16, "textAlign": "left", "shaderEffect": null },  // 粗體/斜體等
"detectedFontSizePx": 24.0,    // 另一份字號估計
"renderedDirection": "horizontal"   // 直排/橫排
```

**偵測 → render 的對應規則（實測心得）：**
1. `fontPrediction.fontFamily` 只是「建議」，renderer **不保證**該字型存在。fallback 優先序是 `defaultFont`（pipeline 參數）→ 系統內建 → 偵測字型。**結論：別指望偵測字型，直接用系統 `defaultFont`。**
2. `fontSizePx` 與 `detectedFontSizePx` 是溢出元兇：偵測值常偏大（如 129px），`lockLayoutBox=false` 時 renderer 照此值畫 → 爆出泡泡。**所以 render 前必須全部 `lockLayoutBox:true` 強制縮放。**
3. `style.shaderEffect`（bold/italic）與 `textColor` render 會沿用——翻譯寫回時別改這兩個欄位，才能保留粗體/顏色。
4. 直排文字靠 `sourceDirection`/`renderedDirection`；譯成繁中後若想改橫排可 `patch renderedDirection`，但短句直排通常保留原樣較自然。

**需要「跟原稿字型相近」時的可行做法：** Koharu 只支援「系統/Google 已下載」的字型做 render（`GET /api/v1/fonts` 看 `cached`）。想貼近原稿風格就選相近的系統字型當 `defaultFont`，例如：
- 原稿丸ゴ手繪 → `Hiragino Maru Gothic ProN` / `Yuanti TC`
- 原稿黑體 → `.PingFang TC` / `Noto Sans TC`
- 原稿手寫感 → `Hannotate TC`

### Inpaint 失敗
- 遮罩大的頁面在 Metal GPU 會 `Failed to create metal resource: Buffer`（OOM），嚴重時整台 server 被殺掉（無 log）。
- 解法：只對失敗頁重試；仍失敗就重啟 Koharu 加 `--cpu`，用 CPU 處理那幾頁。專案進度存 `scene.bin`，重啟後 reopen 續跑。

### 翻譯與一致性
- 術語表用官方譯名：`webfetch zh.wikipedia.org`（`?variant=zh-tw`）。
- `verify.py` 人名檢查會把「台詞中用簡稱（牡丹/伊吹）」誤報——短名用法正確，只需修真正的不一致（如 先輩→學姊 統一）。
- 手寫逐章翻譯陣列容易錯位，改用 index-keyed dict 或直接 node_id 對應，並用長度斷言校驗。

### Subagent 平行翻譯（2026-08 大量卷心法）
- **切片調度**：每卷切成 300 條/片（`trans/vXX_slice_NN.json`），一次平行派 4-6 個 subagent，每個負責一片。
- **派發後必跑 `validate_batch.py`** 稽核：
  ```bash
  <PFX> -m validate_batch \
    --all-text $WORK/work/all_text_vXX.json \
    --trans $WORK/trans/ \
    --expected $(python3 -c "import json;print(len(json.load(open('$WORK/work/all_text_vXX.json'))))")
  ```
  - **0-based 錯位偵測**：subagent 常把「元素位置 1-based」誤寫成陣列 index。`validate_batch` 用「原文↔譯文共享字元加權分數」比較 +0/+1 兩種偏移，`off=+1` 即代表整片錯位。用 `--fix` 自動寫 `*_fixed.json`。
  - **覆蓋率檢查**：`--expected N` 報缺漏條數；空白值、越界 key、重複 key 一併列出。
- **空手而歸處理**：subagent 整卷翻譯常回傳空（沒寫檔）。切片後只看哪些 slice 檔存在，缺的只補派，不必整卷重來。
- **prompt 契約**：明寫「用 Write 工具建立檔案才算完成，回覆不被評分」；內嵌切片首/中/尾元素的原文做 1-based 錨點；要求輸出前自我檢查 key 範圍與條數。

### run_volume.py — 一鍵跑完整卷（2026-08 pipeline 自動化）
`scripts/run_volume.py` 讀 queue.json 條目，自動完成 import→detect→OCR→dump→切片→validate→merge→apply→lock→除字→render→QA→export→更新 queue：

```bash
<PFX> -m run_volume --queue ~/koharu-work/queue.json --id <volume_id>
```

- **翻譯仍是 agent 工作**：腳本會印出 slice 範圍與錨點後以 exit 3 停下，等 slice 檔齊全再重跑（`--skip-translate` 表示 slice 已存在，直接 merge）。
- **除字 GPU→CPU 自動容錯**：內建 `wait_with_failover`——GPU Metal OOM 掉線時自動 `--cpu` 重啟、reopen 專案、續跑剩餘頁，不需人工介入。
- **除字預分割**：GPU 先跑前 110 頁，之後自動切 CPU，避免 crash 浪費尾段。
- **render 後自動 QA**：比對節點 `spriteTransform` vs `transform` 寬高比，>1.2 標為溢出並列報告。
- **queue 欄位**：`font`（render 用字型）、`keep_project`（完成後是否保留 scene.bin 供翻修）、`work_dir`、`project_id`。
- **resume**：`--inpaint-only` 只續跑除字；`--skip-translate` 跳過翻譯直接 merge 既有 slice + render。

### import_epub 卷號模糊比對
- EPUB 檔名含半形/組合假名（如 `ぼ` vs `ぼ`）時精確路徑會對不上。`find_epub_by_volume(dir, vol)` 依卷號 token 模糊匹配（支援 ` 4 ` / `第4巻` / `vol04`）。
- 用法：`import_epub --input <目錄> --volume 4 --output ./pages/`，或 run_volume 在 `source` 路徑不存在時自動對 queue 條目的目錄模糊匹配。

### 保護標題美術／音效不被除字（protect.py）
`comic-text-detector-seg` 常把裝飾性標題美術（title art）與狀聲詞/音效（SFX）當成文字泡泡框進 `segment` mask，inpaint 時一起擦掉，renderer 又會覆寫翻譯。`scripts/protect.py` 在 inpaint 前把受保護區域從 mask 挖掉 + 節點設 `visible:false`，讓原始美術原封不動：

```bash
# 保護特定節點（page:node 逗號分隔）
<PFX> -m protect --server $KOHARU_URL --nodes <pid>:<nid>,<pid2>:<nid2>

# 依 OCR 文字 regex 保護（例如全形片假名短音效）
<PFX> -m protect --server $KOHARU_URL --match '^[ァ-ヶー]{1,6}$'

# 保護整頁（封面/標題頁）
<PFX> -m protect --server $KOHARU_URL --pages <pid>,<pid2>

# 預覽不寫入
<PFX> -m protect --server $KOHARU_URL --pages <pid> --dry-run
```

**run_volume 整合**：queue 條目加 `"protect": "work/protect.json"` 或傳 `--protect protect.json`，OCR 完成後、inpaint 前自動套用。`protect.json` 格式：`{"nodes":["pid:nid"],"pages":["pid"],"match":"regex"}`。

**by-node vs by-page 差異（實測）**：
- `--pages`（整頁保護）：該頁所有文字節點都保留——適合封面、目次、章節標題頁（標題美術）。
- `--nodes pid:nid`（單一節點）：只保護指定節點，**同頁其他對白照常翻譯**——適合「對話頁裡混著一個大音效字」。
- 用 `--match` 依 OCR 文字 regex 批量選節點（例：`'^[ァ-ヶー]{1,8}$'` 純片假名音效）。
- 驗證（by-node 實測）：SFX 節點區域暗像素比 source=inpainted=rendered=0.0201 分毫不差，同頁對白全部有翻譯。

**實作原理**：
1. 抓每頁 `segment` mask blob（L 影像，白=除字區域），把受保護文字節點的 bbox 塗黑，`PUT /api/v1/pages/{id}/masks/segment` 重新上傳 → inpaint 會略過。
2. 受保護節點 `patch {visible:false}` + 清空 translation → renderer 不會畫翻譯覆蓋。
3. 用「受保護區暗像素比 vs 除字後」驗證：相同=美術保留；控制組（未保護）會下降。

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
A: 重新 `koharu.apply UpdateNode` 覆寫 `translation`，然後只跑步驟 13（Render）即可，不必重新 OCR。

**Q: 同一個詞／同一句台詞在不同頁譯法不一致？**
A: 這是 `verify.py`（步驟 11）的 `repeat` 檢查會抓的問題。修法：
1. 編輯 `tm.json`，把該詞/句改成你想要統一的譯文
2. 用 `call_llm.py --tm tm.json` 重跑，相同原文會強制套用新譯
3. 或直接用 `koharu.apply UpdateNode` 手動覆寫那幾頁的 `translation`，再重跑 Render

---

## 相關文件

- [`references/api-reference.md`](references/api-reference.md) — 完整 HTTP API + MCP 參考
- [`references/pipeline-engines.md`](references/pipeline-engines.md) — 引擎目錄與 DAG
- [`references/scene-types.md`](references/scene-types.md) — Scene/Op/Node JSON 型別
- [`references/translation_rules.md`](references/translation_rules.md) — 翻譯通用規則
- [`references/glossary_format.md`](references/glossary_format.md) — 術語表格式
- [`references/workflow-patterns.md`](references/workflow-patterns.md) — 常用工作模式
