#!/usr/bin/env python3
"""End-to-end volume processor for the manga translation queue.

Reads one entry from queue.json and drives the whole Koharu pipeline:
  import -> detect -> OCR -> dump -> slice -> validate -> apply -> inpaint
  (GPU->CPU auto-failover) -> render -> QA -> export -> queue update.

The TRANSLATION itself (subagent slicing + dispatching) is deliberately NOT
automated here: it needs an LLM agent. run_volume.py writes the dump + slices,
validates any existing slice files, and applies them. Pass --dispatch-hint to
have it print the exact slice ranges an agent should hand to subagents.

Usage:
    python run_volume.py --queue ~/koharu-work/queue.json --id awashima-hyakkei-v01
    python run_volume.py --queue ... --id ... --skip-translate   # re-render an existing volume
    python run_volume.py --queue ... --id ... --inpaint-only     # resume inpaint on a project

Exit codes: 0 ok, 1 error, 2 inpaint needs manual GPU restart, 3 blocked on translation.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from import_epub import extract_from_epub, find_epub_by_volume
from import_pages import collect_images
from koharu_api import KoharuAPI

KOHARU_BIN = "/Applications/koharu.app/Contents/MacOS/koharu"
DEFAULT_FONT = "Yuanti TC"
GPU_PAGES_BEFORE_CPU = 110  # hand-tuned: GPU reliably does ~110 before Metal OOM


def log(msg: str) -> None:
    print(f"[run_volume] {msg}", flush=True)


def api_for(port: int) -> KoharuAPI:
    return KoharuAPI(f"http://localhost:{port}")


def wait_server(port: int, timeout: float = 60.0) -> bool:
    api = api_for(port)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            api.get_meta()
            return True
        except Exception:
            time.sleep(2)
    return False


def start_server(port: int = 4000, use_cpu: bool = False) -> bool:
    """Start a Koharu instance on a specific port. Only kills the instance
    already bound to THIS port, so multiple instances can run in parallel."""
    # kill only processes bound to this port (parallel-safe)
    subprocess.run(
        ["pkill", "-f", f"koharu.*--headless.*--port.{port}\\b|koharu.*--port.{port}"],
        capture_output=True,
    )
    time.sleep(2)
    # also match the plain form (no explicit port -> default 4000)
    if port == 4000:
        subprocess.run(["pkill", "-f", "koharu --headless"], capture_output=True)
        time.sleep(1)
    cmd = [KOHARU_BIN, "--headless", "--port", str(port)]
    if use_cpu:
        cmd.append("--cpu")
    logf = open(f"/tmp/koharu-{port}.log" if use_cpu else f"/tmp/koharu-{port}.log", "w")
    subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
    time.sleep(7)
    return wait_server(port)


def start_pipeline(api: KoharuAPI, steps: list[str], **kw) -> str:
    while True:
        try:
            r = api.post("/api/v1/pipelines", json={"steps": steps, **kw}, timeout=60)
            return r.json()["operationId"]
        except Exception:
            time.sleep(2)


def wait_with_failover(
    api: KoharuAPI, op_id: str, proj_id: str, pages_total: int, port: int = 4000,
    timeout: float = 2800,
) -> tuple[str, int]:
    """Poll an operation; on connection-loss (GPU OOM) auto-restart on CPU.

    Returns (final_status, pages_done). Restarts are transparent: the project
    persists in scene.bin, so we reopen and resume the remaining pages.
    """
    start = time.monotonic()
    last_done = 0
    while True:
        try:
            ops = api.get_operations()
            op = next((o for o in ops if o.get("id") == op_id), None)
            if op and op["status"] in ("completed", "failed", "cancelled", "completed_with_errors"):
                scene = api.get_scene()
                done = pages_total - _count_missing_inpaint(scene)
                return op["status"], done
            if time.monotonic() - start > timeout:
                scene = api.get_scene()
                done = pages_total - _count_missing_inpaint(scene)
                return "timeout", done
            time.sleep(40)
        except Exception:
            # connection lost -> GPU OOM. restart on CPU, reopen, resume.
            log("connection lost (GPU OOM) -> restarting on CPU and resuming")
            if not start_server(port=port, use_cpu=True):
                return "restart_failed", last_done
            api = api_for(port)
            for _ in range(8):
                try:
                    api.open_project(proj_id)
                    break
                except Exception:
                    time.sleep(4)
            time.sleep(0.5)
            scene = api.get_scene()
            missing = [pid for pid, page in scene["scene"]["pages"].items()
                       if not any("image" in n.get("kind", {}) and n["kind"]["image"].get("role") == "inpainted"
                                  for n in page.get("nodes", {}).values())]
            last_done = pages_total - len(missing)
            if not missing:
                return "completed", last_done
            op_id = start_pipeline(api, ["lama-manga"], pages=missing)
            start = time.monotonic()
            time.sleep(20)


def _count_missing_inpaint(scene: dict[str, Any]) -> int:
    missing = [pid for pid, page in scene["scene"]["pages"].items()
               if not any("image" in n.get("kind", {}) and n["kind"]["image"].get("role") == "inpainted"
                          for n in page.get("nodes", {}).values())]
    return len(missing)


def prepare_pages(api: KoharuAPI, entry: dict[str, Any], work_dir: Path) -> tuple[list[Path], str]:
    """Resolve source to a flat dir of images. Returns (images, proj_id)."""
    kind = entry.get("kind", "images")
    src = entry["source"]
    pages_dir = work_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    if kind == "epub":
        src_path = Path(src)
        if not src_path.exists() and entry.get("series"):
            # fuzzy match by volume number in the series folder
            vol = re.search(r"v0?(\d+)", entry["id"])
            if vol:
                matched = find_epub_by_volume(src_path.parent, vol.group(1))
                if matched:
                    src_path = matched
                    log(f"epub fuzzy match -> {matched.name}")
        images = extract_from_epub(src_path, pages_dir)
    else:
        images = collect_images(Path(src))
        if not images and entry.get("source_glob"):
            import glob as _glob
            images = [Path(p) for p in sorted(_glob.glob(entry["source_glob"]))]

    if not images:
        raise RuntimeError(f"no images resolved from {src}")
    log(f"images: {len(images)}")
    return images, kind


def run(entry_id: str, queue_path: str, skip_translate: bool, inpaint_only: bool,
        port: int = 4000, dump_only: bool = False) -> int:
    q = json.load(open(queue_path))
    entry = next((e for e in q if e["id"] == entry_id), None)
    if not entry:
        log(f"queue entry not found: {entry_id}")
        return 1
    if entry.get("status") == "done" and not inpaint_only and not dump_only:
        log("already done; use --inpaint-only to resume")
        return 1

    work_dir = Path(entry.get("work_dir") or f"~/koharu-work/{entry_id}").expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    trans_dir = work_dir / "trans"
    trans_dir.mkdir(exist_ok=True)
    out_dir = work_dir / "out"
    out_dir.mkdir(exist_ok=True)

    font = entry.get("font", DEFAULT_FONT)
    proj_id = entry.get("project_id") or entry_id.replace("_", "-")

    # ---- server + project ----
    if not start_server(port=port, use_cpu=inpaint_only):
        log("server failed to start")
        return 1
    api = api_for(port)

    if inpaint_only:
        log("resume mode: reopening project")
        for _ in range(8):
            try:
                api.open_project(proj_id)
                break
            except Exception:
                time.sleep(4)
        proj = None
    else:
        proj = api.create_project(entry.get("name", entry_id))
        proj_id = proj["id"]
        log(f"project: {proj_id}")

    # ---- import (skip in resume) ----
    if not inpaint_only:
        images, _kind = prepare_pages(api, entry, work_dir)
        for i in range(0, len(images), 10):
            api.import_pages(images[i:i + 10], replace=(i == 0))
        log(f"imported {len(images)}")

        # ---- detect + OCR ----
        opid = start_pipeline(api, ["comic-text-bubble-detector", "speech-bubble-segmentation"])
        op = api.wait_for_operation(opid, poll_interval=10.0, timeout=2400)
        log(f"detect: {op.get('status')}")
        if op.get("status") not in ("completed", "completed_with_errors"):
            return 1
        opid = start_pipeline(api, ["comic-text-detector-seg", "paddle-ocr-vl-1.6", "yuzumarker-font-detection"])
        op = api.wait_for_operation(opid, poll_interval=45.0, timeout=5400)
        log(f"ocr: {op.get('status')}")

        # ---- dump ----
        import call_llm as C
        items = C.read_scene_text(f"http://localhost:{port}", reading_order="rtl")
        dump_path = work_dir / f"all_text_{entry_id}.json"
        json.dump(items, open(dump_path, "w"), ensure_ascii=False, indent=1)
        log(f"dumped {len(items)} items -> {dump_path.name}")

        # ---- slices + validate ----
        SLICE = 200
        nslices = (len(items) + SLICE - 1) // SLICE
        anchors = {}
        for i in range(nslices):
            lo = i * SLICE + 1
            hi = min((i + 1) * SLICE, len(items))
            for p in (lo, (lo + hi) // 2, hi):
                anchors.setdefault(i + 1, {})[str(p)] = items[p - 1]["text"].replace("\n", "⏎")[:50]
        log(f"slices: {nslices} (200/slice)  anchors: {json.dumps(anchors, ensure_ascii=False)[:200]}")
        json.dump(anchors, open(work_dir / f"slices_{entry_id}.json", "w"), ensure_ascii=False, indent=1)

        if dump_only:
            log("DUMP-ONLY: source prepared. dispatch subagents per slice_*.json, then re-run without --dump-only")
            entry["status"] = "dumped"
            import datetime
            entry["dumped_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            entry["project_id"] = proj_id
            json.dump(q, open(queue_path, "w"), ensure_ascii=False, indent=2)
            return 0

        # ---- validate existing slice files (if --skip-translate re-run) ----
        import validate_batch
        vf = sorted(trans_dir.glob(f"{entry_id}_slice_*.json"))
        if vf:
            log("found existing slices, validating")
            ok = validate_batch.main_from_files(items, vf, trans_dir)
            if not ok:
                log("existing slices have issues; fix trans/ then re-run")
                return 3

        if not skip_translate:
            present = {f.name for f in trans_dir.glob(f"{entry_id}_slice_*.json")}
            missing = [f"{entry_id}_slice_{i+1:02d}.json" for i in range(nslices)
                       if f"{entry_id}_slice_{i+1:02d}.json" not in present]
            if missing:
                log(f"BLOCKED on translation. slices to dispatch: {missing}")
                log(f"  all_text: {dump_path}")
                log(f"  glossary: {work_dir / 'glossary.locked.json'}")
                log("dispatch subagents per slice (see slices_*.json anchors), then re-run without --skip-translate")
                return 3
            log("all slices present")

        # ---- merge + apply + lock ----
        merged: dict[int, str] = {}
        for f in sorted(trans_dir.glob(f"{entry_id}_slice_*.json")):
            d = json.load(open(f))
            for k, v in d.items():
                if isinstance(v, str) and v.strip():
                    merged[int(k)] = v
        out = json.load(open(dump_path))
        results = [{"page_id": o["page_id"], "node_id": o["node_id"], "translation": merged.get(i, "")}
                   for i, o in enumerate(out, 1)]
        n = sum(1 for r in results if r["translation"].strip())
        log(f"merge: {len(out)} items, {n} translated")
        json.dump(results, open(work_dir / f"translations_{entry_id}.json", "w"), ensure_ascii=False, indent=1)

        ops = [{"updateNode": {"page": r["page_id"], "id": r["node_id"],
                               "patch": {"data": {"text": {"translation": r["translation"]}}}, "prev": {}}}
               for r in results if r["translation"].strip()]
        for i in range(0, len(ops), 50):
            r = api.client.post("/api/v1/history/apply", json={"batch": {"label": f"{entry_id} t", "ops": ops[i:i + 50]}})
            if r.status_code != 200:
                log(f"apply FAIL {r.status_code}: {r.text[:150]}")
                return 1
        log(f"applied {len(ops)}")
        scene = api.get_scene()
        locks = [{"updateNode": {"page": pid, "id": nid, "patch": {"data": {"text": {"lockLayoutBox": True}}}, "prev": {}}}
                 for pid, page in scene["scene"]["pages"].items()
                 for nid, node in page.get("nodes", {}).items() if "text" in node.get("kind", {})]
        for i in range(0, len(locks), 200):
            api.client.post("/api/v1/history/apply", json={"batch": {"label": "lock", "ops": locks[i:i + 200]}})
        log(f"locked {len(locks)}")

    # ---- inpaint (GPU->CPU auto-failover) ----
    scene = api.get_scene()
    pages_total = len(scene["scene"]["pages"])
    missing = _count_missing_inpaint(scene)
    if missing:
        log(f"inpaint: {pages_total - missing}/{pages_total} done, {missing} remaining")
        # pre-split: first chunk on GPU (up to GPU_PAGES_BEFORE_CPU), rest on CPU
        to_do = [pid for pid, page in scene["scene"]["pages"].items()
                 if not any("image" in n.get("kind", {}) and n["kind"]["image"].get("role") == "inpainted"
                            for n in page.get("nodes", {}).values())]
        first = to_do[:GPU_PAGES_BEFORE_CPU]
        opid = start_pipeline(api, ["lama-manga"], pages=first)
        status, done = wait_with_failover(api, opid, proj_id, pages_total, port=port)
        log(f"inpaint chunk1: {status} ({done}/{pages_total})")
        scene = api.get_scene()
        missing = _count_missing_inpaint(scene)
        if missing:
            log("switching to CPU for remaining pages")
            if not start_server(port=port, use_cpu=True):
                return 2
            api = api_for(port)
            for _ in range(8):
                try:
                    api.open_project(proj_id)
                    break
                except Exception:
                    time.sleep(4)
            time.sleep(0.5)
            scene = api.get_scene()
            missing = [pid for pid, page in scene["scene"]["pages"].items()
                       if not any("image" in n.get("kind", {}) and n["kind"]["image"].get("role") == "inpainted"
                                  for n in page.get("nodes", {}).values())]
            opid = start_pipeline(api, ["lama-manga"], pages=missing)
            op = api.wait_for_operation(opid, poll_interval=60.0, timeout=7200)
            log(f"inpaint cpu: {op.get('status')}")
    else:
        log("inpaint already complete")

    scene = api.get_scene()
    missing = _count_missing_inpaint(scene)
    if missing:
        log(f"ERROR: {missing} pages still not inpainted")
        return 1

    # ---- render + QA + export ----
    if not inpaint_only:
        opid = start_pipeline(api, ["koharu-renderer"], targetLanguage="zh-Hant", defaultFont=font)
        op = api.wait_for_operation(opid, poll_interval=10.0, timeout=2400)
        log(f"render: {op.get('status')}")
        if op.get("status") not in ("completed", "completed_with_errors"):
            return 1

        scene = api.get_scene()
        overflow = []
        for pid, page in scene["scene"]["pages"].items():
            for nid, node in page.get("nodes", {}).items():
                if "text" not in node.get("kind", {}):
                    continue
                spr = node["kind"]["text"].get("spriteTransform")
                tr = node.get("transform")
                if spr and tr:
                    sw = spr.get("scaleX", 1) * spr.get("width", 0) or (spr.get("width") or 0)
                    sh = spr.get("scaleY", 1) * spr.get("height", 0) or (spr.get("height") or 0)
                    tw = tr.get("width") or 1
                    th = tr.get("height") or 1
                    if sw > tw * 1.2 or sh > th * 1.2:
                        overflow.append((page.get("name", pid[:8]), nid[:8]))
        if overflow:
            log(f"render QA: {len(overflow)} overflow text nodes: {overflow[:10]}")
        else:
            log("render QA: no overflow detected")

    # ---- export ----
    data = api.export_project("rendered")
    out_path = out_dir / f"{entry_id}.zip"
    out_path.write_bytes(data)
    log(f"exported -> {out_path} ({out_path.stat().st_size / 1e6:.0f} MB)")

    # ---- queue update (keep project unless keep_project false) ----
    import datetime
    entry["status"] = "done"
    entry["done_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    entry["output"] = str(out_path)
    entry["font"] = font
    entry["project_id"] = proj_id
    entry["keep_project"] = entry.get("keep_project", True)
    json.dump(q, open(queue_path, "w"), ensure_ascii=False, indent=2)
    if not entry.get("keep_project", True):
        try:
            api.close_project()
        except Exception:
            pass
    log("queue updated -> done")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--skip-translate", action="store_true", help="merge existing slices / re-render")
    ap.add_argument("--inpaint-only", action="store_true", help="resume inpaint on an existing project")
    ap.add_argument("--port", type=int, default=4000, help="Koharu server port (default 4000)")
    ap.add_argument("--dump-only", action="store_true",
                    help="only import/detect/OCR/dump+slices, then exit (for parallel prep)")
    args = ap.parse_args()
    return run(args.id, args.queue, args.skip_translate, args.inpaint_only,
               port=args.port, dump_only=args.dump_only)


if __name__ == "__main__":
    sys.exit(main())
