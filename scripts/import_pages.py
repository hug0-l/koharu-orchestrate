"""Batch import images into Koharu via HTTP API.

Usage:
    python import_pages.py --server http://localhost:4000 --dir ./pages/
    python import_pages.py --server http://localhost:4000 --dir ./pages/ --replace
    python import_pages.py --server http://localhost:4000 --files page1.png page2.png
"""

import argparse
import re
import sys
from pathlib import Path

from koharu_api import KoharuAPI

SUPPORTED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".avif"}


def _natural_sort_key(path: Path) -> list:
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", path.stem)
    ]


def collect_images(dir_path: Path, recursive: bool = False) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    images: list[Path] = []
    for f in sorted(dir_path.glob(pattern), key=_natural_sort_key):
        if f.suffix.lower() in SUPPORTED_IMG_EXTS and f.is_file():
            images.append(f)
    return images


def main():
    parser = argparse.ArgumentParser(description="Import images into Koharu")
    parser.add_argument("--server", default="http://localhost:4000", help="Koharu server URL")
    parser.add_argument("--dir", type=Path, help="Directory containing images")
    parser.add_argument("--files", nargs="+", type=Path, help="Specific image files")
    parser.add_argument("--replace", action="store_true", help="Replace existing pages")
    parser.add_argument("--page-size", type=int, default=10,
                        help="Images per API call (default: 10)")
    args = parser.parse_args()

    if args.dir:
        images = collect_images(args.dir)
    elif args.files:
        images = [f for f in args.files if f.suffix.lower() in SUPPORTED_IMG_EXTS]
    else:
        print("Error: specify --dir or --files", file=sys.stderr)
        sys.exit(1)

    if not images:
        print("Error: no supported images found", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(images)} images")
    api = KoharuAPI(args.server)

    # Import in batches
    page_ids = []
    for i in range(0, len(images), args.page_size):
        batch = images[i : i + args.page_size]
        replace = args.replace and i == 0  # only replace on first batch
        ids = api.import_pages(batch, replace=replace)
        page_ids.extend(ids)
        print(f"  Imported {len(batch)} images ({i + len(batch)}/{len(images)})")

    if page_ids:
        print(f"\nSuccessfully imported {len(page_ids)} pages into Koharu")
        print(f"First page ID: {page_ids[0]}")
    else:
        print("Warning: no pages imported", file=sys.stderr)


if __name__ == "__main__":
    main()
