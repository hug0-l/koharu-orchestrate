# Scene/Op/Node 型別參考

用於 `koharu.apply` 的 Op JSON 結構。

---

## Op（操作）

所有 Op 透過 `koharu.apply` 套用到場景。Op 是 tagged JSON（`type` 欄位區分）。

### UpdateNode（最常用 — 修改翻譯）

```json
{
  "type": "UpdateNode",
  "page": "page-uuid-v7",
  "id": "node-uuid-v7",
  "patch": {
    "text": "new OCR text",
    "translation": "譯文",
    "confidence": 0.95,
    "style": { "fontSize": 16, "textAlign": "left" },
    "visible": true,
    "transform": { "x": 100, "y": 200, "width": 300, "height": 50, "rotationDeg": 0 }
  },
  "prev": {}
}
```

`prev` 由 Koharu 自動填寫（undo 用），agent 可省略或傳空物件。

### UpdateProjectMeta

```json
{ "type": "UpdateProjectMeta", "patch": { "name": "New Name" }, "prev": {} }
```

### AddPage

```json
{ "type": "AddPage", "page": { "id": "...", "name": "...", "width": 1200, "height": 1800, "nodes": {} }, "at": 0 }
```

### RemovePage

```json
{ "type": "RemovePage", "id": "page-uuid", "prevPage": {}, "prevIndex": 0 }
```

### UpdatePage

```json
{ "type": "UpdatePage", "id": "page-uuid", "patch": { "name": "page-001" }, "prev": {} }
```

### AddNode

```json
{
  "type": "AddNode",
  "page": "page-uuid",
  "node": {
    "id": "new-node-uuid",
    "transform": { "x": 0, "y": 0, "width": 100, "height": 50, "rotationDeg": 0 },
    "visible": true,
    "kind": {
      "Text": {
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

### RemoveNode

```json
{ "type": "RemoveNode", "page": "page-uuid", "id": "node-uuid", "prevNode": {}, "prevIndex": 0 }
```

### ReorderNodes

```json
{ "type": "ReorderNodes", "page": "page-uuid", "order": ["node-id-1", "node-id-2", ...], "prevOrder": [] }
```

### Batch（批次操作 — 建議用於一頁內多個修改）

```json
{
  "type": "Batch",
  "ops": [
    { "type": "UpdateNode", "page": "page-1", "id": "node-1", "patch": { "translation": "譯文1" }, "prev": {} },
    { "type": "UpdateNode", "page": "page-1", "id": "node-2", "patch": { "translation": "譯文2" }, "prev": {} }
  ],
  "label": "translate page 1"
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
