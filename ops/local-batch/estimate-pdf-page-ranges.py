from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError:
    from PyPDF2 import PdfReader


def parse_pages(spec: str, total_pages: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[,;]", spec):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Invalid page range: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end > total_pages:
            raise ValueError(f"{part!r} exceeds page count {total_pages}")
        for page in range(start, end + 1):
            if page not in seen:
                pages.append(page)
                seen.add(page)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("ranges", nargs="+")
    args = parser.parse_args()

    reader = PdfReader(str(Path(args.pdf)))
    for item in args.ranges:
        name, spec = item.split("=", 1)
        pages = parse_pages(spec, len(reader.pages))
        text = "\n".join(reader.pages[p - 1].extract_text() or "" for p in pages)
        print(f"{name}\tpages={len(pages)}\ttext_chars={len(text)}\trange={spec}")


if __name__ == "__main__":
    main()
