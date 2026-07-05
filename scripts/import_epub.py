"""Extract images from manga EPUB for Koharu import.

Usage:
    python import_epub.py --input book.epub --output ./pages/
    python import_epub.py --input ./dir/ --recursive --output ./pages/

Dependencies: httpx, ebooklib, beautifulsoup4, pillow
"""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    from ebooklib import epub
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: need ebooklib and beautifulsoup4. Run:")
    print("  pip install ebooklib beautifulsoup4")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("Error: need Pillow. Run:")
    print("  pip install pillow")
    sys.exit(1)

SUPPORTED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def extract_from_epub(epub_path: Path, output_dir: Path) -> list[Path]:
    """Extract all images from an EPUB. Returns list of saved image paths."""
    book = epub.read_epub(str(epub_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    page_num = 1

    for item in book.get_items():
        if item.get_type() == 59:  # ITEM_IMAGE
            ext = _guess_ext(item.get_name())
            filename = f"page_{page_num:04d}{ext}"
            out_path = output_dir / filename

            # Basic image validation
            try:
                img = Image.open(item.get_content())
                img.verify()
            except Exception:
                continue

            out_path.write_bytes(item.get_content())
            saved.append(out_path)
            page_num += 1

    return saved


def extract_from_directory(
    dir_path: Path, output_dir: Path, recursive: bool = False
) -> list[Path]:
    """Copy images from a directory, sorting naturally."""
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    images: list[Path] = []
    for f in sorted(dir_path.glob(pattern)):
        if f.suffix.lower() in SUPPORTED_IMG_EXTS and f.is_file():
            images.append(f)

    saved = []
    for i, img_path in enumerate(images, 1):
        ext = img_path.suffix.lower()
        filename = f"page_{i:04d}{ext}"
        out_path = output_dir / filename
        out_path.write_bytes(img_path.read_bytes())
        saved.append(out_path)

    return saved


def _guess_ext(item_name: str) -> str:
    _, ext = os.path.splitext(item_name)
    ext = ext.lower()
    if ext in SUPPORTED_IMG_EXTS:
        return ext
    return ".png"  # default


def _natural_sort_key(path: Path) -> list:
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", str(path.stem))
    ]


def main():
    parser = argparse.ArgumentParser(description="Extract images from EPUB or directory")
    parser.add_argument("--input", "-i", required=True, help="Input EPUB file or directory")
    parser.add_argument("--output", "-o", required=True, help="Output directory for images")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recurse subdirectories")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.is_file() and input_path.suffix.lower() == ".epub":
        saved = extract_from_epub(input_path, output_dir)
    elif input_path.is_dir():
        saved = extract_from_directory(input_path, output_dir, args.recursive)
    else:
        print(f"Error: unsupported input: {input_path}", file=sys.stderr)
        sys.exit(1)

    if saved:
        print(f"Extracted {len(saved)} images to {output_dir.resolve()}")
        for p in saved[:5]:
            print(f"  {p.name}")
        if len(saved) > 5:
            print(f"  ... and {len(saved) - 5} more")
    else:
        print("Warning: no images found", file=sys.stderr)


if __name__ == "__main__":
    main()
