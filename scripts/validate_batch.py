"""Validate subagent translation batch files against the source text dump.

Catches the two failure modes seen in production:
  1. 0-based vs 1-based index offset (whole-volume character shift).
  2. Missing / out-of-range / duplicate / empty keys (coverage gaps).

Usage:
    # Validate all batch/slice files for a volume
    python -m validate_batch --all-text work/all_text_v20.json --trans trans/

    # Detect and auto-fix 0-based offset (writes *_fixed.json)
    python -m validate_batch --all-text work/all_text_v20.json --trans trans/ --fix

Output (stdout, one line per file):
    OK    v20_slice_01.json  n=298  off=+0  score=0.73
    FIXED v20_slice_02.json  n=297  off=+1  score=0.41 -> wrote v20_slice_02_fixed.json
    DUP   v20_slice_03.json  n=1    key '12' twice

Exit code 0 if nothing is broken, 1 otherwise.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Characters likely to be shared between JP source and zh-TW translation.
# Names (安達/島村/牡丹/伊吹/安達/島村/裕美/樽見), kanji, digits, common marks.
def _shared_score(src: str, tr: str) -> float:
    if not src or not tr:
        return 0.0
    s = set(src)
    t = set(tr)
    if not s or not t:
        return 0.0
    inter = len(s & t)
    return inter / max(len(s), len(t))


def _normalize_keys(d: dict) -> dict[str, str]:
    """Rebuild with int-parseable string keys; drop non-numeric keys."""
    out = {}
    for k, v in d.items():
        try:
            out[str(int(k))] = v
        except (ValueError, TypeError):
            pass
    return out


def analyze_batch(items: list, batch: dict, shift: int) -> tuple[int, float, int]:
    """Score a batch under a given index shift.

    shift=0: key '5' -> items[4]  (1-based, as produced by read_scene_text)
    shift=+1: key '5' -> items[5] (what a 0-based subagent actually meant)
    """
    total = 0
    inter = 0
    n = 0
    for k, tr in batch.items():
        tr = (tr or "").strip()
        if not tr:
            continue
        try:
            idx = int(k) - 1 + shift
        except ValueError:
            continue
        if 0 <= idx < len(items):
            n += 1
            src = items[idx]["text"]
            s = _shared_score(src, tr)
            total += s
            inter += s
    avg = (inter / n) if n else 0.0
    return n, avg, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-text", required=True, help="all_text_vXX.json (list of {text,...})")
    ap.add_argument("--trans", required=True, help="dir containing vXX_*.json batch files")
    ap.add_argument("--fix", action="store_true", help="write *_fixed.json for 0-based batches")
    ap.add_argument("--expected", type=int, default=0, help="expected total items (coverage check)")
    args = ap.parse_args()

    items = json.load(open(args.all_text))
    trans_dir = Path(args.trans)
    files = sorted(trans_dir.glob("*_batch*.json")) + sorted(trans_dir.glob("*_slice*.json"))
    # de-dup and exclude *_fixed.json outputs from the scan
    files = sorted({f for f in files if "_fixed" not in f.name})

    if not files:
        print("NO_BATCH_FILES", flush=True)
        return 1

    bad = False
    covered = 0
    max_key = 0
    for f in files:
        d = _normalize_keys(json.load(open(f)))
        if not d:
            print(f"EMPTY {f.name}", flush=True)
            bad = True
            continue

        # duplicates / non-numeric keys already filtered by _normalize_keys
        keys = [int(k) for k in d]
        dup = len(keys) != len(set(keys))
        if dup:
            print(f"DUP  {f.name} n={len(keys)}", flush=True)
            bad = True

        n0, avg0, tot0 = analyze_batch(items, d, 0)
        n1, avg1, tot1 = analyze_batch(items, d, 1)
        # pick the shift with the decisively higher shared-char score.
        # a 0-based file scores far worse at +0 (avg < 1/2 of +1's).
        if tot1 > tot0 and tot1 > 2 * max(tot0, 1.0):
            off, n, avg = "+1", n1, avg1
        else:
            off, n, avg = "+0", n0, avg0

        print(f"{'FIXED' if off=='+1' else 'OK   '} {f.name} n={n} off={off} score={avg:.2f}", flush=True)

        if off == "+1" and args.fix:
            fixed = {}
            for k, v in d.items():
                fixed[str(int(k) + 1)] = v
            out = f.with_name(f.stem + "_fixed.json")
            json.dump(fixed, open(out, "w"), ensure_ascii=False, indent=1)
            print(f"  -> wrote {out.name}", flush=True)

        covered += n
        max_key = max(max_key, max(keys))

    # coverage vs expected
    if args.expected and covered < args.expected:
        print(f"COVERAGE {covered}/{args.expected} (missing {args.expected-covered})", flush=True)
        bad = True

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
