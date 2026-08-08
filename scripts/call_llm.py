"""Batch translation via DeepSeek / OpenAI-compatible API.

Usage:
    # Read OCR text from Koharu scene -> translate -> write back
    call_llm.py translate \\
        --server http://localhost:4000 \\
        --api-key sk-... \\
        --model deepseek-chat \\
        --backend https://api.deepseek.com/v1 \\
        --glossary work/glossary.locked.json \\
        --rules references/translation_rules.md \\
        --chapters work/chapters.json \\
        --protected work/protected_pages.json \\
        --tm work/tm.json \\
        --reading-order rtl \\
        --output work/translations.json

    # Translate raw tagged text (stdin) -> output translations (stdout)
    call_llm.py translate \\
        --api-key sk-... \\
        --lang "Traditional Chinese" \\
        --glossary glossary.json \\
        --batch-size 40 \\
        < ocr_text.txt > translations.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

SYSTEM_PROMPT_TPL = """You are a professional manga translator.

Translate the following manga dialogue into {target_language}.
Rules:
1. Preserve all [N] tags — every line MUST start with [N]
2. Preserve HTML/XML tags inside the text
3. Character names and terms must follow the glossary below
4. Keep character speech patterns and tone consistent
5. For names marked "keep source", leave them as-is
6. Output ONLY the tagged translations, no explanations
{consistency_rules}
Glossary:
{glossary_text}
{chapter_context}
{translation_memory}
"""

TM_HINT = "  (previous translation — reuse it verbatim)"


def read_scene_text(
    server_url: str,
    reading_order: str = "rtl",
    protected_page_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Read all text nodes from Koharu scene in reading order.

    Nodes are sorted per page by position: top-to-bottom, then right-to-left
    (``rtl``, Japanese manga) or left-to-right (``ltr``) — the scene dict order
    does NOT reflect visual reading order.
    Returns [{page, node, text, transform}, ...].
    """
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=60)
    r.raise_for_status()
    scene = r.json()
    protected = protected_page_ids or set()
    results: list[dict[str, Any]] = []
    for pid, page in scene["scene"]["pages"].items():
        if pid in protected:
            continue
        items = []
        for nid, n in page.get("nodes", {}).items():
            kind = n.get("kind", {})
            if "text" in kind:
                t = kind["text"]
                src = t.get("text")
                if src and src.strip():
                    items.append({
                        "page_id": pid,
                        "node_id": nid,
                        "page_name": page.get("name", pid[:8]),
                        "text": src,
                        "transform": n.get("transform", {}),
                    })
        results.extend(_sort_in_reading_order(items, reading_order))
    return results


def _sort_in_reading_order(
    items: list[dict[str, Any]], reading_order: str = "rtl"
) -> list[dict[str, Any]]:
    """Sort nodes by center position. Top-to-bottom, then x by reading direction."""
    def key(item: dict[str, Any]) -> tuple[float, float]:
        tr = item.get("transform") or {}
        cx = tr.get("x", 0) + tr.get("width", 0) / 2
        cy = tr.get("y", 0) + tr.get("height", 0) / 2
        return (cy, -cx if reading_order == "rtl" else cx)

    return sorted(items, key=key)


def load_protected_page_ids(path: str | None) -> set[str]:
    """Read page IDs from analyze.py --apply-protection output."""
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {p.get("page_id") for p in data.get("protected_pages", []) if p.get("page_id")}


def load_translation_memory(path: str | None) -> dict[str, str]:
    """Load the source->translation memory map (exact-match wording bank)."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}


def save_translation_memory(path: str, tm: dict[str, str]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(tm.items())), f, ensure_ascii=False, indent=2)


def format_batch(items: list[dict[str, Any]], tm: dict[str, str]) -> str:
    """Format text items as [1]...[N] tagged blocks, hinting at known repeats."""
    lines = []
    for i, item in enumerate(items, 1):
        text = item.get("text") or item.get("source_text") or ""
        hint = ""
        if tm and text in tm:
            hint = TM_HINT + f": {tm[text]}"
        lines.append(f"[{i}] {text}{hint}")
    return "\n".join(lines)


def parse_response(response_text: str, batch_len: int) -> list[str]:
    """Parse tagged [N] response back into per-item translations."""
    # Strip think blocks
    text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)
    translations: list[str] = [""] * batch_len
    pattern = re.compile(r"^\[(\d+)\]\s*(.*)")
    for line in text.split("\n"):
        m = pattern.match(line.strip())
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= batch_len:
                translations[idx - 1] = m.group(2).strip()
    return translations


def call_deepseek(
    api_key: str,
    model: str,
    base_url: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Call OpenAI-compatible chat completion API."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = httpx.post(url, headers=headers, json=body, timeout=120.0)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def load_glossary_text(path: str | None) -> str:
    if not path:
        return "(no glossary)"
    try:
        with open(path, "r", encoding="utf-8") as f:
            g = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return "(no glossary)"

    lines = []
    for c in g.get("characters", []):
        cn = c.get("canonical", "")
        rn = c.get("render", "")
        lines.append(f"  {cn} → {rn}")
    for t in g.get("terms", []):
        src = t.get("src", "")
        dst = t.get("dst", "")
        if dst:
            lines.append(f"  {src} → {dst}")
        elif t.get("keep_source"):
            lines.append(f"  {src} → (keep source)")
    return "\n".join(lines) if lines else "(no glossary entries)"


def load_chapter_context(path: str | None) -> str:
    """Build a chapter-context block for the prompt from chapter.py --json output.

    Each chapter is summarized by its label and first few dialogue lines so the
    model knows what is happening (helps keep names/tone consistent).
    """
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ""

    blocks = []
    for ch in data.get("chapters", [])[:30]:
        label = ch.get("label") or f"Chapter {ch.get('index')}"
        rng = ch.get("page_range") or []
        rng_str = f" (pages {rng[0]}–{rng[1]})" if len(rng) == 2 else ""
        blocks.append(f"Chapter {ch.get('index')}: {label}{rng_str}")
        samples = [
            b.get("text", "")
            for b in ch.get("text_blocks", [])
            if b.get("text") and not b.get("is_chapter_header")
        ][:3]
        for s in samples:
            blocks.append(f"  \"{s[:60]}\"")
    if not blocks:
        return ""
    return "Chapter context (for reference):\n" + "\n".join(blocks)


def build_consistency_rules(tm: dict[str, str], reading_order: str) -> str:
    rules = []
    if reading_order:
        rules.append(f"Order the [N] tags in {reading_order} reading order.")
    if tm:
        rules.append(
            "A line with a 'previous translation' hint MUST reuse that exact "
            "translation verbatim — do not reword it."
        )
    return ("\n".join(rules) + "\n") if rules else ""


def cmd_translate(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: --api-key required or set DEEPSEEK_API_KEY/OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    base_url = args.backend or "https://api.deepseek.com/v1"
    model = args.model or "deepseek-chat"
    target_lang = args.lang or "Traditional Chinese"
    batch_size = args.batch_size or 50
    glossary_text = load_glossary_text(args.glossary)
    chapter_context = load_chapter_context(args.chapters)
    tm = load_translation_memory(args.tm)
    reading_order = (args.reading_order or "rtl").lower()
    if reading_order not in ("rtl", "ltr"):
        reading_order = "rtl"

    # Read items
    protected = load_protected_page_ids(args.protected)
    if args.server:
        try:
            items = read_scene_text(args.server, reading_order, protected)
        except Exception as e:
            print(f"Error: cannot fetch scene from {args.server}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Read {len(items)} text nodes from Koharu "
              f"(skipped {len(protected)} protected pages)", file=sys.stderr)
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            items = json.load(f)
        print(f"Read {len(items)} items from {args.input}", file=sys.stderr)
    elif not sys.stdin.isatty():
        items = json.load(sys.stdin)
        print(f"Read {len(items)} items from stdin", file=sys.stderr)
    else:
        print("Error: specify --server, --input, or pipe JSON to stdin", file=sys.stderr)
        sys.exit(1)

    # Filter protected pages regardless of input source
    if protected:
        before = len(items)
        items = [it for it in items if it.get("page_id") not in protected]
        skipped = before - len(items)
        if skipped:
            print(f"Skipped {skipped} protected items", file=sys.stderr)

    # Translate in batches
    system_prompt = SYSTEM_PROMPT_TPL.format(
        target_language=target_lang,
        consistency_rules=build_consistency_rules(tm, reading_order),
        glossary_text=glossary_text,
        chapter_context=chapter_context,
        translation_memory=(
            "Existing translations (reuse these for identical lines):\n" +
            "\n".join(f"  {k} → {v}" for k, v in sorted(tm.items()))
            if tm else ""
        ),
    )

    if args.rules:
        try:
            with open(args.rules, "r", encoding="utf-8") as f:
                system_prompt += f"\n\nAdditional rules:\n{f.read()}"
        except FileNotFoundError:
            pass

    all_translations: list[dict[str, Any]] = []
    total = len(items)

    for start in range(0, total, batch_size):
        batch = items[start : start + batch_size]
        tagged = format_batch(batch, tm)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": tagged},
        ]

        print(f"  [{start + len(batch)}/{total}] Calling {model}...", file=sys.stderr)
        resp = call_deepseek(api_key, model, base_url, messages, args.temperature)
        translations = parse_response(resp, len(batch))

        for i, item in enumerate(batch):
            source = item.get("text") or item.get("source_text") or ""
            # Exact-match translation memory override: identical source lines
            # must reuse the stored wording, guaranteeing cross-page consistency.
            trans = tm.get(source) or (translations[i] if i < len(translations) else "")
            # Learn new exact-match pairs (first occurrence wins).
            if trans and source and source not in tm:
                tm[source] = trans
            all_translations.append({
                "page_id": item.get("page_id"),
                "node_id": item.get("node_id"),
                "page_name": item.get("page_name", ""),
                "source_text": source,
                "translation": trans,
            })

    if args.tm and tm:
        save_translation_memory(args.tm, tm)
        print(f"Translation memory updated: {len(tm)} entries → {args.tm}", file=sys.stderr)

    # Output
    output = args.output
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(all_translations, f, ensure_ascii=False, indent=2)
        print(f"Written {len(all_translations)} translations to {output}", file=sys.stderr)
    else:
        print(json.dumps(all_translations, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Batch manga translation via DeepSeek/OpenAI API")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("translate", help="Translate text nodes from Koharu or JSON")
    t.add_argument("--server", help="Koharu server URL (e.g. http://localhost:4000)")
    t.add_argument("--input", "-i", help="Input JSON file (page_id, node_id, text)")
    t.add_argument("--output", "-o", help="Output JSON file")
    t.add_argument("--api-key", help="DeepSeek/OpenAI API key (or DEEPSEEK_API_KEY env)")
    t.add_argument("--model", default="deepseek-chat", help="Model name")
    t.add_argument("--backend", help="API base URL (default: https://api.deepseek.com/v1)")
    t.add_argument("--lang", default="Traditional Chinese", help="Target language")
    t.add_argument("--glossary", help="Path to glossary.locked.json")
    t.add_argument("--rules", help="Path to translation rules file (e.g. translation_rules.md)")
    t.add_argument("--chapters", help="Path to chapter.py --json output (chapter context for the prompt)")
    t.add_argument("--protected", help="Path to analyze.py protected_pages.json (pages to skip)")
    t.add_argument("--tm", help="Path to translation-memory JSON (reuse wording for repeated source lines)")
    t.add_argument("--reading-order", choices=["rtl", "ltr"], default="rtl",
                   help="Reading direction used to order nodes (default: rtl for manga)")
    t.add_argument("--batch-size", type=int, default=50, help="Items per API call")
    t.add_argument("--temperature", type=float, default=0.3)

    args = parser.parse_args()
    cmd_translate(args)


if __name__ == "__main__":
    main()
