"""Shared Koharu HTTP API client."""

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}


class KoharuAPI:
    """Thin client for Koharu's HTTP API."""

    def __init__(self, base_url: str = "http://localhost:4000", max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.client = httpx.Client(base_url=self.base_url, timeout=300.0)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.request(method, path, **kwargs)
                r.raise_for_status()
                return r
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(1 * (attempt + 1))
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self._request("DELETE", path, **kwargs)

    def patch(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PATCH", path, **kwargs)

    # ---- Project ----

    def list_projects(self) -> list[dict[str, Any]]:
        r = self.get("/api/v1/projects")
        return r.json()["projects"]

    def create_project(self, name: str) -> dict[str, Any]:
        r = self.post("/api/v1/projects", json={"name": name})
        return r.json()

    def open_project(self, project_id: str) -> dict[str, Any]:
        r = self.put("/api/v1/projects/current", json={"id": project_id})
        return r.json()

    def close_project(self) -> None:
        self.delete("/api/v1/projects/current")

    def export_project(
        self, fmt: str = "rendered", pages: list[str] | None = None
    ) -> bytes:
        body: dict[str, Any] = {"format": fmt}
        if pages:
            body["pages"] = pages
        r = self.post("/api/v1/projects/current/export", json=body)
        return r.content

    # ---- Pages ----

    def import_pages(self, image_paths: list[Path], replace: bool = False) -> list[str]:
        """Import images as pages. Returns list of page IDs."""
        files = []
        for p in image_paths:
            mime = MIME_BY_EXT.get(p.suffix.lower(), "application/octet-stream")
            files.append(("images", (p.name, p.read_bytes(), mime)))
        params: dict[str, str] = {}
        if replace:
            params["replace"] = "true"
        r = self.post("/api/v1/pages", files=files, params=params)
        return r.json()["pages"]

    # ---- Scene ----

    def get_scene(self) -> dict[str, Any]:
        r = self.get("/api/v1/scene.json")
        return r.json()

    # ---- Operations / Pipeline ----

    def get_operations(self) -> list[dict[str, Any]]:
        r = self.get("/api/v1/operations")
        return r.json()["operations"]

    def wait_for_operation(
        self, op_id: str, poll_interval: float = 2.0, timeout: float = 600.0
    ) -> dict[str, Any]:
        """Poll until an operation completes or fails."""
        start = time.monotonic()
        terminal = ("completed", "failed", "cancelled", "completed_with_errors")
        while True:
            ops = self.get_operations()
            for op in ops:
                if op["id"] == op_id:
                    status = op["status"]
                    if status in terminal:
                        return op
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"Operation {op_id} did not complete within {timeout}s")
            time.sleep(poll_interval)

    # ---- Config ----

    def get_config(self) -> dict[str, Any]:
        r = self.get("/api/v1/config")
        return r.json()

    def patch_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        r = self.patch("/api/v1/config", json=patch)
        return r.json()

    # ---- LLM ----

    def get_llm_catalog(self) -> dict[str, Any]:
        r = self.get("/api/v1/llm/catalog")
        return r.json()

    def get_llm_current(self) -> dict[str, Any]:
        r = self.get("/api/v1/llm/current")
        return r.json()

    def load_llm(self, target: dict[str, Any], options: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"target": target}
        if options:
            body["options"] = options
        self.put("/api/v1/llm/current", json=body)

    def unload_llm(self) -> None:
        self.delete("/api/v1/llm/current")

    # ---- Engines ----

    def list_engines(self) -> dict[str, Any]:
        r = self.get("/api/v1/engines")
        return r.json()

    # ---- Events (SSE) ----

    def events_stream(self, max_events: int | None = None, read_timeout: float = 30.0) -> list[dict[str, Any]]:
        """Read events from the SSE endpoint as a streaming response.

        `/api/v1/events` is a long-lived stream; reading it as a plain GET would
        hang until the server closes the connection. This drains lines incrementally
        and stops after `read_timeout` seconds (or once `max_events` is collected).
        Returns a list of parsed event dicts (non-`data:` lines ignored).
        """
        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + read_timeout
        with self.client.stream("GET", "/api/v1/events", timeout=read_timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        continue
                    if max_events and len(events) >= max_events:
                        break
                if time.monotonic() > deadline:
                    break
        return events

    def wait_for_event(
        self, event_type: str, timeout: float = 120.0, interval: float = 2.0
    ) -> dict[str, Any] | None:
        """Wait for a specific event type by opening short streaming windows.

        Preferred over this helper for pipeline monitoring: poll
        ``GET /api/v1/operations`` via :meth:`wait_for_operation` instead.
        """
        start = time.monotonic()
        while True:
            for ev in self.events_stream(read_timeout=interval):
                if ev.get("type") == event_type:
                    return ev
            if time.monotonic() - start > timeout:
                return None

    # ---- LLM (extended) ----

    def wait_for_llm_ready(self, timeout: float = 120.0, poll_interval: float = 1.0) -> dict[str, Any]:
        """Poll until LLM is loaded and ready."""
        start = time.monotonic()
        while True:
            state = self.get_llm_current()
            status = state.get("status", "")
            if status == "ready":
                return state
            if status == "failed":
                raise RuntimeError(f"LLM load failed: {state.get('error', 'unknown')}")
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"LLM did not become ready within {timeout}s")
            time.sleep(poll_interval)

    # ---- Provider Credentials ----

    def set_provider_secret(self, provider_id: str, api_key: str) -> None:
        """Store a provider API key in Koharu's credential store."""
        self.put(f"/api/v1/config/providers/{provider_id}/secret", content=api_key)

    def clear_provider_secret(self, provider_id: str) -> None:
        """Remove a provider API key from Koharu's credential store."""
        self.delete(f"/api/v1/config/providers/{provider_id}/secret")

    # ---- Microsoft ----

    def list_fonts(self) -> list[dict[str, Any]]:
        r = self.get("/api/v1/fonts")
        return r.json()

    # ---- Google Fonts ----

    def list_google_fonts(self) -> list[dict[str, Any]]:
        r = self.get("/api/v1/google-fonts")
        return r.json()

    def fetch_google_font(self, family: str) -> dict[str, Any]:
        r = self.post(f"/api/v1/google-fonts/{family}/fetch")
        return r.json()

    # ---- Downloads ----

    def list_downloads(self) -> list[dict[str, Any]]:
        r = self.get("/api/v1/downloads")
        return r.json()["downloads"]

    def start_download(self, model_id: str) -> str:
        r = self.post("/api/v1/downloads", json={"modelId": model_id})
        return r.json()["operationId"]

    # ---- Blobs ----

    def get_blob(self, blob_hash: str) -> bytes:
        r = self.get(f"/api/v1/blobs/{blob_hash}")
        return r.content

    # ---- Meta ----

    def get_meta(self) -> dict[str, Any]:
        r = self.get("/api/v1/meta")
        return r.json()
