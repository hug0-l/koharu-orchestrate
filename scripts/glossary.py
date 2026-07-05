"""Glossary management for Koharu orchestrate skill.

Usage:
    # Create empty template
    python glossary.py template --out ./work/glossary.locked.json

    # Import from AiNiee config.json
    python glossary.py import-ainiee \
        --config ~/Library/Application\ Support/AiNiee/config.json \
        --out ./work/glossary.locked.json

    # Fetch official Chinese title from Wikipedia
    python glossary.py fetch --series "上伊那ぼたん、酔へる姿は百合の花" --out glossary.json
    python glossary.py fetch --from-dir "./[塀] 上伊那ぼたん 第08巻" --out glossary.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from urllib.parse import quote

GLOSSARY_TEMPLATE: dict[str, Any] = {
    "characters": [
        {
            "canonical": "Example Name",
            "render": "Example Name",
            "aliases": ["Alias1", "Alias2"],
            "gender": "M",
            "note": "protagonist, keep source",
        }
    ],
    "terms": [
        {
            "src": "Source Term",
            "dst": "譯名",
            "category": "concept",
        }
    ],
    "non_translate": [
        {
            "marker": "<i>",
            "category": "tag",
        }
    ],
}


def cmd_template(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(GLOSSARY_TEMPLATE, f, ensure_ascii=False, indent=2)
    print(f"Written: {out.resolve()}")
    print("Edit the file to add your characters and terms before translating.")


def cmd_import_ainiee(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    glossary: dict[str, Any] = {
        "characters": [],
        "terms": [],
        "non_translate": [],
    }

    # Import prompt_dictionary_data (terms + characters)
    dict_data = config.get("prompt_dictionary_data", {})
    term_list = dict_data.get("data", dict_data.get("dictionary", []))

    # Also try alternative key paths
    if not term_list:
        term_list = config.get("dictionary", [])

    for entry in term_list:
        src = entry.get("src", entry.get("source_text", ""))
        dst = entry.get("dst", entry.get("translated_text", ""))
        category = entry.get("category", "term")

        if not src:
            continue

        # Characters vs terms heuristic
        is_character = category in ("character", "person", "name") or _looks_like_name(
            src, dst
        )

        item: dict[str, Any] = {"src": src}

        if is_character:
            # Determine whether to keep source or translate
            render = dst if _is_non_english(dst) else src
            glossary["characters"].append(
                {
                    "canonical": src,
                    "render": render,
                    "aliases": [src],
                    "gender": entry.get("gender", "-"),
                    "note": entry.get("note", ""),
                }
            )
        else:
            item["dst"] = dst
            item["category"] = category
            glossary["terms"].append(item)

    # Deduplicate
    glossary["characters"] = _deduplicate_by_key(
        glossary["characters"], "canonical"
    )
    glossary["terms"] = _deduplicate_by_key(glossary["terms"], "src")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    print(f"Imported {len(glossary['characters'])} characters, "
          f"{len(glossary['terms'])} terms from AiNiee config")
    print(f"Written: {out.resolve()}")
    print("\n⚠ Review and lock the glossary before translating!")
    print("  Check that aliases are complete and render values are correct.")


# ── Wikipedia fetch ──────────────────────────────────────────

# ── Wikipedia fetch ──────────────────────────────────────────


def _detect_series_from_dir(dir_path: str) -> str:
    """Extract series name from a directory path like '[塀] 上伊那ぼたん 第08巻'."""
    name = Path(dir_path).name
    # Remove brackets content like [塀]
    name = re.sub(r"^\[.*?\]\s*", "", name)
    # Remove volume suffix: 第08巻, Vol.8, v08, etc.
    name = re.sub(r"[第\s]*(?:Vol\.?|v|volume|巻|話|話数)?\s*\.?\s*\d+[\s\-]*.*$", "", name)
    # Remove trailing spaces/dashes
    name = name.strip(" ␣　-–—")
    # If nothing left, use parent dir
    if not name:
        name = Path(dir_path).parent.name
    return name


def cmd_fetch(args: argparse.Namespace) -> None:
    """Detect series name and generate lookup URLs. Agent then uses webfetch."""

    series = args.series
    if not series and args.from_dir:
        series = _detect_series_from_dir(args.from_dir)
        print(f"Detected series name: {series}", file=sys.stderr)
    if not series:
        print("Error: specify --series or --from-dir", file=sys.stderr)
        sys.exit(1)

    # Build search URLs for agent to webfetch
    wiki_search_url = (
        f"https://zh.wikipedia.org/w/index.php?"
        f"search={quote(series)}&variant=zh-tw"
    )

    glossary: dict[str, Any] = {
        "characters": [],
        "terms": [{
            "src": series,
            "dst": series,
            "category": "series",
            "_search_urls": {
                "wikipedia_zh": wiki_search_url,
                "wikipedia_ja": f"https://ja.wikipedia.org/wiki/{quote(series)}",
                "bangumi": f"https://bgm.tv/subject_search/{quote(series)}?cat=1",
            }
        }],
        "non_translate": [],
        "_series_raw": series,
        "_agent_instructions": (
            f"1. Use webfetch to search Wikipedia: {wiki_search_url}\n"
            f"2. Find the official Traditional Chinese title and character names\n"
            f"3. Also check Bangumi or the publisher's site for official translations\n"
            f"4. Fill in the 'characters' array with {{canonical, render, aliases}}\n"
            f"5. Set the series term's 'dst' to the official Chinese title\n"
            f"6. Review and lock the glossary before translating"
        ),
        "_note": "Auto-generated skeleton — agent must webfetch sources and fill in",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    print(f"Written: {out.resolve()}", file=sys.stderr)
    print(f"\nAgent workflow:", file=sys.stderr)
    print(f"  1. webfetch \"{wiki_search_url}\"", file=sys.stderr)
    print(f"  2. Extract official Chinese title + character names", file=sys.stderr)
    print(f"  3. Fill glossary characters and terms", file=sys.stderr)
    print(f"  4. Lock glossary before translating", file=sys.stderr)


def _looks_like_name(src: str, dst: str) -> bool:
    """Heuristic: if both src and dst are short capitalized words, treat as name."""
    return bool(
        re.match(r"^[A-Z][a-z]+", src) and (not dst or re.match(r"^[A-Z]", dst))
    )


def _is_non_english(s: str) -> bool:
    """Check if a string contains non-ASCII characters (suggesting translation)."""
    return any(ord(c) > 127 for c in s)


def _deduplicate_by_key(items: list[dict], key: str) -> list[dict]:
    seen: set[str] = set()
    result = []
    for item in items:
        val = item.get(key, "")
        if val not in seen:
            seen.add(val)
            result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description="Koharu glossary management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # template
    tpl = subparsers.add_parser("template", help="Create empty glossary template")
    tpl.add_argument("--out", "-o", required=True, help="Output path")

    # import-ainiee
    imp = subparsers.add_parser("import-ainiee", help="Import from AiNiee config.json")
    imp.add_argument("--config", "-c", required=True, help="AiNiee config.json path")
    imp.add_argument("--out", "-o", required=True, help="Output path")

    # fetch
    fet = subparsers.add_parser("fetch", help="Fetch official Chinese title from Wikipedia")
    fet.add_argument("--series", "-s", help="Series name (Japanese or English)")
    fet.add_argument("--from-dir", "-d", help="Directory path to auto-detect series name")
    fet.add_argument("--out", "-o", required=True, help="Output path")

    args = parser.parse_args()

    if args.command == "template":
        cmd_template(args)
    elif args.command == "import-ainiee":
        cmd_import_ainiee(args)
    elif args.command == "fetch":
        cmd_fetch(args)


if __name__ == "__main__":
    main()
