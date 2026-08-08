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
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try ebooklib first (respects OPF manifest order).
    try:
        from ebooklib import epub as _epub

        book = _epub.read_epub(str(epub_path))
        items = [it for it in book.get_items() if it.get_type() == 59]  # ITEM_IMAGE
    except Exception:
        items = []

    if items:
        saved = []
        for page_num, item in enumerate(items, 1):
            ext = _guess_ext(item.get_name())
            filename = f"page_{page_num:04d}{ext}"
            out_path = output_dir / filename
            try:
                img = Image.open(item.get_content())
                img.verify()
            except Exception:
                continue
            out_path.write_bytes(item.get_content())
            saved.append(out_path)
        if saved:
            return saved

    # Fallback: walk the raw zip archive and collect image files in path order.
    # Handles EPUBs where ebooklib reports no ITEM_IMAGE (some scan/卷 EPUBs).
    import zipfile

    saved = []
    try:
        with zipfile.ZipFile(epub_path) as zf:
            names = [n for n in zf.namelist() if _guess_ext(n) in SUPPORTED_IMG_EXTS]
            # sort by path segments so dirs like OEBPS/image stay in filename order
            names.sort(key=lambda n: (n.split("/")[-1].zfill(8), n))
            for page_num, name in enumerate(names, 1):
                ext = _guess_ext(name)
                filename = f"page_{page_num:04d}{ext}"
                out_path = output_dir / filename
                try:
                    img = Image.open(zf.open(name))
                    img.verify()
                except Exception:
                    continue
                out_path.write_bytes(zf.read(name))
                saved.append(out_path)
    except Exception:
        pass

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


def find_epub_by_volume(search_dir: Path, volume: str) -> Path | None:
    """Fuzzy-match an EPUB in a directory by volume number.

    Handles half-width/combining kana in filenames by matching the volume
    number as a standalone token (e.g. " 4 " / " 第4巻 " / "vol04").
    """
    target = str(volume).strip().lstrip("0") or "0"
    best: Path | None = None
    for f in sorted(search_dir.glob("*.epub")):
        name = f.name
        # match standalone number token
        if re.search(rf"(?<!\d){re.escape(target)}(?!\d)", name):
            return f
        # match 第N巻 / volN
        if re.search(rf"(?:巻|卷|vol\.?)\s*{re.escape(target)}", name, re.IGNORECASE):
            return f
    return best


def main():
    parser = argparse.ArgumentParser(description="Extract images from EPUB or directory")
    parser.add_argument("--input", "-i", required=True, help="Input EPUB file or directory")
    parser.add_argument("--output", "-o", required=True, help="Output directory for images")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recurse subdirectories")
    parser.add_argument("--volume", "-v", help="Volume number for fuzzy matching inside a directory")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    # fuzzy-match: if input doesn't exist but --volume given, search parent dir
    if not input_path.exists() and args.volume:
        search_dir = input_path if input_path.is_dir() else input_path.parent
        if not search_dir.exists():
            search_dir = input_path
        matched = find_epub_by_volume(search_dir, args.volume)
        if matched:
            print(f"fuzzy match: {matched.name}")
            input_path = matched

    if not input_path.exists():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.is_file() and input_path.suffix.lower() == ".epub":
        saved = extract_from_epub(input_path, output_dir)
    elif input_path.is_dir():
        # if --volume given and dir has epubs, extract that one; else extract all
        if args.volume:
            epub = find_epub_by_volume(input_path, args.volume)
            if epub:
                saved = extract_from_epub(epub, output_dir)
            else:
                print(f"Error: no EPUB matching volume {args.volume} in {input_path}", file=sys.stderr)
                sys.exit(1)
        else:
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
