from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def module_letter(title: str) -> str:
    match = re.match(r"\s*([A-E]\d*|B)(?:-|$)", title)
    if not match:
        raise ValueError(f"Cannot infer module letter from title: {title!r}")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--pdf-dir", required=True)
    parser.add_argument("--prefix", default="mml-book")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    pdf_dir = Path(args.pdf_dir)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    for row in rows:
        letter = module_letter(row.get("title", ""))
        module_pdf = pdf_dir / f"{args.prefix}-{letter}.pdf"
        if not module_pdf.exists():
            raise FileNotFoundError(module_pdf)
        row["file"] = str(module_pdf)
        row["page_range"] = ""

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"updated_rows={len(rows)}")
    print(f"csv={csv_path}")


if __name__ == "__main__":
    main()
