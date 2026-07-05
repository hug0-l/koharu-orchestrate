"""Glossary management for Koharu orchestrate skill.

Usage:
    # Create empty template
    python glossary.py template --out ./work/glossary.locked.json

    # Import from AiNiee config.json
    python glossary.py import-ainiee \
        --config ~/Library/Application\ Support/AiNiee/config.json \
        --out ./work/glossary.locked.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

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

    args = parser.parse_args()

    if args.command == "template":
        cmd_template(args)
    elif args.command == "import-ainiee":
        cmd_import_ainiee(args)


if __name__ == "__main__":
    main()
