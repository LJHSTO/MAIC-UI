import os
import base64
import json
import io
import time
import logging
import asyncio
import httpx
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image
import PyPDF2
from abc import ABC, abstractmethod
from .prompts import ai_prompts
from .mineru_pdf_parser import enhance_pdf_text_context_with_mineru
from dataclasses import dataclass, field
import re

# Configure logging
logger = logging.getLogger(__name__)

DEFAULT_PDF_IMAGE_CONVERSION_PAGE_LIMIT = 50
DEFAULT_PDF_ANALYSIS_IMAGE_PAGE_LIMIT = 16
DEFAULT_PDF_WEBSITE_IMAGE_PAGE_LIMIT = 16
DEFAULT_PDF_TEXT_CONTEXT_MAX_CHARS = 80000
DEFAULT_PDF_TEXT_PAGE_CHAR_LIMIT = 1800
DEFAULT_AI_REQUEST_TIMEOUT_SECONDS = 360


def _get_int_env(name: str, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value == "":
        return default

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using default %s", name, raw_value, default)
        return default

    if value < minimum:
        logger.warning("%s=%s is below minimum %s; using %s", name, value, minimum, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("%s=%s is above maximum %s; using %s", name, value, maximum, maximum)
        return maximum
    return value


def get_pdf_analysis_image_page_limit() -> int:
    return _get_int_env("PDF_ANALYSIS_IMAGE_PAGE_LIMIT", DEFAULT_PDF_ANALYSIS_IMAGE_PAGE_LIMIT, 1, 100)


def get_pdf_website_image_page_limit() -> int:
    return _get_int_env("PDF_WEBSITE_IMAGE_PAGE_LIMIT", DEFAULT_PDF_WEBSITE_IMAGE_PAGE_LIMIT, 1, 100)


def get_pdf_image_conversion_page_limit() -> int:
    return _get_int_env("PDF_IMAGE_CONVERSION_PAGE_LIMIT", DEFAULT_PDF_IMAGE_CONVERSION_PAGE_LIMIT, 1, 100)


def get_pdf_text_context_max_chars() -> int:
    return _get_int_env("PDF_TEXT_CONTEXT_MAX_CHARS", DEFAULT_PDF_TEXT_CONTEXT_MAX_CHARS, 1000, 300000)


def get_pdf_text_page_char_limit() -> int:
    return _get_int_env("PDF_TEXT_PAGE_CHAR_LIMIT", DEFAULT_PDF_TEXT_PAGE_CHAR_LIMIT, 500, 10000)


def get_ai_request_timeout_seconds() -> int:
    return _get_int_env("AI_REQUEST_TIMEOUT_SECONDS", DEFAULT_AI_REQUEST_TIMEOUT_SECONDS, 30, 1800)


INNOSPARK_BASE_URL = "https://api.innospark.cn/v1"
INNOSPARK_MODEL_ALIASES = {
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gpt-5": "gpt-5.4",
    "gpt-5.4-pro": "gpt-5.4",
    "gpt-5.5": "gpt-5.4",
    "claude-opus-4-7": "claude-opus-4-6",
    "qwen3.6-27b": "Qwen3.6-35B-inno",
    "qwen3.6-35b-a3b": "Qwen3.6-35B-inno",
}
INNOSPARK_MODELS = [
    "gpt-5.4-pro",
    "gpt-5.4",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v3.2",
    "doubao-seed-2-0-pro-260215",
    "doubao-seed-2-0-code-preview-260215",
    "kimi-k2.6",
    "Qwen3.6-35B-inno",
]
INNOSPARK_MODEL_INPUTS = set(INNOSPARK_MODELS) | set(INNOSPARK_MODEL_ALIASES)


def is_innospark_model(model: Optional[str]) -> bool:
    return bool(model and model in INNOSPARK_MODEL_INPUTS)


def resolve_innospark_model(model: str) -> str:
    return INNOSPARK_MODEL_ALIASES.get(model, model)


def get_innospark_api_key() -> Optional[str]:
    return os.getenv("INNOSPARK_API_KEY")


def get_innospark_base_url() -> str:
    return os.getenv("INNOSPARK_BASE_URL", INNOSPARK_BASE_URL)


def _compact_pdf_text(text: str) -> str:
    """Normalize extracted PDF text while preserving readable boundaries."""
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _append_pdf_context_to_prompt(prompt: str, user_preferences: Optional[Dict], *, language: str = "zh") -> str:
    """Add extracted PDF text and learner goals to an analysis prompt."""
    user_preferences = user_preferences or {}
    pdf_context = user_preferences.get("_pdf_text_context") or {}
    excerpt = pdf_context.get("excerpt")
    description = user_preferences.get("description") or user_preferences.get("user_instruction")

    additions = []
    if description:
        additions.append(
            "User learning goals / 用户学习目标:\n"
            f"{description}"
        )

    if excerpt:
        if language == "en":
            additions.append(
                "Extracted PDF text context. Treat this text as the primary source of truth. "
                "If it differs from page images, prioritize the extracted text.\n"
                f"Text source: {pdf_context.get('source', 'unknown')}; "
                f"pages with text: {pdf_context.get('pages_with_text', 0)}; "
                f"included text pages: {pdf_context.get('included_pages', 0)}; "
                f"selection: {pdf_context.get('selection_strategy', 'sequential')}; "
                f"total text chars: {pdf_context.get('total_chars', 0)}.\n\n"
                f"{excerpt}"
            )
        else:
            additions.append(
                "PDF抽取文本上下文。请把这段文本作为主要依据；如果它与页面图片判断冲突，优先依据文本。"
                "如果原文是英文，输出仍使用简体中文，并保留必要英文术语及中文译名。\n"
                f"文本来源: {pdf_context.get('source', 'unknown')}; "
                f"有文本页数: {pdf_context.get('pages_with_text', 0)}; "
                f"已纳入文本页数: {pdf_context.get('included_pages', 0)}; "
                f"文本选择方式: {pdf_context.get('selection_strategy', 'sequential')}; "
                f"文本总字符数: {pdf_context.get('total_chars', 0)}.\n\n"
                f"{excerpt}"
            )

    if not additions:
        return prompt

    return prompt.rstrip() + "\n\n" + "\n\n".join(additions)


def _extract_json_from_text(response_text: str) -> Optional[Dict]:
    """Extract the outermost JSON object from model text."""
    if not response_text:
        return None

    cleaned = response_text.replace("```json", "").replace("```html", "").replace("```", "")
    json_start = cleaned.find("{")
    json_end = cleaned.rfind("}") + 1
    if json_start == -1 or json_end <= json_start:
        return None

    try:
        return json.loads(cleaned[json_start:json_end])
    except json.JSONDecodeError:
        return None


def _build_text_aware_fallback_analysis(user_preferences: Optional[Dict], *, language: str = "zh") -> Dict:
    """Create a useful fallback analysis from extracted text instead of a generic shell."""
    user_preferences = user_preferences or {}
    pdf_context = user_preferences.get("_pdf_text_context") or {}
    text = (pdf_context.get("excerpt") or "").lower()

    term_map = [
        ("vector calculus", "向量微积分", "Vector Calculus"),
        ("difference quotient", "差商", "Difference Quotient"),
        ("derivative", "导数", "Derivative"),
        ("partial derivative", "偏导数", "Partial Derivative"),
        ("gradient", "梯度", "Gradient"),
        ("chain rule", "链式法则", "Chain Rule"),
        ("taylor", "泰勒展开", "Taylor Series"),
        ("jacobian", "雅可比矩阵", "Jacobian"),
        ("hessian", "海森矩阵", "Hessian"),
        ("optimization", "优化", "Optimization"),
    ]
    matched = []
    for needle, zh, en in term_map:
        if needle in text and zh not in matched:
            matched.append(zh if language != "en" else en)

    if matched:
        key_concepts = matched[:8]
        return {
            "main_topics": ["向量微积分", "机器学习中的函数变化与优化"] if language != "en" else ["Vector Calculus", "Function Change and Optimization in Machine Learning"],
            "key_concepts": key_concepts,
            "learning_objectives": [
                "用图像和变化率直觉理解导数",
                "理解偏导数如何描述多变量函数的单方向变化",
                "理解梯度为什么指向函数增长最快的方向",
                "理解链式法则与神经网络反向传播的联系",
            ] if language != "en" else [
                "Understand derivatives through rates of change and graphs",
                "Explain partial derivatives as one-direction changes in multivariable functions",
                "Understand why the gradient points toward steepest ascent",
                "Connect the chain rule to neural-network backpropagation",
            ],
            "prerequisite_knowledge": ["函数", "坐标系", "斜率", "基础代数"] if language != "en" else ["Functions", "Coordinate systems", "Slope", "Basic algebra"],
            "difficulty_level": "中级" if language != "en" else "intermediate",
            "target_grade_level": user_preferences.get("grade_level", 6),
            "content_structure": [
                {"title": "函数与变化率", "page_start": 1, "page_end": 5, "topics": ["函数", "差商", "导数"]},
                {"title": "多变量变化", "page_start": 6, "page_end": 15, "topics": ["偏导数", "梯度", "雅可比矩阵"]},
                {"title": "近似与优化", "page_start": 16, "page_end": 33, "topics": ["链式法则", "泰勒展开", "机器学习优化"]},
            ],
            "visual_elements": ["函数曲线", "切线斜率", "等高线", "梯度箭头"],
            "subject_area": "数学",
            "procedural_concepts": [
                {
                    "name": "从差商到导数",
                    "description": "用两点平均斜率逐渐逼近一点处的瞬时变化率。",
                    "key_steps": ["选择函数图像上的两个点", "计算差商作为平均变化率", "缩小间距并观察切线斜率"],
                    "complexity": "中等"
                },
                {
                    "name": "从偏导数组装梯度",
                    "description": "分别观察多变量函数在各坐标方向上的变化，再合成为梯度向量。",
                    "key_steps": ["固定其他变量", "计算每个方向的偏导数", "把偏导数组成梯度并解释方向"],
                    "complexity": "中等"
                },
                {
                    "name": "用链式法则理解反向传播",
                    "description": "把复合函数拆成层层依赖，沿依赖关系传递局部变化率。",
                    "key_steps": ["拆分复合函数结构", "计算每层局部导数", "相乘得到整体影响并连接梯度下降"],
                    "complexity": "复杂"
                }
            ],
            "analysis_diagnostics": {
                "fallback_used": True,
                "fallback_reason": "ai_content_analysis_failed_text_heuristic",
                "pdf_text_chars": pdf_context.get("total_chars", 0),
            }
        }

    return {
        "main_topics": ["PDF学习内容"] if language != "en" else ["PDF Learning Content"],
        "key_concepts": ["核心概念", "重点关系", "应用场景"] if language != "en" else ["Core concepts", "Key relationships", "Applications"],
        "learning_objectives": ["理解PDF中的核心概念", "把概念应用到例题或情境中"] if language != "en" else ["Understand the core concepts", "Apply them to examples or scenarios"],
        "prerequisite_knowledge": [],
        "difficulty_level": "中级" if language != "en" else "intermediate",
        "target_grade_level": user_preferences.get("grade_level", 6),
        "content_structure": [],
        "visual_elements": [],
        "subject_area": "综合教育" if language != "en" else "General Education",
        "procedural_concepts": [
            {
                "name": "提取核心概念" if language != "en" else "Extract Core Concepts",
                "description": "从材料中找出最关键的概念和它们之间的关系。" if language != "en" else "Identify the most important concepts and relationships.",
                "key_steps": ["定位主题", "解释概念", "应用练习"] if language != "en" else ["Find topics", "Explain concepts", "Practice application"],
                "complexity": "中等" if language != "en" else "intermediate"
            }
        ],
        "analysis_diagnostics": {
            "fallback_used": True,
            "fallback_reason": "ai_content_analysis_failed_generic",
            "pdf_text_chars": pdf_context.get("total_chars", 0),
        }
    }


# ============================================================================
# Data Model Classes for Modular Pipeline
# ============================================================================

@dataclass
class ScientificModel:
    """Stage 1 output"""
    core_formulas: List[str] = field(default_factory=list)           # core formulas
    principles: List[str] = field(default_factory=list)              # fundamental principles
    mechanism: List[str] = field(default_factory=list)               # working mechanism
    constraints: List[str] = field(default_factory=list)             # constraints to follow
    forbidden_errors: List[str] = field(default_factory=list)        # forbidden errors
    variable_relationships: Dict[str, str] = field(default_factory=dict)  # variable relationships
    validation_checks: List[str] = field(default_factory=list)       # validation checkpoints
    raw_analysis: str = ""                                           # raw analysis text

    @classmethod
    def from_dict(cls, data: Dict) -> 'ScientificModel':
        """Create ScientificModel from dictionary."""
        return cls(
            core_formulas=data.get('core_formulas', []),
            principles=data.get('principles', []),
            mechanism=data.get('mechanism', []),
            constraints=data.get('constraints', []),
            forbidden_errors=data.get('forbidden_errors', []),
            variable_relationships=data.get('variable_relationships', {}),
            validation_checks=data.get('validation_checks', []),
            raw_analysis=data.get('raw_analysis', '')
        )
    
# pdf2image removed - using PyMuPDF only

# Import PyMuPDF as a better alternative
try:
    import fitz  # PyMuPDF is imported as fitz
    PYMUPDF_AVAILABLE = True
    print("PyMuPDF (fitz) imported successfully")
except ImportError as e:
    PYMUPDF_AVAILABLE = False
    print(f"Warning: PyMuPDF not available. Error: {e}. Using fallback PDF processing method.")

# Import AI providers

try:
    from zai import ZhipuAiClient
    ZHIPU_AVAILABLE = True
except ImportError:
    ZHIPU_AVAILABLE = False

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("Anthropic SDK not available. Install with: pip install anthropic")

try:
    from openai import OpenAI as OpenAIClient
    OPENAI_SDK_AVAILABLE = True
except ImportError:
    OPENAI_SDK_AVAILABLE = False
    logger.warning("OpenAI SDK not available. Install with: pip install openai")


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    @abstractmethod
    async def analyze_content(self, images: List[Dict], user_preferences: Dict) -> Dict:
        """Analyze PDF content and return structured analysis."""
        pass

    @abstractmethod
    async def generate_website(self, images: List[Dict], analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate interactive learning website."""
        pass

    @abstractmethod
    async def generate_website_from_concept(self, concept_data: Dict, user_preferences: Dict) -> Dict:
        """Generate interactive learning website from concept data."""
        pass

    @abstractmethod
    async def modify_website_ui(self, original_html: str, user_prompt: str, document_context: Dict) -> Dict:
        """Modify existing website UI based on user prompt."""
        pass

    @abstractmethod
    async def customize_template(
        self,
        template,
        content_info: Dict,
        user_preferences: Dict,
        customization_params: Optional[Dict] = None
    ) -> str:
        """Customize a template HTML using LLM."""
        pass

    @abstractmethod
    async def generate_knowledge_cards(self, analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate prerequisite knowledge cards as markdown summaries."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Get the name of the AI provider."""
        pass


class EnglishProvider(AIProvider):
    """Unified English AI provider implementation using Chinese middle-transfer API for both Gemini and OpenAI models."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4.1"):
        """
        Initialize unified English provider.

        Args:
            api_key: API key for the middle-transfer service
            model: Model to use - supports both gemini-3-pro-image-preview and gpt-4.1
        """
        if not api_key:
            # Try multiple environment variables for flexibility
            api_key = (os.getenv("ENGLISH_API_KEY") or
                      os.getenv("MIDDLE_TRANSFER_API_KEY") or
                      os.getenv("TRANSFER_API_KEY") or
                      os.getenv("GEMINI_API_KEY") or
                      os.getenv("OPENAI_API_KEY"))

        if not api_key:
            raise ValueError("API key not provided. Set ENGLISH_API_KEY, MIDDLE_TRANSFER_API_KEY, TRANSFER_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY environment variable or pass api_key parameter.")

        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.siliconflow.cn/v1beta"

        # Validate model choice
        supported_models = [
            "gemini-3-pro-image-preview",
            "gpt-4.1",
            "gemini-pro-vision",
            "gpt-4-vision-preview"
        ]

        if self.model not in supported_models:
            print(f"Warning: Model '{self.model}' not in supported list {supported_models}. Using default 'gpt-4.1'")
            self.model = "gpt-4.1"

    async def analyze_content(self, images: List[Dict], user_preferences: Dict) -> Dict:
        """Analyze content using unified English API (supports both Gemini and OpenAI models)."""
        analysis_start = time.time()
        logger.info(f"🧠 Starting content analysis with {self.get_provider_name()} using {len(images)} images")

        user_preferences = user_preferences or {}
        grade_level = user_preferences.get('grade_level', 'middle school')
        interests = user_preferences.get('interests', [])

        # Prepare content for API
        prep_start = time.time()
        content = []

        # Add the text prompt
        prompt = _append_pdf_context_to_prompt(
            self._get_content_analysis_prompt(grade_level, interests),
            user_preferences,
            language="en"
        )
        content.append({"type": "text", "text": prompt})

        # Add images with a configurable cap for long PDFs.
        max_images = min(len(images), get_pdf_analysis_image_page_limit())
        for i in range(max_images):
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{images[i]['image_data']}",
                    "detail": "low"
                }
            })
        prep_time = time.time() - prep_start
        logger.info(f"📝 Content prepared for API in {prep_time:.2f}s - Using {max_images} images")

        try:
            api_start = time.time()
            response = await self._make_api_call(content)
            api_time = time.time() - api_start
            logger.info(f"🌐 AI API call completed in {api_time:.2f}s")

            parse_start = time.time()
            result = self._extract_json_from_response(response, user_preferences)
            parse_time = time.time() - parse_start
            logger.info(f"📄 Response parsed in {parse_time:.2f}s")

            total_analysis_time = time.time() - analysis_start
            logger.info(f"✅ Content analysis completed in {total_analysis_time:.2f}s total")
            return result
        except Exception as e:
            logger.error(f"❌ Error analyzing content with English Provider ({self.model}): {e}")
            if user_preferences.get("_pdf_text_context", {}).get("excerpt"):
                try:
                    text_only_response = await self._make_api_call([{"type": "text", "text": prompt}])
                    parsed = _extract_json_from_text(text_only_response)
                    if parsed:
                        logger.info("✅ English content analysis recovered using extracted PDF text")
                        return parsed
                except Exception as text_error:
                    logger.error(f"❌ Text-only English content analysis failed: {text_error}")
            return self._generate_fallback_analysis(user_preferences)

    async def generate_website(self, images: List[Dict], analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate website using unified English API."""
        website_start = time.time()
        logger.info(f"🎨 Starting website generation with {self.get_provider_name()} using {len(images)} images")

        user_preferences = user_preferences or {}
        grade_level = user_preferences.get('grade_level', 6)
        interests = user_preferences.get('interests', [])

        # Prepare content for API
        prep_start = time.time()
        content = []

        # Add the website generation prompt
        prompt = self._get_website_generation_prompt(grade_level, interests, analysis)
        content.append({"type": "text", "text": prompt})

        # Add images for website generation with a configurable cap for long PDFs.
        max_images = min(len(images), get_pdf_website_image_page_limit())
        for i in range(max_images):
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{images[i]['image_data']}",
                    "detail": "low"
                }
            })
        prep_time = time.time() - prep_start
        logger.info(f"📝 Website content prepared in {prep_time:.2f}s - Using {max_images} images")

        try:
            api_start = time.time()
            response = await self._make_api_call(content)
            api_time = time.time() - api_start
            logger.info(f"🌐 Website API call completed in {api_time:.2f}s")

            parse_start = time.time()
            result = self._extract_json_from_response(response)
            parse_time = time.time() - parse_start
            logger.info(f"📄 Website response parsed in {parse_time:.2f}s")

            total_website_time = time.time() - website_start
            logger.info(f"✅ Website generation completed in {total_website_time:.2f}s total")
            return result
        except Exception as e:
            logger.error(f"❌ Error generating website with English Provider ({self.model}): {e}")
            return self._generate_fallback_website(images, analysis, user_preferences)

    def get_provider_name(self) -> str:
        """Get provider name with model info."""
        if "gemini" in self.model.lower():
            return f"Gemini ({self.model})"
        else:
            return f"OpenAI ({self.model})"

    async def generate_website_from_concept(self, concept_data: Dict, user_preferences: Dict) -> Dict:
        """Generate website from concept - not implemented for English provider."""
        raise NotImplementedError("generate_website_from_concept is not implemented for English provider. Please use Zhipu provider.")

    async def modify_website_ui(self, original_html: str, user_prompt: str, document_context: Dict) -> Dict:
        """Modify website UI - not implemented for English provider."""
        raise NotImplementedError("modify_website_ui is not implemented for English provider. Please use Zhipu provider.")

    async def generate_knowledge_cards(self, analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate prerequisite knowledge cards - not implemented for English provider."""
        raise NotImplementedError("generate_knowledge_cards is not implemented for English provider. Please use Zhipu provider.")

    async def customize_template(
        self,
        template,
        content_info: Dict,
        user_preferences: Dict,
        customization_params: Optional[Dict] = None
    ) -> str:
        """Customize template - not implemented for English provider."""
        raise NotImplementedError("customize_template is not implemented for English provider. Please use Zhipu provider.")

    async def _make_api_call(self, content: List[Dict]) -> str:
        """Make API call to the middle-transfer service."""
        import httpx

        # Determine API endpoint based on model
        if "gemini" in self.model.lower():
            # Gemini-style endpoint
            endpoint = f"{self.base_url}/models/{self.model}:streamGenerateContent"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": content
                }],
                "generationConfig": {
                    "responseModalities": ["TEXT"],
                    "temperature": 0.7
                }
            }
        else:
            # OpenAI-style endpoint (default)
            endpoint = f"{self.base_url}/chat/completions"
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": content
                }],
                "max_tokens": 4000,
                "temperature": 0.7
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=900.0) as client:  # 15 minutes for long AI processing
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()

            if "gemini" in self.model.lower():
                # Gemini response format
                result = response.json()
                if 'candidates' in result and result['candidates']:
                    return result['candidates'][0]['content']['parts'][0]['text']
                else:
                    raise ValueError("Invalid Gemini response format")
            else:
                # OpenAI response format
                result = response.json()
                if 'choices' in result and result['choices']:
                    return result['choices'][0]['message']['content']
                else:
                    raise ValueError("Invalid OpenAI response format")

    def _extract_json_from_response(self, response_text: str, user_preferences: Optional[Dict] = None) -> Dict:
        """Extract JSON from API response text."""
        if not response_text:
            return self._generate_fallback_analysis(user_preferences)

        parsed = _extract_json_from_text(response_text)
        if parsed:
            return parsed

        # If no JSON found, return fallback
        return self._generate_fallback_analysis(user_preferences)

    def _get_content_analysis_prompt(self, grade_level: str, interests: List[str]) -> str:
        """Get the content analysis prompt for English providers."""
        return ai_prompts.english_content_analysis_prompt(grade_level, interests)

    def _get_knowledge_card_generation_prompt(self, analysis: Dict, user_preferences: Dict) -> str:
        """Get the knowledge card generation prompt for English providers."""
        return ai_prompts.english_knowledge_card_prompt(analysis)

    def _get_website_generation_prompt(self, grade_level: int, interests: List[str], analysis: Dict) -> str:
        """Get the website generation prompt for English providers."""
        return ai_prompts.english_website_generation_prompt(grade_level, interests, analysis)

    def _generate_fallback_analysis(self, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate fallback analysis when API fails."""
        if user_preferences and user_preferences.get("_pdf_text_context", {}).get("excerpt"):
            return _build_text_aware_fallback_analysis(user_preferences, language="en")
        return {
            "main_topics": ["Educational Content"],
            "key_concepts": ["Learning", "Understanding"],
            "learning_objectives": ["Comprehend the material"],
            "prerequisite_knowledge": [],
            "difficulty_level": "intermediate",
            "target_grade_level": 6,
            "content_structure": [],
            "visual_elements": [],
            "subject_area": "General Education",
            "analysis_diagnostics": {
                "fallback_used": True,
                "fallback_reason": "ai_content_analysis_failed_generic"
            }
        }

    def _generate_fallback_website(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate fallback website."""
        user_preferences = user_preferences or {}
        grade_level = user_preferences.get('grade_level', 6)

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Interactive Learning - {analysis.get('subject_area', 'Education')}</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gradient-to-br from-blue-50 to-indigo-100 min-h-screen">
            <div class="container mx-auto px-4 py-8">
                <header class="mb-8 text-center">
                    <h1 class="text-4xl font-bold text-blue-600 mb-2">
                        Interactive Learning
                    </h1>
                    <p class="text-gray-600">
                        Subject: {analysis.get('subject_area', 'Education')} | Grade Level: {grade_level} | AI: {self.get_provider_name()}
                    </p>
                </header>

                <nav class="mb-8">
                    <div class="flex flex-wrap gap-2 justify-center">
                        {"".join([f'<button onclick="scrollToPage({i+1})" class="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">Page {i+1}</button>' for i in range(min(len(pdf_images), 10))])}
                    </div>
                </nav>

                <main class="space-y-8">
                    <section class="bg-white rounded-lg shadow-lg p-6">
                        <h2 class="text-2xl font-semibold mb-4">Key Concepts</h2>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {"".join([f'<div class="bg-blue-50 p-4 rounded-lg"><h3 class="font-semibold">{concept}</h3><p class="text-sm text-gray-600">Important concept to understand</p></div>' for concept in analysis.get('key_concepts', ['Learning'])])}
                        </div>
                    </section>

                    <section class="bg-white rounded-lg shadow-lg p-6">
                        <h2 class="text-2xl font-semibold mb-4">Content Pages</h2>
                        <div class="space-y-8">
                            {"".join([f'''
                            <div class="page-section" id="page-{page['page']}">
                                <h3 class="text-xl font-semibold mb-4">Page {page['page']}</h3>
                                <div class="bg-gray-50 rounded-lg p-4">
                                    <img src="data:image/png;base64,{page['image_data']}"
                                         alt="Page {page['page']}"
                                         class="w-full max-w-4xl mx-auto shadow-md rounded">
                                </div>
                            </div>
                            ''' for page in pdf_images[:10]])}
                        </div>
                    </section>

                    <section class="bg-green-50 rounded-lg p-6">
                        <h3 class="text-xl font-semibold mb-3">Learning Objectives</h3>
                        <ul class="list-disc list-inside space-y-2">
                            {"".join([f'<li>{obj}</li>' for obj in analysis.get('learning_objectives', ['Understand the content'])])}
                        </ul>
                    </section>
                </main>
            </div>

            <script>
                function scrollToPage(pageNum) {{
                    const element = document.getElementById('page-' + pageNum);
                    if (element) {{
                        element.scrollIntoView({{ behavior: 'smooth' }});
                    }}
                }}
            </script>
        </body>
        </html>
        """

        return {
            "html": html_content,
            "metadata": {
                "title": f"Interactive Learning - {analysis.get('subject_area', 'Education')}",
                "subject": analysis.get('subject_area', 'Education'),
                "grade_level": grade_level,
                "estimated_time_minutes": 30,
                "learning_objectives": analysis.get('learning_objectives', ['Understand the content']),
                "ai_provider": self.get_provider_name()
            },
            "interactive_elements": []
        }


class GeminiClient:
    """Thin wrapper around Google Gemini API that mimics OpenAI client interface."""

    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, client: "GeminiClient"):
            self._client = client
            self.completions = self._Completions(client)

        class _Completions:
            def __init__(self, client: "GeminiClient"):
                self._client = client

            def create(self, **params) -> Any:
                return _gemini_sync_call(self._client, **params)


def _gemini_sync_call(client: GeminiClient, **params) -> Any:
    """Make synchronous Gemini API call, returning OpenAI-compatible response object."""
    model = params.get("model", "gemini-2.5-flash")
    messages = params.get("messages", [])
    max_tokens = params.get("max_tokens")
    temperature = params.get("temperature")

    # Convert OpenAI messages to Gemini contents format
    contents = []
    for msg in messages:
        parts = []
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    parts.append({"text": item["text"]})
                elif item.get("type") == "image_url":
                    # Gemini expects inline_data for images
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        mime, b64 = url.split(",", 1) if "," in url else ("image/png", url)
                        mime = mime.replace("data:", "").split(";")[0]
                        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
        role = "user" if msg.get("role") != "assistant" else "model"
        contents.append({"role": role, "parts": parts})

    # Build request
    url = f"{client.base_url}/models/{model}:generateContent?key={client.api_key}"
    body = {"contents": contents}
    if max_tokens:
        body.setdefault("generationConfig", {})["maxOutputTokens"] = max_tokens
    if temperature is not None:
        body.setdefault("generationConfig", {})["temperature"] = temperature

    # Make request
    response = httpx.post(url, json=body, timeout=900.0)
    response.raise_for_status()
    data = response.json()

    # Convert Gemini response to OpenAI-compatible format
    candidates = data.get("candidates", [])
    text = ""
    if candidates and candidates[0].get("content", {}).get("parts"):
        text = candidates[0]["content"]["parts"][0].get("text", "")

    return _GeminiResponse(text=text, model=model)


class _GeminiResponse:
    """Mimics OpenAI response object."""

    def __init__(self, text: str, model: str = ""):
        self.choices = [_GeminiChoice(text)]
        self.model = model


class _GeminiChoice:
    def __init__(self, text: str):
        self.message = _GeminiMessage(text)
        self.finish_reason = "stop"


class _GeminiMessage:
    def __init__(self, content: str):
        self.content = content


class ChineseProvider(AIProvider):
    """Unified Chinese AI provider supporting Anthropic, Zhipu, and OpenAI-compatible models."""

    # Model detection
    ANTHROPIC_MODELS = []  # Claude now goes through uuapi (openai_compat), not Anthropic SDK
    ZHIPU_MODELS = ["glm-4.7", "glm-4.6", "glm-4.6v", "glm-5", "glm-5.1"]
    OPENAI_COMPAT_MODELS = [
        # Claude (via Innospark)
        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
        # OpenAI GPT (via Innospark)
        "gpt-5", "gpt-5.4-pro", "gpt-5.4", "gpt-5.5",
        # DeepSeek
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2",
        # Gemini (via Innospark)
        "gemini-3.1-pro", "gemini-3.1-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
        # Doubao
        "doubao-seed-2-0-pro-260215", "doubao-seed-2-0-code-preview-260215",
        # Kimi (月之暗面)
        "kimi-k2.6",
        # GLM (via Zhipu OpenAI-compatible API)
        "glm-4.7", "glm-4.6", "glm-4.6v", "glm-5", "glm-5.1",
        # Others (via SiliconFlow)
        "minimax-m2.5", "qwen3.6-27b", "qwen3.6-35b-a3b", "Qwen3.6-35B-inno",
    ]

    # Internal name → SiliconFlow API model ID mapping
    SILICONFLOW_MODEL_MAP = {
        "minimax-m2.5": "MiniMaxAI/MiniMax-M2.5",
        "qwen3.6-27b": "Qwen/Qwen3.6-27B",
        "qwen3.6-35b-a3b": "Qwen/Qwen3.6-35B-A3B",
    }

    # Model categories for routing to different API proxies
    CLAUDE_MODELS = ["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"]
    GPT_MODELS = ["gpt-5", "gpt-5.4-pro", "gpt-5.4", "gpt-5.5"]
    GEMINI_MODELS = [
        "gemini-3.1-pro", "gemini-3.1-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
    ]
    DEEPSEEK_MODELS = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2"]
    ZHIPU_MODELS = ["glm-4.7", "glm-4.6", "glm-4.6v", "glm-5", "glm-5.1"]
    KIMI_MODELS = ["kimi-k2.6"]
    DOUBAO_MODELS = ["doubao-seed-2-0-pro-260215", "doubao-seed-2-0-code-preview-260215"]

    def _resolve_model(self, model: str) -> str:
        """Resolve internal model name to actual API model ID."""
        if self.backend == "openai_compat":
            if is_innospark_model(model):
                return resolve_innospark_model(model)
            # GLM pass through (Zhipu OpenAI-compatible API handles it)
            if model in self.ZHIPU_MODELS:
                return model
            # DeepSeek model ID mapping for official API
            if model in self.DEEPSEEK_MODELS:
                return self.DEEPSEEK_MODEL_MAP.get(model, model)
            # Kimi pass through (Moonshot API handles it)
            if model in self.KIMI_MODELS:
                return model
            # Claude pass through (uuapi handles it with same model names)
            if model in self.CLAUDE_MODELS:
                return model
            # GPT pass through (uuapi handles it)
            if model in self.GPT_MODELS:
                return model
            # Gemini → use appropriate model map based on provider type
            if model in self.GEMINI_MODELS:
                # Check if using uuapi (OpenAI-compatible) or Google SDK
                if self.gemini_client is not None and not isinstance(self.gemini_client, GeminiClient):
                    return self.GEMINI_UUAPI_MODEL_MAP.get(model, model)
                return self.GEMINI_MODEL_MAP.get(model, model)
            # Others → SiliconFlow mapping
            return self.SILICONFLOW_MODEL_MAP.get(model, model)
        return model

    # Internal name → Google Gemini API model ID mapping
    GEMINI_MODEL_MAP = {
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
        "gemini-3.5-flash": "gemini-3.5-flash",
        "gemini-3-flash-preview": "gemini-3-flash-preview",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash": "gemini-2.5-flash",
    }

    # Internal name → uuapi Gemini model ID mapping
    GEMINI_UUAPI_MODEL_MAP = {
        "gemini-3.1-pro": "gemini-3.1-pro-high",
    }

    # Internal name → DeepSeek API model name mapping
    DEEPSEEK_MODEL_MAP = {
        "deepseek-v4-pro": "deepseek-reasoner",
        "deepseek-v4-flash": "deepseek-chat",
        "deepseek-v3.2": "deepseek-v3.2",
    }

    def _get_client_for_model(self, model: str):
        """Get the appropriate OpenAI client based on model category."""
        if self.backend != "openai_compat":
            return self.openai_client
        if is_innospark_model(model) and self.innospark_client is not None:
            return self.innospark_client
        if model in self.CLAUDE_MODELS and self.claude_client is not None:
            return self.claude_client
        if model in self.GPT_MODELS and self.gpt_client is not None:
            return self.gpt_client
        if model in self.GEMINI_MODELS and self.gemini_client is not None:
            return self.gemini_client
        if model in self.ZHIPU_MODELS and self.zhipu_oa_client is not None:
            return self.zhipu_oa_client
        if model in self.DEEPSEEK_MODELS and self.deepseek_client is not None:
            return self.deepseek_client
        if model in self.KIMI_MODELS and self.kimi_client is not None:
            return self.kimi_client
        return self.openai_client

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "glm-4.6v",
        base_url: Optional[str] = None
    ):
        """
        Initialize the Chinese Provider.

        Args:
            api_key: API key. If not provided, auto-detects from environment based on model.
            model: Model to use. Determines which backend (Anthropic, Zhipu, or openai_compat) to use.
            base_url: Optional base URL for API proxy.
        """
        self.model = model
        self.backend = self._detect_backend(model)
        self.request_timeout_seconds = get_ai_request_timeout_seconds()

        # Auto-detect API key based on backend
        if not api_key:
            if self.backend == "anthropic":
                api_key = os.getenv("TRANSFER_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
            elif self.backend == "openai_compat":
                if is_innospark_model(model):
                    api_key = get_innospark_api_key()
                elif model in self.ZHIPU_MODELS:
                    api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("TRANSFER_API_KEY")
                else:
                    api_key = os.getenv("TRANSFER_API_KEY") or os.getenv("OPENAI_API_KEY")
            else:
                api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("TRANSFER_API_KEY")

        if not api_key:
            env_var_map = {"anthropic": "TRANSFER_API_KEY", "openai_compat": "TRANSFER_API_KEY", "zhipu": "ZHIPU_API_KEY"}
            env_var = "INNOSPARK_API_KEY" if is_innospark_model(model) else env_var_map.get(self.backend, "TRANSFER_API_KEY")
            raise ValueError(f"API key not provided. Set {env_var} environment variable or pass api_key parameter.")

        # Initialize appropriate client
        if self.backend == "anthropic":
            if not ANTHROPIC_AVAILABLE:
                raise ImportError("Anthropic SDK not installed. Install with: pip install anthropic")

            client_kwargs = {"api_key": api_key}
            # Derive Anthropic base URL from TRANSFER_BASE_URL (strip /v1 suffix)
            if base_url:
                client_kwargs["base_url"] = base_url
            else:
                transfer_url = os.getenv("TRANSFER_BASE_URL", "")
                if transfer_url:
                    client_kwargs["base_url"] = transfer_url.replace("/v1", "")
                elif os.getenv("ANTHROPIC_BASE_URL"):
                    client_kwargs["base_url"] = os.getenv("ANTHROPIC_BASE_URL")

            self.anthropic_client = Anthropic(**client_kwargs)
            self.zhipu_client = None
            self.openai_client = None
            self.gpt_client = None
            self.claude_client = None
            self.zhipu_oa_client = None
            self.deepseek_client = None
            self.gemini_client = None
            self.kimi_client = None
            self.innospark_client = None
            self.text_model = model
            logger.info(f"🎨 ChineseProvider initialized with Anthropic backend, model: {self.model}")

        elif self.backend == "openai_compat":
            if not OPENAI_SDK_AVAILABLE:
                raise ImportError("OpenAI SDK not installed. Install with: pip install openai")

            # Main fallback client: SiliconFlow (for models not covered by Innospark or official APIs)
            compat_base_url = base_url or os.getenv("TRANSFER_BASE_URL", "https://api.siliconflow.cn/v1")
            self.openai_client = OpenAIClient(api_key=api_key, base_url=compat_base_url, timeout=self.request_timeout_seconds)
            self.anthropic_client = None
            self.zhipu_client = None
            self.text_model = model
            self.default_text_model = model

            innospark_key = api_key if is_innospark_model(model) else get_innospark_api_key()
            innospark_url = base_url if is_innospark_model(model) and base_url else get_innospark_base_url()
            if innospark_key:
                self.innospark_client = OpenAIClient(api_key=innospark_key, base_url=innospark_url, timeout=self.request_timeout_seconds)
                logger.info(f"馃帹 Innospark client initialized: {innospark_url}")
            else:
                self.innospark_client = None

            # GPT client: uuapi.net
            gpt_key = os.getenv("GPT_API_KEY")
            gpt_url = os.getenv("UUAPI_BASE_URL", "https://uuapi.net/v1")
            if gpt_key:
                self.gpt_client = OpenAIClient(api_key=gpt_key, base_url=gpt_url, timeout=self.request_timeout_seconds)
                logger.info(f"🎨 GPT client initialized via uuapi: {gpt_url}")
            else:
                self.gpt_client = None

            # Claude client: uuapi.net
            claude_key = os.getenv("ANTHROPIC_API_KEY")
            claude_url = os.getenv("UUAPI_BASE_URL", "https://uuapi.net/v1")
            if claude_key:
                self.claude_client = OpenAIClient(api_key=claude_key, base_url=claude_url, timeout=self.request_timeout_seconds)
                logger.info(f"🎨 Claude client initialized via uuapi: {claude_url}")
            else:
                self.claude_client = None

            # Gemini client: auto-detect uuapi (OpenAI-compatible) vs Google official API
            gemini_key = os.getenv("GEMINI_API_KEY")
            gemini_url = os.getenv("GEMINI_BASE_URL", "https://uuapi.net/v1")
            if gemini_key:
                if "uuapi.net" in gemini_url:
                    # uuapi is OpenAI-compatible, use regular OpenAIClient
                    self.gemini_client = OpenAIClient(api_key=gemini_key, base_url=gemini_url, timeout=self.request_timeout_seconds)
                    logger.info(f"🎨 Gemini client initialized via uuapi (OpenAI-compat): {gemini_url}")
                else:
                    # Google official API, use GeminiClient adapter
                    self.gemini_client = GeminiClient(api_key=gemini_key, base_url=gemini_url)
                    logger.info(f"🎨 Gemini client initialized via Google API: {gemini_url}")
            else:
                self.gemini_client = None

            # Zhipu client: open.bigmodel.cn (OpenAI-compatible)
            zhipu_key = os.getenv("ZHIPU_API_KEY")
            zhipu_url = "https://open.bigmodel.cn/api/paas/v4"
            if zhipu_key:
                self.zhipu_oa_client = OpenAIClient(api_key=zhipu_key, base_url=zhipu_url, timeout=self.request_timeout_seconds)
                logger.info(f"🎨 Zhipu GLM client initialized via OpenAI-compat: {zhipu_url}")
            else:
                self.zhipu_oa_client = None

            # DeepSeek client: api.deepseek.com
            deepseek_key = os.getenv("DEEPSEEK_API_KEY")
            deepseek_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            if deepseek_key:
                self.deepseek_client = OpenAIClient(api_key=deepseek_key, base_url=deepseek_url, timeout=self.request_timeout_seconds)
                logger.info(f"🎨 DeepSeek client initialized via official API: {deepseek_url}")
            else:
                self.deepseek_client = None

            # Kimi client: api.moonshot.cn
            kimi_key = os.getenv("KIMI_API_KEY")
            kimi_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
            if kimi_key:
                self.kimi_client = OpenAIClient(api_key=kimi_key, base_url=kimi_url, timeout=self.request_timeout_seconds)
                logger.info(f"🎨 Kimi client initialized via moonshot: {kimi_url}")
            else:
                self.kimi_client = None

            logger.info(f"🎨 ChineseProvider initialized with OpenAI-compat backend, model: {self.model}, base_url: {compat_base_url}")

        else:  # zhipu
            if not ZHIPU_AVAILABLE:
                raise ImportError("Zhipu AI SDK not installed. Install with: pip install zai-sdk")

            self.zhipu_client = ZhipuAiClient(api_key=api_key)
            self.anthropic_client = None
            self.openai_client = None
            self.gpt_client = None
            self.claude_client = None
            self.zhipu_oa_client = None
            self.deepseek_client = None
            self.gemini_client = None
            self.kimi_client = None
            self.innospark_client = None
            self.default_text_model = os.getenv("ZHIPU_TEXT_MODEL", "glm-4.7")
            self.text_model = self.default_text_model
            logger.info(f"🎨 ChineseProvider initialized with Zhipu backend, model: {self.model}")

    def _detect_backend(self, model: str) -> str:
        """Detect which backend to use based on model name."""
        # Claude models now go through uuapi (openai_compat), not Anthropic SDK
        if model in self.OPENAI_COMPAT_MODELS:
            return "openai_compat"
        return "zhipu"  # Default

    def _map_grade_level_to_string(self, grade_level: int) -> str:
        """Convert integer grade level to Chinese grade level string."""
        grade_mapping = {
            0: "幼儿园",
            1: "小学一年级",
            2: "小学二年级",
            3: "小学三年级",
            4: "小学四年级",
            5: "小学五年级",
            6: "小学六年级",
            7: "初中一年级",
            8: "初中二年级",
            9: "初中三年级",
            10: "高中一年级",
            11: "高中二年级",
            12: "高中三年级",
            13: "本科",
            14: "研究生"
        }
        return grade_mapping.get(grade_level, f"年级{grade_level}")

    async def _run_zhipu_call(self, model: str, messages: List[Dict], thinking_params: Optional[Dict] = None, max_tokens: Optional[int] = None) -> Any:
        """Run synchronous Zhipu API call in a thread pool to avoid blocking the event loop.

        Also transparently redirects to OpenAI-compat backend when applicable.

        Args:
            model: Model name to use
            messages: List of message dicts
            thinking_params: Optional thinking parameters
            max_tokens: Optional max tokens limit for response
        """
        # Redirect to openai_compat if that's the active backend
        if self.backend == "openai_compat" and self.openai_client is not None:
            return await self._run_openai_compat_call(model, messages, max_tokens=max_tokens)

        if self.zhipu_client is None:
            raise RuntimeError("Zhipu client not initialized. This provider is configured for Anthropic.")

        def _make_sync_call():
            params = {
                "model": model,
                "messages": messages,
            }
            if thinking_params:
                params["thinking"] = thinking_params
            if max_tokens:
                params["max_tokens"] = max_tokens

            return self.zhipu_client.chat.completions.create(**params)

        loop = asyncio.get_event_loop()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(None, _make_sync_call),
                timeout=self.request_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Zhipu API call timed out after {self.request_timeout_seconds}s for model {model}"
            ) from exc

        # Log truncation reason for diagnostics.
        try:
            finish_reason = None
            if response and getattr(response, "choices", None):
                first_choice = response.choices[0]
                finish_reason = getattr(first_choice, "finish_reason", None)

            if finish_reason in ["length", "max_tokens"]:
                logger.warning(
                    "⚠️ Zhipu response may be truncated: finish_reason=%s, model=%s, max_tokens=%s",
                    finish_reason,
                    model,
                    max_tokens
                )
        except Exception:
            # Never break generation due to diagnostic logging.
            pass

        return response

    async def _run_openai_compat_call(self, model: str, messages: List[Dict], max_tokens: Optional[int] = None) -> Any:
        """Run OpenAI-compatible API call via appropriate transfer station.

        Routes to Innospark when available, otherwise provider-specific OpenAI-compatible clients.
        Returns the same response format as _run_zhipu_call (choices[0].message.content).
        """
        if self.openai_client is None:
            raise RuntimeError("OpenAI-compat client not initialized.")

        # Resolve internal model name to API model ID
        resolved_model = self._resolve_model(model)
        # Route to correct client based on model category
        client = self._get_client_for_model(model)

        def _make_sync_call():
            params = {
                "model": resolved_model,
                "messages": messages,
            }
            if max_tokens:
                params["max_tokens"] = max_tokens

            return client.chat.completions.create(**params)

        loop = asyncio.get_event_loop()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(None, _make_sync_call),
                timeout=self.request_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"OpenAI-compatible API call timed out after {self.request_timeout_seconds}s for model {model}"
            ) from exc

        # Log truncation diagnostics
        try:
            if response and getattr(response, "choices", None):
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                if finish_reason in ["length", "max_tokens"]:
                    logger.warning(
                        "⚠️ OpenAI-compat response may be truncated: finish_reason=%s, model=%s",
                        finish_reason, model
                    )
        except Exception:
            pass

        return response

    # Threshold for using streaming (to avoid 10-minute timeout for large responses)
    ANTHROPIC_STREAMING_THRESHOLD = 16000

    async def _run_anthropic_call(
        self,
        model: str,
        messages: List[Dict],
        max_tokens: int = 64000,
        thinking_enabled: bool = False
    ) -> Any:
        """Run synchronous Anthropic API call in a thread pool to avoid blocking the event loop.

        Uses streaming for large max_tokens to avoid 10-minute timeout errors.
        """
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client not initialized. This provider is configured for Zhipu.")

        use_streaming = max_tokens > self.ANTHROPIC_STREAMING_THRESHOLD

        def _make_sync_call():
            params = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages
            }
            # Extended thinking support for compatible models
            if thinking_enabled and "claude-3-7" in model:
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": 4096
                }

            if use_streaming:
                # Use streaming for large responses to avoid 10-minute timeout
                logger.info(f"🔄 Using streaming mode for Anthropic API call (max_tokens={max_tokens})")
                return self._collect_streaming_response(params)
            else:
                return self.anthropic_client.messages.create(**params)

        loop = asyncio.get_event_loop()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(None, _make_sync_call),
                timeout=self.request_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Anthropic API call timed out after {self.request_timeout_seconds}s for model {model}"
            ) from exc

        # Log truncation reason for diagnostics.
        try:
            stop_reason = getattr(response, "stop_reason", None) if response else None
            if stop_reason in ["max_tokens", "model_context_window_exceeded"]:
                logger.warning(
                    "⚠️ Anthropic response may be truncated: stop_reason=%s, model=%s, max_tokens=%s",
                    stop_reason,
                    model,
                    max_tokens
                )
        except Exception:
            # Never break generation due to diagnostic logging.
            pass

        return response

    def _collect_streaming_response(self, params: Dict) -> Any:
        """Collect streaming response from Anthropic API and return a response-like object."""
        from dataclasses import dataclass, field
        from typing import List as TypingList

        @dataclass
        class TextBlock:
            text: str
            type: str = "text"

        @dataclass
        class StreamedResponse:
            content: TypingList[TextBlock] = field(default_factory=list)
            stop_reason: str = ""
            model: str = ""
            usage: Dict = field(default_factory=dict)

        collected_text = []
        stop_reason = ""
        model_used = params.get("model", "")
        usage_info = {}

        with self.anthropic_client.messages.stream(**params) as stream:
            for text in stream.text_stream:
                collected_text.append(text)

            # Get final message info after streaming completes
            final_message = stream.get_final_message()
            if final_message:
                stop_reason = getattr(final_message, "stop_reason", "")
                model_used = getattr(final_message, "model", model_used)
                usage_info = getattr(final_message, "usage", {})
                if hasattr(usage_info, "__dict__"):
                    usage_info = usage_info.__dict__

        full_text = "".join(collected_text)
        logger.info(f"✅ Streaming completed, collected {len(full_text)} characters")

        return StreamedResponse(
            content=[TextBlock(text=full_text)],
            stop_reason=stop_reason,
            model=model_used,
            usage=usage_info
        )

    def _extract_text_from_anthropic_response(self, response) -> str:
        """Extract text content from Anthropic response."""
        if response and response.content:
            content_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    content_text += block.text
            return content_text
        return ""

    async def analyze_content(self, images: List[Dict], user_preferences: Dict) -> Dict:
        """Analyze content using the appropriate backend."""
        analysis_start = time.time()
        logger.info(f"🧠 Starting content analysis with ChineseProvider ({self.backend}) using {len(images)} images")

        user_preferences = user_preferences or {}
        grade_level_int = user_preferences.get('grade_level', 6)
        grade_level = self._map_grade_level_to_string(grade_level_int)
        interests = user_preferences.get('interests', [])

        # Prepare images with a configurable cap for long PDFs.
        max_pages = min(len(images), get_pdf_analysis_image_page_limit())
        prompt = _append_pdf_context_to_prompt(
            self._get_content_analysis_prompt(grade_level, interests),
            user_preferences,
            language="zh"
        )

        if self.backend == "anthropic":
            return await self._analyze_content_anthropic(images, max_pages, prompt, analysis_start, user_preferences)
        else:
            return await self._analyze_content_zhipu(images, max_pages, prompt, analysis_start, user_preferences)

    async def _analyze_content_anthropic(self, images: List[Dict], max_pages: int, prompt: str, analysis_start: float, user_preferences: Dict) -> Dict:
        """Analyze content using Anthropic."""
        content = []

        # Add images
        for i in range(max_pages):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": images[i]['image_data']
                }
            })

        content.append({"type": "text", "text": prompt})

        try:
            api_start = time.time()
            response = await self._run_anthropic_call(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=4096
            )
            api_time = time.time() - api_start
            logger.info(f"🌐 Anthropic API call completed in {api_time:.2f}s")

            content_text = self._extract_text_from_anthropic_response(response)
            if content_text:
                json_start = content_text.find('{')
                json_end = content_text.rfind('}') + 1

                if json_start != -1 and json_end > json_start:
                    json_str = content_text[json_start:json_end]
                    result = json.loads(json_str)
                    total_analysis_time = time.time() - analysis_start
                    logger.info(f"✅ Anthropic content analysis completed in {total_analysis_time:.2f}s total")
                    return result

            logger.warning(f"⚠️ Using fallback analysis for Anthropic")
            return await self._recover_or_fallback_content_analysis(prompt, user_preferences, analysis_start)

        except Exception as e:
            logger.error(f"❌ Error analyzing content with Anthropic: {e}")
            return await self._recover_or_fallback_content_analysis(prompt, user_preferences, analysis_start)

    async def _analyze_content_zhipu(self, images: List[Dict], max_pages: int, prompt: str, analysis_start: float, user_preferences: Dict) -> Dict:
        """Analyze content using Zhipu."""
        content = []
        content.append({"type": "text", "text": prompt})

        # Add images
        for i in range(max_pages):
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{images[i]['image_data']}"}
            })

        try:
            api_start = time.time()
            response = await self._run_zhipu_call(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                thinking_params={"type": "disabled"}
            )
            api_time = time.time() - api_start
            logger.info(f"🌐 Zhipu API call completed in {api_time:.2f}s")

            if response and response.choices and response.choices[0].message:
                content_text = response.choices[0].message.content
                content_text = content_text.replace("```html", "")

                if content_text:
                    json_start = content_text.find('{')
                    json_end = content_text.rfind('}') + 1

                    if json_start != -1 and json_end > json_start:
                        json_str = content_text[json_start:json_end]
                        result = json.loads(json_str)
                        total_analysis_time = time.time() - analysis_start
                        logger.info(f"✅ Zhipu content analysis completed in {total_analysis_time:.2f}s total")
                        return result

            logger.warning(f"⚠️ Using fallback analysis for Zhipu")
            return await self._recover_or_fallback_content_analysis(prompt, user_preferences, analysis_start)

        except Exception as e:
            logger.error(f"❌ Error analyzing content with Zhipu: {e}")
            return await self._recover_or_fallback_content_analysis(prompt, user_preferences, analysis_start)

    async def _recover_or_fallback_content_analysis(self, prompt: str, user_preferences: Dict, analysis_start: float) -> Dict:
        """Retry content analysis with extracted PDF text only before falling back."""
        if user_preferences.get("_pdf_text_context", {}).get("excerpt"):
            try:
                response = await self._run_zhipu_call(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "disabled"},
                    max_tokens=4096
                )
                if response and response.choices and response.choices[0].message:
                    parsed = _extract_json_from_text(response.choices[0].message.content)
                    if parsed:
                        total_analysis_time = time.time() - analysis_start
                        logger.info(f"✅ Content analysis recovered from extracted PDF text in {total_analysis_time:.2f}s total")
                        return parsed
            except Exception as text_error:
                logger.error(f"❌ Text-only content analysis failed: {text_error}")

        return self._generate_fallback_analysis(user_preferences)

    async def generate_website(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate interactive learning website using the appropriate backend."""
        website_start = time.time()
        logger.info(f"🎨 Starting website generation with ChineseProvider ({self.backend})")

        user_preferences = user_preferences or {}

        try:
            # Extract procedural concepts from analysis
            procedural_concepts = analysis.get('procedural_concepts', [])
            if not procedural_concepts:
                procedural_concepts = [
                    {
                        "name": concept,
                        "description": f"Understanding and applying the concept of {concept}",
                        "key_steps": ["Step 1: Understand the concept", "Step 2: Practice with examples", "Step 3: Apply to problems"],
                        "complexity": "中等"
                    }
                    for concept in analysis.get('key_concepts', [])[:2]
                ]

            procedural_concepts = procedural_concepts[:3]

            # Generate HTML content
            html_content = await self._generate_html_content(procedural_concepts, analysis, user_preferences)

            # Generate metadata and interactive elements
            metadata_and_elements = await self._generate_metadata_and_interactive(procedural_concepts, analysis, user_preferences)

            total_website_time = time.time() - website_start
            logger.info(f"✅ Website generation completed in {total_website_time:.2f}s total")

            return {
                "html": html_content,
                "metadata": metadata_and_elements.get("metadata", {}),
                "interactive_elements": metadata_and_elements.get("interactive_elements", [])
            }

        except Exception as e:
            logger.error(f"❌ Error generating website: {e}")
            return self._generate_fallback_website(pdf_images, analysis, user_preferences)

    async def generate_website_from_concept(self, concept_data: Dict, user_preferences: Dict) -> Dict:
        """Generate interactive learning website from concept data."""
        try:
            pipeline_start = time.time()
            logger.info(f"🎨 Starting concept-based website generation with ChineseProvider ({self.backend})")

            if self.backend == "anthropic":
                return await self._generate_website_from_concept_anthropic(concept_data, user_preferences, pipeline_start)
            else:
                # Use modular pipeline for Zhipu
                pipeline = ModularGenerationPipeline(self)
                result = await pipeline.execute(concept_data, user_preferences)
                return result

        except Exception as e:
            logger.error(f"❌ Pipeline failed: {str(e)}")
            raise

    async def _generate_website_from_concept_anthropic(self, concept_data: Dict, user_preferences: Dict, pipeline_start: float) -> Dict:
        """Generate website from concept using Anthropic."""
        grade_level_int = user_preferences.get('grade_level', 10)
        grade_level = self._map_grade_level_to_string(grade_level_int)

        # Stage 1: Scientific modeling
        logger.info("[1/3] Scientific modeling stage...")
        scientific_prompt = ai_prompts.anthropic_scientific_prompt(concept_data)

        sci_response = await self._run_anthropic_call(
            model=self.model,
            messages=[{"role": "user", "content": scientific_prompt}],
            max_tokens=4096
        )

        sci_text = self._extract_text_from_anthropic_response(sci_response)
        scientific_analysis = sci_text

        # Parse scientific model
        constraints_summary = ""
        try:
            json_start = sci_text.find('{')
            json_end = sci_text.rfind('}') + 1
            if json_start != -1 and json_end > json_start:
                sci_data = json.loads(sci_text[json_start:json_end])
                if sci_data.get('core_formulas'):
                    constraints_summary += f"核心公式: {', '.join(sci_data['core_formulas'][:3])}\n"
                if sci_data.get('mechanism'):
                    constraints_summary += f"工作原理: {'; '.join(sci_data['mechanism'][:3])}\n"
                if sci_data.get('constraints'):
                    constraints_summary += f"必须遵守: {'; '.join(sci_data['constraints'][:2])}"
        except:
            constraints_summary = "科学准确性优先"

        # Stage 2: Generate interactive website
        logger.info("[2/3] Generating interactive website...")
        html_prompt = ai_prompts.anthropic_concept_html_prompt(
            concept_data,
            constraints_summary
        )

        html_response = await self._run_anthropic_call(
            model=self.model,
            messages=[{"role": "user", "content": html_prompt}],
            max_tokens=64000
        )

        html_text = self._extract_text_from_anthropic_response(html_response)
        html_text = html_text.replace("```html", "").replace("```HTML", "").replace("```", "").strip()

        # Extract HTML from response
        html_start = html_text.find('<!DOCTYPE html>')
        if html_start == -1:
            html_start = html_text.find('<html')
        html_end_index = html_text.rfind('</html>')
        html_end = html_end_index + len('</html>') if html_end_index != -1 else -1

        if html_start != -1 and html_end > html_start:
            final_html = html_text[html_start:html_end]
        elif html_start != -1:
            final_html = html_text[html_start:]
        else:
            final_html = html_text

        # Stage 3: Post-processing (inject KaTeX)
        logger.info("[3/3] Injecting formula rendering engine...")
        final_html = self._inject_katex(final_html)

        total_time = time.time() - pipeline_start
        logger.info(f"✅ Generation completed! Total time: {total_time:.1f}s")

        return {
            "html": final_html,
            "scientific_analysis": scientific_analysis,
            "metadata": {
                "title": f"交互式学习 - {concept_data.get('concept_name', '')}",
                "total_time_seconds": round(total_time, 2),
                "ai_provider": f"ChineseProvider-Anthropic-{self.model}"
            }
        }

    def _inject_katex(self, html: str) -> str:
        """Inject KaTeX resources for math rendering."""
        katex_injection = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<script>
document.addEventListener("DOMContentLoaded", function() {
    renderMathInElement(document.body, {
        delimiters: [
            {left: '\\\\[', right: '\\\\]', display: true},
            {left: '\\\\(', right: '\\\\)', display: false},
            {left: '$$', right: '$$', display: true},
            {left: '$', right: '$', display: false}
        ],
        throwOnError: false
    });
});
</script>"""
        if '</head>' in html:
            return html.replace('</head>', katex_injection + '</head>')
        return html + katex_injection

    async def modify_website_ui(self, original_html: str, user_prompt: str, document_context: Dict) -> Dict:
        """Modify website UI using the appropriate backend."""
        modification_start = time.time()
        logger.info(f"🎨 Starting UI modification with ChineseProvider ({self.backend})")

        try:
            if self.backend == "anthropic":
                prompt = ai_prompts.anthropic_modify_ui_prompt(original_html, user_prompt, document_context)
                response = await self._run_anthropic_call(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=64000
                )
                content_text = self._extract_text_from_anthropic_response(response)
            else:
                prompt = ai_prompts.zhipu_modify_ui_prompt(original_html, user_prompt, document_context)
                response = await self._run_zhipu_call(
                    model=self.text_model,
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "enabled"}
                )
                content_text = response.choices[0].message.content if response and response.choices else ""

            if content_text:
                content_text = content_text.replace("```html", "").replace("```HTML", "").replace("```", "").strip()
                html_start = content_text.find('<!DOCTYPE html>')
                if html_start == -1:
                    html_start = content_text.find('<html')
                html_end_index = content_text.rfind('</html>')
                html_end = html_end_index + len('</html>') if html_end_index != -1 else -1

                if html_start != -1 and html_end > html_start:
                    modified_html = content_text[html_start:html_end]
                elif html_start != -1:
                    modified_html = content_text[html_start:]
                else:
                    modified_html = content_text

                total_time = time.time() - modification_start
                logger.info(f"✅ UI modification completed in {total_time:.2f}s")

                return {
                    "status": "success",
                    "modified_html": modified_html
                }

            return {
                "status": "success",
                "modified_html": original_html
            }

        except Exception as e:
            logger.error(f"❌ Error modifying UI: {e}")
            return {
                "status": "error",
                "error": str(e),
                "modified_html": original_html
            }

    async def generate_knowledge_cards(self, analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate prerequisite knowledge cards."""
        logger.info(f"🖼️ Starting knowledge card generation with ChineseProvider ({self.backend})")

        prompt = self._get_knowledge_card_generation_prompt(analysis, user_preferences)

        try:
            if self.backend == "anthropic":
                response = await self._run_anthropic_call(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                content_text = self._extract_text_from_anthropic_response(response)
            else:
                response = await self._run_zhipu_call(
                    model=self.text_model,
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "disabled"}
                )
                content_text = response.choices[0].message.content if response and response.choices else ""

            if content_text:
                json_start = content_text.find('{')
                json_end = content_text.rfind('}') + 1
                if json_start != -1 and json_end > json_start:
                    result = json.loads(content_text[json_start:json_end])
                    if isinstance(result, dict) and "cards" in result:
                        return result

            return {"cards": []}

        except Exception as e:
            logger.error(f"Error generating knowledge cards: {e}")
            return {"cards": []}

    async def customize_template(
        self,
        template,
        content_info: Dict,
        user_preferences: Dict,
        customization_params: Optional[Dict] = None
    ) -> str:
        """Customize a template HTML using LLM."""
        from .template_customizer import TemplateCustomizer
        customizer = TemplateCustomizer(self)
        return await customizer.customize_template(
            template=template,
            content_info=content_info,
            user_preferences=user_preferences,
            customization_params=customization_params
        )

    def get_provider_name(self) -> str:
        """Get the name of the AI provider."""
        if self.backend == "anthropic":
            return f"ChineseProvider-Anthropic ({self.model})"
        if self.backend == "openai_compat":
            if is_innospark_model(self.model):
                return f"Innospark ({resolve_innospark_model(self.model)})"
            if self.model in self.GPT_MODELS:
                return f"UUAPI GPT (OpenAI-compatible)"
            if self.model in self.CLAUDE_MODELS:
                return f"UUAPI Claude (OpenAI-compatible)"
            if self.model in self.GEMINI_MODELS:
                return f"UUAPI Gemini (OpenAI-compatible)"
            if self.model in self.DEEPSEEK_MODELS:
                return f"DeepSeek official (OpenAI-compatible)"
            if self.model in self.KIMI_MODELS:
                return f"Moonshot Kimi (OpenAI-compatible)"
            if self.model in self.ZHIPU_MODELS:
                return f"Zhipu GLM (OpenAI-compatible)"
            if self.model.startswith("minimax-") or self.model.startswith("qwen"):
                return f"SiliconFlow (OpenAI-compatible)"
            return f"OpenAI-compatible transfer"
        return f"ChineseProvider-Zhipu ({self.model})"

    async def _generate_html_content(self, procedural_concepts: List[Dict], analysis: Dict, user_preferences: Dict) -> str:
        """Generate HTML content only."""
        grade_level_int = user_preferences.get('grade_level', 6)
        grade_level = self._map_grade_level_to_string(grade_level_int)
        interests = user_preferences.get('interests', [])
        user_instruction = user_preferences.get('description', '无')

        try:
            if self.backend == "anthropic":
                prompt = ai_prompts.anthropic_procedural_html_prompt(
                    procedural_concepts, grade_level, interests, user_instruction
                )
                response = await self._run_anthropic_call(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=64000
                )
                content_text = self._extract_text_from_anthropic_response(response)
            else:
                prompt = ai_prompts.zhipu_one_shot_html_prompt(
                    procedural_concepts, grade_level, interests, user_instruction
                )
                response = await self._run_zhipu_call(
                    model=self.text_model,
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "enabled"}
                )
                content_text = response.choices[0].message.content if response and response.choices else ""

            if content_text:
                content_text = content_text.replace("```html", "").replace("```HTML", "").replace("```", "").strip()
                html_start = content_text.find('<!DOCTYPE html>')
                if html_start == -1:
                    html_start = content_text.find('<html')
                html_end_index = content_text.rfind('</html>')
                html_end = html_end_index + len('</html>') if html_end_index != -1 else -1

                if html_start != -1 and html_end > html_start:
                    return content_text[html_start:html_end]
                if html_start != -1:
                    return content_text[html_start:]
                return content_text

            return self._generate_fallback_html([], analysis, user_preferences)

        except Exception as e:
            logger.error(f"Error generating HTML content: {e}")
            return self._generate_fallback_html([], analysis, user_preferences)

    async def _generate_metadata_and_interactive(self, procedural_concepts: List[Dict], analysis: Dict, user_preferences: Dict) -> Dict:
        """Generate metadata and interactive elements only."""
        grade_level_int = user_preferences.get('grade_level', 6)
        grade_level = self._map_grade_level_to_string(grade_level_int)
        interests = user_preferences.get('interests', [])

        try:
            if self.backend == "anthropic":
                prompt = ai_prompts.anthropic_procedural_metadata_prompt(
                    procedural_concepts, analysis, grade_level
                )
                response = await self._run_anthropic_call(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                content_text = self._extract_text_from_anthropic_response(response)
            else:
                prompt = ai_prompts.zhipu_procedural_metadata_prompt(
                    procedural_concepts, analysis, grade_level, interests
                )
                response = await self._run_zhipu_call(
                    model=self.text_model,
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "disabled"}
                )
                content_text = response.choices[0].message.content if response and response.choices else ""

            if content_text:
                json_start = content_text.find('{')
                json_end = content_text.rfind('}') + 1
                if json_start != -1 and json_end > json_start:
                    json_str = content_text[json_start:json_end]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        pass

            return self._generate_fallback_metadata([], analysis, user_preferences)

        except Exception as e:
            logger.error(f"Error generating metadata and interactive elements: {e}")
            return self._generate_fallback_metadata([], analysis, user_preferences)

    def _get_content_analysis_prompt(self, grade_level: str, interests: List[str]) -> str:
        """Get the content analysis prompt."""
        if self.backend == "anthropic":
            return ai_prompts.anthropic_content_analysis_prompt(grade_level, interests)
        return ai_prompts.zhipu_content_analysis_prompt(grade_level, interests)

    def _get_knowledge_card_generation_prompt(self, analysis: Dict, user_preferences: Dict) -> str:
        """Get the knowledge card generation prompt."""
        if self.backend == "anthropic":
            return ai_prompts.anthropic_knowledge_card_prompt(analysis)
        return ai_prompts.zhipu_knowledge_card_prompt(analysis)

    def _generate_fallback_analysis(self, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate fallback analysis when API fails."""
        if user_preferences and user_preferences.get("_pdf_text_context", {}).get("excerpt"):
            return _build_text_aware_fallback_analysis(user_preferences, language="zh")
        return {
            "main_topics": ["教育内容"],
            "key_concepts": ["学习", "理解"],
            "learning_objectives": ["理解材料"],
            "prerequisite_knowledge": [],
            "difficulty_level": "中级",
            "target_grade_level": 6,
            "content_structure": [],
            "visual_elements": [],
            "subject_area": "综合教育",
            "procedural_concepts": [
                {
                    "name": "基本学习过程",
                    "description": "理解和掌握基本学习步骤",
                    "key_steps": ["理解概念", "练习应用", "检查掌握程度"],
                    "complexity": "简单"
                }
            ],
            "analysis_diagnostics": {
                "fallback_used": True,
                "fallback_reason": "ai_content_analysis_failed_generic"
            }
        }

    def _generate_fallback_html(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Optional[Dict] = None) -> str:
        """Generate fallback HTML content."""
        user_preferences = user_preferences or {}
        grade_level = self._map_grade_level_to_string(user_preferences.get('grade_level', 6))
        subject = analysis.get('subject_area', '教育')

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>交互式学习 - {subject}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gradient-to-br from-purple-50 to-blue-100 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="mb-8 text-center">
            <h1 class="text-4xl font-bold text-purple-600 mb-2">交互式学习平台</h1>
            <p class="text-gray-600">学科: {subject} | 年级: {grade_level} | AI: ChineseProvider</p>
        </header>
        <main class="bg-white rounded-lg shadow-lg p-6">
            <h2 class="text-2xl font-semibold mb-4">欢迎开始学习</h2>
            <p class="text-gray-600">内容正在生成中，请稍候...</p>
        </main>
    </div>
</body>
</html>"""

    def _generate_fallback_metadata(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate fallback metadata."""
        user_preferences = user_preferences or {}
        grade_level = self._map_grade_level_to_string(user_preferences.get('grade_level', 6))
        subject = analysis.get('subject_area', '教育')

        return {
            "metadata": {
                "title": f"交互式{subject}学习",
                "subject": subject,
                "grade_level": grade_level,
                "estimated_time_minutes": 30,
                "learning_objectives": [f"理解{subject}的基本概念"],
                "ai_provider": self.get_provider_name()
            },
            "interactive_elements": []
        }

    def _generate_fallback_website(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate fallback website."""
        return {
            "html": self._generate_fallback_html(pdf_images, analysis, user_preferences),
            "metadata": self._generate_fallback_metadata(pdf_images, analysis, user_preferences).get("metadata", {}),
            "interactive_elements": []
        }


# ============================================================================
# Modular Pipeline Classes for Scientific-First Generation
# ============================================================================

class ScientificEngine:
    """Stage 1: Scientific fact modeler - responsible for extracting core principles and constraints"""

    def __init__(self, ai_provider):
        self.ai_provider = ai_provider

    async def run(self, concept_data: Dict) -> 'ScientificModel':
        print(f"[1/3] Scientific modeling stage...")
        prompt = self._build_prompt(concept_data)
        
        try:
            response = await self.ai_provider._run_zhipu_call(
                model=self.ai_provider.text_model,
                messages=[{"role": "user", "content": prompt}],
                thinking_params={"type": "enabled"}
            )

            if response and response.choices and response.choices[0].message:
                content_text = response.choices[0].message.content
                
                # Logic preserved: precisely extract JSON portion
                json_start = content_text.find('{')
                json_end = content_text.rfind('}') + 1

                if json_start != -1 and json_end > json_start:
                    json_str = content_text[json_start:json_end]
                    data = json.loads(json_str)
                    model = ScientificModel.from_dict(data)
                    model.raw_analysis = content_text
                    return model

                return self._fallback_extraction(content_text)
        except Exception as e:
            print(f"Modeling failed: {e}")
            return ScientificModel(raw_analysis="Error during modeling")

    def _build_prompt(self, concept_data: Dict) -> str:
        """Build scientific modeling prompt using centralized prompt template."""
        return ai_prompts.anthropic_scientific_prompt(concept_data)

    def _fallback_extraction(self, text: str) -> 'ScientificModel':
        # Original logic preserved: extract from unstructured text
        model = ScientificModel(raw_analysis=text)
        formula_pattern = r'\$\$?([^$]+)\$\$?'
        model.core_formulas = re.findall(formula_pattern, text) or ["未识别到公式"]
        model.constraints = ["科学准确性优先"]
        model.forbidden_errors = ["不得违背基本物理定律"]
        return model


class VisualEngine:
    """Stage 2: Interactive visual builder - responsible for generating HTML/JS"""
    
    def __init__(self, ai_provider):
        self.ai_provider = ai_provider

    async def run(self, concept_data: Dict, scientific_model: 'ScientificModel', user_prefs: Dict) -> str:
        print(f"[2/3] Generating interactive website...")
        
        constraints_summary = self._format_constraints(scientific_model)
        grade_level = self.ai_provider._map_grade_level_to_string(user_prefs.get('grade_level', 10))
        
        prompt = ai_prompts.visual_engine_prompt(
                concept_data,
                constraints_summary,
                grade_level
        )
        print("Generated prompt for visual engine.")
        print("Prompt preview:", prompt[:5000], "...\n")
        print("Using ai provider model:", self.ai_provider.text_model)
        response = await self.ai_provider._run_zhipu_call(
            model=self.ai_provider.text_model,
            messages=[{"role": "user", "content": prompt}],
            thinking_params={"type": "enabled"},
            max_tokens=int(os.getenv("HTML_GENERATION_MAX_TOKENS", "16000"))
        )
        
        if response and response.choices and response.choices[0].message:
            content_text = response.choices[0].message.content
            content_text = content_text.replace("```html", "").replace("```HTML", "").replace("```", "").strip()
            # Logic preserved: precisely extract content between HTML tags
            html_start = content_text.find('<!DOCTYPE html>')
            if html_start == -1: html_start = content_text.find('<html')
            html_end_index = content_text.rfind('</html>')
            html_end = html_end_index + len('</html>') if html_end_index != -1 else -1

            if html_start != -1 and html_end > html_start:
                return content_text[html_start:html_end]
            if html_start != -1:
                return content_text[html_start:]
            return content_text
        return ""

    def _format_constraints(self, model: 'ScientificModel') -> str:
        # Original logic preserved: original constraint formatting method
        lines = []
        if model.core_formulas: lines.append(f"核心公式: {', '.join(model.core_formulas[:3])}")
        if model.mechanism: lines.append(f"工作原理: {'; '.join(model.mechanism[:3])}")
        if model.constraints: lines.append(f"必须遵守: {'; '.join(model.constraints[:2])}")
        # if model.forbidden_errors: lines.append(f"严禁: {', '.join(model.forbidden_errors[:2])}")
        # if model.validation_checks: lines.append(f"验证检查: {'; '.join(model.validation_checks[:2])}")
        return '\n'.join(lines)


class PostProcessor:
    """Stage 3: Post-rendering processor - handles LaTeX and static resources"""
    
    @staticmethod
    def run(html_content: str) -> str:
        print(f"[3/3] Injecting formula rendering engine and cleaning...")

        # 1. Logic preserved: convert LaTeX delimiters and protect scripts
        processed_html = PostProcessor._convert_latex_delimiters(html_content)

        # 2. Logic preserved: inject KaTeX resources
        if 'katex' not in processed_html.lower():
            processed_html = PostProcessor._inject_katex(processed_html)

        return processed_html

    @staticmethod
    def _convert_latex_delimiters(html: str) -> str:
        # Original logic preserved: script tag protection logic
        script_blocks = []
        def protect_script(match):
            script_blocks.append(match.group(0))
            return f"__SCRIPT_BLOCK_{len(script_blocks)-1}__"

        html = re.sub(r'<script[^>]*>.*?</script>', protect_script, html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'\$\$([^$]+)\$\$', r'\\[\1\\]', html)
        html = re.sub(r'\$([^$\n]+?)\$', r'\\(\1\\)', html)

        for i, block in enumerate(script_blocks):
            html = html.replace(f"__SCRIPT_BLOCK_{i}__", block)
        return html

    @staticmethod
    def _inject_katex(html: str) -> str:
        katex_injection = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<script>
document.addEventListener("DOMContentLoaded", function() {
    const katexOptions = {
        delimiters: [
            {left: '\\\\[', right: '\\\\]', display: true},
            {left: '\\\\(', right: '\\\\)', display: false}, 
            {left: '$$', right: '$$', display: true}, 
            {left: '$', right: '$', display: false}
        ],
        throwOnError: false,
        strict: false,
        trust: true
    };

    // 1. define a safe render function with debounce
    let renderTimeout;
    function safeRender() {
        if (renderTimeout) clearTimeout(renderTimeout);
        renderTimeout = setTimeout(() => {
            renderMathInElement(document.body, katexOptions);
        }, 100);
    }

    // 2. Initial render
    renderMathInElement(document.body, katexOptions);

    // 3. Set up MutationObserver to watch for DOM changes
    const observer = new MutationObserver((mutations) => {
        let shouldRender = false;
        mutations.forEach((mutation) => {
            if (mutation.target && 
                mutation.target.className && 
                typeof mutation.target.className === 'string' && 
                mutation.target.className.includes('katex')) {
                return;
            }
            shouldRender = true;
        });
        
        if (shouldRender) {
            safeRender();
        }
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true
    });
    
    // 4. Periodic check for any remaining unrendered LaTeX
    setInterval(() => {
        const text = document.body.innerText;
        if (text.includes('\\\\(') || text.includes('$$')) {
            safeRender();
        }
    }, 2000);
});
</script>"""
        return html.replace('</head>', katex_injection + '</head>') if '</head>' in html else html + katex_injection
    
class ModularGenerationPipeline:
    """Core controller: orchestrates the execution of three stages"""

    def __init__(self, ai_provider):
        self.sci_engine = ScientificEngine(ai_provider)
        self.vis_engine = VisualEngine(ai_provider)
        self.post_processor = PostProcessor()

    async def execute(self, concept_data: Dict, user_preferences: Dict) -> Dict:
        pipeline_start = time.time()

        # Stage 1: Scientific modeling
        scientific_model = await self.sci_engine.run(concept_data)

        # Stage 2: Page generation
        raw_html = await self.vis_engine.run(concept_data, scientific_model, user_preferences)

        # Stage 3: Post-processing
        final_html = self.post_processor.run(raw_html)

        total_time = time.time() - pipeline_start
        print(f"\n✅ Generation completed! Total time: {total_time:.1f}s")

        # Return structure remains the same
        return {
            "html": final_html,
            "scientific_analysis": scientific_model.raw_analysis,
            "metadata": {
                "title": f"交互式学习 - {concept_data.get('concept_name', '')}",
                "total_time_seconds": round(total_time, 2),
                "ai_provider": "Zhipu-Modular-Fast-Refactored"
            }
        }


class AIProcessor:
    """Main AI processor that manages different AI providers."""

    def __init__(self, provider: Optional[AIProvider] = None, provider_config: Optional[Dict] = None,
                 generation_mode: str = "fast"):
        """
        Initialize the AI processor with a specific provider.

        Args:
            provider: AIProvider instance (if None, will create from config)
            provider_config: Configuration for creating a provider
            generation_mode: HTML generation mode - "fast" (~30s) or "heavy" (~3-5min)
        """
        if provider:
            self.provider = provider
        else:
            self.provider = self._create_provider_from_config(provider_config or {})

        self.generation_mode = generation_mode

    def _create_provider_from_config(self, config: Dict) -> AIProvider:
        """Create AI provider from configuration."""
        provider_type = config.get('provider', 'english').lower()

        if provider_type in ['english', 'gemini', 'openai']:
            # Unified English provider supports both Gemini and OpenAI models
            model = config.get('model', 'gpt-4.1')

            # Default models for different provider types
            if provider_type == 'gemini':
                model = config.get('model', 'gemini-3-pro-image-preview')
            elif provider_type == 'openai':
                model = config.get('model', 'gpt-4.1')

            return EnglishProvider(
                api_key=config.get('api_key'),
                model=model
            )
        elif provider_type == 'chinese':
            # Unified Chinese provider supports both Anthropic and Zhipu models
            provider = ChineseProvider(
                api_key=config.get('api_key'),
                model=config.get('model', 'glm-4.6v'),
                base_url=config.get('base_url')
            )
            # Set text model if specified
            if config.get('text_model'):
                provider.text_model = config.get('text_model')
            return provider
        else:
            raise ValueError(f"Unsupported provider: {provider_type}. Supported providers: english (supports gemini/openai), chinese (supports anthropic/zhipu)")

    async def process_pdf_complete(self, pdf_path: str, user_preferences: Optional[Dict] = None) -> Dict:
        """
        Complete PDF processing pipeline using the configured AI provider.
        """
        processing_start = time.time()
        user_preferences = user_preferences or {}
        include_prerequisites = user_preferences.get("include_prerequisites", True)
        include_exercises = user_preferences.get("include_exercises", True)

        # Update Zhipu text model if specified in user preferences
        if hasattr(self.provider, 'text_model') and "zhipu_text_model" in user_preferences:
            self.provider.text_model = user_preferences["zhipu_text_model"]
            logger.info(f"🔄 Using Zhipu text model from user preferences: {self.provider.text_model}")

        logger.info(f"🚀 Starting PDF processing pipeline with {self.provider.get_provider_name()}")

        # Step 1: Get PDF metadata
        metadata_start = time.time()
        metadata = self.get_pdf_metadata(pdf_path)
        metadata_time = time.time() - metadata_start
        logger.info(f"📋 PDF metadata extracted in {metadata_time:.2f}s - Pages: {metadata.get('page_count', 'unknown')}")

        # Step 1.5: Extract text so English PDFs and non-vision models still get real source content.
        text_start = time.time()
        pdf_text_context = self.extract_pdf_text_context(pdf_path)
        pdf_text_context = await enhance_pdf_text_context_with_mineru(
            pdf_path,
            pdf_text_context,
            logger=logger,
        )
        text_time = time.time() - text_start
        logger.info(
            "📝 PDF text extracted in %.2fs - source: %s, pages with text: %s, chars: %s",
            text_time,
            pdf_text_context.get("source", "unknown"),
            pdf_text_context.get("pages_with_text", 0),
            pdf_text_context.get("total_chars", 0)
        )

        analysis_preferences = {
            **user_preferences,
            "_pdf_text_context": pdf_text_context
        }

        # Step 2: Convert PDF to images
        logger.info(f"🔄 Converting PDF to images...")
        conversion_start = time.time()
        visual_page_limit = get_pdf_image_conversion_page_limit()
        pdf_images = self.convert_pdf_to_images(pdf_path, max_pages=visual_page_limit)
        conversion_time = time.time() - conversion_start
        logger.info(f"🖼️ PDF to images conversion completed in {conversion_time:.2f}s - Generated {len(pdf_images)} images")

        # Step 3: Analyze content with AI provider
        logger.info(f"🧠 Starting AI content analysis...")
        analysis_start = time.time()
        content_analysis = await self.analyze_content_with_ai(pdf_images, analysis_preferences)
        if not include_prerequisites and "prerequisite_knowledge" in content_analysis:
            content_analysis = {**content_analysis}
            content_analysis.pop("prerequisite_knowledge", None)
        analysis_time = time.time() - analysis_start
        logger.info(f"📊 Content analysis completed in {analysis_time:.2f}s")

        # Step 4: Generate knowledge cards 
        if include_prerequisites:
            logger.info("🖼️ Starting knowledge card generation...")
            knowledge_cards_start = time.time()
            knowledge_cards = await self.generate_knowledge_cards(content_analysis, user_preferences)
            knowledge_cards_time = time.time() - knowledge_cards_start
            logger.info(f"🖼️ Knowledge card generation completed in {knowledge_cards_time:.2f}s")
            logger.info(f"{knowledge_cards}")

            cards = knowledge_cards.get("cards") if isinstance(knowledge_cards, dict) else None
            cards = cards if isinstance(cards, list) else []
            prerequisite_items = content_analysis.get("prerequisite_knowledge")
            prerequisite_items = prerequisite_items if isinstance(prerequisite_items, list) else []

            if not prerequisite_items and cards:
                prerequisite_items = [card.get("title") for card in cards if card.get("title")]

            if prerequisite_items:
                card_by_title = {
                    card.get("title"): card
                    for card in cards
                    if isinstance(card, dict) and card.get("title")
                }
                aligned_cards = []
                for index, item in enumerate(prerequisite_items):
                    card = card_by_title.get(item)
                    if not card and index < len(cards):
                        candidate = cards[index]
                        if isinstance(candidate, dict):
                            card = {**candidate, "title": item}
                    if not card:
                        card = {
                            "title": item,
                            "summary_md": f"**{item}**\n\n请复习该知识点的定义、关键概念和常见题型。"
                        }
                    aligned_cards.append(card)
                knowledge_cards = {"cards": aligned_cards}
                content_analysis = {**content_analysis, "prerequisite_knowledge": prerequisite_items}
            else:
                knowledge_cards = {"cards": []}
        else:
            knowledge_cards = {}

        # Step 5: Generate interactive website
        logger.info(f"🎨 Starting website generation...")
        website_start = time.time()
        website_content = await self.generate_interactive_website(
            pdf_images,
            content_analysis,
            user_preferences
        )
        if not include_exercises:
            website_content["interactive_elements"] = []
        website_time = time.time() - website_start
        logger.info(f"🌐 Website generation completed in {website_time:.2f}s")

        total_processing_time = time.time() - processing_start
        logger.info(f"✅ Complete PDF processing finished in {total_processing_time:.2f}s total")

        return {
            "status": "success",
            "metadata": metadata,
            "analysis": content_analysis,
            "knowledge_cards": knowledge_cards,
            "website": website_content,
            "processing_info": {
                "total_pages": metadata.get("page_count", len(pdf_images)),
                "pages_processed": len(pdf_images),
                "images_generated": len(pdf_images),
                "visual_page_limit": visual_page_limit,
                "analysis_image_page_limit": get_pdf_analysis_image_page_limit(),
                "website_image_page_limit": get_pdf_website_image_page_limit(),
                "pdf_text_chars": pdf_text_context.get("total_chars", 0),
                "pdf_text_pages": pdf_text_context.get("pages_with_text", 0),
                "pdf_text_included_pages": pdf_text_context.get("included_pages", 0),
                "pdf_text_included_chars": pdf_text_context.get("included_chars", 0),
                "pdf_text_source": pdf_text_context.get("source", "unknown"),
                "mineru_enhancement": pdf_text_context.get("mineru_enhancement", {}),
                "analysis_diagnostics": content_analysis.get("analysis_diagnostics", {}),
                "processing_method": f"ai-vision-{self.provider.get_provider_name().lower()}",
                "ai_provider": self.provider.get_provider_name()
            }
        }

        # except Exception as e:
        #     return {
        #         "status": "error",
        #         "error": str(e),
        #         "metadata": {},
        #         "analysis": {},
        #         "website": None,
        #         "processing_info": {
        #             "error_occurred": True,
        #             "ai_provider": self.provider.get_provider_name()
        #         }
        #     }

    async def analyze_content_with_ai(self, pdf_images: List[Dict], user_preferences: Optional[Dict] = None) -> Dict:
        """Analyze content using the configured AI provider."""
        return await self.provider.analyze_content(pdf_images, user_preferences or {})

    async def generate_interactive_website(self, pdf_images: List[Dict], analysis: Dict, user_preferences: Optional[Dict] = None, mode: Optional[str] = None) -> Dict:
        """
        Generate website using the configured AI provider and specified mode.

        Args:
            pdf_images: List of PDF page images
            analysis: Content analysis
            user_preferences: User preferences for personalization
            mode: Generation mode - "fast" or "heavy" (overrides instance setting)

        Returns:
            Dict with generated HTML and metadata
        """
        mode = mode or self.generation_mode
        user_preferences = user_preferences or {}

        # Respect user's language preference if provided, otherwise default to Chinese
        if 'language' not in user_preferences:
            user_preferences['language'] = 'zh-CN'
            user_preferences['output_language'] = '简体中文'

        # Import generators here to avoid circular imports
        from .html_generation import FastGenerator, HeavyGenerator

        try:
            if mode == "heavy":
                logger.info(f"🎨 Using Heavy Mode (2-stage pipeline) for generation")
                generator = HeavyGenerator(self.provider)
            else:
                logger.info(f"🎨 Using Fast Mode (one-shot) for generation")
                generator = FastGenerator(self.provider)

            result = await generator.generate(pdf_images, analysis, user_preferences)
            result["mode_used"] = mode
            return result

        except Exception as e:
            logger.error(f"❌ Generator failed: {e}, falling back to provider method")
            # Fallback to original provider method
            return await self.provider.generate_website(pdf_images, analysis, user_preferences)

    async def modify_website_ui(self, original_html: str, user_prompt: str, document_context: Dict) -> Dict:
        """Modify website UI using the configured AI provider."""
        return await self.provider.modify_website_ui(original_html, user_prompt, document_context)

    async def generate_knowledge_cards(self, analysis: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate prerequisite knowledge cards using the configured AI provider."""
        return await self.provider.generate_knowledge_cards(analysis, user_preferences or {})

    async def process_concept_complete(self, concept_data: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """
        Process concept input and generate interactive learning website.
        This is an alternative to PDF processing that works directly with text-based concept descriptions.
        """
        processing_start = time.time()
        user_preferences = user_preferences or {}
        include_exercises = user_preferences.get("include_exercises", True)
        logger.info(f"🚀 Starting concept processing pipeline with {self.provider.get_provider_name()}")
        logger.info(f"📝 Concept: {concept_data.get('subject')} - {concept_data.get('concept_name')}")

        try:
            # Generate interactive website directly from concept
            logger.info(f"🎨 Starting website generation from concept...")
            website_start = time.time()
            website_content = await self.generate_website_from_concept(
                concept_data,
                user_preferences
            )
            if not include_exercises:
                website_content["interactive_elements"] = []
            website_time = time.time() - website_start
            logger.info(f"🌐 Website generation completed in {website_time:.2f}s")

            total_processing_time = time.time() - processing_start
            logger.info(f"✅ Complete concept processing finished in {total_processing_time:.2f}s total")

            # Create metadata for concept-based document
            metadata = {
                "title": concept_data.get('concept_name', 'Concept Learning'),
                "subject": concept_data.get('subject', ''),
                "concept_overview": concept_data.get('concept_overview', ''),
                "page_count": 1,
                "source_type": "concept_input"
            }

            # Create analysis from concept data
            analysis = {
                "main_topics": [concept_data.get('concept_name', '')],
                "key_concepts": concept_data.get('mastery_points', []).split('\n') if concept_data.get('mastery_points') else [],
                "learning_objectives": concept_data.get('mastery_points', []).split('\n') if concept_data.get('mastery_points') else [],
                "subject_area": concept_data.get('subject', ''),
                "difficulty_level": "intermediate"
            }

            return {
                "status": "success",
                "metadata": metadata,
                "analysis": analysis,
                "website": website_content,
                "processing_info": {
                    "total_pages": 1,
                    "pages_processed": 1,
                    "processing_method": f"concept-ai-{self.provider.get_provider_name().lower()}",
                    "ai_provider": self.provider.get_provider_name(),
                    "concept_data": concept_data
                }
            }

        except Exception as e:
            logger.error(f"❌ Error processing concept: {str(e)}")
            error_text = str(e)
            if "524" in error_text or "timeout" in error_text.lower() or "timed out" in error_text.lower():
                logger.warning("⚠️ Concept generation timed out upstream; using local interactive fallback")
                fallback_analysis = self._build_concept_fallback_analysis(concept_data)
                fallback_website = self._build_local_interactive_website(
                    fallback_analysis,
                    user_preferences,
                    fallback_reason=error_text
                )
                total_processing_time = time.time() - processing_start
                return {
                    "status": "success",
                    "metadata": {
                        "title": concept_data.get('concept_name', 'Concept Learning'),
                        "subject": concept_data.get('subject', ''),
                        "concept_overview": concept_data.get('concept_overview', ''),
                        "page_count": 1,
                        "source_type": "concept_input",
                        "fallback_used": True
                    },
                    "analysis": fallback_analysis,
                    "website": fallback_website,
                    "processing_info": {
                        "total_pages": 1,
                        "pages_processed": 1,
                        "processing_method": "concept-local-timeout-fallback",
                        "ai_provider": self.provider.get_provider_name(),
                        "concept_data": concept_data,
                        "upstream_error": error_text,
                        "total_time_seconds": round(total_processing_time, 2)
                    }
                }
            return {
                "status": "error",
                "error": error_text,
                "metadata": {},
                "analysis": {},
                "website": None,
                "processing_info": {
                    "error_occurred": True,
                    "ai_provider": self.provider.get_provider_name()
                }
            }

    def _build_concept_fallback_analysis(self, concept_data: Dict) -> Dict:
        """Build analysis from concept input when upstream generation times out."""
        mastery_points = [
            item.strip(" -\t\r")
            for item in (concept_data.get('mastery_points') or '').split('\n')
            if item.strip()
        ]
        design_points = [
            item.strip(" -\t\r")
            for item in (concept_data.get('design_idea') or '').split('\n')
            if item.strip()
        ]
        concept_name = concept_data.get('concept_name') or '知识点'
        overview = concept_data.get('concept_overview') or ''

        procedural_steps = mastery_points[:6] or design_points[:6] or ["理解概念", "观察可视化", "完成练习反馈"]
        return {
            "main_topics": [concept_name],
            "key_concepts": mastery_points or [concept_name],
            "learning_objectives": mastery_points or [f"理解{concept_name}的核心思想"],
            "prerequisite_knowledge": [],
            "difficulty_level": "中级",
            "target_grade_level": concept_data.get("grade_level", 10),
            "content_structure": [
                {
                    "title": concept_name,
                    "page_start": 1,
                    "page_end": 1,
                    "topics": mastery_points[:5] or [concept_name]
                }
            ],
            "visual_elements": design_points,
            "subject_area": concept_data.get('subject') or '综合教育',
            "procedural_concepts": [
                {
                    "name": concept_name,
                    "description": overview or f"围绕{concept_name}建立概念直觉、操作步骤和即时反馈。",
                    "key_steps": procedural_steps,
                    "complexity": "中等"
                }
            ],
            "analysis_diagnostics": {
                "fallback_used": True,
                "fallback_reason": "upstream_generation_timeout"
            }
        }

    def _build_local_interactive_website(self, analysis: Dict, user_preferences: Dict, fallback_reason: str = "") -> Dict:
        """Generate a local deterministic interactive page from analysis."""
        from .html_generation.heavy_generator import HeavyGenerator

        html = HeavyGenerator(self.provider)._emergency_template(analysis, user_preferences)
        return {
            "html": html,
            "metadata": {
                "title": analysis.get("main_topics", ["学习页"])[0],
                "subject": analysis.get("subject_area", "综合教育"),
                "fallback_used": True,
                "fallback_reason": "upstream_generation_timeout",
            },
            "interactive_elements": [],
            "generation_info": {
                "mode": "local_fallback",
                "fallback_used": True,
                "upstream_error": fallback_reason[:1000],
            }
        }

    async def generate_website_from_concept(self, concept_data: Dict, user_preferences: Optional[Dict] = None) -> Dict:
        """Generate website from concept using the configured AI provider."""
        user_preferences = user_preferences or {}

        # Update Zhipu text model if specified in user preferences
        if hasattr(self.provider, 'text_model') and "zhipu_text_model" in user_preferences:
            self.provider.text_model = user_preferences["zhipu_text_model"]
            logger.info(f"🔄 Using Zhipu text model from user preferences: {self.provider.text_model}")

        return await self.provider.generate_website_from_concept(concept_data, user_preferences)

    def get_pdf_metadata(self, pdf_path: str) -> Dict:
        """Extract basic metadata from PDF file."""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                metadata = {
                    "title": pdf_reader.metadata.get('/Title', 'Unknown'),
                    "author": pdf_reader.metadata.get('/Author', 'Unknown'),
                    "subject": pdf_reader.metadata.get('/Subject', ''),
                    "creator": pdf_reader.metadata.get('/Creator', ''),
                    "producer": pdf_reader.metadata.get('/Producer', ''),
                    "creation_date": str(pdf_reader.metadata.get('/CreationDate', '')),
                    "modification_date": str(pdf_reader.metadata.get('/ModDate', '')),
                    "page_count": len(pdf_reader.pages)
                }
                return metadata
        except Exception as e:
            print(f"Error extracting PDF metadata: {e}")
            return {"page_count": 0, "title": "Unknown"}

    def extract_pdf_text_context(self, pdf_path: str, max_chars: Optional[int] = None) -> Dict:
        """Extract readable text from a PDF for content analysis prompts."""
        max_chars = max_chars if max_chars is not None else get_pdf_text_context_max_chars()
        page_char_limit = get_pdf_text_page_char_limit()
        pages = []
        source = "pymupdf" if PYMUPDF_AVAILABLE else "pypdf2"

        try:
            if PYMUPDF_AVAILABLE:
                doc = fitz.open(pdf_path)
                try:
                    for page_num in range(len(doc)):
                        text = _compact_pdf_text(doc[page_num].get_text("text"))
                        if text:
                            pages.append({"page": page_num + 1, "text": text})
                finally:
                    doc.close()
            else:
                with open(pdf_path, 'rb') as file:
                    pdf_reader = PyPDF2.PdfReader(file)
                    for page_num, page in enumerate(pdf_reader.pages):
                        text = _compact_pdf_text(page.extract_text() or "")
                        if text:
                            pages.append({"page": page_num + 1, "text": text})
        except Exception as e:
            logger.warning(f"⚠️ PDF text extraction failed: {e}")

        total_chars = sum(len(page["text"]) for page in pages)
        excerpts = []
        remaining = max_chars
        included_chars = 0
        approx_block_chars = page_char_limit + 80
        max_page_blocks = max(1, max_chars // approx_block_chars)
        selection_strategy = "all_pages"
        selected_pages = pages

        if len(pages) > max_page_blocks:
            selection_strategy = "balanced_page_sample"
            if max_page_blocks == 1:
                selected_indices = [0]
            else:
                step = (len(pages) - 1) / (max_page_blocks - 1)
                selected_indices = sorted({round(index * step) for index in range(max_page_blocks)})
            selected_pages = [pages[index] for index in selected_indices]

        for page in selected_pages:
            if remaining <= 0:
                break
            page_text = page["text"]
            if len(page_text) > page_char_limit:
                page_text = page_text[:page_char_limit].rstrip() + "\n...[page text truncated]"
            block = f"[Page {page['page']}]\n{page_text}"
            if len(block) > remaining:
                block = block[:remaining].rstrip() + "\n...[context truncated]"
            excerpts.append(block)
            included_chars += len(block)
            remaining -= len(block) + 2

        return {
            "source": source,
            "pages_with_text": len(pages),
            "total_chars": total_chars,
            "included_pages": len(excerpts),
            "included_chars": included_chars,
            "max_chars": max_chars,
            "page_char_limit": page_char_limit,
            "selection_strategy": selection_strategy,
            "excerpt": "\n\n".join(excerpts),
        }

    def convert_pdf_to_images(self, pdf_path: str, max_pages: Optional[int] = None) -> List[Dict]:
        """Convert PDF pages to images using PyMuPDF."""
        try:
            if PYMUPDF_AVAILABLE:
                # Use PyMuPDF (no external dependencies)
                try:
                    return self._convert_with_pymupdf(pdf_path, max_pages=max_pages)
                except Exception as pymupdf_error:
                    print(f"PyMuPDF failed ({pymupdf_error}), using text fallback")
                    return self._create_text_based_representation(pdf_path, max_pages=max_pages)
            else:
                # PyMuPDF not available - fallback
                print("PyMuPDF not available, using fallback method")
                return self._create_text_based_representation(pdf_path, max_pages=max_pages)

        except Exception as e:
            raise Exception(f"Error converting PDF to images: {str(e)}")

    def _convert_with_pymupdf(self, pdf_path: str, max_pages: Optional[int] = None) -> List[Dict]:
        """Convert PDF to images using PyMuPDF (preferred method)."""
        try:
            doc_open_start = time.time()
            doc = fitz.open(pdf_path)
            doc_open_time = time.time() - doc_open_start
            logger.info(f"📖 PDF opened with PyMuPDF in {doc_open_time:.2f}s - {len(doc)} pages")

            processed_images = []
            processing_start = time.time()
            page_count = len(doc)
            pages_to_convert = min(page_count, max_pages or page_count)
            if pages_to_convert < page_count:
                logger.info("📄 Converting first %s of %s PDF pages to images", pages_to_convert, page_count)

            for page_num in range(pages_to_convert):
                page_start = time.time()
                page = doc[page_num]

                # Render page to pixmap with good quality
                render_start = time.time()
                pix = page.get_pixmap(dpi=200)
                render_time = time.time() - render_start

                # Convert pixmap to PIL Image
                convert_start = time.time()
                img_data = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_data))
                convert_time = time.time() - convert_start

                # Convert PIL image to base64
                encode_start = time.time()
                buffered = io.BytesIO()
                image.save(buffered, format="PNG", quality=90)
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                encode_time = time.time() - encode_start

                processed_images.append({
                    "page": page_num + 1,
                    "image_data": img_base64,
                    "format": "PNG",
                    "width": pix.width,
                    "height": pix.height
                })

            doc.close()
            return processed_images

        except Exception as e:
            logger.error(f"❌ PyMuPDF conversion failed: {str(e)}")
            raise Exception(f"PyMuPDF conversion failed: {str(e)}")

  
    def _create_text_based_representation(self, pdf_path: str, max_pages: Optional[int] = None) -> List[Dict]:
        """Create a text-based representation of PDF pages when image conversion is not available."""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                processed_images = []

                pages_to_convert = min(len(pdf_reader.pages), max_pages or len(pdf_reader.pages))
                if pages_to_convert < len(pdf_reader.pages):
                    logger.info("📄 Creating text-based images for first %s of %s PDF pages", pages_to_convert, len(pdf_reader.pages))

                for page_num in range(pages_to_convert):
                    page = pdf_reader.pages[page_num]
                    text = page.extract_text()

                    # Create a simple text-based "image" representation
                    img = Image.new('RGB', (800, 1000), color='white')
                    from PIL import ImageDraw, ImageFont

                    draw = ImageDraw.Draw(img)

                    try:
                        font = ImageFont.load_default()
                    except:
                        font = None

                    # Add title
                    draw.text((50, 50), f"Page {page_num + 1}", fill='black', font=font)

                    # Add extracted text (truncated)
                    y_offset = 100
                    for line in text.split('\n')[:30]:  # Limit lines
                        if y_offset < 900:  # Leave margin at bottom
                            # Truncate long lines
                            if len(line) > 80:
                                line = line[:77] + "..."
                            draw.text((50, y_offset), line, fill='black', font=font)
                            y_offset += 30

                    # Convert to base64
                    buffered = io.BytesIO()
                    img.save(buffered, format="JPEG", quality=85)
                    img_base64 = base64.b64encode(buffered.getvalue()).decode()

                    processed_images.append({
                        "page": page_num + 1,
                        "image_data": img_base64,
                        "format": "JPEG",
                        "width": 800,
                        "height": 1000,
                        "is_fallback": True
                    })

                return processed_images

        except Exception as e:
            raise Exception(f"Error creating text-based representation: {str(e)}")

    async def search_templates_for_user(
        self,
        content_info: Dict,
        workflow_type: str,
        db_session_factory,
        max_results: int = 5
    ) -> Dict:
        """
        Step 1: Search templates for user selection.

        Returns template options that user can review and choose from.
        """
        from .template_registry import get_template_registry

        registry = get_template_registry(db_session_factory)

        template_options = registry.search_templates_for_user_selection(
            content_info=content_info,
            workflow_type=workflow_type,
            max_results=max_results
        )

        return {
            "status": "success",
            "workflow_type": workflow_type,
            "templates_found": len(template_options),
            "template_options": template_options
        }

    async def generate_with_selected_template(
        self,
        template_id: str,
        content_info: Dict,
        user_preferences: Dict,
        workflow_type: str,
        db_session_factory,
        customization_params: Optional[Dict] = None
    ) -> Dict:
        """
        Step 2: Generate using user-selected template.
        """
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"=" * 80)
        logger.info(f"AI_PROCESSOR: generate_with_selected_template called")
        logger.info(f"Template ID: {template_id}")
        logger.info(f"Workflow type: {workflow_type}")
        logger.info(f"Content info: {content_info}")
        logger.info(f"User preferences: {user_preferences}")
        logger.info(f"=" * 80)

        from .template_registry import get_template_registry

        logger.info(f"Getting template registry...")
        registry = get_template_registry(db_session_factory)

        logger.info(f"Getting template by ID: {template_id}")
        template = registry.get_template_by_id(template_id)

        if not template:
            logger.error(f"Template not found: {template_id}")
            return {"status": "error", "error": f"Template not found: {template_id}"}

        logger.info(f"Template found: {template.display_name}")
        logger.info(f"Calling provider.customize_template...")

        # Customize template using LLM
        customized_html = await self.provider.customize_template(
            template=template,
            content_info=content_info,
            user_preferences=user_preferences,
            customization_params=customization_params
        )

        logger.info(f"Customization completed. HTML length: {len(customized_html) if customized_html else 0}")

        # Update template usage count
        logger.info(f"Updating template usage count...")
        registry.update_template_usage(template_id)
        logger.info(f"Template usage count updated")

        logger.info(f"Returning success response")

        return {
            "status": "success",
            "html": customized_html,
            "metadata": {
                "template_used": template_id,
                "template_name": template.display_name,
                "generation_method": "template_based"
            }
        }

    async def generate_without_template(
        self,
        content_info: Dict,
        user_preferences: Dict,
        workflow_type: str
    ) -> Dict:
        """
        Fallback: Generate without template (pure AI).
        """
        if workflow_type == 'website_pdf':
            # This would need images and analysis from content_info
            return await self.provider.generate_website(
                images=content_info.get('images', []),
                analysis=content_info.get('analysis', {}),
                user_preferences=user_preferences
            )
        elif workflow_type == 'website_concept':
            return await self.provider.generate_website_from_concept(
                concept_data=content_info,
                user_preferences=user_preferences
            )
        else:
            return {"status": "error", "error": f"Unsupported workflow type: {workflow_type}"}

    def get_provider_name(self) -> str:
        """Get the name of the current AI provider."""
        return self.provider.get_provider_name()

    def get_provider_info(self) -> Dict:
        """Get information about the current AI provider."""
        return {
            "provider": self.get_provider_name(),
            "available_providers": self._get_available_providers()
        }

    def _get_available_providers(self) -> List[str]:
        """Get list of available AI providers."""
        available = ["english"]  # English provider is always available (uses httpx)
        if ZHIPU_AVAILABLE:
            available.append("zhipu")
        if ANTHROPIC_AVAILABLE:
            available.append("anthropic")
        if OPENAI_SDK_AVAILABLE:
            available.append("openai_compat")
        return available


# Global AI processor instance
_ai_processor_instance = None

def get_ai_processor():
    """
    Get or create the global AI processor instance.

    This is a convenience function that creates a singleton AI processor
    using environment variables for configuration.

    Returns:
        AIProcessor: The global AI processor instance

    Raises:
        ValueError: If no API keys are configured
    """
    global _ai_processor_instance

    if _ai_processor_instance is None:
        # Determine which provider to use based on available API keys
        provider_config = {}

        # Check for Zhipu API key (preferred for Chinese content and PPT processing)
        zhipu_api_key = os.getenv("ZHIPU_API_KEY")
        if zhipu_api_key:
            provider_config = {
                'provider': 'chinese',
                'api_key': zhipu_api_key,
                'model': 'glm-4.6v',
                'text_model': os.getenv("ZHIPU_TEXT_MODEL", "glm-4.7")
            }
            logger.info("Using Zhipu AI provider")
        else:
            # Check for transfer station API key (supports Claude/GPT/DeepSeek/Gemini/Kimi)
            transfer_api_key = os.getenv("TRANSFER_API_KEY")
            anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
            api_key = transfer_api_key or anthropic_api_key
            if api_key:
                model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
                transfer_url = os.getenv("TRANSFER_BASE_URL", "")
                base_url = transfer_url.replace("/v1", "") if transfer_url else os.getenv("ANTHROPIC_BASE_URL")
                provider_config = {
                    'provider': 'chinese',
                    'api_key': api_key,
                    'base_url': base_url,
                    'model': model
                }
                logger.info(f"Using Anthropic provider via transfer station with model {model}")
            else:
                # Check for English API keys
                english_api_key = (
                    os.getenv("ENGLISH_API_KEY") or
                    os.getenv("MIDDLE_TRANSFER_API_KEY") or
                    os.getenv("TRANSFER_API_KEY") or
                    os.getenv("OPENAI_API_KEY")
                )
                if english_api_key:
                    model = os.getenv("ENGLISH_MODEL", "gpt-5.4")
                    provider_config = {
                        'provider': 'english',
                        'api_key': english_api_key,
                        'model': model
                    }
                    logger.info(f"Using English provider with model {model}")
                else:
                    raise ValueError(
                        "No AI provider API key found. Please set one of:\n"
                        "- ZHIPU_API_KEY (recommended)\n"
                        "- TRANSFER_API_KEY (for transfer station: Claude/GPT/DeepSeek/Gemini/Kimi)\n"
                        "- ENGLISH_API_KEY\n"
                        "- OPENAI_API_KEY"
                    )

        _ai_processor_instance = AIProcessor(provider_config=provider_config)

    return _ai_processor_instance
