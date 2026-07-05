# 術語表格式 (glossary.locked.json)

## 概覽

術語表為 `glossary.locked.json`，是一個 JSON 檔案，包含三個頂層陣列：

```json
{
  "characters": [ ... ],
  "terms": [ ... ],
  "non_translate": [ ... ]
}
```

## characters（角色/人名）

每項定義一個角色的翻譯處理方式：

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `canonical` | string | ✅ | 正式名稱（原文，如 `Cecil`、`アリア`） |
| `render` | string | ✅ | 輸出值：英文=保留原文，非英文=使用該譯名 |
| `aliases` | string[] | | 該角色的其他稱呼（全名、暱稱、別名變體） |
| `gender` | string | | 性別標記 `M`/`F`/`-`（供翻譯代詞參考） |
| `note` | string | | 備註（如角色定位、頭銜、種族） |

**範例：**

```json
{
  "characters": [
    {
      "canonical": "Cecil",
      "render": "Cecil",
      "aliases": ["Cecil Harvey", "Captain Cecil"],
      "gender": "M",
      "note": "主角，保留原文"
    },
    {
      "canonical": "アリア",
      "render": "亞莉亞",
      "aliases": ["Aria", "アリア・ローズ"],
      "gender": "F",
      "note": "女主角"
    },
    {
      "canonical": "ローザ",
      "render": "Rosa",
      "aliases": ["Rosa"],
      "gender": "F",
      "note": "保留英文名"
    }
  ]
}
```

### render 值判斷邏輯

- `render` 值**只含英文字母**（a-zA-Z 空格 . ' -）→ 該名字保留原文，不翻譯
- `render` 值**包含非英文字母**（如中文字元）→ 使用該譯名

### aliases 的作用

在 verify 時 agent 會檢查原文中出現的 **任何 alias** 是否在譯文中被正確處理。例如 `canonical` 為 `Cecil`、`aliases` 含 `Cecil Harvey`，則原文無論出現哪個都應對應到 `Cecil`（保留原文）。

## terms（術語）

每項定義一個非角色專有名詞：

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `src` | string | ✅ | 原文術語 |
| `dst` | string | | 譯名（缺省=不翻譯） |
| `category` | string | | 分類：`faction`、`location`、`item`、`skill`、`title`、`concept` 等 |
| `keep_source` | boolean | | `true` 時強制保留原文，忽略 dst |

**範例：**

```json
{
  "terms": [
    { "src": "Shinigami", "dst": "死神", "category": "concept" },
    { "src": "Red Dragon", "dst": "赤龍", "category": "creature" },
    { "src": "Highmark", "keep_source": true, "category": "location" },
    { "src": "聖剣エクスカリバー", "dst": "聖劍 Excalibur", "category": "item" }
  ]
}
```

## non_translate（不翻譯模式）

定義不應被翻譯的標記/模式：

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `marker` | string | ✅ | 標記內容或正則模式 |
| `category` | string | | 分類：`tag`、`pattern`、`url` |

**範例：**

```json
{
  "non_translate": [
    { "marker": "<i>", "category": "tag" },
    { "marker": "{{.*?}}", "category": "pattern" },
    { "marker": "http\\S+", "category": "url" }
  ]
}
```

## 完整的 glossary.locked.json

```json
{
  "characters": [
    { "canonical": "Cecil", "render": "Cecil", "aliases": ["Cecil Harvey"], "gender": "M" },
    { "canonical": "アリア", "render": "亞莉亞", "aliases": ["Aria"], "gender": "F", "note": "女主角" },
    { "canonical": "ローザ", "render": "Rosa", "aliases": ["Rosa"], "gender": "F" }
  ],
  "terms": [
    { "src": "Shinigami", "dst": "死神", "category": "concept" },
    { "src": "Highmark", "keep_source": true, "category": "location" },
    { "src": "聖剣エクスカリバー", "dst": "聖劍 Excalibur", "category": "item" }
  ],
  "non_translate": [
    { "marker": "<i>", "category": "tag" }
  ]
}
```
