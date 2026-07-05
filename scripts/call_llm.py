"""Batch translation via DeepSeek / OpenAI-compatible API.

Usage:
    # Read OCR text from Koharu scene → translate → write back
    call_llm.py translate \\
        --server http://localhost:4000 \\
        --api-key sk-... \\
        --model deepseek-chat \\
        --backend https://api.deepseek.com/v1 \\
        --glossary work/glossary.locked.json \\
        --rules references/translation_rules.md \\
        --output work/translations.json

    # Translate raw tagged text (stdin) → output translations (stdout)
    call_llm.py translate \\
        --api-key sk-... \\
        --lang "Traditional Chinese" \\
        --glossary glossary.json \\
        --batch-size 40
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

Glossary:
{glossary_text}
"""


def read_scene_text(server_url: str) -> list[dict[str, Any]]:
    """Read all text nodes from Koharu scene, return [{page, node, text}, ...]."""
    r = httpx.get(f"{server_url}/api/v1/scene.json", timeout=60)
    r.raise_for_status()
    scene = r.json()
    results: list[dict[str, Any]] = []
    for pid, page in scene["scene"]["pages"].items():
        for nid, n in page.get("nodes", {}).items():
            kind = n.get("kind", {})
            if "text" in kind:
                t = kind["text"]
                src = t.get("text")
                if src and src.strip():
                    results.append({
                        "page_id": pid,
                        "node_id": nid,
                        "page_name": page.get("name", pid[:8]),
                        "text": src,
                    })
    return results


def format_batch(items: list[dict[str, Any]]) -> str:
    """Format text items as [1]...[N] tagged blocks."""
    lines = []
    for i, item in enumerate(items, 1):
        text = item.get("text") or item.get("source_text") or ""
        lines.append(f"[{i}] {text}")
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

    # Read items
    if args.server:
        try:
            items = read_scene_text(args.server)
        except Exception as e:
            print(f"Error: cannot fetch scene from {args.server}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Read {len(items)} text nodes from Koharu", file=sys.stderr)
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

    # Translate in batches
    system_prompt = SYSTEM_PROMPT_TPL.format(
        target_language=target_lang,
        glossary_text=glossary_text,
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
        tagged = format_batch(batch)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": tagged},
        ]

        print(f"  [{start + len(batch)}/{total}] Calling {model}...", file=sys.stderr)
        resp = call_deepseek(api_key, model, base_url, messages)
        translations = parse_response(resp, len(batch))

        for i, item in enumerate(batch):
            trans = translations[i] if i < len(translations) else ""
            source = item.get("text") or item.get("source_text") or ""
            all_translations.append({
                "page_id": item.get("page_id"),
                "node_id": item.get("node_id"),
                "page_name": item.get("page_name", ""),
                "source_text": source,
                "translation": trans,
            })

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
    t.add_argument("--batch-size", type=int, default=50, help="Items per API call")
    t.add_argument("--temperature", type=float, default=0.3)

    args = parser.parse_args()
    cmd_translate(args)


if __name__ == "__main__":
    main()
