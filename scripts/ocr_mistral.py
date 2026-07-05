"""Rescan manga pages with Mistral OCR API and write corrected text back to Koharu.

Usage:
    # Rescan all pages, save corrected OCR JSON
    ocr_mistral.py rescan \\
        --server http://localhost:4000 \\
        --api-key "\$MISTRAL_API_KEY" \\
        --output /tmp/corrected_ocr.json

    # Apply corrections back to Koharu scene
    ocr_mistral.py apply \\
        --server http://localhost:4000 \\
        --input /tmp/corrected_ocr.json

    # Rescan + apply in one step
    ocr_mistral.py rescan --server ... --api-key ... --apply
"""

import argparse
import base64
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
IOU_THRESHOLD = 0.05  # matching threshold for text block overlap


# ── Helpers ────────────────────────────────────────────────


def fetch_scene(server_url: str) -> dict[str, Any]:
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=30)
    r.raise_for_status()
    return r.json()


def download_blob(server_url: str, blob_hash: str) -> bytes:
    r = httpx.get(f"{server_url}/api/v1/blobs/{blob_hash}", timeout=60)
    r.raise_for_status()
    return r.content


def mistral_ocr(api_key: str, image_bytes: bytes, model: str = "mistral-ocr-latest") -> dict[str, Any]:
    """Send image to Mistral OCR API. Returns full response."""
    b64 = base64.b64encode(image_bytes).decode()
    data_uri = f"data:image/jpeg;base64,{b64}"

    payload = {
        "model": model,
        "document": {
            "type": "image_url",
            "image_url": data_uri,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = httpx.post(MISTRAL_OCR_URL, headers=headers, json=payload, timeout=120.0)
    r.raise_for_status()
    return r.json()


def iou(box_a: list[float], box_b: list[float]) -> float:
    """IoU of two boxes [x1,y1,x2,y2]."""
    xa = max(box_a[0], box_b[0])
    ya = max(box_a[1], box_b[1])
    xb = min(box_a[2], box_b[2])
    yb = min(box_a[3], box_b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def transform_to_bbox(t: dict[str, float]) -> list[float]:
    """Convert scene transform {x,y,width,height} to [x1,y1,x2,y2]."""
    return [t["x"], t["y"], t["x"] + t["width"], t["y"] + t["height"]]


# ── Rescan ──────────────────────────────────────────────────


def cmd_rescan(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("Error: --api-key required or set MISTRAL_API_KEY", file=sys.stderr)
        sys.exit(1)

    scene = fetch_scene(args.server)
    pages_data = scene.get("scene", {}).get("pages", {})
    total_pages = len(pages_data)
    page_ids = list(pages_data.keys())

    corrections: list[dict[str, Any]] = []
    errors: list[str] = []

    for idx, pid in enumerate(page_ids):
        page = pages_data[pid]
        page_name = page.get("name", pid[:8])

        # Find source image blob
        source_blob = None
        text_nodes: list[dict[str, Any]] = []
        for nid, node in page.get("nodes", {}).items():
            kind = node.get("kind", {})
            if "image" in kind and kind["image"].get("role") in ("source", "Source"):
                source_blob = kind["image"]["blob"]
            if "text" in kind:
                t = kind["text"]
                text_nodes.append({
                    "node_id": nid,
                    "transform": node.get("transform", {}),
                    "old_text": t.get("text") or "",
                    "old_translation": t.get("translation") or "",
                })

        if not source_blob:
            print(f"  [{idx+1}/{total_pages}] {page_name} — no source image, skipping")
            continue
        if not text_nodes:
            print(f"  [{idx+1}/{total_pages}] {page_name} — no text nodes, skipping")
            continue

        # Download image
        try:
            img_bytes = download_blob(args.server, source_blob)
        except Exception as e:
            msg = f"download failed: {e}"
            print(f"  [{idx+1}/{total_pages}] {page_name} — {msg}", file=sys.stderr)
            errors.append(f"{page_name}: {msg}")
            continue

        # Call Mistral OCR
        for attempt in range(3):
            try:
                ocr_result = mistral_ocr(api_key, img_bytes, args.model)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                msg = f"Mistral OCR failed: {e}"
                print(f"  [{idx+1}/{total_pages}] {page_name} — {msg}", file=sys.stderr)
                errors.append(f"{page_name}: {msg}")
                ocr_result = None

        if not ocr_result:
            continue

        # Parse Mistral response
        # Response format: { "pages": [{ "index": 0, "markdown": "...", "text_blocks": [...] }] }
        mistral_pages = ocr_result.get("pages", [])
        if not mistral_pages:
            print(f"  [{idx+1}/{total_pages}] {page_name} — no pages in response", file=sys.stderr)
            continue

        mp = mistral_pages[0]
        full_text = mp.get("markdown", "")
        blocks = mp.get("blocks", mp.get("text_blocks", []))

        # Match blocks to text nodes via IoU
        matched = [False] * len(text_nodes)
        new_blocks: list[str] = []

        for block in blocks:
            # Skip image blocks
            if block.get("type") == "image":
                continue
            block_text = block.get("content", "").strip()
            if not block_text:
                continue
            # Strip markdown heading markers
            block_text = block_text.lstrip("#").strip()
            if not block_text:
                continue
            # Build bbox from Mistral format [top_left_x, top_left_y, bottom_right_x, bottom_right_y]
            bbox = [
                block.get("top_left_x", 0),
                block.get("top_left_y", 0),
                block.get("bottom_right_x", 0),
                block.get("bottom_right_y", 0),
            ]
            if bbox[0] == 0 and bbox[1] == 0 and bbox[2] == 0 and bbox[3] == 0:
                new_blocks.append(block_text)
                continue

            # Find best matching text node
            best_idx = -1
            best_iou = 0
            for ti, tn in enumerate(text_nodes):
                if matched[ti]:
                    continue
                tbox = tn.get("transform", {})
                if not all(k in tbox for k in ("x", "y", "width", "height")):
                    continue
                scene_bbox = transform_to_bbox(tbox)
                i = iou(bbox, scene_bbox)
                if i > best_iou:
                    best_iou = i
                    best_idx = ti

            if best_idx >= 0 and best_iou >= IOU_THRESHOLD:
                matched[best_idx] = True
                corrections.append({
                    "page_id": pid,
                    "page_name": page_name,
                    "node_id": text_nodes[best_idx]["node_id"],
                    "old_text": text_nodes[best_idx]["old_text"],
                    "new_text": block_text,
                    "has_translation": bool(text_nodes[best_idx]["old_translation"]),
                    "confidence": block.get("confidence", 1.0),
                })
            else:
                new_blocks.append(block_text)

        # Log unmatched text nodes
        unmatched_count = matched.count(False)
        block_count = len(blocks)
        correct_count = sum(matched)

        print(f"  [{idx+1}/{total_pages}] {page_name} — "
              f"matched {correct_count}/{len(text_nodes)} nodes, "
              f"{len(new_blocks)} new blocks"
              f"{' ⚠ unmatched' if unmatched_count else ''}"
              f"{' ⚠ errors' if errors else ''}")

        # Small delay between API calls
        time.sleep(0.5)

    # Save output
    output = {
        "total_pages_processed": len(corrections),
        "total_nodes_corrected": len(corrections),
        "errors": errors,
        "corrections": corrections,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(corrections)} corrections to {out_path}", file=sys.stderr)
    if errors:
        print(f"Errors: {len(errors)}", file=sys.stderr)
        for e in errors[:10]:
            print(f"  {e}", file=sys.stderr)

    if args.apply:
        args.input = args.output
        cmd_apply(args)


def cmd_apply(args: argparse.Namespace) -> None:
    """Write corrected OCR text back to Koharu scene."""
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    corrections = data.get("corrections", [])
    if not corrections:
        print("No corrections to apply", file=sys.stderr)
        return

    # Build batch ops — only update nodes where text actually changed
    ops = []
    for c in corrections:
        if c.get("new_text") and c["new_text"] != c.get("old_text", ""):
            ops.append({
                "updateNode": {
                    "page": c["page_id"],
                    "id": c["node_id"],
                    "patch": {"data": {"text": {"text": c["new_text"]}}},
                    "prev": {},
                }
            })

    if not ops:
        print("No text changes to apply", file=sys.stderr)
        return

    # Apply in batches of 50
    batch_size = 50
    total = 0
    for i in range(0, len(ops), batch_size):
        batch = ops[i : i + batch_size]
        r = httpx.post(
            f"{args.server}/api/v1/history/apply",
            json={"batch": {"label": f"mistral-ocr {i//batch_size+1}", "ops": batch}},
            timeout=60,
        )
        if r.status_code == 200:
            total += len(batch)
            print(f"  Batch {i//batch_size+1}: {len(batch)} applied ({total}/{len(ops)})")
        else:
            print(f"  Batch {i//batch_size+1} FAILED: {r.status_code} {r.text[:100]}", file=sys.stderr)

    print(f"\nApplied {total} OCR corrections", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Rescan manga pages with Mistral OCR")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("rescan", help="Rescan pages with Mistral OCR")
    r.add_argument("--server", default="http://localhost:4000", help="Koharu server URL")
    r.add_argument("--api-key", help="Mistral API key (or MISTRAL_API_KEY env)")
    r.add_argument("--model", default="mistral-ocr-latest")
    r.add_argument("--output", "-o", default="/tmp/corrected_ocr.json")
    r.add_argument("--apply", action="store_true", help="Apply corrections after scan")

    a = sub.add_parser("apply", help="Apply saved OCR corrections to Koharu")
    a.add_argument("--server", default="http://localhost:4000")
    a.add_argument("--input", "-i", required=True)

    args = parser.parse_args()
    if args.command == "rescan":
        cmd_rescan(args)
    elif args.command == "apply":
        cmd_apply(args)


if __name__ == "__main__":
    main()
