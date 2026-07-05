"""Detect chapter boundaries in manga and group OCR text for summarization.

Usage:
    # Detect chapters and show report
    chapter.py detect --server http://localhost:4000

    # Output grouped OCR text per chapter for agent summarization
    chapter.py detect --server http://localhost:4000 --json > chapters.json

    # Apply chapter metadata to scene (tag pages with chapter number)
    chapter.py detect --server http://localhost:4000 --apply-tags
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

# ── Chapter header detection patterns ──────────────────────

# ── Chapter header detection patterns ──────────────────────
# Conservative: only match explicit chapter markers.

CHAPTER_HEADER_PATTERNS = [
    re.compile(r"第\s*(\d+)\s*[話话巻卷章]"),         # 第1話, 第 1 話
    re.compile(r"([Cc]hapter|CHAP?\.?|Ch\.?)\s*(\d+)"),   # Chapter 1, CH.1
    re.compile(r"VOL\.?\s*(\d+)", re.IGNORECASE),          # VOL.1
    re.compile(r"(\d+)\s*[話话]"),                          # 1話  (note: may be loose)
    re.compile(r"([Ss]tage|[Aa]ct|[Pp]art)\s*(\d+)"),     # Stage 1, Act 2
    re.compile(r"第\s*([一二三四五六七八九十]+)\s*[話话章]"), # 第一話
]

PAGE_NUM_PATTERN = re.compile(r"^\s*\d+\s*$")


@dataclass
class TextBlock:
    node_id: str
    page_id: str
    page_name: str
    text: str
    confidence: float | None
    is_chapter_header: bool = False
    is_page_number: bool = False


@dataclass
class Chapter:
    index: int
    label: str | None             # "第1話", "Chapter 2", etc.
    label_text: str | None        # The actual OCR text of the header
    start_page_index: int
    end_page_index: int | None = None
    pages: list[str] = field(default_factory=list)       # page_ids
    blocks: list[TextBlock] = field(default_factory=list)


@dataclass
class VolumeAnalysis:
    pages: list[dict[str, Any]] = field(default_factory=list)
    all_blocks: list[TextBlock] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    total_pages: int = 0
    total_blocks: int = 0


def fetch_scene(server_url: str) -> dict[str, Any]:
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=30)
    r.raise_for_status()
    return r.json()


def is_chapter_header(text: str) -> bool:
    """Conservative: only match explicit chapter patterns, not arbitrary short text."""
    text = text.strip()
    if not text or PAGE_NUM_PATTERN.match(text):
        return False
    for pat in CHAPTER_HEADER_PATTERNS:
        if pat.search(text):
            return True
    return False


MIN_BLOCKS_FOR_DIVIDER = 3    # pages with ≤ this many blocks may be dividers
MAX_TEXT_LEN_FOR_DIVIDER = 30  # blocks shorter than this may be decorative


def _page_is_divider(pages_data: dict, pid: str, all_blocks: list[TextBlock]) -> bool:
    """Check if a page looks like a chapter divider (few very short blocks)."""
    page_blocks = [b for b in all_blocks if b.page_id == pid]
    if not page_blocks:
        return False
    if len(page_blocks) > MIN_BLOCKS_FOR_DIVIDER:
        return False
    # All blocks are short (decorative/page numbers)
    return all(len(b.text.strip()) < MAX_TEXT_LEN_FOR_DIVIDER for b in page_blocks)


def detect_chapters(
    scene: dict[str, Any],
    smart_detect: bool = True,
    min_chapter_pages: int = 3,
) -> VolumeAnalysis:
    pages_data = scene.get("scene", {}).get("pages", {})
    va = VolumeAnalysis()

    # Collect all pages in order
    page_ids = list(pages_data.keys())
    va.total_pages = len(page_ids)

    # Extract all text blocks
    for idx, pid in enumerate(page_ids):
        page = pages_data[pid]
        page_name = page.get("name", pid[:8])
        va.pages.append({"id": pid, "name": page_name, "index": idx})

        for nid, node in page.get("nodes", {}).items():
            kind = node.get("kind", {})
            if "text" not in kind:
                continue
            t = kind["text"]
            text = (t.get("text") or "").strip()
            if not text:
                continue

            block = TextBlock(
                node_id=nid,
                page_id=pid,
                page_name=page_name,
                text=text,
                confidence=t.get("confidence"),
                is_chapter_header=is_chapter_header(text),
                is_page_number=bool(PAGE_NUM_PATTERN.match(text)),
            )
            va.all_blocks.append(block)

    va.total_blocks = len(va.all_blocks)

    # Detect chapter boundaries
    chapters: list[Chapter] = []
    current_chapter = Chapter(index=0, label="Prologue", label_text=None, start_page_index=0, pages=[], blocks=[])
    seen_chapter_pages: set[str] = set()

    for block in va.all_blocks:
        if block.is_chapter_header:
            if block.page_id not in seen_chapter_pages:
                seen_chapter_pages.add(block.page_id)
                label = block.text.strip()[:60]
                ch_num = len(chapters) + 1
                ch = Chapter(
                    index=ch_num,
                    label=label,
                    label_text=block.text,
                    start_page_index=next(p["index"] for p in va.pages if p["id"] == block.page_id),
                    pages=[block.page_id],
                    blocks=[block],
                )
                chapters.append(ch)
                current_chapter = ch
        else:
            if block.page_id not in current_chapter.pages:
                current_chapter.pages.append(block.page_id)
            current_chapter.blocks.append(block)

    # If no chapter headers found, try smart detection (natural breaks)
    if not chapters and smart_detect:
        chapters = _detect_natural_chapters(va, pages_data, min_chapter_pages)

    # Final fallback: entire volume as one chapter
    if not chapters:
        chap = Chapter(
            index=1, label="Volume", label_text=None,
            start_page_index=0, end_page_index=va.total_pages - 1,
            pages=[p["id"] for p in va.pages],
            blocks=[b for b in va.all_blocks],
        )
        chapters.append(chap)

    # Reindex and set end_page_index
    chapters.sort(key=lambda c: c.start_page_index)
    for i, ch in enumerate(chapters):
        ch.index = i + 1
        if ch.label is None:
            ch.label = f"Section {ch.index}"
        if i + 1 < len(chapters):
            next_start = chapters[i + 1].start_page_index
            ch.end_page_index = max(ch.start_page_index, next_start - 1)
        else:
            ch.end_page_index = va.total_pages - 1

    va.chapters = chapters
    return va


def _detect_natural_chapters(
    va: VolumeAnalysis,
    pages_data: dict[str, Any],
    min_pages: int = 3,
) -> list[Chapter]:
    """Detect chapter-like boundaries by finding divider pages."""
    divider_indices: list[int] = [
        idx for idx, pinfo in enumerate(va.pages)
        if _page_is_divider(pages_data, pinfo["id"], va.all_blocks)
    ]

    if not divider_indices:
        return []

    # Group consecutive dividers → pick last of each group as boundary
    boundaries: list[int] = []
    prev_div = divider_indices[0]
    for d in divider_indices[1:]:
        if d > prev_div + 1:
            boundaries.append(prev_div + 1)
        prev_div = d
    boundaries.append(prev_div + 1)
    boundaries = [b for b in boundaries if 0 < b < va.total_pages]

    chapters: list[Chapter] = []
    prev = 0
    for idx, b in enumerate(boundaries):
        if b - prev >= min_pages:
            page_ids = [va.pages[p]["id"] for p in range(prev, b)]
            chap = Chapter(
                index=idx + 1, label=None, label_text=None,
                start_page_index=prev,
                pages=page_ids,
                blocks=[blk for blk in va.all_blocks if blk.page_id in page_ids],
            )
            chapters.append(chap)
            prev = b

    # Last section
    if va.total_pages - prev >= min_pages:
        page_ids = [va.pages[p]["id"] for p in range(prev, va.total_pages)]
        chap = Chapter(
            index=len(chapters) + 1, label=None, label_text=None,
            start_page_index=prev,
            pages=page_ids,
            blocks=[blk for blk in va.all_blocks if blk.page_id in page_ids],
        )
        chapters.append(chap)

    return chapters


def report(va: VolumeAnalysis) -> None:
    print(f"Total pages: {va.total_pages}")
    print(f"Total text blocks: {va.total_blocks}")
    print(f"Chapters detected: {len(va.chapters)}")
    print()

    for ch in va.chapters:
        if ch.end_page_index is None:
            ch.end_page_index = va.total_pages - 1
        page_count = max(0, ch.end_page_index - ch.start_page_index + 1)
        block_count = len(ch.blocks)
        label = ch.label or "(no label)"
        print(f"  Chapter {ch.index}: {label}")
        print(f"    Pages: {ch.start_page_index + 1}–{ch.end_page_index + 1} ({page_count} pages)")
        print(f"    Blocks: {block_count}")

        # Show first few text samples
        if ch.label_text:
            print(f"    Header: \"{ch.label_text[:80]}\"")
        dialogue = [b for b in ch.blocks if not b.is_chapter_header and not b.is_page_number]
        for d in dialogue[:4]:
            print(f"    \"{d.text[:80]}\"")
        if len(dialogue) > 4:
            print(f"    ... and {len(dialogue) - 4} more")
        print()


def output_json(va: VolumeAnalysis) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total_pages": va.total_pages,
        "total_blocks": va.total_blocks,
        "chapters": [],
    }
    for ch in va.chapters:
        ch_data = {
            "index": ch.index,
            "label": ch.label,
            "label_text": ch.label_text,
            "page_range": [ch.start_page_index + 1, (ch.end_page_index or va.total_pages - 1) + 1],
            "page_count": ch.end_page_index - ch.start_page_index + 1 if ch.end_page_index else 1,
            "page_ids": ch.pages,
            "block_count": len(ch.blocks),
            "text_blocks": [
                {
                    "node_id": b.node_id,
                    "page_id": b.page_id,
                    "page_name": b.page_name,
                    "text": b.text,
                    "is_chapter_header": b.is_chapter_header,
                }
                for b in ch.blocks
            ],
        }
        result["chapters"].append(ch_data)
    return result


def apply_tags(server_url: str, va: VolumeAnalysis) -> None:
    """Add chapter metadata as translations (for reference) or tags."""
    pages_data = {p["id"]: p for p in va.pages}

    ops = []
    for ch in va.chapters:
        for page_id in ch.pages:
            page = pages_data.get(page_id)
            if not page:
                continue
            # Add a note-like translation to the first block if empty
            # This is a lightweight way to embed chapter info
            page_nodes = []  # We'd need to re-fetch scene for this
            ops.append({
                "tag": "chapter",
                "page_id": page_id,
                "chapter_index": ch.index,
                "chapter_label": ch.label,
            })

    # For now, just output the tag structure
    print(json.dumps(ops, ensure_ascii=False, indent=2))
    print(f"\nWould tag {len(ops)} pages with chapter info.", file=sys.stderr)
    print("Apply via script with --apply-tags to write to scene.", file=sys.stderr)


def cmd_detect(args: argparse.Namespace) -> None:
    scene = fetch_scene(args.server)
    va = detect_chapters(scene)

    if args.json:
        data = output_json(va)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        report(va)

    if args.apply_tags:
        apply_tags(args.server, va)


def main():
    parser = argparse.ArgumentParser(description="Detect chapters in manga volume")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("detect", help="Detect chapter boundaries from OCR text")
    d.add_argument("--server", default="http://localhost:4000", help="Koharu server URL")
    d.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    d.add_argument("--apply-tags", action="store_true", help="Write chapter metadata to scene")
    args = parser.parse_args()

    cmd_detect(args)


if __name__ == "__main__":
    main()
