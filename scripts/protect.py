"""Protect title art / SFX / cover text from being erased by inpaint and
overwritten by the renderer.

Koharu's inpaint uses the per-page `segment` mask (white = erase). Text the
segmentation flagged as dialogue bubbles gets masked, but decorative title art
and sound effects (SFX) often get caught too and erased. This script:

  1. Re-uploads a modified `segment` mask with protected regions painted black
     (so lama-manga / aot-inpainting leave them untouched), and
  2. Sets the protected text nodes `visible: false` (so the renderer does not
     draw a translation on top of the preserved original), and clears any
     translation.

Usage:
    # protect specific nodes: page:node or page:node,page:node (repeat)
    python protect.py --server http://localhost:4000 --nodes <pid1>:<nid1>,<pid2>:<nid2>

    # protect nodes whose OCR text matches a regex (e.g. all-caps katakana SFX)
    python protect.py --server http://localhost:4000 --match '^[ァ-ヶー]{1,6}$'

    # protect an entire page (cover / title page)
    python protect.py --server http://localhost:4000 --pages <pid1>,<pid2>

    # dry-run: report what would be protected, don't touch the scene
    python protect.py --server ... --pages <pid> --dry-run

Output lists every protected page:node with its OCR text, then applies the
mask carve + visibility flip as one atomic batch.
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from koharu_api import KoharuAPI

MASK_ROLE = "segment"
WHITE = 255


def fetch_scene(api: KoharuAPI) -> dict:
    return api.get_scene()


def page_segment_blob(scene: dict, page_id: str) -> str | None:
    for nid, node in scene["scene"]["pages"][page_id].get("nodes", {}).items():
        k = node.get("kind", {})
        if "mask" in k and k["mask"].get("role") == MASK_ROLE:
            return k["mask"]["blob"]
    return None


def carve_mask(api: KoharuAPI, mask_blob: str, regions: list[tuple[int, int, int, int]]) -> bytes:
    """Fetch the segment mask image, black-out each (x,y,w,h) region, re-encode PNG."""
    im = Image.open(io.BytesIO(api.get_blob(mask_blob))).convert("L")
    px = im.load()
    w, h = im.size
    for x, y, bw, bh in regions:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(w, int(x + bw)), min(h, int(y + bh))
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                px[xx, yy] = 0
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def collect_regions(scene: dict, targets: dict[str, list[str]]) -> dict[str, list[tuple]]:
    """Map page_id -> list of (x,y,w,h) bboxes for the protected text nodes."""
    out: dict[str, list[tuple]] = {}
    for pid, nids in targets.items():
        if pid not in scene["scene"]["pages"]:
            continue
        for nid in nids:
            node = scene["scene"]["pages"][pid]["nodes"].get(nid)
            if not node or "text" not in node.get("kind", {}):
                continue
            tr = node.get("transform") or {}
            if "x" in tr and "y" in tr and "width" in tr and "height" in tr:
                out.setdefault(pid, []).append(
                    (tr["x"], tr["y"], tr["width"], tr["height"])
                )
    return out


def apply_spec(api: KoharuAPI, spec: dict, dry_run: bool = False) -> int:
    """Apply a protection spec dict {nodes:[page:node], pages:[...], match:regex}.
    Returns number of protected nodes."""
    scene = fetch_scene(api)
    targets: dict[str, list[str]] = {}
    for pair in spec.get("nodes", []) or []:
        pid, _, nid = str(pair).strip().partition(":")
        targets.setdefault(pid, []).append(nid)
    for pid in spec.get("pages", []) or []:
        pid = str(pid).strip()
        if pid:
            targets.setdefault(pid, []).append("*")
    if spec.get("match"):
        rx = re.compile(spec["match"])
        for pid, page in scene["scene"]["pages"].items():
            for nid, node in page.get("nodes", {}).items():
                if "text" in node.get("kind", {}):
                    t = node["kind"]["text"].get("text") or ""
                    if rx.search(t):
                        targets.setdefault(pid, []).append(nid)

    expanded = _expand(scene, targets)
    print(f"=== protected ({sum(len(v) for v in expanded.values())} nodes) ===")
    for pid, nids in expanded.items():
        for nid in nids:
            node = scene["scene"]["pages"][pid]["nodes"].get(nid)
            if not node:
                continue
            t = node["kind"]["text"] if "text" in node.get("kind", {}) else {}
            txt = (t.get("text") or "").replace("\n", "/")[:40]
            print(f"  {pid[:16]} : {nid[:12]}  '{txt}'")
    if dry_run:
        return sum(len(v) for v in expanded.values())

    regions_by_page = collect_regions(scene, expanded)
    for pid, regions in regions_by_page.items():
        blob = page_segment_blob(scene, pid)
        if not blob:
            continue
        if not regions:
            continue
        new_bytes = carve_mask(api, blob, regions)
        api.put(f"/api/v1/pages/{pid}/masks/{MASK_ROLE}", content=new_bytes)

    ops = []
    for pid, nids in expanded.items():
        for nid in nids:
            node = scene["scene"]["pages"][pid]["nodes"].get(nid)
            if not node or "text" not in node.get("kind", {}):
                continue
            ops.append({
                "updateNode": {
                    "page": pid, "id": nid,
                    "patch": {"visible": False, "data": {"text": {"translation": ""}}},
                    "prev": {},
                }
            })
    for i in range(0, len(ops), 50):
        api.client.post("/api/v1/history/apply",
                        json={"batch": {"label": "protect", "ops": ops[i:i + 50]}})
    return len(ops)


def _expand(scene: dict, targets: dict[str, list[str]]) -> dict[str, list[str]]:
    expanded: dict[str, list[str]] = {}
    for pid, nids in targets.items():
        if pid not in scene["scene"]["pages"]:
            continue
        page = scene["scene"]["pages"][pid]
        if "*" in nids:
            expanded[pid] = [nid for nid, n in page.get("nodes", {}).items() if "text" in n.get("kind", {})]
        else:
            expanded[pid] = list(dict.fromkeys(nids))
    return expanded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:4000")
    ap.add_argument("--nodes", help="comma list of page:node")
    ap.add_argument("--pages", help="comma list of page ids (protect whole page)")
    ap.add_argument("--match", help="regex; protect text nodes whose OCR text matches")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = ap.parse_args()

    api = KoharuAPI(args.server)

    spec: dict = {}
    if args.nodes:
        spec["nodes"] = args.nodes.split(",")
    if args.pages:
        spec["pages"] = args.pages.split(",")
    if args.match:
        spec["match"] = args.match

    n = apply_spec(api, spec, dry_run=args.dry_run)
    if not n:
        print("no targets matched", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry-run: no changes made")
        return 0
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
