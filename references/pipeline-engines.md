# Pipeline 引擎參考

## 引擎目錄（15 個）

| Engine ID | 名稱 | 需要 | 產生 | 預設 slot | 步驟 |
|-----------|------|------|------|-----------|------|
| `pp-doclayout-v3` | PP-DocLayout V3 | — | `TextBoxes` | `detector` | Detect |
| `comic-text-detector` | Comic Text Detector | — | `TextBoxes`, `SegmentMask` | `segmenter` | Detect |
| `comic-text-detector-seg` | CTD (Segmentation) | `TextBoxes` | `SegmentMask` | — | Detect |
| `comic-text-bubble-detector` | Comic Text & Bubble | — | `TextBoxes` | — | Detect |
| `anime-text` | Anime Text YOLO | — | `TextBoxes` | — | Detect |
| `speech-bubble-segmentation` | Speech Bubble Seg | — | `BubbleMask` | `bubble_segmenter` | Detect |
| `yuzumarker-font-detection` | YuzuMarker Font | `TextBoxes` | `FontPredictions` | `font_detector` | Detect |
| `manga-ocr` | Manga OCR | `TextBoxes` | `OcrText` | — | OCR |
| `mit48px-ocr` | MIT 48px OCR | `TextBoxes` | `OcrText` | — | OCR |
| `paddle-ocr-vl-1.5` | PaddleOCR-VL 1.5 | `TextBoxes` | `OcrText` | `ocr` | OCR |
| `paddle-ocr-vl-1.6` | PaddleOCR-VL 1.6 | `TextBoxes` | `OcrText` | — | OCR |
| `llm` | LLM | `OcrText` | `Translations` | `translator` | Translate |
| `lama-manga` | Lama Manga | `SegmentMask`, `BubbleMask` | `Inpainted` | `inpainter` | Inpaint |
| `aot-inpainting` | AOT Inpainting | `SegmentMask`, `BubbleMask` | `Inpainted` | — | Inpaint |
| `flux2-klein` | Flux.2 Klein | `SegmentMask`, `BubbleMask` | `Inpainted` | — | Inpaint |
| `koharu-renderer` | Koharu Renderer | `Inpainted`, `Translations`, `FontPredictions` | `FinalRender`, `RenderedSprites` | `renderer` | Render |

> 注意：`llm` engine 在 koharu-orchestrate 技能中**不使用** — 翻譯由 Agent 自行處理。列在此僅供參考。

## Artifact 依賴圖

```
SourceImage
    ↓
TextBoxes (detector)
    ↓              ↘
OcrText (OCR)    SegmentMask
    ↓              ↓
Translations    BubbleMask
(Agent 翻譯)       ↓
    ↓          Inpainted
    ↓              ↓
    └──→ koharu-renderer ←──┘
              + FontPredictions
              ↓
         FinalRender
```

## 預設管線設定

| 槽位 | 預設引擎 | 用途 |
|------|---------|------|
| `detector` | `comic-text-bubble-detector` | 文字區塊 + 泡泡偵測 |
| `font_detector` | `yuzumarker-font-detection` | 字體/顏色分析 |
| `segmenter` | `comic-text-detector-seg` | 除字遮罩 |
| `bubble_segmenter` | `speech-bubble-segmentation` | 泡泡區域遮罩 |
| `ocr` | `paddle-ocr-vl-1.5` | 文字辨識 |
| `translator` | `llm` | 翻譯（技能中由 Agent 取代） |
| `inpainter` | `lama-manga` | 除字 |
| `renderer` | `koharu-renderer` | 合成 |

可在 `PATCH /api/v1/config` 中切換各槽位的引擎。

## 典型管線順序（DAG）

```
1. comic-text-bubble-detector  (detect text blocks + bubbles)
   speech-bubble-segmentation  (detect bubble masks)
   [可並行，無相依]

2. comic-text-detector-seg  (generate segment mask, needs TextBoxes)
   yuzumarker-font-detection (font analysis, needs TextBoxes)

3. paddle-ocr-vl-1.5  (OCR text, needs TextBoxes)

4. [Agent 翻譯 — 非 engine 步驟]

5. lama-manga  (inpaint, needs SegmentMask + BubbleMask)

6. koharu-renderer  (final composite, needs Inpainted + Translations + FontPredictions)
```

## PipelineRunOptions

傳給 `koharu.start_pipeline` 的選項：

| 欄位 | 型別 | 用途 | 使用引擎 |
|------|------|------|---------|
| `targetLanguage` | string | 目標語言 | `koharu-renderer`（斷字、字型） |
| `defaultFont` | string | 字型名稱 | `koharu-renderer` |
| `readingOrder` | `"rtl"`/`"ltr"`/`"custom"` | 閱讀順序 | detectors |
| `textNodeIds` | string[] | 限定處理哪些 node | `llm`（技能中不用） |
| `region` | `{x,y,width,height}` | 局部重繪範圍 | inpainter |
