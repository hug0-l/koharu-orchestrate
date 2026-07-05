"""Shared Koharu HTTP API client."""

import json
import time
from pathlib import Path
from typing import Any

import httpx


class KoharuAPI:
    """Thin client for Koharu's HTTP API."""

    def __init__(self, base_url: str = "http://localhost:4000"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=300.0)

    # ---- Project ----

    def list_projects(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/v1/projects")
        r.raise_for_status()
        return r.json()["projects"]

    def create_project(self, name: str) -> dict[str, Any]:
        r = self.client.post("/api/v1/projects", json={"name": name})
        r.raise_for_status()
        return r.json()

    def open_project(self, project_id: str) -> dict[str, Any]:
        r = self.client.put("/api/v1/projects/current", json={"id": project_id})
        r.raise_for_status()
        return r.json()

    def close_project(self) -> None:
        self.client.delete("/api/v1/projects/current")

    def export_project(
        self, fmt: str = "rendered", pages: list[str] | None = None
    ) -> bytes:
        body: dict[str, Any] = {"format": fmt}
        if pages:
            body["pages"] = pages
        r = self.client.post("/api/v1/projects/current/export", json=body)
        r.raise_for_status()
        return r.content

    # ---- Pages ----

    def import_pages(self, image_paths: list[Path], replace: bool = False) -> list[str]:
        """Import images as pages. Returns list of page IDs."""
        files = []
        for p in image_paths:
            files.append(("images", (p.name, p.read_bytes(), f"image/{p.suffix[1:]}")))
        params: dict[str, str] = {}
        if replace:
            params["replace"] = "true"
        r = self.client.post("/api/v1/pages", files=files, params=params)
        r.raise_for_status()
        return r.json()["pages"]

    # ---- Scene ----

    def get_scene(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/scene.json")
        r.raise_for_status()
        return r.json()

    # ---- Operations / Pipeline ----

    def get_operations(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/v1/operations")
        r.raise_for_status()
        return r.json()["operations"]

    def wait_for_operation(
        self, op_id: str, poll_interval: float = 2.0, timeout: float = 600.0
    ) -> dict[str, Any]:
        """Poll until an operation completes or fails."""
        start = time.monotonic()
        while True:
            ops = self.get_operations()
            for op in ops:
                if op["id"] == op_id:
                    status = op["status"]
                    if status in ("completed", "failed", "cancelled"):
                        return op
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"Operation {op_id} did not complete within {timeout}s")
            time.sleep(poll_interval)

    # ---- Config ----

    def get_config(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/config")
        r.raise_for_status()
        return r.json()

    def patch_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        r = self.client.patch("/api/v1/config", json=patch)
        r.raise_for_status()
        return r.json()

    # ---- LLM ----

    def get_llm_catalog(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/llm/catalog")
        r.raise_for_status()
        return r.json()

    def get_llm_current(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/llm/current")
        r.raise_for_status()
        return r.json()

    def load_llm(self, target: dict[str, Any], options: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"target": target}
        if options:
            body["options"] = options
        self.client.put("/api/v1/llm/current", json=body)

    def unload_llm(self) -> None:
        self.client.delete("/api/v1/llm/current")

    # ---- Fonts ----

    def list_fonts(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/v1/fonts")
        r.raise_for_status()
        return r.json()

    # ---- Engines ----

    def list_engines(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/engines")
        r.raise_for_status()
        return r.json()

    # ---- Meta ----

    def get_meta(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/meta")
        r.raise_for_status()
        return r.json()
