"""Verify glossary compliance and translation wording consistency.

Usage:
    # Check a live Koharu scene against the locked glossary
    verify.py check --server http://localhost:4000 --glossary work/glossary.locked.json

    # Check from a saved scene.json (offline)
    verify.py check --scene scene.json --glossary work/glossary.locked.json

    # Machine-readable output for agent post-processing
    verify.py check --server http://localhost:4000 --glossary g.json --json

    # Also load the translation memory to detect drifted wordings
    verify.py check --server http://localhost:4000 --glossary g.json --tm work/tm.json
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Renders containing only ASCII letters/spaces/punctuation -> keep source name.
_ASCII_NAME_RE = re.compile(r"^[A-Za-z .'\-]+$")
SENTENCE_PUNCT = set("。！？…、?!：；～")


def is_english_render(value: str) -> bool:
    return bool(_ASCII_NAME_RE.match(value or ""))


def normalize(text: str) -> str:
    """Normalize for exact-repeat comparison (strip whitespace/punctuation)."""
    return re.sub(r"\s+", "", text or "")


@dataclass
class CheckIssue:
    kind: str  # character | term | repeat | untranslated
    page_name: str
    node_id: str
    source: str
    translation: str
    detail: str


@dataclass
class VerifyResult:
    issues: list[CheckIssue] = field(default_factory=list)
    checked_nodes: int = 0
    translated_nodes: int = 0
    glossary_entries: dict[str, int] = field(default_factory=dict)


def fetch_scene(server_url: str) -> dict[str, Any]:
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=60)
    r.raise_for_status()
    return r.json()


def load_scene(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_glossary(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        g = json.load(f)
    return {
        "characters": g.get("characters", []),
        "terms": g.get("terms", []),
        "non_translate": g.get("non_translate", []),
    }


def text_nodes(scene: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Yield (page_name, node_id, text_data) for every text node in the scene."""
    out = []
    for pid, page in scene.get("scene", {}).get("pages", {}).items():
        for nid, node in page.get("nodes", {}).items():
            kind = node.get("kind", {})
            if "text" in kind:
                out.append((page.get("name", pid[:8]), nid, kind["text"]))
    return out


def check_glossary_compliance(result: VerifyResult, nodes: list, glossary: dict[str, Any]) -> None:
    """Check every translated node against characters/terms rules."""
    chars = glossary.get("characters", [])
    terms = glossary.get("terms", [])

    for page_name, nid, t in nodes:
        src = t.get("text") or ""
        dst = t.get("translation") or ""
        if not dst:
            continue
        for c in chars:
            canonical = c.get("canonical", "")
            aliases = [canonical] + [a for a in c.get("aliases", []) if a]
            names_in_src = [n for n in aliases if n and n in src]
            if not names_in_src:
                continue
            render = c.get("render", "") or canonical
            if is_english_render(render):
                # Keep source: the source name must survive in the translation.
                if not any(n in dst for n in names_in_src) and render not in dst:
                    result.issues.append(CheckIssue(
                        "character", page_name, nid, src, dst,
                        f"name '{names_in_src[0]}' should be kept (render '{render}')",
                    ))
            else:
                # Translated render must appear in the translation.
                if render not in dst:
                    result.issues.append(CheckIssue(
                        "character", page_name, nid, src, dst,
                        f"name should render as '{render}'",
                    ))
        for term in terms:
            s = term.get("src", "")
            if not s or s not in src:
                continue
            dst_val = term.get("dst")
            if term.get("keep_source"):
                if s not in dst:
                    result.issues.append(CheckIssue(
                        "term", page_name, nid, src, dst,
                        f"term '{s}' must be kept as-is (keep_source)",
                    ))
            elif dst_val and dst_val not in dst:
                result.issues.append(CheckIssue(
                    "term", page_name, nid, src, dst,
                    f"term should use '{dst_val}' instead of '{s}'",
                ))


def check_repeat_consistency(result: VerifyResult, nodes: list) -> None:
    """Flag identical source lines translated differently across the book."""
    groups: dict[str, list[tuple[str, str, str, str]]] = {}
    for page_name, nid, t in nodes:
        src = t.get("text") or ""
        dst = t.get("translation") or ""
        if not src or not dst:
            continue
        key = normalize(src)
        if len(key) < 2:
            continue
        groups.setdefault(key, []).append((page_name, nid, src, dst))

    for key, entries in groups.items():
        variants = {normalize(dst) for _, _, _, dst in entries}
        if len(variants) > 1:
            # Choose the most common wording as the suggested canonical one.
            from collections import Counter
            counts = Counter(normalize(dst) for _, _, _, dst in entries)
            suggested = max(counts, key=counts.get)
            for page_name, nid, src, dst in entries:
                if normalize(dst) != suggested:
                    result.issues.append(CheckIssue(
                        "repeat", page_name, nid, src, dst,
                        f"same source translated differently; suggested canonical: '{suggested}'",
                    ))


def check_untranslated(result: VerifyResult, nodes: list) -> None:
    for page_name, nid, t in nodes:
        src = (t.get("text") or "").strip()
        dst = (t.get("translation") or "").strip()
        if src and not dst:
            result.issues.append(CheckIssue(
                "untranslated", page_name, nid, src, "", "translation missing",
            ))


def cmd_check(args: argparse.Namespace) -> None:
    if args.server:
        try:
            scene = fetch_scene(args.server)
        except Exception as e:
            print(f"Error: cannot fetch scene from {args.server}: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.scene:
        try:
            scene = load_scene(args.scene)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error: cannot read scene file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: specify --server or --scene", file=sys.stderr)
        sys.exit(1)

    try:
        glossary = load_glossary(args.glossary)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: cannot read glossary: {e}", file=sys.stderr)
        sys.exit(1)

    result = VerifyResult()
    nodes = text_nodes(scene)
    result.checked_nodes = len(nodes)
    result.translated_nodes = sum(1 for _, _, t in nodes if (t.get("translation") or "").strip())
    result.glossary_entries = {
        "characters": len(glossary["characters"]),
        "terms": len(glossary["terms"]),
    }

    check_glossary_compliance(result, nodes, glossary)
    check_repeat_consistency(result, nodes)
    check_untranslated(result, nodes)

    if args.json:
        print(json.dumps({
            "checked_nodes": result.checked_nodes,
            "translated_nodes": result.translated_nodes,
            "glossary_entries": result.glossary_entries,
            "issue_count": len(result.issues),
            "issues": [
                {
                    "kind": i.kind,
                    "page_name": i.page_name,
                    "node_id": i.node_id,
                    "source": i.source,
                    "translation": i.translation,
                    "detail": i.detail,
                }
                for i in result.issues
            ],
        }, ensure_ascii=False, indent=2))
        return

    print(f"Nodes checked: {result.checked_nodes}  (translated: {result.translated_nodes})")
    print(f"Glossary: {result.glossary_entries['characters']} characters, "
          f"{result.glossary_entries['terms']} terms")
    print()

    if not result.issues:
        print("No issues found — glossary compliant and no repeated-line drift.")
        return

    by_kind: dict[str, list[CheckIssue]] = {}
    for i in result.issues:
        by_kind.setdefault(i.kind, []).append(i)

    for kind, items in by_kind.items():
        print(f"=== {kind} ({len(items)}) ===")
        for i in items[:15]:
            src = i.source[:50]
            dst = i.translation[:50]
            print(f"  {i.page_name}: {src} → {dst}")
            print(f"      {i.detail}")
        if len(items) > 15:
            print(f"  ... and {len(items) - 15} more")
        print()


def main():
    parser = argparse.ArgumentParser(description="Verify glossary compliance and wording consistency")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="Check a scene against the glossary")
    c.add_argument("--server", help="Koharu server URL")
    c.add_argument("--scene", help="Saved scene.json path (offline mode)")
    c.add_argument("--glossary", required=True, help="Path to glossary.locked.json")
    c.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args()
    cmd_check(args)


if __name__ == "__main__":
    main()
