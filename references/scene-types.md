# Scene/Op/Node 型別參考

用於 `koharu.apply` 的 Op JSON 結構。

---

## Op（操作）

所有 Op 透過 `koharu.apply` 套用到場景。Op 是 **internally-tagged enum**，變體名稱用 snake_case。

> ⚠ Op JSON 格式：`{"updateNode": { ... }}`，**不是** `{"type": "UpdateNode", ...}`。

### updateNode（最常用 — 修改翻譯）

`NodePatch` 使用巢狀結構：`patch → data → {text|image|mask} → fields`。

```json
{
  "updateNode": {
    "page": "page-uuid-v7",
    "id": "node-uuid-v7",
    "patch": {
      "data": {
        "text": {
          "translation": "譯文",
          "text": "new OCR text",
          "confidence": 0.95
        }
      },
      "visible": true,
      "transform": { "x": 100, "y": 200, "width": 300, "height": 50, "rotationDeg": 0 }
    },
    "prev": {}
  }
}
```

`prev` 由 Koharu 自動填寫（undo 用），agent 可省略或傳空物件。

`patch.data` 的變體與 `NodeKind` 對應（`text` / `image` / `mask`）。
`TextDataPatch` 可用欄位：`translation`、`text`、`confidence`、`style`、`fontPrediction`、`sprite`、`spriteTransform`、`renderedDirection`、`sourceDirection`、`rotationDeg`、`detectedFontSizePx`、`detector`、`lockLayoutBox`。

### updateProjectMeta

```json
{ "updateProjectMeta": { "patch": { "name": "New Name" }, "prev": {} } }
```

### addPage

```json
{ "addPage": { "page": { "id": "...", "name": "...", "width": 1200, "height": 1800, "nodes": {} }, "at": 0 } }
```

### removePage

```json
{ "removePage": { "id": "page-uuid", "prevPage": {}, "prevIndex": 0 } }
```

### updatePage

```json
{ "updatePage": { "id": "page-uuid", "patch": { "name": "page-001" }, "prev": {} } }
```

### addNode

```json
{
  "addNode": {
    "page": "page-uuid",
    "node": {
      "id": "new-node-uuid",
      "transform": { "x": 0, "y": 0, "width": 100, "height": 50, "rotationDeg": 0 },
      "visible": true,
      "kind": {
        "text": {
        "text": "source text",
        "translation": "譯文",
        "confidence": null,
        "style": null,
        "fontPrediction": null,
        "sprite": null,
        "spriteTransform": null,
        "rotationDeg": null,
        "detectedFontSizePx": null,
        "linePolygons": null,
        "sourceDirection": null,
        "renderedDirection": null,
        "detector": null,
        "lockLayoutBox": null
      }
    }
  },
  "at": 0
}
```

### removeNode

```json
{ "removeNode": { "page": "page-uuid", "id": "node-uuid", "prevNode": {}, "prevIndex": 0 } }
```

### reorderNodes

```json
{ "reorderNodes": { "page": "page-uuid", "order": ["node-id-1", "node-id-2"], "prevOrder": [] } }
```

### batch（批次操作 — 建議用於一頁內多個修改）

```json
{
  "batch": {
    "ops": [
      { "addNode": { "page": "page-uuid", ... } },
      { "updateNode": { "page": "page-uuid", "id": "...", "patch": { "translation": "譯文1" }, "prev": {} } },
      { "updateNode": { "page": "page-uuid", "id": "...", "patch": { "translation": "譯文2" }, "prev": {} } }
    ],
    "label": "translate page 1"
  }
}
```

---

## NodeKind

### Text（文字區塊 — 翻譯目標）

```json
{
  "Text": {
    "text": "OCR 辨識原文",              // string, 來源文字
    "translation": "翻譯文字",            // string | null
    "confidence": 0.92,                 // float | null
    "style": {
      "fontSize": 16,
      "textAlign": "left" | "center" | "right",
      "shaderEffect": "italic" | "bold"
    },                                  // TextStyle | null
    "fontPrediction": {
      "fontFamily": "Noto Sans",
      "fontSizePx": 32,
      "textColor": { "r": 0, "g": 0, "b": 0 }
    },                                  // FontPrediction | null
    "rotationDeg": 0.0,                 // float | null
    "detectedFontSizePx": 24.0,         // float | null
    "linePolygons": [[[x,y],...]],      // float[][][] | null
    "sourceDirection": "horizontal",    // "horizontal" | "vertical" | null
    "renderedDirection": "horizontal",  // "horizontal" | "vertical" | null
    "detector": "pp-doclayout-v3",      // string | null
    "sprite": null,                     // BlobRef | null（Render 後有值）
    "spriteTransform": null,            // Transform | null
    "lockLayoutBox": false              // boolean | null
  }
}
```

### Image（圖層）

```json
{
  "Image": {
    "role": "Source" | "Inpainted" | "Rendered" | "Custom",
    "blob": "blake3-hex-hash",
    "opacity": 1.0,
    "naturalWidth": 1200,
    "naturalHeight": 1800,
    "name": "page-001.png"
  }
}
```

### Mask（遮罩）

```json
{
  "Mask": {
    "role": "Segment" | "Bubble" | "BrushInpaint",
    "blob": "blake3-hex-hash"
  }
}
```

---

## Transform（位置變換）

```json
{
  "x": 100.0,
  "y": 200.0,
  "width": 300.0,
  "height": 50.0,
  "rotationDeg": 0.0
}
```

---

## Scene 結構（`GET /api/v1/scene.json`）

```json
{
  "epoch": 42,
  "scene": {
    "project": {
      "name": "My Manga",
      "createdAt": "...",
      "updatedAt": "...",
      "style": {}
    },
    "pages": {
      "page-uuid-1": {
        "id": "page-uuid-1",
        "name": "page-001",
        "width": 1200,
        "height": 1800,
        "nodes": {
          "node-uuid-1": {
            "id": "node-uuid-1",
            "transform": { "x": 0, "y": 0, "width": 1200, "height": 1800, "rotationDeg": 0 },
            "visible": true,
            "kind": { "Image": { "role": "Source", "blob": "abc123...", "opacity": 1.0, "naturalWidth": 1200, "naturalHeight": 1800, "name": null } }
          },
          "node-uuid-2": {
            "id": "node-uuid-2",
            "transform": { "x": 100, "y": 200, "width": 300, "height": 50, "rotationDeg": 0 },
            "visible": true,
            "kind": { "Text": { "text": "Hello!", "translation": null, ... } }
          }
        }
      }
    }
  }
}
```

---

## NodePatch（UpdateNode 可修改的欄位）

```json
{
  "text": "new text",
  "translation": "new translation",
  "style": { "fontSize": 18 },
  "visible": true,
  "transform": { "x": 100, "y": 200, "width": 300, "height": 50, "rotationDeg": 0 },
  "fontPrediction": { ... }
}
```

只需傳要修改的欄位，其餘保持不變。
