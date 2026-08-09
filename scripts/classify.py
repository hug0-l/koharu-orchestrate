"""Classify manga text nodes into "translate" vs "skip" and protect the skipped.

Goal: only translate speech-bubble dialogue + obvious narration/paragraph text.
Everything else (SFX, title art, page headers, brand/credit, TOC, decorations)
is NOT translated, NOT inpainted, and NOT re-rendered — the original pixels stay.

Classification (per node):
  SKIP (protected) if:
    - contains brand/TOC/credit marker (コミックス, COMICS, BOOK☆WALKER,
      Cover Design, Special Thanks, 初出, 第一刷, 二〇XX年, 目次, あとがき, ...)
    - pure katakana short (SFX), e.g. ドキドキ / モダンダース
    - big decorative text (fontSizePx >= 45 and short)
  TRANSLATE otherwise (bubble dialogue + narration paragraphs + normal lines).

Protection = carve the node's bbox out of the segment mask (inpaint skips it)
+ set node visible:false + clear translation (renderer won't overwrite).

Slice output = 1-based indices of ONLY the translate nodes (relative to the
full all_text dump), so subagents translate exactly the right set.

Usage:
    python classify.py --server http://localhost:4000
        --dump work/all_text_v01.json
        --slices work/slices_v01.json
        --glossary work/glossary.locked.json
        --trans-dir work/trans/
        [--apply-protect]      # actually carve masks + hide nodes
        [--json report.json]   # write machine-readable classification
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from koharu_api import KoharuAPI

MASK_ROLE = "segment"
BRAND_RE = re.compile(
    r'(コミックス|COMICS|BOOK☆WALKER|Cover\s*Design|Special\s*Thanks|初出|'
    r'第一刷|二〇[一二三四五六七八九十]+年|目次|あとがき|巻頭|特典|'
    r'RawLazy|DL-Raw|Contents|INDEX)'
)
PURE_KATAKANA_RE = re.compile(r'^[ァ-ヶー・!！？]{1,8}$')


def _overlap(mask: Image.Image, bbox: tuple) -> float:
    x, y, w, h = bbox
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(mask.width, int(x + w)), min(mask.height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    crop = mask.crop((x0, y0, x1, y1))
    px = list(crop.getdata())
    return sum(1 for p in px if p >= 2) / len(px) if px else 0.0


def page_bubble_mask(api: KoharuAPI, scene: dict, page_id: str) -> Image.Image | None:
    for node in scene["scene"]["pages"][page_id].get("nodes", {}).values():
        k = node.get("kind", {})
        if "mask" in k and k["mask"].get("role") == "bubble":
            return Image.open(io.BytesIO(api.get_blob(k["mask"]["blob"]))).convert("L")
    return None


def classify_node(text: str, font_size_px: float, bubble_overlap: float) -> bool:
    """Return True if the node should be TRANSLATED (else skip/protect)."""
    joined = (text or "").replace("\n", "")
    if not joined.strip():
        return False
    if BRAND_RE.search(joined):
        return False
    if PURE_KATAKANA_RE.match(joined):
        return False  # SFX
    if font_size_px >= 40 and len(joined) <= 30:
        return False  # big decorative title art
    # vertical single-char-per-line title art (e.g. 淡/島/百/景): >=3 lines,
    # each <=2 chars, no kana particles -> decorative
    lines = (text or "").split("\n")
    if len(lines) >= 3 and all(len(l) <= 2 for l in lines):
        if not any(c in "".join(lines) for c in "はがをにへでとものこと"):
            return False
    if bubble_overlap >= 0.3:
        return True  # inside a speech bubble -> dialogue
    return True  # narration paragraph / ordinary line


def read_scene_items(api: KoharuAPI) -> list[dict]:
    """Return the raw text items (page_id/node_id/text) from the live scene."""
    scene = api.get_scene()
    out = []
    for pid, page in scene["scene"]["pages"].items():
        for nid, node in page.get("nodes", {}).items():
            if "text" not in node.get("kind", {}):
                continue
            t = node["kind"]["text"]
            if (t.get("text") or "").strip():
                out.append({"page_id": pid, "node_id": nid,
                            "page_name": page.get("name"), "text": t.get("text")})
    return out


def classify_scene(api: KoharuAPI, scene: dict) -> dict[str, bool]:
    """Return { "page:node": is_translate } for every text node."""
    out: dict[str, bool] = {}
    for pid, page in scene["scene"]["pages"].items():
        mask = page_bubble_mask(api, scene, pid)
        for nid, node in page.get("nodes", {}).items():
            if "text" not in node.get("kind", {}):
                continue
            t = node["kind"]["text"]
            txt = t.get("text") or ""
            if not txt.strip():
                out[f"{pid}:{nid}"] = False
                continue
            fs = (t.get("fontPrediction") or {}).get("fontSizePx") or 0
            tr = node.get("transform") or {}
            ov = 0.0
            if mask and "x" in tr:
                ov = _overlap(mask, (tr["x"], tr["y"], tr["width"], tr["height"]))
            out[f"{pid}:{nid}"] = classify_node(txt, fs, ov)
    return out


def protect_nodes(api: KoharuAPI, scene: dict, skip: list[str], dry_run: bool = False) -> int:
    """Carve segment mask + hide nodes for the skip set. Returns nodes hidden."""
    # group by page
    by_page: dict[str, list[str]] = {}
    for pair in skip:
        pid, _, nid = pair.partition(":")
        by_page.setdefault(pid, []).append(nid)

    # 1) carve masks
    for pid, nids in by_page.items():
        seg = None
        for node in scene["scene"]["pages"][pid].get("nodes", {}).values():
            k = node.get("kind", {})
            if "mask" in k and k["mask"].get("role") == MASK_ROLE:
                seg = k["mask"]["blob"]
                break
        if not seg:
            continue
        im = Image.open(io.BytesIO(api.get_blob(seg))).convert("L")
        px = im.load()
        for nid in nids:
            node = scene["scene"]["pages"][pid]["nodes"].get(nid)
            if not node:
                continue
            tr = node.get("transform") or {}
            if not all(k in tr for k in ("x", "y", "width", "height")):
                continue
            x0, y0 = max(0, int(tr["x"])), max(0, int(tr["y"]))
            x1, y1 = min(im.width, int(tr["x"] + tr["width"])), min(im.height, int(tr["y"] + tr["height"]))
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    px[xx, yy] = 0
        if not dry_run:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            api.put(f"/api/v1/pages/{pid}/masks/{MASK_ROLE}", content=buf.getvalue())

    # 2) hide + clear translation
    ops = []
    for pid, nids in by_page.items():
        for nid in nids:
            node = scene["scene"]["pages"][pid]["nodes"].get(nid)
            if not node or "text" not in node.get("kind", {}):
                continue
            ops.append({"updateNode": {"page": pid, "id": nid,
                                       "patch": {"visible": False,
                                                 "data": {"text": {"translation": ""}}},
                                       "prev": {}}})
    if not dry_run:
        for i in range(0, len(ops), 100):
            api.client.post("/api/v1/history/apply",
                            json={"batch": {"label": "classify-protect", "ops": ops[i:i + 100]}})
    return len(ops)


def write_translate_slices(dump: list, classify: dict[str, bool], out_path: Path,
                           trans_dir: Path, slice_size: int = 200) -> dict:
    """Write slices containing ONLY translate nodes. Returns anchors.

    `dump` is the all_text list (page_id/node_id/text) in 1-based order.
    `classify` maps "page:node" -> is_translate.
    Keys written to slices are the 1-based positions of the translate nodes.
    """
    translate_positions = [
        i for i, o in enumerate(dump, 1)
        if classify.get(f"{o['page_id']}:{o['node_id']}", False)
    ]
    trans_dir.mkdir(parents=True, exist_ok=True)
    # clear old slices
    for f in trans_dir.glob("*_slice_*.json"):
        f.unlink()
    anchors: dict[str, dict] = {}
    n = (len(translate_positions) + slice_size - 1) // slice_size
    for si in range(n):
        chunk = translate_positions[si * slice_size:(si + 1) * slice_size]
        name = f"slice_{si + 1:02d}.json"
        # write placeholder (empty dict) so subagents have a target file
        json.dump({}, open(trans_dir / name, "w"), ensure_ascii=False)
        if chunk:
            lo, mid, hi = chunk[0], chunk[len(chunk) // 2], chunk[-1]
            anchors[name] = {
                str(lo): dump[lo - 1]["text"].replace("\n", "⏎")[:50],
                str(mid): dump[mid - 1]["text"].replace("\n", "⏎")[:50],
                str(hi): dump[hi - 1]["text"].replace("\n", "⏎")[:50],
            }
    if out_path:
        json.dump({"translate": translate_positions,
                   "n_translate": len(translate_positions),
                   "n_total": len(dump),
                   "slices": anchors},
                  open(out_path, "w"), ensure_ascii=False, indent=1)
    return anchors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:4000")
    ap.add_argument("--dump", required=True, help="all_text_vXX.json (page_id/node_id/text)")
    ap.add_argument("--slices", required=True, help="output slices_*.json path")
    ap.add_argument("--trans-dir", required=True, help="dir for slice_*.json placeholders")
    ap.add_argument("--report", help="optional machine-readable classification output")
    ap.add_argument("--apply-protect", action="store_true", help="carve masks + hide skipped nodes")
    ap.add_argument("--slice-size", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    api = KoharuAPI(args.server)
    scene = api.get_scene()
    dump = json.load(open(args.dump))

    classify = classify_scene(api, scene)
    n_trans = sum(1 for v in classify.values() if v)
    n_skip = len(classify) - n_trans
    print(f"nodes: total={len(classify)} translate={n_trans} skip={n_skip}")

    if args.report:
        json.dump({"n_total": len(classify), "n_translate": n_trans, "n_skip": n_skip},
                  open(args.report, "w"), ensure_ascii=False, indent=1)

    skip_pairs = [p for p, is_t in classify.items() if not is_t]
    n_hid = protect_nodes(api, scene, skip_pairs, dry_run=args.dry_run or not args.apply_protect)
    print(f"protected (skipped) nodes: {n_hid}" + (" [dry-run]" if args.dry_run or not args.apply_protect else ""))

    anchors = write_translate_slices(dump, classify, Path(args.slices),
                                     Path(args.trans_dir), args.slice_size)
    print(f"slices written with {len(anchors)} files; translate positions anchored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
