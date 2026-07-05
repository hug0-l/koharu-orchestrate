"""Analyze Koharu scene for page quality, protection candidates, and translation readiness.

Usage:
    # Analyze scene and show report
    analyze.py --server http://localhost:4000

    # Output machine-readable JSON for agent processing
    analyze.py --server http://localhost:4000 --json > analysis.json

    # Apply protection — mark low-quality pages to skip translation
    analyze.py --server http://localhost:4000 --apply-protection
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Characters that suggest decorative/non-dialogue text
DECORATIVE_PATTERN = re.compile(r"^[\.．…⋯•●○◎◇◆■□☆★♪♫♩†‡※〽️]+$")
PAGE_NUM_PATTERN = re.compile(r"^\d+$")
SHORT_SYMBOL_THRESHOLD = 5  # text shorter than this is likely decorative


@dataclass
class BlockAnalysis:
    node_id: str
    text: str
    translation: str | None
    confidence: float | None
    text_len: int
    is_decorative: bool
    is_page_number: bool
    has_ocr: bool
    has_translation: bool


@dataclass
class PageAnalysis:
    page_id: str
    page_name: str
    blocks: list[BlockAnalysis] = field(default_factory=list)
    total_blocks: int = 0
    blocks_with_ocr: int = 0
    blocks_with_translation: int = 0
    avg_confidence: float = 0.0
    is_cover: bool = False
    is_decorative_page: bool = False
    is_low_confidence: bool = False
    is_empty: bool = False
    protect_reason: str | None = None


def fetch_scene(server_url: str) -> dict[str, Any]:
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=30)
    r.raise_for_status()
    return r.json()


def analyze_pages(
    scene: dict[str, Any],
    confidence_threshold: float = 0.3,
) -> list[PageAnalysis]:
    pages_data = scene.get("scene", {}).get("pages", {})
    results: list[PageAnalysis] = []

    for pid, page in pages_data.items():
        page_name = page.get("name", pid[:8])
        pa = PageAnalysis(page_id=pid, page_name=page_name)

        for nid, node in page.get("nodes", {}).items():
            kind = node.get("kind", {})
            if "text" not in kind:
                continue

            t = kind["text"]
            text = t.get("text") or ""
            translation = t.get("translation")
            confidence = t.get("confidence")
            text_len = len(text.strip())

            ba = BlockAnalysis(
                node_id=nid,
                text=text,
                translation=translation,
                confidence=confidence,
                text_len=text_len,
                is_decorative=bool(DECORATIVE_PATTERN.match(text.strip())),
                is_page_number=bool(PAGE_NUM_PATTERN.match(text.strip())),
                has_ocr=bool(text and text.strip()),
                has_translation=bool(translation and translation.strip()),
            )
            pa.blocks.append(ba)

        pa.total_blocks = len(pa.blocks)
        pa.blocks_with_ocr = sum(1 for b in pa.blocks if b.has_ocr)
        pa.blocks_with_translation = sum(1 for b in pa.blocks if b.has_translation)

        confidences = [b.confidence for b in pa.blocks if b.confidence is not None]
        pa.avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Heuristics for page type
        non_decorative = [b for b in pa.blocks if not b.is_decorative]
        short_blocks = [b for b in pa.blocks if b.text_len < SHORT_SYMBOL_THRESHOLD]
        all_decorative = pa.total_blocks > 0 and all(
            b.is_decorative or b.is_page_number for b in pa.blocks
        )

        # Cover page: mostly short/decorative text
        if all_decorative and pa.total_blocks <= 3:
            pa.is_cover = True
            pa.protect_reason = "cover_page"

        # Decorative page: all blocks are non-language
        elif all_decorative:
            pa.is_decorative_page = True
            pa.protect_reason = "decorative"

        # No OCR text at all
        elif pa.blocks_with_ocr == 0 and pa.total_blocks > 0:
            pa.is_empty = True
            pa.protect_reason = "no_ocr_text"

        # Low average confidence
        elif pa.avg_confidence < confidence_threshold and pa.total_blocks > 0:
            pa.is_low_confidence = True
            pa.protect_reason = f"low_confidence({pa.avg_confidence:.2f})"

        results.append(pa)

    return results


def report(analyses: list[PageAnalysis]) -> None:
    total = len(analyses)
    protected = [a for a in analyses if a.protect_reason]
    ok = [a for a in analyses if not a.protect_reason]

    print(f"Total pages: {total}")
    print(f"  OK to translate: {len(ok)} pages")
    print(f"  Protected (skip): {len(protected)} pages")
    print()

    if protected:
        print("=== Protected Pages ===")
        for a in protected:
            print(f"  {a.page_name} ({a.page_id[:8]}) — {a.protect_reason}")
            for b in a.blocks[:3]:
                preview = b.text[:60] if b.text else "(empty)"
                conf = f"conf={b.confidence:.2f}" if b.confidence else "no-conf"
                print(f"    {b.node_id[:8]} {preview} [{conf}]")
            if len(a.blocks) > 3:
                print(f"    ... and {len(a.blocks)-3} more")
        print()

    if ok:
        print("=== Pages Ready for Translation ===")
        for a in ok[:10]:
            conf = f"avg_conf={a.avg_confidence:.2f}" if a.avg_confidence else ""
            print(f"  {a.page_name} ({a.page_id[:8]}) {a.blocks_with_ocr}/{a.total_blocks} blocks {conf}")
        if len(ok) > 10:
            print(f"  ... and {len(ok)-10} more")

    # Summary stats
    print()
    total_blocks = sum(a.total_blocks for a in analyses)
    total_ocr = sum(a.blocks_with_ocr for a in analyses)
    total_translated = sum(a.blocks_with_translation for a in analyses)
    print(f"Total text blocks: {total_blocks}")
    print(f"  With OCR: {total_ocr}")
    print(f"  With translation: {total_translated}")


def apply_protection(server_url: str, analyses: list[PageAnalysis], output: str | None = None) -> None:
    """Write protected page IDs to a JSON file so the translation step can skip them.
    Non-destructive — does not modify scene data."""
    protected = [a for a in analyses if a.protect_reason]
    if not protected:
        print("No pages to protect.")
        return

    out_path = Path(output or "protected_pages.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "protected_pages": [
            {
                "page_id": a.page_id,
                "page_name": a.page_name,
                "protect_reason": a.protect_reason,
            }
            for a in protected
        ]
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Protected {len(protected)} pages.")
    print(f"Written: {out_path.resolve()}")
    print("The translation step will skip these pages.")


def cmd_analyze(args: argparse.Namespace) -> None:
    try:
        scene = fetch_scene(args.server)
    except Exception as e:
        print(f"Error: cannot fetch scene from {args.server}: {e}", file=sys.stderr)
        sys.exit(1)
    analyses = analyze_pages(scene, args.confidence)

    if args.json:
        output = []
        for a in analyses:
            output.append({
                "page_id": a.page_id,
                "page_name": a.page_name,
                "total_blocks": a.total_blocks,
                "blocks_with_ocr": a.blocks_with_ocr,
                "blocks_with_translation": a.blocks_with_translation,
                "avg_confidence": round(a.avg_confidence, 3),
                "is_cover": a.is_cover,
                "is_decorative_page": a.is_decorative_page,
                "is_low_confidence": a.is_low_confidence,
                "is_empty": a.is_empty,
                "protect_reason": a.protect_reason,
                "blocks": [
                    {
                        "node_id": b.node_id,
                        "text_preview": b.text[:80],
                        "has_ocr": b.has_ocr,
                        "has_translation": b.has_translation,
                        "is_decorative": b.is_decorative,
                        "confidence": b.confidence,
                    }
                    for b in a.blocks
                ],
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        report(analyses)

    if args.apply_protection:
        try:
            apply_protection(args.server, analyses, args.protection_output)
        except Exception as e:
            print(f"Error writing protection file: {e}", file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Analyze Koharu scene for page quality")
    parser.add_argument("--server", default="http://localhost:4000", help="Koharu server URL")
    parser.add_argument("--confidence", type=float, default=0.3, help="Confidence threshold (default: 0.3)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--apply-protection", action="store_true", help="Mark low-quality pages to skip translation (non-destructive)")
    parser.add_argument("--protection-output", default="protected_pages.json", help="Output path for protection list (default: protected_pages.json)")
    args = parser.parse_args()
    cmd_analyze(args)


if __name__ == "__main__":
    main()
