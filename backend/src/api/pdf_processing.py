from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Form, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List, Callable, Awaitable, Tuple
import os
import uuid
import json
import time
import logging
import re
from pathlib import Path

from ..core.database import get_db, SessionLocal
from ..models.document import Document
from ..models.user import User
from ..services.ai_processor import (
    AIProcessor,
    get_pdf_image_conversion_page_limit,
    get_innospark_api_key,
    get_innospark_base_url,
    is_innospark_model,
    resolve_innospark_model,
)
from ..services.generation_metadata import build_generation_metadata, get_generation_metadata
from ..core.security import get_current_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

MIN_GENERATED_HTML_BYTES = 8 * 1024
MIN_GENERATED_HTML_CONTROLS = 3

# Model to provider mapping
CHINESE_MODELS = ["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
                  "glm-4.7", "glm-4.6", "glm-4.6v", "glm-5", "glm-5.1",
                  "gpt-5", "gpt-5.4-pro", "gpt-5.4", "gpt-5.5",
                  "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2",
                  "gemini-3.1-pro", "gemini-3.1-pro-preview",
                  "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash",
                  "doubao-seed-2-0-pro-260215", "doubao-seed-2-0-code-preview-260215",
                  "kimi-k2.6",
                  "minimax-m2.5", "qwen3.6-27b", "qwen3.6-35b-a3b", "Qwen3.6-35B-inno"]
ZHIPU_MODELS = {"glm-4.7", "glm-4.6", "glm-4.6v", "glm-5", "glm-5.1"}
TRANSFER_MODELS = {
    "minimax-m2.5",
    "qwen3.6-27b",
    "qwen3.6-35b-a3b",
}


def get_provider_for_model(model: str) -> str:
    """
    Determine the provider type based on the model name.

    Args:
        model: Model name

    Returns:
        Provider type: "chinese" or "english"
    """
    if model in CHINESE_MODELS:
        return "chinese"
    return "english"


# Initialize AI processor with configurable provider
def get_ai_processor(generation_mode: str = "fast", ai_model: Optional[str] = None):
    """
    Get AI processor instance based on configuration.

    Args:
        generation_mode: HTML generation mode ("fast" or "heavy")
        ai_model: Specific model to use. If provided, provider is inferred from model name.

    Returns:
        AIProcessor instance
    """
    # If a specific model is provided, infer provider from model
    if ai_model:
        provider_type = get_provider_for_model(ai_model)
        logger.info(f"Using model {ai_model} with inferred provider: {provider_type}")
    else:
        provider_type = os.getenv('AI_PROVIDER', 'chinese').lower()

    # Set model and API key based on provider type
    if provider_type in ['english', 'gemini', 'openai']:
        if provider_type == 'gemini':
            model = ai_model or os.getenv('GEMINI_MODEL', 'gemini-3-pro-image-preview')
            api_key = os.getenv('GEMINI_API_KEY') or os.getenv('ENGLISH_API_KEY') or os.getenv('TRANSFER_API_KEY') or os.getenv('MIDDLE_TRANSFER_API_KEY')
        elif provider_type == 'openai':
            model = ai_model or os.getenv('OPENAI_MODEL', 'gpt-4.1')
            api_key = os.getenv('OPENAI_API_KEY') or os.getenv('ENGLISH_API_KEY') or os.getenv('TRANSFER_API_KEY') or os.getenv('MIDDLE_TRANSFER_API_KEY')
        else:  # english (default)
            model = ai_model or os.getenv('ENGLISH_MODEL', 'gpt-4.1')
            api_key = os.getenv('ENGLISH_API_KEY') or os.getenv('TRANSFER_API_KEY') or os.getenv('MIDDLE_TRANSFER_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('GEMINI_API_KEY')

        provider_config = {
            'provider': 'english',  # Always use unified English provider
            'model': model,
            'api_key': api_key
        }
    elif provider_type == 'chinese':
        # Unified Chinese provider - auto-detect backend from model name
        # For vision tasks, prefer glm-4.6v; for text generation, use specified model
        if ai_model and is_innospark_model(ai_model):
            model = resolve_innospark_model(ai_model)
            api_key = get_innospark_api_key()
            base_url = get_innospark_base_url()
        elif ai_model and ai_model.startswith('claude-'):
            # Anthropic model via transfer station
            model = ai_model
            api_key = os.getenv('TRANSFER_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
            transfer_url = os.getenv('TRANSFER_BASE_URL', '')
            base_url = transfer_url.replace('/v1', '') if transfer_url else os.getenv('ANTHROPIC_BASE_URL')
        elif ai_model and ai_model in TRANSFER_MODELS:
            # OpenAI-compatible model via transfer station
            model = ai_model
            api_key = os.getenv('TRANSFER_API_KEY') or os.getenv('OPENAI_API_KEY')
            base_url = os.getenv('TRANSFER_BASE_URL')
        elif ai_model and ai_model in ZHIPU_MODELS:
            model = ai_model
            api_key = os.getenv('ZHIPU_API_KEY') or os.getenv('TRANSFER_API_KEY')
            base_url = None
        else:
            # Default: use transfer station for all models
            model = ai_model or os.getenv('TRANSFER_MODEL', 'glm-4.7')
            api_key = os.getenv('TRANSFER_API_KEY') or os.getenv('ZHIPU_API_KEY')
            base_url = None

        text_model = ai_model if ai_model in CHINESE_MODELS else os.getenv('ZHIPU_MODEL', 'glm-4.7')
        provider_config = {
            'provider': 'chinese',
            'model': model,
            'api_key': api_key,
            'base_url': base_url,
            'text_model': text_model
        }
    else:
        raise ValueError(f"Unsupported provider: {provider_type}. Supported providers: english, chinese")

    return AIProcessor(provider_config=provider_config, generation_mode=generation_mode)


def _clean_pdf_concept_data(raw_concept_data: Optional[Dict[str, Any]], subject: Optional[str] = None) -> Dict[str, str]:
    """Normalize optional concept-focus fields submitted with a PDF upload."""
    if not isinstance(raw_concept_data, dict):
        raw_concept_data = {}

    concept_data = {
        "subject": raw_concept_data.get("subject") or subject or "",
        "lesson_id": raw_concept_data.get("lesson_id") or "",
        "source_chapter": raw_concept_data.get("source_chapter") or "",
        "source_section": raw_concept_data.get("source_section") or "",
        "page_range": raw_concept_data.get("page_range") or "",
        "concept_name": raw_concept_data.get("concept_name") or "",
        "concept_overview": raw_concept_data.get("concept_overview") or "",
        "mastery_points": raw_concept_data.get("mastery_points") or "",
        "design_idea": raw_concept_data.get("design_idea") or "",
        "assessment_focus": raw_concept_data.get("assessment_focus") or "",
        "tracking_plan": raw_concept_data.get("tracking_plan") or "",
    }

    return {
        key: str(value).strip()
        for key, value in concept_data.items()
        if value is not None and str(value).strip()
    }


def _build_pdf_concept_instruction(concept_data: Dict[str, str]) -> str:
    if not concept_data:
        return ""

    lines = [
        "PDF + 知识点聚焦生成要求：",
        "请以 PDF 内容为事实依据，但围绕以下教师自定义知识点目标组织课程、例子、互动和练习。"
    ]
    field_labels = {
        "subject": "科目",
        "lesson_id": "课程序号",
        "source_chapter": "来源章节",
        "source_section": "来源小节",
        "page_range": "原始 PDF 页码范围",
        "concept_name": "知识点",
        "concept_overview": "知识点概述",
        "mastery_points": "学生掌握要点",
        "design_idea": "教学设计思路",
        "assessment_focus": "追踪评估重点",
        "tracking_plan": "学习行为追踪计划",
    }

    for key, label in field_labels.items():
        value = concept_data.get(key)
        if value:
            lines.append(f"- {label}: {value}")

    lines.append("如果 PDF 中存在多个主题，请优先选择与该知识点最相关的章节和内容。")
    return "\n".join(lines)


def _apply_pdf_concept_preferences(
    user_prefs: Dict[str, Any],
    subject: Optional[str],
    description: Optional[str]
) -> Dict[str, Any]:
    """Attach PDF+concept focus settings to user preferences used by the AI pipeline."""
    concept_data = _clean_pdf_concept_data(user_prefs.get("concept_data"), subject)
    if not concept_data:
        return user_prefs

    concept_instruction = _build_pdf_concept_instruction(concept_data)
    existing_description = (description or user_prefs.get("description") or "").strip()
    user_prefs["concept_data"] = concept_data
    user_prefs["pdf_generation_mode"] = "pdf_with_concept_focus"
    user_prefs["pdf_concept_instruction"] = concept_instruction
    user_prefs["description"] = (
        f"{existing_description}\n\n{concept_instruction}".strip()
        if existing_description
        else concept_instruction
    )
    user_prefs["learning_goal"] = user_prefs["description"]
    return user_prefs


def _get_generation_fallback_reason(result: Dict[str, Any]) -> Optional[str]:
    """Return the fallback reason when a generation result used fallback output."""
    if not isinstance(result, dict):
        return None

    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    processing_info = result.get("processing_info") if isinstance(result.get("processing_info"), dict) else {}
    website = result.get("website") if isinstance(result.get("website"), dict) else {}
    website_metadata = website.get("metadata") if isinstance(website.get("metadata"), dict) else {}
    generation_info = website.get("generation_info") if isinstance(website.get("generation_info"), dict) else {}

    fallback_sources = (metadata, processing_info, website, website_metadata, generation_info)
    if not any(source.get("fallback_used") for source in fallback_sources):
        return None

    for source in fallback_sources:
        for key in ("fallback_reason", "upstream_error", "error"):
            value = source.get(key)
            if value:
                return str(value)[:1000]

    return "generation returned fallback output"


def _extract_generated_html(result: Dict[str, Any]) -> str:
    """Extract generated HTML from either normal or template generation results."""
    if not isinstance(result, dict):
        return ""

    website = result.get("website")
    if isinstance(website, dict) and isinstance(website.get("html"), str):
        return website["html"]

    html = result.get("html")
    return html if isinstance(html, str) else ""


def _count_generated_html_controls(html: str) -> int:
    """Count visible learner controls likely to support active interaction."""
    control_patterns = (
        r"<\s*(?:button|input|select|textarea)\b",
        r"\brole\s*=\s*['\"]button['\"]",
    )
    return sum(len(re.findall(pattern, html, flags=re.IGNORECASE)) for pattern in control_patterns)


def _validate_generated_html(html: str) -> List[str]:
    """Validate that generated HTML is a usable active-learning page before marking ready."""
    issues: List[str] = []

    if not isinstance(html, str) or not html.strip():
        return ["empty generated HTML"]

    html_size = len(html.encode("utf-8"))
    if html_size < MIN_GENERATED_HTML_BYTES:
        issues.append(f"HTML too small: {html_size} bytes < {MIN_GENERATED_HTML_BYTES} bytes")

    if "trackEvent" not in html:
        issues.append("missing trackEvent learning-event logger")

    if re.search(r"<\s*canvas\b", html, flags=re.IGNORECASE) is None:
        issues.append("missing canvas element")

    control_count = _count_generated_html_controls(html)
    if control_count < MIN_GENERATED_HTML_CONTROLS:
        issues.append(f"too few learner controls: {control_count} < {MIN_GENERATED_HTML_CONTROLS}")

    return issues


def _build_html_quality_payload(
    passed: bool,
    issues: List[str],
    attempts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "passed": passed,
        "issues": issues,
        "attempts": attempts,
        "rules": {
            "min_html_bytes": MIN_GENERATED_HTML_BYTES,
            "requires_track_event": True,
            "requires_canvas": True,
            "min_controls": MIN_GENERATED_HTML_CONTROLS,
        },
    }


async def _generate_with_html_quality_retry(
    generate_once: Callable[[], Awaitable[Dict[str, Any]]],
    workflow_label: str,
    max_retries: int = 1,
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    """Run generation and retry once when final HTML fails quality validation."""
    attempts: List[Dict[str, Any]] = []
    last_result: Dict[str, Any] = {}
    last_issues: List[str] = []

    for attempt_index in range(max_retries + 1):
        last_result = await generate_once()
        if not isinstance(last_result, dict):
            last_result = {
                "status": "error",
                "error": "generation returned non-dict result",
            }

        if last_result.get("status") == "error":
            attempts.append({
                "attempt": attempt_index + 1,
                "status": "error",
                "error": str(last_result.get("error", ""))[:1000],
            })
            return last_result, [], attempts

        html = _extract_generated_html(last_result)
        last_issues = _validate_generated_html(html)
        fallback_reason = _get_generation_fallback_reason(last_result)
        attempts.append({
            "attempt": attempt_index + 1,
            "status": "failed_quality" if last_issues else "passed",
            "html_bytes": len(html.encode("utf-8")) if isinstance(html, str) else 0,
            "control_count": _count_generated_html_controls(html) if isinstance(html, str) else 0,
            "issues": last_issues,
            "fallback_used": bool(fallback_reason),
            "fallback_reason": fallback_reason,
        })

        if not last_issues:
            return last_result, [], attempts

        if attempt_index < max_retries:
            logger.warning(
                "%s generated invalid HTML on attempt %s/%s; retrying. Issues: %s",
                workflow_label,
                attempt_index + 1,
                max_retries + 1,
                "; ".join(last_issues),
            )

    logger.error("%s failed HTML quality validation after retry: %s", workflow_label, "; ".join(last_issues))
    return last_result, last_issues, attempts


# Ensure uploads directory exists
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

async def process_pdf_background(
    document_id: int,
    file_path: str,
    user_prefs: Dict,
    db: Session,
    generation_mode: str = "fast"
):
    """
    Background task to process PDF asynchronously.
    This runs in the background and doesn't block the API endpoint.

    Args:
        document_id: Database ID of the document
        file_path: Path to the PDF file
        user_prefs: User preferences dict
        db: Database session
        generation_mode: HTML generation mode ("fast" or "heavy")
    """
    logger.info(f"🔄 Starting background PDF processing for document {document_id}")
    start_time = time.time()

    # Create a new database session for this background task
    from ..core.database import SessionLocal
    background_db = SessionLocal()

    try:
        # Get the document
        document = background_db.query(Document).filter(Document.id == document_id).first()
        if not document:
            logger.error(f"❌ Document {document_id} not found for background processing")
            return

        # Get AI model from user preferences
        ai_model = user_prefs.get("ai_model")

        # Get AI processor instance with the selected model
        ai_processor = get_ai_processor(generation_mode=generation_mode, ai_model=ai_model)
        logger.info(f"🤖 AI processor initialized for background processing - Provider: {ai_processor.get_provider_name()}, Mode: {generation_mode}, Model: {ai_model}")

        # Process PDF with configured AI provider
        logger.info(f"🔄 Starting AI processing pipeline in background...")
        ai_processing_start = time.time()
        async def generate_pdf_once() -> Dict[str, Any]:
            return await ai_processor.process_pdf_complete(
                str(file_path),
                user_preferences=user_prefs
            )

        result, html_quality_issues, html_quality_attempts = await _generate_with_html_quality_retry(
            generate_pdf_once,
            workflow_label=f"PDF document {document_id}",
            max_retries=1,
        )
        ai_processing_time = time.time() - ai_processing_start
        logger.info(f"✅ Background AI processing completed in {ai_processing_time:.2f}s")

        if result.get("status") == "error":
            logger.error("Background processing failed: %s", result.get("error"))
            document.status = "error"
            document.error_message = result.get("error", "Generation failed")
        else:
            fallback_reason = _get_generation_fallback_reason(result)
            if html_quality_issues:
                error_message = (
                    "Generated HTML failed quality validation after retry; "
                    f"issues: {'; '.join(html_quality_issues)}"
                )
                logger.error(f"Background PDF processing failed HTML validation: {error_message}")
                document.status = "error"
                document.error_message = error_message
                document.page_count = result.get("metadata", {}).get("page_count", 0)
                document.pdf_metadata = result.get("metadata", {})
                document.processing_results = {
                    "analysis": result.get("analysis", {}),
                    "knowledge_cards": result.get("knowledge_cards", {}),
                    "failed_website": _extract_generated_html(result),
                    "processing_info": result.get("processing_info", {}),
                    "generation_mode": result.get("website", {}).get("mode_used", generation_mode),
                    "html_quality_validation": _build_html_quality_payload(
                        False,
                        html_quality_issues,
                        html_quality_attempts,
                    ),
                    "fallback_used": bool(fallback_reason),
                    "fallback_reason": fallback_reason,
                }
                background_db.commit()
                total_time = time.time() - start_time
                logger.info(f"Background PDF processing failed due to invalid HTML for document {document_id} in {total_time:.2f}s total")
                return

            website_result = result.get("website", {})
            generation_metadata = build_generation_metadata(
                ai_processor,
                requested_model=ai_model,
                generation_mode=website_result.get("mode_used", generation_mode),
                workflow_type="pdf",
                generation_method="ai"
            )
            concept_data = _clean_pdf_concept_data(user_prefs.get("concept_data"), document.subject)
            # Update document with processing results
            document.status = "ready"
            document.page_count = result.get("metadata", {}).get("page_count", 0)
            document.pdf_metadata = result.get("metadata", {})
            document.processing_results = {
                "analysis": result.get("analysis", {}),
                "knowledge_cards": result.get("knowledge_cards", {}),
                "website": website_result.get("html", ""),
                "interactive_elements": website_result.get("interactive_elements", []),
                "processing_info": result.get("processing_info", {}),
                "generation_mode": website_result.get("mode_used", generation_mode),
                "concept_data": concept_data or None,
                "generation_metadata": generation_metadata,
                "ai_model": generation_metadata.get("model"),
                "ai_provider": generation_metadata.get("provider"),
                "html_quality_validation": _build_html_quality_payload(
                    True,
                    [],
                    html_quality_attempts,
                ),
                "fallback_used": bool(fallback_reason),
                "fallback_reason": fallback_reason,
            }
            logger.info(f"✅ Document {document_id} marked as ready with generation_mode: {website_result.get('mode_used', generation_mode)}")
            logger.info(f"✅ Document {document_id} marked as ready")

        background_db.commit()

        total_time = time.time() - start_time
        logger.info(f"🎉 Background PDF processing completed for document {document_id} in {total_time:.2f}s total")

    except Exception as processing_error:
        logger.error(f"❌ Background processing error for document {document_id}: {str(processing_error)}")
        # Update document status to error
        try:
            document = background_db.query(Document).filter(Document.id == document_id).first()
            if document:
                document.status = "error"
                document.error_message = str(processing_error)
                background_db.commit()
        except Exception as db_error:
            logger.error(f"❌ Failed to update error status: {str(db_error)}")

    finally:
        # Always close the database session
        background_db.close()

async def process_concept_background(
    document_id: int,
    concept_data: Dict,
    user_prefs: Dict,
    db: Session
):
    """
    Background task to process concept asynchronously.
    This runs in the background and doesn't block the API endpoint.
    """
    logger.info(f"🔄 Starting background concept processing for document {document_id}")
    start_time = time.time()

    # Create a new database session for this background task
    from ..core.database import SessionLocal
    background_db = SessionLocal()

    try:
        # Get the document
        document = background_db.query(Document).filter(Document.id == document_id).first()
        if not document:
            logger.error(f"❌ Document {document_id} not found for background concept processing")
            return

        # Get AI model from user preferences
        ai_model = user_prefs.get("ai_model")

        # Get AI processor instance with the selected model
        ai_processor = get_ai_processor(ai_model=ai_model)
        logger.info(f"🤖 AI processor initialized for background concept processing - Provider: {ai_processor.get_provider_name()}, Model: {ai_model}")

        # Process concept with configured AI provider
        logger.info(f"🔄 Starting AI concept processing in background...")
        ai_processing_start = time.time()
        async def generate_concept_once() -> Dict[str, Any]:
            return await ai_processor.process_concept_complete(
                concept_data=concept_data,
                user_preferences=user_prefs
            )

        result, html_quality_issues, html_quality_attempts = await _generate_with_html_quality_retry(
            generate_concept_once,
            workflow_label=f"Concept document {document_id}",
            max_retries=1,
        )
        ai_processing_time = time.time() - ai_processing_start
        logger.info(f"✅ Background AI concept processing completed in {ai_processing_time:.2f}s")

        if result.get("status") == "error":
            logger.error("Background concept processing failed: %s", result.get("error"))
            document.status = "error"
            document.error_message = result.get("error", "Generation failed")
        else:
            fallback_reason = _get_generation_fallback_reason(result)
            if html_quality_issues:
                error_message = (
                    "Generated HTML failed quality validation after retry; "
                    f"issues: {'; '.join(html_quality_issues)}"
                )
                logger.error(f"Background concept processing failed HTML validation: {error_message}")
                document.status = "error"
                document.error_message = error_message
                document.page_count = result.get("metadata", {}).get("page_count", 1)
                document.pdf_metadata = result.get("metadata", {})
                document.processing_results = {
                    "analysis": result.get("analysis", {}),
                    "failed_website": _extract_generated_html(result),
                    "processing_info": result.get("processing_info", {}),
                    "concept_data": result.get("processing_info", {}).get("concept_data", {}),
                    "html_quality_validation": _build_html_quality_payload(
                        False,
                        html_quality_issues,
                        html_quality_attempts,
                    ),
                    "fallback_used": bool(fallback_reason),
                    "fallback_reason": fallback_reason,
                }
                background_db.commit()
                total_time = time.time() - start_time
                logger.info(f"Background concept processing failed due to invalid HTML for document {document_id} in {total_time:.2f}s total")
                return

            generation_metadata = build_generation_metadata(
                ai_processor,
                requested_model=ai_model,
                workflow_type="concept",
                generation_method="ai"
            )
            website_result = result.get("website", {})
            # Update document with processing results
            document.status = "ready"
            document.page_count = result.get("metadata", {}).get("page_count", 1)
            document.pdf_metadata = result.get("metadata", {})
            document.processing_results = {
                "analysis": result.get("analysis", {}),
                "website": website_result.get("html", ""),
                "interactive_elements": website_result.get("interactive_elements", []),
                "processing_info": result.get("processing_info", {}),
                "concept_data": result.get("processing_info", {}).get("concept_data", {}),
                "generation_metadata": generation_metadata,
                "ai_model": generation_metadata.get("model"),
                "ai_provider": generation_metadata.get("provider"),
                "html_quality_validation": _build_html_quality_payload(
                    True,
                    [],
                    html_quality_attempts,
                ),
                "fallback_used": bool(fallback_reason),
                "fallback_reason": fallback_reason,
            }
            logger.info(f"✅ Document {document_id} marked as ready")

        background_db.commit()

        total_time = time.time() - start_time
        logger.info(f"🎉 Background concept processing completed for document {document_id} in {total_time:.2f}s total")

    except Exception as processing_error:
        logger.error(f"❌ Background concept processing error for document {document_id}: {str(processing_error)}")
        # Update document status to error
        try:
            document = background_db.query(Document).filter(Document.id == document_id).first()
            if document:
                document.status = "error"
                document.error_message = str(processing_error)
                background_db.commit()
        except Exception as db_error:
            logger.error(f"❌ Failed to update error status: {str(db_error)}")

    finally:
        # Always close the database session
        background_db.close()


@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    subject: Optional[str] = Form(None),
    grade_level: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    is_public: bool = Form(False),
    user_preferences: Optional[str] = Form(None),
    generation_mode: str = Form("fast"),
    ai_model: Optional[str] = Form(None),
    zhipu_text_model: Optional[str] = Form(None),  # Kept for backward compatibility
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload and process a PDF file using AI to convert it into an interactive learning website.
    Processing happens in the background, so the endpoint returns immediately.

    Generation Modes:
    - fast: Quick generation (~30 seconds) using one-shot prompting
    - heavy: High-quality generation (~3-5 minutes) using 4-stage pipeline with validation
    - auto: Use system default (currently "fast")
    """
    start_time = time.time()
    logger.info(f"🚀 Starting PDF upload process for file: {file.filename}")

    try:
        # Validate file type
        if not file.content_type == "application/pdf":
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")

        # Generate unique filename
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename

        # Save uploaded file
        file_save_start = time.time()
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        file_save_time = time.time() - file_save_start
        logger.info(f"📁 File saved in {file_save_time:.2f}s - Size: {len(content)/1024/1024:.2f}MB")

        # Parse user preferences if provided
        user_prefs = {}
        if user_preferences:
            try:
                user_prefs = json.loads(user_preferences)
            except json.JSONDecodeError:
                user_prefs = {}

        # Add grade level from form if provided
        if grade_level is not None:
            user_prefs["grade_level"] = grade_level

        # Add description from form if provided
        if description:
            user_prefs["description"] = description

        user_prefs = _apply_pdf_concept_preferences(user_prefs, subject, description)

        # Add AI model - prefer ai_model, fallback to zhipu_text_model for backward compatibility
        selected_model = ai_model or zhipu_text_model
        if selected_model:
            user_prefs["ai_model"] = selected_model
            # Also set zhipu_text_model for backward compatibility with ZhipuProvider
            if selected_model.startswith("glm-"):
                user_prefs["zhipu_text_model"] = selected_model

        # Create document record with "processing" status
        db_start = time.time()
        document = Document(
            title=title,
            original_filename=file.filename,
            file_path=str(file_path),
            file_size=len(content),
            user_id=current_user.id,
            subject=subject,
            grade_level=grade_level,
            description=description,
            is_public=is_public,
            status="processing"
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        db_time = time.time() - db_start
        logger.info(f"🗃️ Database record created in {db_time:.2f}s - Document ID: {document.id}")

        # Add background task to process the PDF
        background_tasks.add_task(
            process_pdf_background,
            document_id=document.id,
            file_path=str(file_path),
            user_prefs=user_prefs,
            db=db,
            generation_mode=generation_mode
        )

        total_time = time.time() - start_time
        logger.info(f"✅ PDF upload endpoint completed in {total_time:.2f}s - Processing continues in background")

        # Return immediately with document info
        result = {
            "id": document.id,
            "title": document.title,
            "original_filename": document.original_filename,
            "subject": document.subject,
            "grade_level": document.grade_level,
            "status": "processing",
            "message": "PDF uploaded successfully. Processing is happening in the background. Use the /documents/{id}/processing-status endpoint to check progress.",
            "created_at": document.created_at
        }

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/documents")
async def get_documents(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get list of user's processed PDF documents with version information.
    Returns aggregated root documents with version counts.
    """
    try:
        # Get all documents for the user
        all_documents = db.query(Document).filter(
            Document.user_id == current_user.id
        ).all()

        # Group documents by root_document_id
        grouped_docs = {}
        for doc in all_documents:
            root_id = doc.root_document_id or doc.id
            if root_id not in grouped_docs:
                grouped_docs[root_id] = []
            grouped_docs[root_id].append(doc)

        # For each group, return the current version (or root if no current)
        result = []
        for root_id, doc_group in grouped_docs.items():
            # Find current version
            current_doc = next((d for d in doc_group if d.is_current == 1), None)
            # If no current version, use the root document or latest version
            if not current_doc:
                current_doc = next((d for d in doc_group if d.root_document_id is None), doc_group[-1])
            
            version_count = len(doc_group)
            generation_metadata = get_generation_metadata(
                current_doc.processing_results,
                current_doc.pdf_metadata
            )
            
            result.append({
                "id": current_doc.id,
                "title": current_doc.title,
                "original_filename": current_doc.original_filename,
                "page_count": current_doc.page_count,
                "subject": current_doc.subject,
                "grade_level": current_doc.grade_level,
                "status": current_doc.status,
                "created_at": current_doc.created_at,
                "updated_at": current_doc.updated_at,
                "root_document_id": current_doc.root_document_id,
                "version_number": current_doc.version_number,
                "is_current": current_doc.is_current,
                "version_count": version_count,
                "user_prompt": current_doc.user_prompt,
                "generation_metadata": generation_metadata,
                "ai_model": generation_metadata.get("model") if generation_metadata else None,
                "ai_provider": generation_metadata.get("provider") if generation_metadata else None
            })

        # Sort by created_at descending
        result.sort(key=lambda x: x["created_at"], reverse=True)
        
        # Apply pagination
        paginated_result = result[skip:skip + limit] if limit > 0 else result[skip:]

        return paginated_result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch documents: {str(e)}")

@router.get("/documents/{document_id}")
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get specific document details including generated website.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        result = {
            "id": document.id,
            "title": document.title,
            "original_filename": document.original_filename,
            "page_count": document.page_count,
            "subject": document.subject,
            "grade_level": document.grade_level,
            "description": document.description,
            "status": document.status,
            "pdf_metadata": document.pdf_metadata,
            "created_at": document.created_at,
            "updated_at": document.updated_at
        }

        # Include processing results if available
        if document.processing_results:
            generation_metadata = get_generation_metadata(document.processing_results, document.pdf_metadata)
            result["website"] = document.processing_results.get("website")
            result["analysis"] = document.processing_results.get("analysis")
            result["knowledge_cards"] = document.processing_results.get("knowledge_cards")
            result["interactive_elements"] = document.processing_results.get("interactive_elements")
            result["processing_info"] = document.processing_results.get("processing_info")
            result["generation_mode"] = document.processing_results.get("generation_mode")
            result["generation_metadata"] = generation_metadata
            result["ai_model"] = generation_metadata.get("model") if generation_metadata else None
            result["ai_provider"] = generation_metadata.get("provider") if generation_metadata else None

            # Get concept_data from either processing_results or processing_info
            concept_data = document.processing_results.get("concept_data")
            if not concept_data:
                # Fallback to processing_info.concept_data for backward compatibility
                processing_info = document.processing_results.get("processing_info", {})
                concept_data = processing_info.get("concept_data")
            result["concept_data"] = concept_data

        if document.error_message:
            result["error_message"] = document.error_message

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document: {str(e)}")

@router.get("/documents/{document_id}/processing-status")
async def get_processing_status(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get processing status of a document.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        progress = 100 if document.status == "ready" else (50 if document.status == "processing" else 0)
        message = "Processing complete" if document.status == "ready" else \
                 ("Processing PDF..." if document.status == "processing" else "Error occurred")

        if document.error_message:
            message = f"Error: {document.error_message}"

        return {
            "document_id": document.id,
            "status": document.status,
            "progress": progress,
            "message": message
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch processing status: {str(e)}")

@router.get("/documents/{document_id}/website")
async def get_generated_website(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the generated interactive website for a document.
    Returns the HTML content directly.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        if document.status != "ready":
            raise HTTPException(status_code=400, detail="Document not ready or processing failed")

        if not document.processing_results or not document.processing_results.get("website"):
            raise HTTPException(status_code=404, detail="Website content not found")

        return JSONResponse({
            "html": document.processing_results["website"],
            "metadata": {
                "title": document.title,
                "subject": document.subject,
                "grade_level": document.grade_level
            }
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch website: {str(e)}")

@router.get("/public/documents/{document_id}")
async def get_public_document(
    document_id: int,
    db: Session = Depends(get_db)
):
    """
    Get specific document details including generated website for public access.
    Only works for documents that are marked as public.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.is_public == True
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Public document not found")

        result = {
            "id": document.id,
            "title": document.title,
            "original_filename": document.original_filename,
            "page_count": document.page_count,
            "subject": document.subject,
            "grade_level": document.grade_level,
            "description": document.description,
            "status": document.status,
            "pdf_metadata": document.pdf_metadata,
            "created_at": document.created_at,
            "updated_at": document.updated_at
        }

        # Include processing results if available
        if document.processing_results:
            generation_metadata = get_generation_metadata(document.processing_results, document.pdf_metadata)
            result["website"] = document.processing_results.get("website")
            result["analysis"] = document.processing_results.get("analysis")
            result["knowledge_cards"] = document.processing_results.get("knowledge_cards")
            result["interactive_elements"] = document.processing_results.get("interactive_elements")
            result["processing_info"] = document.processing_results.get("processing_info")
            result["generation_mode"] = document.processing_results.get("generation_mode")
            result["generation_metadata"] = generation_metadata
            result["ai_model"] = generation_metadata.get("model") if generation_metadata else None
            result["ai_provider"] = generation_metadata.get("provider") if generation_metadata else None

            # Get concept_data from either processing_results or processing_info
            concept_data = document.processing_results.get("concept_data")
            if not concept_data:
                # Fallback to processing_info.concept_data for backward compatibility
                processing_info = document.processing_results.get("processing_info", {})
                concept_data = processing_info.get("concept_data")
            result["concept_data"] = concept_data

        if document.error_message:
            result["error_message"] = document.error_message

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch public document: {str(e)}")

@router.get("/public/documents/{document_id}/view/{version_id}")
async def get_public_document_view_by_version(
    document_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Get the website HTML content for direct viewing of a specific version of a public document.
    Returns only the website HTML for rendering in a clean view.

    Path Parameters:
        document_id: The root document ID (used for grouping versions)
        version_id: The version number to view 
    """
    try:
        # Get the specific version by version_id (which is the document id)
        # Verify it's public and belongs to the document chain
        version_document = db.query(Document).filter(
            Document.version_number == version_id,
            Document.is_public == True,
            (Document.root_document_id == document_id) | (Document.id == document_id)
        ).first()

        if not version_document:
            raise HTTPException(status_code=404, detail="Document version not found")

        if not version_document.processing_results:
            raise HTTPException(status_code=404, detail="Document content not found")

        website_html = version_document.processing_results.get("website")

        if not website_html:
            raise HTTPException(status_code=404, detail="Website content not available")

        return {
            "id": version_document.id,
            "title": version_document.title,
            "version_number": version_document.version_number,
            "website_html": website_html
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document version view: {str(e)}")


@router.get("/public/documents/{document_id}/view")
async def get_public_document_view(
    document_id: int,
    db: Session = Depends(get_db)
):
    """
    Get the website HTML content for direct viewing of a public document.
    Returns only the website HTML for rendering in a clean view.
    If document_id is a root document, returns the current version (is_current=1).
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.is_public == True
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Public document not found")

        # If this is a root document (root_document_id is None), find the current version
        if document.root_document_id is None:
            current_version = db.query(Document).filter(
                Document.root_document_id == document_id,
                Document.is_current == 1,
                Document.is_public == True
            ).first()
            if current_version:
                document = current_version

        if not document.processing_results:
            raise HTTPException(status_code=404, detail="Document content not found")

        website_html = document.processing_results.get("website")

        if not website_html:
            raise HTTPException(status_code=404, detail="Website content not available")

        return {
            "id": document.id,
            "title": document.title,
            "website_html": website_html
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document view: {str(e)}")

@router.get("/public/documents/{document_id}/processing-status")
async def get_public_processing_status(
    document_id: int,
    db: Session = Depends(get_db)
):
    """
    Get processing status of a public document.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.is_public == True
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Public document not found")

        progress = 100 if document.status == "ready" else (50 if document.status == "processing" else 0)
        message = "Processing complete" if document.status == "ready" else \
                 ("Processing PDF..." if document.status == "processing" else "Error occurred")

        if document.error_message:
            message = f"Error: {document.error_message}"

        return {
            "document_id": document.id,
            "status": document.status,
            "progress": progress,
            "message": message
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch public processing status: {str(e)}")

@router.get("/public/documents/{document_id}/download")
async def download_public_document(
    document_id: int,
    db: Session = Depends(get_db)
):
    """
    Download the original PDF file for a public document.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.is_public == True
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Public document not found")

        if not os.path.exists(document.file_path):
            raise HTTPException(status_code=404, detail="PDF file not found")

        # Return the file with appropriate headers for download
        return FileResponse(
            path=document.file_path,
            filename=document.original_filename,
            media_type='application/pdf'
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download public document: {str(e)}")

@router.get("/public/documents")
async def get_public_documents(
    skip: int = 0,
    limit: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get list of all public documents for public browsing.
    Optional date filtering: date_from and date_to should be in YYYY-MM-DD format.
    """
    try:
        query = db.query(Document).filter(
            Document.is_public == True,
            Document.status == "ready"
        )

        # Apply date filtering if provided
        if date_from:
            try:
                from datetime import datetime
                date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
                query = query.filter(Document.created_at >= date_from_dt)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_from format. Use YYYY-MM-DD")

        if date_to:
            try:
                from datetime import datetime
                date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
                # Add one day to make it inclusive of the end date
                from datetime import timedelta
                date_to_dt = date_to_dt + timedelta(days=1)
                query = query.filter(Document.created_at < date_to_dt)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_to format. Use YYYY-MM-DD")

        # Order by creation date (newest first)
        query = query.order_by(Document.created_at.desc())

        documents = query.offset(skip).limit(limit).all()

        return [
            {
                "id": doc.id,
                "title": doc.title,
                "original_filename": doc.original_filename,
                "page_count": doc.page_count,
                "subject": doc.subject,
                "grade_level": doc.grade_level,
                "description": doc.description,
                "status": doc.status,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
                # Version management fields
                "root_document_id": doc.root_document_id,
                "version_number": doc.version_number or 1,
                "is_current": doc.is_current or 0,
                "user_prompt": doc.user_prompt
            }
            for doc in documents
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch public documents: {str(e)}")

@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Download the original PDF file for a document.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        if not os.path.exists(document.file_path):
            raise HTTPException(status_code=404, detail="PDF file not found")

        # Return the file with appropriate headers for download
        return FileResponse(
            path=document.file_path,
            filename=document.original_filename,
            media_type='application/pdf'
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download document: {str(e)}")


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a document and all versions in the same version chain.
    Also removes associated files from the filesystem when present.
    """
    try:
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Delete the whole version chain so dashboard cards and DB stay in sync.
        root_id = document.root_document_id or document.id
        chain_documents = db.query(Document).filter(
            Document.user_id == current_user.id,
            (Document.id == root_id) | (Document.root_document_id == root_id)
        ).all()

        if not chain_documents:
            chain_documents = [document]

        deleted_document_ids: List[int] = []

        # Delete files from filesystem first (if the document has a stored file path)
        for doc in chain_documents:
            if doc.file_path and os.path.exists(doc.file_path):
                os.remove(doc.file_path)
            deleted_document_ids.append(doc.id)

        # Delete from database
        for doc in chain_documents:
            db.delete(doc)
        db.commit()

        return {
            "message": "Document deleted successfully",
            "deleted_document_ids": deleted_document_ids,
            "deleted_count": len(deleted_document_ids),
            "deleted_root_document_id": root_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")

@router.post("/concept/upload")
async def upload_concept(
    background_tasks: BackgroundTasks,
    subject: str = Form(...),
    concept_name: str = Form(...),
    concept_overview: str = Form(...),
    mastery_points: str = Form(...),
    design_idea: str = Form(...),
    grade_level: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    is_public: bool = Form(False),
    interests: Optional[str] = Form(None),
    include_exercises: bool = Form(True),
    include_prerequisites: bool = Form(True),
    ai_model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),  # Language preference from frontend
    zhipu_text_model: Optional[str] = Form(None),  # Kept for backward compatibility
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload concept data and generate an interactive learning website using AI.
    Processing happens in the background, so the endpoint returns immediately.
    This is an alternative to PDF upload that works directly with text-based concept descriptions.
    """
    start_time = time.time()
    logger.info(f"🚀 Starting concept upload process for: {subject} - {concept_name}")

    try:
        # Prepare concept data
        concept_data = {
            "subject": subject,
            "concept_name": concept_name,
            "concept_overview": concept_overview,
            "mastery_points": mastery_points,
            "design_idea": design_idea
        }

        # Prepare user preferences
        user_preferences = {
            "grade_level": grade_level,
            "interests": interests.split(',') if interests else [],
            "description": description,
            "include_exercises": include_exercises,
            "include_prerequisites": include_prerequisites,
            "language": language or "zh"  # Use frontend language preference or default to Chinese
        }

        # Add AI model - prefer ai_model, fallback to zhipu_text_model for backward compatibility
        selected_model = ai_model or zhipu_text_model
        if selected_model:
            user_preferences["ai_model"] = selected_model
            # Also set zhipu_text_model for backward compatibility with ZhipuProvider
            if selected_model.startswith("glm-"):
                user_preferences["zhipu_text_model"] = selected_model

        # Create document record
        db_start = time.time()
        document = Document(
            title=concept_name,
            original_filename=f"concept_{concept_name}",
            file_path="",  # No file for concept input
            file_size=0,
            user_id=current_user.id,
            subject=subject,
            grade_level=grade_level,
            description=description,
            is_public=is_public,
            status="processing"
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        db_time = time.time() - db_start
        logger.info(f"🗃️ Database record created in {db_time:.2f}s - Document ID: {document.id}")

        # Add background task to process the concept
        background_tasks.add_task(
            process_concept_background,
            document_id=document.id,
            concept_data=concept_data,
            user_prefs=user_preferences,
            db=db
        )

        total_time = time.time() - start_time
        logger.info(f"✅ Concept upload endpoint completed in {total_time:.2f}s - Processing continues in background")

        # Return immediately with document info
        result = {
            "id": document.id,
            "title": document.title,
            "original_filename": document.original_filename,
            "subject": document.subject,
            "grade_level": document.grade_level,
            "status": "processing",
            "message": "Concept uploaded successfully. Processing is happening in the background. Use the /documents/{id}/processing-status endpoint to check progress.",
            "created_at": document.created_at
        }

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Concept upload failed: {str(e)}")


@router.post("/concept/search-templates")
async def search_templates_for_concept(
    subject: str = Form(...),
    concept_name: str = Form(...),
    concept_overview: str = Form(...),
    grade_level: Optional[int] = Form(None),
    max_results: int = Form(5),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Search for templates matching a concept description.

    This endpoint allows users to see template options before committing to full generation.
    Returns a list of suitable templates that the user can then select.

    Form Fields:
        subject: Subject area (e.g., "Mathematics", "Science")
        concept_name: Name of the concept
        concept_overview: Brief description of the concept
        grade_level: Target grade level
        max_results: Maximum number of template results (default: 5)
    """
    try:
        # Build content info for template search
        content_info = {
            "title": concept_name,
            "description": concept_overview,
            "subject": subject,
            "grade_level": grade_level or 6,
            "category": subject.lower()
        }

        # Get AI processor and search templates
        ai_processor = get_ai_processor()

        result = await ai_processor.search_templates_for_user(
            content_info=content_info,
            workflow_type="website_concept",
            db_session_factory=lambda: db,
            max_results=max_results
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Template search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Template search failed: {str(e)}")


@router.post("/concept/generate-with-template")
async def generate_concept_with_template(
    background_tasks: BackgroundTasks,
    subject: str = Form(...),
    concept_name: str = Form(...),
    concept_overview: str = Form(...),
    mastery_points: str = Form(...),
    design_idea: str = Form(...),
    template_id: str = Form(...),  # Selected template ID
    grade_level: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    is_public: bool = Form(False),
    interests: Optional[str] = Form(None),
    customization_params: Optional[str] = Form(None),  # JSON string
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate a website from concept using a selected template.

    Form Fields:
        subject: Subject area
        concept_name: Name of the concept
        concept_overview: Brief description
        mastery_points: Learning objectives
        design_idea: Design approach
        template_id: ID of the selected template
        grade_level: Target grade level
        description: Optional description
        is_public: Whether to make public
        interests: User interests (comma-separated)
        customization_params: Optional JSON string with template customization parameters
    """
    start_time = time.time()
    logger.info(f"🚀 Starting template-based concept generation for: {concept_name} with template: {template_id}")

    try:
        # Prepare concept data
        concept_data = {
            "subject": subject,
            "concept_name": concept_name,
            "concept_overview": concept_overview,
            "mastery_points": mastery_points,
            "design_idea": design_idea
        }

        # Prepare user preferences
        user_preferences = {
            "grade_level": grade_level,
            "interests": interests.split(',') if interests else [],
            "description": description,
            "subject": subject
        }

        # Parse customization params if provided
        custom_params = {}
        if customization_params:
            try:
                custom_params = json.loads(customization_params)
            except json.JSONDecodeError:
                logger.warning("Invalid customization_params JSON, using empty dict")

        # Create document record
        document = Document(
            title=concept_name,
            original_filename=f"concept_{concept_name}",
            file_path="",
            file_size=0,
            user_id=current_user.id,
            subject=subject,
            grade_level=grade_level,
            description=description,
            is_public=is_public,
            status="processing"
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        # Add background task to process with template
        background_tasks.add_task(
            process_concept_with_template_background,
            document_id=document.id,
            concept_data=concept_data,
            user_prefs=user_preferences,
            template_id=template_id,
            customization_params=custom_params,
            db_session_factory=lambda: SessionLocal()
        )

        total_time = time.time() - start_time
        logger.info(f"✅ Template-based concept generation initiated in {total_time:.2f}s")

        return {
            "id": document.id,
            "title": document.title,
            "subject": document.subject,
            "grade_level": document.grade_level,
            "status": "processing",
            "message": f"Generating website using template: {template_id}",
            "template_id": template_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Template-based concept generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


async def process_concept_with_template_background(
    document_id: int,
    concept_data: Dict,
    user_prefs: Dict,
    template_id: str,
    customization_params: Dict,
    db_session_factory
):
    """Background task to generate concept website using template."""
    logger.info(f"🔄 Starting template-based concept processing for document {document_id}")
    start_time = time.time()

    # Create a new database session for this background task
    from ..core.database import SessionLocal
    background_db = db_session_factory()

    try:
        # Get the document
        document = background_db.query(Document).filter(Document.id == document_id).first()
        if not document:
            logger.error(f"❌ Document {document_id} not found")
            return

        selected_model = user_prefs.get("ai_model") or user_prefs.get("zhipu_text_model")

        # Get AI processor instance
        ai_processor = get_ai_processor(ai_model=selected_model)

        # Build content info for template generation
        content_info = {
            "title": concept_data.get("concept_name"),
            "description": concept_data.get("concept_overview"),
            "subject": concept_data.get("subject"),
            "grade_level": user_prefs.get("grade_level", 6),
            "mastery_points": concept_data.get("mastery_points"),
            "design_idea": concept_data.get("design_idea")
        }

        # Generate using selected template
        logger.info(f"🔄 Generating website with template {template_id}...")
        generation_start = time.time()

        async def generate_template_concept_once() -> Dict[str, Any]:
            return await ai_processor.generate_with_selected_template(
                template_id=template_id,
                content_info=content_info,
                user_preferences=user_prefs,
                workflow_type="website_concept",
                db_session_factory=db_session_factory,
                customization_params=customization_params
            )

        result, html_quality_issues, html_quality_attempts = await _generate_with_html_quality_retry(
            generate_template_concept_once,
            workflow_label=f"Template concept document {document_id}",
            max_retries=1,
        )

        generation_time = time.time() - generation_start
        logger.info(f"✅ Template generation completed in {generation_time:.2f}s")

        if result.get("status") == "error":
            logger.error(f"❌ Template generation failed: {result.get('error')}")
            document.status = "error"
            document.error_message = result.get('error')
        elif html_quality_issues:
            error_message = (
                "Generated HTML failed quality validation after retry; "
                f"issues: {'; '.join(html_quality_issues)}"
            )
            logger.error(f"Template concept processing failed HTML validation: {error_message}")
            document.status = "error"
            document.error_message = error_message
            document.page_count = 1
            document.pdf_metadata = {
                "template_used": template_id,
                "generation_method": "template_based",
            }
            document.processing_results = {
                "failed_website": _extract_generated_html(result),
                "template_used": template_id,
                "generation_method": "template_based",
                "metadata": result.get("metadata", {}),
                "html_quality_validation": _build_html_quality_payload(
                    False,
                    html_quality_issues,
                    html_quality_attempts,
                ),
            }
        else:
            generation_metadata = build_generation_metadata(
                ai_processor,
                requested_model=selected_model,
                workflow_type="concept",
                generation_method="template_based"
            )
            # Update document with results
            document.status = "ready"
            document.page_count = 1
            document.pdf_metadata = {
                "template_used": template_id,
                "generation_method": "template_based",
                "generation_metadata": generation_metadata
            }
            document.processing_results = {
                "website": result.get("html", ""),
                "template_used": template_id,
                "generation_method": "template_based",
                "metadata": result.get("metadata", {}),
                "generation_metadata": generation_metadata,
                "ai_model": generation_metadata.get("model"),
                "ai_provider": generation_metadata.get("provider"),
                "html_quality_validation": _build_html_quality_payload(
                    True,
                    [],
                    html_quality_attempts,
                ),
            }
            logger.info(f"✅ Document {document_id} marked as ready")

        background_db.commit()

        total_time = time.time() - start_time
        logger.info(f"🎉 Template-based concept processing completed for document {document_id} in {total_time:.2f}s")

    except Exception as processing_error:
        logger.error(f"❌ Template-based concept processing error: {str(processing_error)}")
        try:
            document = background_db.query(Document).filter(Document.id == document_id).first()
            if document:
                document.status = "error"
                document.error_message = str(processing_error)
                background_db.commit()
        except Exception as db_error:
            logger.error(f"❌ Failed to update error status: {str(db_error)}")

    finally:
        background_db.close()


@router.post("/pdf/{document_id}/search-templates")
async def search_templates_for_pdf(
    document_id: int,
    max_results: int = 5,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Search for templates matching an uploaded PDF document.

    This endpoint analyzes the PDF and returns template options for website generation.
    """
    try:
        # Get document
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Build content info from document metadata
        content_info = {
            "title": document.title,
            "description": document.description or "",
            "subject": document.subject or "General",
            "grade_level": document.grade_level or 6,
            "category": (document.subject or "").lower()
        }

        # Get AI processor and search templates
        ai_processor = get_ai_processor()

        result = await ai_processor.search_templates_for_user(
            content_info=content_info,
            workflow_type="website_pdf",
            db_session_factory=lambda: db,
            max_results=max_results
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF template search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Template search failed: {str(e)}")


@router.post("/pdf/{document_id}/generate-with-template")
async def generate_pdf_website_with_template(
    document_id: int,
    background_tasks: BackgroundTasks,
    template_id: str = Form(...),
    customization_params: Optional[str] = Form(None),  # JSON string
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate a website from an uploaded PDF using a selected template.

    This endpoint regenerates the website for an existing PDF document using the specified template.
    """
    try:
        # Get document
        document = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id
        ).first()

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Parse customization params if provided
        custom_params = {}
        if customization_params:
            try:
                custom_params = json.loads(customization_params)
            except json.JSONDecodeError:
                logger.warning("Invalid customization_params JSON, using empty dict")

        # Build user preferences
        user_preferences = {
            "grade_level": document.grade_level,
            "subject": document.subject,
            "description": document.description
        }

        # Update document status
        document.status = "processing"
        db.commit()
        db.refresh(document)

        # Add background task to process with template
        background_tasks.add_task(
            process_pdf_with_template_background,
            document_id=document_id,
            file_path=document.file_path,
            user_prefs=user_preferences,
            template_id=template_id,
            customization_params=custom_params,
            db_session_factory=lambda: SessionLocal()
        )

        return {
            "id": document.id,
            "status": "processing",
            "message": f"Regenerating website using template: {template_id}",
            "template_id": template_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF template generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


async def process_pdf_with_template_background(
    document_id: int,
    file_path: str,
    user_prefs: Dict,
    template_id: str,
    customization_params: Dict,
    db_session_factory
):
    """Background task to generate PDF website using template."""
    logger.info(f"🔄 Starting template-based PDF processing for document {document_id}")
    start_time = time.time()

    # Create a new database session for this background task
    from ..core.database import SessionLocal
    background_db = db_session_factory()

    try:
        # Get the document
        document = background_db.query(Document).filter(Document.id == document_id).first()
        if not document:
            logger.error(f"❌ Document {document_id} not found")
            return

        existing_generation_metadata = get_generation_metadata(document.processing_results, document.pdf_metadata)
        selected_model = (
            user_prefs.get("ai_model") or
            user_prefs.get("zhipu_text_model") or
            (existing_generation_metadata or {}).get("requested_model") or
            (existing_generation_metadata or {}).get("model")
        )
        if selected_model == "default":
            selected_model = None

        # Get AI processor instance
        ai_processor = get_ai_processor(ai_model=selected_model)

        # Build content info for template generation
        content_info = {
            "title": document.title,
            "description": document.description or "",
            "subject": document.subject or "General",
            "grade_level": user_prefs.get("grade_level", 6)
        }

        # For PDF workflow, we need to extract images first
        # Get processed images from PDF
        logger.info(f"🔄 Extracting images from PDF...")
        processed_images = ai_processor.convert_pdf_to_images(
            file_path,
            max_pages=get_pdf_image_conversion_page_limit()
        )

        # Add images to content_info
        content_info["images"] = processed_images
        content_info["pdf_path"] = file_path

        # Generate using selected template
        logger.info(f"🔄 Generating website with template {template_id}...")
        generation_start = time.time()

        async def generate_template_pdf_once() -> Dict[str, Any]:
            return await ai_processor.generate_with_selected_template(
                template_id=template_id,
                content_info=content_info,
                user_preferences=user_prefs,
                workflow_type="website_pdf",
                db_session_factory=db_session_factory,
                customization_params=customization_params
            )

        result, html_quality_issues, html_quality_attempts = await _generate_with_html_quality_retry(
            generate_template_pdf_once,
            workflow_label=f"Template PDF document {document_id}",
            max_retries=1,
        )

        generation_time = time.time() - generation_start
        logger.info(f"✅ Template generation completed in {generation_time:.2f}s")

        if result.get("status") == "error":
            logger.error(f"❌ Template generation failed: {result.get('error')}")
            document.status = "error"
            document.error_message = result.get('error')
        elif html_quality_issues:
            error_message = (
                "Generated HTML failed quality validation after retry; "
                f"issues: {'; '.join(html_quality_issues)}"
            )
            logger.error(f"Template PDF processing failed HTML validation: {error_message}")
            document.status = "error"
            document.error_message = error_message
            document.page_count = len(processed_images)
            document.pdf_metadata = {
                "template_used": template_id,
                "generation_method": "template_based",
                "page_count": len(processed_images),
            }
            document.processing_results = {
                "failed_website": _extract_generated_html(result),
                "template_used": template_id,
                "generation_method": "template_based",
                "metadata": result.get("metadata", {}),
                "html_quality_validation": _build_html_quality_payload(
                    False,
                    html_quality_issues,
                    html_quality_attempts,
                ),
            }
        else:
            generation_metadata = build_generation_metadata(
                ai_processor,
                requested_model=selected_model,
                workflow_type="pdf",
                generation_method="template_based"
            )
            # Update document with results
            document.status = "ready"
            document.page_count = len(processed_images)
            document.pdf_metadata = {
                "template_used": template_id,
                "generation_method": "template_based",
                "page_count": len(processed_images),
                "generation_metadata": generation_metadata
            }
            document.processing_results = {
                "website": result.get("html", ""),
                "template_used": template_id,
                "generation_method": "template_based",
                "metadata": result.get("metadata", {}),
                "generation_metadata": generation_metadata,
                "ai_model": generation_metadata.get("model"),
                "ai_provider": generation_metadata.get("provider"),
                "html_quality_validation": _build_html_quality_payload(
                    True,
                    [],
                    html_quality_attempts,
                ),
            }
            logger.info(f"✅ Document {document_id} marked as ready")

        background_db.commit()

        total_time = time.time() - start_time
        logger.info(f"🎉 Template-based PDF processing completed for document {document_id} in {total_time:.2f}s")

    except Exception as processing_error:
        logger.error(f"❌ Template-based PDF processing error: {str(processing_error)}")
        try:
            document = background_db.query(Document).filter(Document.id == document_id).first()
            if document:
                document.status = "error"
                document.error_message = str(processing_error)
                background_db.commit()
        except Exception as db_error:
            logger.error(f"❌ Failed to update error status: {str(db_error)}")

    finally:
        background_db.close()
