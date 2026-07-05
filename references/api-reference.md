# API 參考文件

Koharu 提供兩套介面：HTTP REST API + MCP（Model Context Protocol）。

基礎 URL：`http://localhost:4000`

---

## MCP Tools (`/mcp`)

Streamable HTTP transport。共 6 個工具。

### `koharu.open_project`

開啟或建立專案。

**Input:**
```json
{
  "path": "/absolute/path/to/project.khrproj",
  "createName": "My Project"       // 可選，有值則新建
}
```

**Output:** `{ name: string, path: string }`

### `koharu.close_project`

關閉當前專案。

**Input:** none

**Output:** null

### `koharu.apply`

對當前場景套用一個 Op。

**Input:**
```json
{
  "op": { /* Op JSON */ }
}
```

Op 類型見 `scene-types.md`。

**Output:** `{ epoch: number }`

### `koharu.undo`

復原上一個 Op。

**Input:** none

**Output:** `{ epoch: number | null }`（null = 已無可復原）

### `koharu.redo`

重做上一個復原。

**Input:** none

**Output:** `{ epoch: number | null }`

### `koharu.start_pipeline`

啟動管線執行。

**Input:**
```json
{
  "steps": ["engine-id-1", "engine-id-2"],
  "pages": ["page-uuid"],            // 可選，null=全部頁面
  "textNodeIds": ["node-uuid"],      // 可選
  "targetLanguage": "Chinese",       // 可選
  "systemPrompt": "...",              // 可選
  "defaultFont": "Noto Sans SC",     // 可選
  "readingOrder": "rtl"              // 可選
}
```

**Output:** `{ jobId: string }`

---

## HTTP API (`/api/v1/*`)

### 專案

#### GET `/api/v1/projects`
列出已管理的專案。

**Response:** `{ projects: [{ id, name, path, updatedAtMs }] }`

#### POST `/api/v1/projects`
新建專案。

**Body:** `{ name: string }`

**Response:** `ProjectSummary`

#### PUT `/api/v1/projects/current`
開啟專案。

**Body:** `{ id: string }`

**Response:** `ProjectSummary`

#### DELETE `/api/v1/projects/current`
關閉當前專案。

**Response:** 204

#### DELETE `/api/v1/projects/{id}`
刪除專案。

**Response:** 204

#### POST `/api/v1/projects/current/export`
匯出。

**Body:**
```json
{
  "format": "rendered" | "psd" | "khr" | "inpainted",
  "pages": ["page-uuid"],
  "defaultFont": "optional-font"
}
```

**Response:** binary (ZIP/PSD)

### 頁面

#### POST `/api/v1/pages`
上傳圖片建立頁面。multipart/form-data。

**Params:**
- `images` — 多個檔案欄位
- `replace=true`（可選，清除現有頁面）

**Response:** `{ pages: [pageId, ...] }`

#### POST `/api/v1/pages/from-paths`
Tauri 模式。直接從磁碟路徑讀取。

**Body:** `{ paths: ["/path/img.png"], replace: false }`

**Response:** `{ pages: [pageId, ...] }`

#### GET `/api/v1/pages/{id}/thumbnail`
取得頁面縮圖 (WebP, max 320px)。

### 場景

#### GET `/api/v1/scene.json`
完整場景 JSON。

**Response:** `{ epoch: number, scene: { project: {...}, pages: {...} } }`

#### GET `/api/v1/scene.bin`
Postcard 編碼的二進位場景。

### Blobs

#### GET `/api/v1/blobs/{hash}
取得 blob 原始位元組。

### 歷史

#### POST `/api/v1/history/apply`
套用 Op。

**Body:** Op JSON

**Response:** `{ epoch: number }`

#### POST `/api/v1/history/undo`
復原。

**Response:** `{ epoch: number | null }`

#### POST `/api/v1/history/redo`
重做。

**Response:** `{ epoch: number | null }`

### 管線

#### POST `/api/v1/pipelines`
啟動管線。

**Body:** 同 `koharu.start_pipeline` input。

**Response:** `{ operationId: string }`

### 作業/下載

#### GET `/api/v1/operations`
列出進行中或最近完成的作業。

**Response:** `{ operations: [{ id, kind, status, error }] }`

#### DELETE `/api/v1/operations/{id}`
取消/清除作業。

#### GET `/api/v1/downloads`
列出下載狀態。

**Response:** `{ downloads: [{ id, filename, downloaded, total, status }] }`

#### POST `/api/v1/downloads`
啟動模型下載。

**Body:** `{ modelId: string }`

**Response:** `{ operationId: string }`

### LLM

#### GET `/api/v1/llm/current`
當前 LLM 狀態。

**Response:** `{ status, target, error }`

#### PUT `/api/v1/llm/current`
載入 LLM。

**Body:**
```json
{
  "target": { "kind": "local" | "provider", "modelId": "...", "providerId": "..." },
  "options": { "temperature": 0.7, "maxTokens": 4096 }
}
```

**Response:** 204

#### DELETE `/api/v1/llm/current`
卸載 LLM。

#### GET `/api/v1/llm/catalog`
列出可用模型/Provider。

### 字型

#### GET `/api/v1/fonts`
系統 + Google Fonts 列表。

### 設定

#### GET `/api/v1/config`
讀取設定。

**Response:** `AppConfig`

#### PATCH `/api/v1/config`
更新設定。

**Body:**
```json
{
  "pipeline": { "detector": "comic-text-detector", "ocr": "manga-ocr" },
  "providers": [{ "id": "openai", "apiKey": "sk-..." }],
  "data": { "path": "/data/path" },
  "http": { "connectTimeout": 30 }
}
```

所有欄位可選（sparse patch）。`apiKey: ""` 清除；`"[REDACTED]"` 保留原值。

### 引擎

#### GET `/api/v1/engines`
註冊的引擎列表（依類別分組）。

### Meta

#### GET `/api/v1/meta`
版本與 ML 設備資訊。

### 事件 (SSE)

#### GET `/api/v1/events`
Server-Sent Events 串流。

事件類型：
- `Snapshot` — 連線初始狀態
- `JobStarted` / `JobProgress` / `JobWarning` / `JobFinished` — 管線事件
- `DownloadProgress` — 下載進度
- `LlmLoading` / `LlmLoaded` / `LlmFailed` / `LlmUnloaded` — LLM 事件
- `ConfigChanged` — 設定變更
