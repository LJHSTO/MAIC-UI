from __future__ import annotations

import argparse
import csv
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError:
    from PyPDF2 import PdfReader


def count_page_images(page) -> int:
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        count = 0
        for obj in xobjects.values():
            resolved = obj.get_object()
            if resolved.get("/Subtype") == "/Image":
                count += 1
        return count
    except Exception:
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", required=True)
    parser.add_argument("--prefix", default="mml-book")
    parser.add_argument("--csv", default="")
    args = parser.parse_args()

    targets: list[tuple[str, Path]] = []
    if args.csv:
        with Path(args.csv).open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                targets.append((row.get("title", ""), Path(row["file"])))
    else:
        pdf_dir = Path(args.pdf_dir)
        for letter in "ABCDE":
            targets.append((letter, pdf_dir / f"{args.prefix}-{letter}.pdf"))

    for label, path in targets:
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        image_count = sum(count_page_images(page) for page in reader.pages)
        print(
            f"{label}\tpages={len(reader.pages)}\ttext_chars={len(text)}"
            f"\tembedded_images={image_count}\tsize={path.stat().st_size}\tpath={path}"
        )


if __name__ == "__main__":
    main()
