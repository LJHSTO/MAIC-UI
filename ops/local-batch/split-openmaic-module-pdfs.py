from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    from PyPDF2 import PdfReader, PdfWriter


def parse_page_range(page_range: str, total_pages: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[,;]", page_range):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Invalid page range part: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1 or start > end:
            raise ValueError(f"Invalid page range: {part!r}")
        if end > total_pages:
            raise ValueError(f"Page range {part!r} exceeds PDF page count {total_pages}")
        for page in range(start, end + 1):
            if page not in seen:
                pages.append(page)
                seen.add(page)
    if not pages:
        raise ValueError("Empty page range")
    return pages


def module_letter(title: str) -> str:
    match = re.match(r"\s*([A-E]\d*|B)(?:-|$)", title)
    if not match:
        raise ValueError(f"Cannot infer module letter from title: {title!r}")
    return match.group(1)


def extract_text_chars(reader: PdfReader, pages_1_based: list[int]) -> int:
    text_parts: list[str] = []
    for page_num in pages_1_based:
        text_parts.append(reader.pages[page_num - 1].extract_text() or "")
    return len("\n".join(text_parts))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--courses-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", default="mml-book")
    args = parser.parse_args()

    source_pdf = Path(args.source_pdf)
    courses_csv = Path(args.courses_csv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(source_pdf))
    total_pages = len(reader.pages)

    rows: list[dict[str, str]]
    with courses_csv.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f"No rows in CSV: {courses_csv}")

    print(f"source={source_pdf}")
    print(f"source_pages={total_pages}")
    print(f"csv_rows={len(rows)}")
    print("")

    for row in rows:
        title = (row.get("title") or "").strip()
        page_range = (row.get("page_range") or "").strip()
        letter = module_letter(title)
        pages = parse_page_range(page_range, total_pages)

        writer = PdfWriter()
        for page_num in pages:
            writer.add_page(reader.pages[page_num - 1])

        output_path = output_dir / f"{args.prefix}-{letter}.pdf"
        with output_path.open("wb") as f:
            writer.write(f)

        text_chars = extract_text_chars(reader, pages)
        size = output_path.stat().st_size
        print(
            f"{letter}\tpages={len(pages)}\ttext_chars={text_chars}\tsize={size}\tpath={output_path}"
        )


if __name__ == "__main__":
    main()
