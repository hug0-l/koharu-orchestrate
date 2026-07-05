# 常用工作模式

## 模式 1：端到端完整翻譯

從空白專案到完成翻譯的完整流程。

```
start_koharu → open_project → import_pages → detect → review_boxes → ocr → translate → inpaint → render → review → export → close
```

適用：首次翻譯一部新漫畫。

**關鍵參數決策點：**
- Detect engine：`pp-doclayout-v3`（預設，一般漫畫效果佳）/ `comic-text-bubble-detector`（有泡泡結構時）/ `anime-text`（動畫截圖用）
- OCR engine：`paddle-ocr-vl-1.6`（最好但最重）/ `manga-ocr`（輕量，日文為主）
- Inpainter：`lama-manga`（快）/ `aot-inpainting`（品質好）/ `flux2-klein`（最好但需 GPU）

## 模式 2：單頁/局部重譯

只重新翻譯特定頁面的特定對話框，不重新 OCR。

```
koharu.apply UpdateNode → render (單頁)
```

**應用場景：** 翻譯審查時發現某個對話框譯文不理想，或用戶要求修改特定對話框。

**流程：**
```
1. GET /api/v1/scene.json → 找到目標 node (page_id, node_id)
2. koharu.apply UpdateNode { patch: { translation: "新譯文" } }
3. koharu.start_pipeline { steps: ["koharu-renderer"], pages: ["<page_id>"] }
4. export
```

## 模式 3：批次導入 + 自動處理

一次匯入大量圖片，自動跑完整管線。

**應用場景：** 整卷漫畫（100+ 頁）、系列批量處理。

**流程：**
```
1. 將所有圖片放在目錄（或從 EPUB 提取）
2. import_pages --dir ./all-pages --replace
3. detect → ocr → 翻譯 → inpaint → render（全頁）
4. 翻譯時可用多 agent 並行（分頁分配）
```

## 模式 4：錯誤恢復

管線某步驟失敗時的策略。

| 錯誤類型 | 處理方式 |
|---------|---------|
| OCR model 載入失敗（VRAM 不足） | `PATCH /api/v1/config` 換較輕的 OCR（`manga-ocr`） |
| Inpaint 失敗 | 換 `aot-inpainting` 或跳過 Inpaint 直接 Render（用 Source Image） |
| 特定頁面失敗 | `start_pipeline` 設 `pages` 參數只跑失敗的頁面 |
| Koharu crash | 重啟 Koharu → `open_project`（進度已存 scene.bin）→ 從中斷處繼續 |

**跳過 Inpaint：**
Render 引擎在沒有 `Inpainted` image 時會自動 fallback 使用 `Source` image。所以可以省略 Inpaint 步驟直接 Render。

## 模式 5：翻譯品質檢查

翻譯完成後的 QA 流程。

```
1. 用 GET /api/v1/scene.json 提取所有 translation
2. 對照 glossary.locked.json：
   - 角色名在譯文中是否依 render 值處理
   - 術語是否一致
3. 發現問題 → UpdateNode 修正 → 重新 render
4. 確認無誤後 export
```

## 模式 6：多 Agent 並行翻譯（大量頁面加速）

當頁數很多（50+）、且翻譯風格已在前幾頁鎖定後，可派多個 subagent 並行翻譯不同頁面範圍。

**規則：**
- Subagent **不直接寫入 Koharu**（避免並發衝突）
- 每個 subagent 負責一個頁面範圍，輸出 `{page_id, node_id, translation}` 的 JSON
- 主控 agent 串行 `koharu.apply` 寫回所有 subagent 的結果
- 共享同一份 `glossary.locked.json` + `translation_rules.md`
