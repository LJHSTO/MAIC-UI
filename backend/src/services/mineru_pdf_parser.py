from __future__ import annotations

import asyncio
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


DEFAULT_OPENMAIC_ENV_PATH = r"D:\Projects\OpenMAIC\.env.local"
DEFAULT_MINERU_CLOUD_BASE_URL = "https://mineru.net/api/v4"


@dataclass
class MinerUParseResult:
    markdown: str
    page_count: int
    image_count: int
    batch_id: str
    elapsed_seconds: float


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_mineru_enhancement_enabled() -> bool:
    return _env_flag("MAIC_UI_MINERU_ENHANCED", True)


def _read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _mineru_cloud_config() -> Optional[Dict[str, str]]:
    env_path = Path(os.getenv("OPENMAIC_ENV_PATH", DEFAULT_OPENMAIC_ENV_PATH))
    openmaic_env = _read_env_file(env_path)

    api_key = (
        os.getenv("PDF_MINERU_CLOUD_API_KEY")
        or os.getenv("MINERU_CLOUD_API_KEY")
        or openmaic_env.get("PDF_MINERU_CLOUD_API_KEY")
        or openmaic_env.get("MINERU_CLOUD_API_KEY")
    )
    if not api_key:
        return None

    base_url = (
        os.getenv("PDF_MINERU_CLOUD_BASE_URL")
        or os.getenv("MINERU_CLOUD_BASE_URL")
        or openmaic_env.get("PDF_MINERU_CLOUD_BASE_URL")
        or openmaic_env.get("MINERU_CLOUD_BASE_URL")
        or DEFAULT_MINERU_CLOUD_BASE_URL
    )

    return {"api_key": api_key, "base_url": base_url.rstrip("/")}


def _sanitize_pdf_name(pdf_path: str) -> str:
    name = Path(pdf_path).name or "document.pdf"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:180]
    return name if name.lower().endswith(".pdf") else "document.pdf"


async def _read_json_response(response: httpx.Response, context: str) -> Any:
    text = response.text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MinerU Cloud {context}: invalid JSON: {text[:300]}") from exc

    if response.status_code >= 400:
        message = payload.get("msg") if isinstance(payload, dict) else text[:300]
        raise RuntimeError(f"MinerU Cloud {context}: HTTP {response.status_code}: {message}")

    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise RuntimeError(f"MinerU Cloud {context}: {payload.get('msg') or 'unknown error'}")

    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def _count_pages_from_content_list(content_list: Any) -> int:
    if not isinstance(content_list, list):
        return 0
    pages = {item.get("page_idx") for item in content_list if isinstance(item, dict)}
    return len({page for page in pages if page is not None})


def _count_images(content_list: Any, zip_file: zipfile.ZipFile) -> int:
    if isinstance(content_list, list):
        count = sum(
            1
            for item in content_list
            if isinstance(item, dict) and item.get("type") == "image"
        )
        if count:
            return count
    return sum(1 for name in zip_file.namelist() if re.search(r"\.(png|jpe?g|webp|gif)$", name, re.I))


async def parse_pdf_with_mineru_cloud(pdf_path: str) -> MinerUParseResult:
    config = _mineru_cloud_config()
    if not config:
        raise RuntimeError("MinerU Cloud is not configured")

    pdf_bytes = Path(pdf_path).read_bytes()
    upload_name = _sanitize_pdf_name(pdf_path)
    started = time.time()
    headers = {"Authorization": f"Bearer {config['api_key']}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        create_response = await client.post(
            f"{config['base_url']}/file-urls/batch",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "files": [{"name": upload_name}],
                "enable_formula": True,
                "enable_table": True,
                "model_version": "vlm",
                "language": "ch",
            },
        )
        batch_data = await _read_json_response(create_response, "create batch")
        batch_id = batch_data.get("batch_id") if isinstance(batch_data, dict) else None
        upload_urls = (batch_data or {}).get("file_urls") or (batch_data or {}).get("files")
        if not batch_id or not upload_urls:
            raise RuntimeError("MinerU Cloud create batch response is missing batch_id or upload URL")

        put_response = await client.put(upload_urls[0], content=pdf_bytes)
        if put_response.status_code >= 400:
            raise RuntimeError(
                f"MinerU Cloud upload failed: HTTP {put_response.status_code}: {put_response.text[:300]}"
            )

        await asyncio.sleep(1.5)
        deadline = time.time() + float(os.getenv("MAIC_UI_MINERU_POLL_TIMEOUT_SECONDS", "900"))
        full_zip_url = ""

        while time.time() < deadline:
            poll_response = await client.get(
                f"{config['base_url']}/extract-results/batch/{batch_id}",
                headers={**headers, "Accept": "application/json"},
            )
            status_data = await _read_json_response(poll_response, "poll batch")
            rows = (status_data or {}).get("extract_result") if isinstance(status_data, dict) else None
            if isinstance(rows, dict):
                rows = [rows]
            rows = rows if isinstance(rows, list) else []
            row = next((item for item in rows if item.get("file_name") == upload_name), rows[0] if rows else {})
            state = row.get("state")
            if state == "failed":
                raise RuntimeError(f"MinerU Cloud parsing failed: {row.get('err_msg') or 'unknown error'}")
            if state == "done" and row.get("full_zip_url"):
                full_zip_url = row["full_zip_url"]
                break
            await asyncio.sleep(2.5)

        if not full_zip_url:
            raise RuntimeError(f"MinerU Cloud timed out while polling batch {batch_id}")

        zip_response = await client.get(full_zip_url)
        if zip_response.status_code >= 400:
            raise RuntimeError(f"MinerU Cloud ZIP download failed: HTTP {zip_response.status_code}")

    import io

    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        full_md_name = next((name for name in names if name.lower().endswith("full.md")), "")
        if not full_md_name:
            raise RuntimeError("MinerU Cloud ZIP does not contain full.md")
        markdown = zf.read(full_md_name).decode("utf-8", errors="replace")

        content_list_name = next(
            (
                name
                for name in names
                if name.lower().endswith("content_list.json")
                or name.lower().endswith("_content_list.json")
            ),
            "",
        )
        content_list: Any = None
        if content_list_name:
            try:
                content_list = json.loads(zf.read(content_list_name).decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                content_list = None

        return MinerUParseResult(
            markdown=markdown,
            page_count=_count_pages_from_content_list(content_list),
            image_count=_count_images(content_list, zf),
            batch_id=batch_id,
            elapsed_seconds=round(time.time() - started, 2),
        )


def _context_from_markdown(markdown: str, existing_context: Dict[str, Any]) -> Dict[str, Any]:
    max_chars = int(existing_context.get("max_chars") or 80000)
    cleaned = re.sub(r"\n{3,}", "\n\n", markdown or "").strip()
    excerpt = cleaned[:max_chars].rstrip()
    if len(cleaned) > max_chars:
        excerpt += "\n...[context truncated]"
    return {
        **existing_context,
        "source": "mineru-cloud",
        "total_chars": len(cleaned),
        "included_chars": len(excerpt),
        "included_pages": existing_context.get("included_pages", 0),
        "selection_strategy": "mineru_markdown_truncated",
        "excerpt": excerpt,
    }


async def enhance_pdf_text_context_with_mineru(
    pdf_path: str,
    existing_context: Dict[str, Any],
    logger: Any = None,
) -> Dict[str, Any]:
    if not is_mineru_enhancement_enabled():
        return {
            **existing_context,
            "mineru_enhancement": {"enabled": False, "used": False},
        }
    if not _mineru_cloud_config():
        return {
            **existing_context,
            "mineru_enhancement": {"enabled": True, "used": False, "reason": "not_configured"},
        }

    try:
        result = await parse_pdf_with_mineru_cloud(pdf_path)
        if not result.markdown.strip():
            return {
                **existing_context,
                "mineru_enhancement": {"enabled": True, "used": False, "reason": "empty_markdown"},
            }
        enhanced = _context_from_markdown(result.markdown, existing_context)
        enhanced["mineru_enhancement"] = {
            "enabled": True,
            "used": True,
            "page_count": result.page_count,
            "image_count": result.image_count,
            "markdown_chars": len(result.markdown),
            "elapsed_seconds": result.elapsed_seconds,
            "batch_id": result.batch_id,
        }
        return enhanced
    except Exception as exc:
        if logger:
            logger.warning("MinerU enhancement failed; falling back to local PDF text: %s", exc)
        return {
            **existing_context,
            "mineru_enhancement": {
                "enabled": True,
                "used": False,
                "error": str(exc)[:1000],
            },
        }
