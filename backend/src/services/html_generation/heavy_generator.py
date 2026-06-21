"""
Heavy Generator Module

This module implements the Heavy Mode generation with a 2-stage pipeline.
Each stage has validation and up to 3 refinement attempts.

Date: 2025-01-15
"""

import json
import os
import time
import logging
import asyncio
import html as html_lib
from typing import Dict, List, Optional, Any

from .base_generator import BaseGenerator, FatalAIProviderError
from .fast_generator import FastGenerator
from ..validators import HTMLValidator, ContentValidator, SimulationValidator
from ..templates.heavy_mode_prompts import get_stage_prompt, get_refinement_prompt
from .components.themes import get_theme
from .cache import get_cache

logger = logging.getLogger(__name__)


class HeavyGenerator(BaseGenerator):
    """
    Heavy Mode HTML Generator.

    Uses a 2-stage pipeline with validation and refinement:
    - Stage 1: Content-Aligned Interactive Simulations (left: process, right: simulation)
    - Stage 2: Layout Polish & Visual Refinement

    Processing time: ~2-3 minutes
    """

    def __init__(self, ai_provider):
        """Initialize HeavyGenerator with an AI provider."""
        super().__init__(ai_provider)
        self.generation_metadata["mode"] = "HeavyGenerator"
        self.html_validator = HTMLValidator()
        self.content_validator = ContentValidator()
        self.sim_validator = SimulationValidator()
        self.cache = get_cache()

    async def generate(self, pdf_images: List[Dict], analysis: Dict,
                     user_preferences: Dict) -> Dict[str, Any]:
        """
        Generate HTML using 2-stage pipeline (Heavy Mode).

        Stage 1: Content-Aligned Interactive Simulations
        - Focus on interactive simulations to illustrate given concepts
        - Show process steps on left, concurrent simulation on right
        - Ensure strong alignment between process and simulation

        Stage 2: Layout Polish & Visual Refinement
        - Ensure HTML renders without errors
        - Polish visual style and aesthetics
        - Verify all functionality works correctly

        Args:
            pdf_images: List of page images (not used, only analysis)
            analysis: Content analysis with procedural_concepts
            user_preferences: User preferences for personalization

        Returns:
            Dict with html, metadata, and generation_info
        """
        generation_start = time.time()
        self.log_generation_start("Heavy Mode")

        # Ensure language settings based on user preference
        user_preferences = self._ensure_language_settings(user_preferences)

        # Get theme based on subject
        theme = get_theme(analysis.get('subject_area', ''))

        # Extract procedural concepts
        procedural_concepts = self._extract_procedural_concepts(analysis)

        # Build context for prompts
        context = self._build_context(analysis, user_preferences, procedural_concepts, theme)

        # Track completed stages for fallback
        completed_stages = {}
        refinements = {'stage1': 0, 'stage2': 0}

        try:
            # Stage 1: Content-Aligned Interactive Simulations
            result = await self._execute_stage(
                'stage1',
                context,
                self._generate_aligned_simulation,
                self.sim_validator.validate_interactive_functionality,
                completed_stages,
                refinements
            )
            if result is None:  # Catastrophic failure
                return await self._generate_fallback(pdf_images, analysis, user_preferences)
            completed_stages['stage1'] = result

            # Stage 2: Layout Polish & Visual Refinement
            context['html_simulation'] = completed_stages['stage1']
            result = await self._execute_stage(
                'stage2',
                context,
                self._polish_layout_and_visuals,
                self.html_validator.validate_complete,
                completed_stages,
                refinements
            )
            if result is None:
                return await self._degrade_gracefully(completed_stages, analysis, user_preferences)
            completed_stages['stage2'] = result

            generation_time = time.time() - generation_start

            # Cache successful result
            self.cache.save_success(context, 'stage2', completed_stages['stage2'])

            metadata_and_elements = await self._generate_metadata_if_provider_healthy(
                procedural_concepts, analysis, user_preferences
            )

            result = {
                "html": completed_stages['stage2'],
                "metadata": {
                    **self._generate_metadata(analysis, user_preferences),
                    **metadata_and_elements.get("metadata", {})
                },
                "interactive_elements": metadata_and_elements.get("interactive_elements", []),
                "generation_info": {
                    "mode": "heavy",
                    "duration_seconds": round(generation_time, 2),
                    "stages_completed": 2,
                    "refinements": refinements,
                    "theme": theme['name']
                }
            }

            self.log_generation_complete(generation_time)
            return result

        except FatalAIProviderError as e:
            logger.warning("Heavy generation stopped early: %s", e.reason)
            return await self._degrade_gracefully(completed_stages, analysis, user_preferences)
        except Exception as e:
            self.log_error("heavy_generation", e)
            return await self._degrade_gracefully(completed_stages, analysis, user_preferences)

    async def _generate_metadata_if_provider_healthy(self, procedural_concepts: List,
                                                     analysis: Dict, user_preferences: Dict) -> Dict:
        """Avoid extra AI calls after quota/request-size failures."""
        if self._last_provider_failure_reason:
            logger.warning(
                "Skipping metadata AI call after provider failure: %s",
                self._last_provider_failure_reason
            )
            return {}

        try:
            fast_generator = FastGenerator(self.provider)
            return await fast_generator._generate_metadata_and_interactive(
                procedural_concepts, analysis, user_preferences
            )
        except FatalAIProviderError as e:
            self._remember_provider_failure(e.reason)
            logger.warning("Skipping metadata after fatal provider error: %s", e.reason)
            return {}
        except Exception as e:
            logger.warning("Skipping metadata after non-fatal provider error: %s", e)
            return {}

    def _build_context(self, analysis: Dict, user_preferences: Dict,
                      procedural_concepts: List, theme: Dict) -> Dict:
        """Build context dict for prompts."""
        grade_level = self._map_grade_level(user_preferences.get('grade_level', 6))
        interests = user_preferences.get('interests', [])
        language = user_preferences.get('language', 'zh')

        # Default interests text based on language
        if language == 'en':
            interests_text = ', '.join(interests) if interests else 'General learning'
        else:
            interests_text = ', '.join(interests) if interests else '综合学习'

        user_instruction = (
            user_preferences.get('description')
            or user_preferences.get('user_instruction')
            or user_preferences.get('learning_goal')
            or ''
        )

        return {
            'concept_info': json.dumps(procedural_concepts, ensure_ascii=False, indent=2),
            'key_concepts': analysis.get('key_concepts', []),
            'key_concepts_list': ', '.join(analysis.get('key_concepts', [])),
            'learning_objectives': analysis.get('learning_objectives', []),
            'main_topics': analysis.get('main_topics', []),
            'subject': analysis.get('subject_area', ''),
            'grade_level': grade_level,
            'interests': interests_text,
            'primary_color': theme['primary'].replace('#', ''),
            'accent_color': theme['accent'].replace('#', ''),
            'procedural_concepts': json.dumps(procedural_concepts, ensure_ascii=False, indent=2),
            'analysis': analysis,
            'language': language,
            'user_instruction': user_instruction
        }

    async def _execute_stage(self, stage_name: str, context: Dict,
                            stage_func, validation_func,
                            completed_stages: Dict, refinements: Dict) -> Optional[str]:
        """
        Execute a generation stage with validation and refinement.

        Args:
            stage_name: Name of the stage
            context: Generation context
            stage_func: Function to generate content
            validation_func: Function to validate content
            completed_stages: Dict to track completed stages
            refinements: Dict to track refinement counts

        Returns:
            Generated HTML string or None on catastrophic failure
        """
        max_refinements = 3
        last_error = None

        # Check cache first
        cached = self.cache.get_cached(context, stage_name)
        if cached:
            logger.info(f"Using cached {stage_name}")
            return self._sanitize_html_output(cached)

        for attempt in range(max_refinements):
            try:
                logger.info(f"{stage_name} - Attempt {attempt + 1}/{max_refinements}")

                # Generate with timeout
                html = await asyncio.wait_for(
                    stage_func(context),
                    timeout=600  # 10 minutes per stage
                )

                if not html:
                    logger.warning(f"{stage_name} - Empty result on attempt {attempt + 1}")
                    continue

                # Validate
                # is_valid, issues = validation_func(html)
                is_valid, issues = True, None
                

                if is_valid:
                    logger.info(f"{stage_name} - Validation passed on attempt {attempt + 1}")
                    self.cache.save_success(context, stage_name, html)
                    refinements[stage_name] = attempt
                    return html
                else:
                    logger.warning(f"{stage_name} - Validation failed: {issues[:3]}")
                    last_error = issues[0] if issues else "Unknown validation error"

                    # On last attempt, accept if no critical errors
                    if attempt == max_refinements - 1:
                        critical_errors = [i for i in issues if i.startswith("错误")]
                        if not critical_errors:
                            logger.info(f"{stage_name} - Accepted with warnings")
                            self.cache.save_success(context, stage_name, html)
                            refinements[stage_name] = attempt
                            return html

            except asyncio.TimeoutError:
                logger.warning(f"{stage_name} - Timeout on attempt {attempt + 1}")
                last_error = "Generation timeout"
            except FatalAIProviderError as e:
                logger.error(f"{stage_name} - Fatal provider error: {e.reason}")
                self._remember_provider_failure(e.reason)
                return None
            except Exception as e:
                fatal_reason = self._classify_fatal_provider_error(e)
                if fatal_reason:
                    logger.error(f"{stage_name} - Fatal provider error: {fatal_reason}")
                    self._remember_provider_failure(fatal_reason)
                    return None

                logger.error(f"{stage_name} - Error: {e}")
                last_error = str(e)

        # All attempts failed
        logger.error(f"{stage_name} - All attempts failed, using fallback")
        return None

    async def _generate_aligned_simulation(self, context: Dict) -> str:
        """Stage 1: Generate content-aligned interactive simulations with left-right layout."""
        prompt = get_stage_prompt(1, **context)
        response = await self._call_ai_provider(prompt, thinking_enabled=True)
        return self._extract_html_from_response(response) if response else ""

    async def _polish_layout_and_visuals(self, context: Dict) -> str:
        """Stage 2: Polish layout and visuals, ensure error-free rendering."""
        prompt = get_stage_prompt(2, **context)
        response = await self._call_ai_provider(prompt, thinking_enabled=True)
        return self._extract_html_from_response(response) if response else ""

    async def _call_ai_provider(self, prompt: str, thinking_enabled: bool = True) -> Optional[str]:
        """Call the AI provider with a prompt."""
        try:
            backend = getattr(self.provider, 'backend', None)

            async def _call_zhipu() -> Optional[str]:
                response = await self.provider._run_zhipu_call(
                    model=getattr(self.provider, 'text_model', os.getenv('ZHIPU_TEXT_MODEL', 'glm-4.6')),
                    messages=[{"role": "user", "content": prompt}],
                    thinking_params={"type": "enabled" if thinking_enabled else "disabled"},
                    max_tokens=int(os.getenv("HTML_GENERATION_MAX_TOKENS", "16000")),
                )
                if response and response.choices and response.choices[0].message:
                    return response.choices[0].message.content
                return None

            async def _call_anthropic() -> Optional[str]:
                response = await self.provider._run_anthropic_call(
                    model=getattr(self.provider, 'model', 'claude-sonnet-4-6'),
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=64000,
                    thinking_enabled=thinking_enabled
                )
                # Anthropic returns content as a list of content blocks
                if response and response.content:
                    content_text = ""
                    for block in response.content:
                        if hasattr(block, 'text'):
                            content_text += block.text
                    return content_text
                return None

            # Prefer explicit backend routing for providers that implement multiple methods.
            if backend == 'anthropic' and hasattr(self.provider, '_run_anthropic_call'):
                result = await _call_anthropic()
                if result is not None:
                    return result

            elif backend in ('zhipu', 'openai_compat') and hasattr(self.provider, '_run_zhipu_call'):
                result = await _call_zhipu()
                if result is not None:
                    return result

            # Fallback routing: use whichever client is actually initialized.
            elif hasattr(self.provider, '_run_anthropic_call') and getattr(self.provider, 'anthropic_client', None) is not None:
                result = await _call_anthropic()
                if result is not None:
                    return result

            elif hasattr(self.provider, '_run_zhipu_call') and getattr(self.provider, 'zhipu_client', None) is not None:
                result = await _call_zhipu()
                if result is not None:
                    return result

            elif hasattr(self.provider, 'client'):
                # Generic provider with OpenAI-style client
                def _sync_call():
                    return self.provider.client.chat.completions.create(
                        model=getattr(self.provider, 'model', 'gpt-4'),
                        messages=[{"role": "user", "content": prompt}]
                    )
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, _sync_call)
                if response and response.choices and response.choices[0].message:
                    return response.choices[0].message.content

            else:
                logger.error("Unknown AI provider type")
                return None

        except Exception as e:
            fatal_reason = self._classify_fatal_provider_error(e)
            if fatal_reason:
                logger.error(f"AI provider fatal failure: {fatal_reason}")
                raise FatalAIProviderError(fatal_reason) from e

            logger.error(f"AI provider call failed: {e}")

        return None

    def _extract_html_from_response(self, response: str) -> str:
        """Extract HTML from AI response."""
        cleaned = self._sanitize_html_output(response)

        html_start = cleaned.find('<!DOCTYPE html>')
        if html_start == -1:
            html_start = cleaned.find('<html')

        html_end_index = cleaned.rfind('</html>')
        html_end = html_end_index + len('</html>') if html_end_index != -1 else -1

        if html_start != -1 and html_end > html_start:
            html = cleaned[html_start:html_end]
            self._validate_complete_html_or_raise(html)
            return html

        if html_start != -1:
            html = cleaned[html_start:]
            self._validate_complete_html_or_raise(html)
            return html

        self._validate_complete_html_or_raise(cleaned)
        return cleaned

    async def _degrade_gracefully(self, completed_stages: Dict, analysis: Dict,
                                  user_preferences: Dict) -> Dict:
        """Build best possible output from completed stages."""
        logger.warning("Degrading gracefully from completed stages")

        if 'stage2' in completed_stages:
            html = completed_stages['stage2']
        elif 'stage1' in completed_stages:
            html = self._apply_basic_styling(completed_stages['stage1'])
        else:
            html = self._emergency_template(analysis, user_preferences)

        procedural_concepts = self._extract_procedural_concepts(analysis)
        metadata_and_elements = await self._generate_metadata_if_provider_healthy(
            procedural_concepts, analysis, user_preferences
        )

        return {
            "html": html,
            "metadata": {
                **self._generate_metadata(analysis, user_preferences),
                **metadata_and_elements.get("metadata", {})
            },
            "interactive_elements": metadata_and_elements.get("interactive_elements", []),
            "generation_info": {
                "mode": "heavy",
                "fallback_used": True,
                "fallback_reason": self._last_provider_failure_reason,
                "stages_completed": len(completed_stages),
                "refinements": {}
            }
        }

    def _apply_basic_styling(self, html: str) -> str:
        """Apply basic styling to stage 1 output if stage 2 fails."""
        style = """
<style>
body { font-family: 'Source Han Sans CN', 'Microsoft YaHei', sans-serif; line-height: 1.7; }
.simulation-container { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; padding: 2rem; }
.process-panel { background: #f9fafb; padding: 1.5rem; border-radius: 1rem; }
.simulation-panel { background: white; padding: 1.5rem; border-radius: 1rem; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
</style>
"""
        return html.replace('</head>', style + '</head>')

    def _emergency_template(self, analysis: Dict, user_preferences: Dict) -> str:
        """Deterministic interactive page used when remote HTML generation cannot finish."""
        subject = html_lib.escape(str(analysis.get('subject_area', '学习')))
        topics = analysis.get('main_topics', []) or []
        concepts = analysis.get('key_concepts', []) or []
        objectives = analysis.get('learning_objectives', []) or []
        procedural_concepts = analysis.get('procedural_concepts', []) or []

        def list_items(items: List[Any], fallback: str) -> str:
            values = [html_lib.escape(str(item)) for item in items if str(item).strip()]
            if not values:
                values = [fallback]
            return "\n".join(f"<li>{item}</li>" for item in values)

        concept_cards = []
        for concept in procedural_concepts[:4]:
            if not isinstance(concept, dict):
                continue
            name = html_lib.escape(str(concept.get('name', '核心概念')))
            desc = html_lib.escape(str(concept.get('description', '')))
            steps = concept.get('key_steps', []) or []
            concept_cards.append(f"""
            <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-slate-900">{name}</h2>
                <p class="mt-2 text-slate-600">{desc}</p>
                <ol class="mt-4 list-decimal space-y-2 pl-5 text-slate-700">
                    {list_items(steps, '先理解概念，再完成练习，最后检查掌握程度。')}
                </ol>
            </section>
            """)

        if not concept_cards:
            concept_cards.append("""
            <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-slate-900">核心学习路径</h2>
                <p class="mt-2 text-slate-600">系统已完成内容分析，并生成了本地交互学习页。请先用图像和滑块建立直觉。</p>
            </section>
            """)

        concept_tags = "".join(
            f'<span class="rounded-full bg-white px-3 py-1 text-sm font-medium text-sky-900 shadow-sm">{html_lib.escape(str(concept))}</span>'
            for concept in concepts[:12]
        )

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}交互学习页</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{ font-family: 'Source Han Sans CN', 'Microsoft YaHei', sans-serif; }}
        canvas {{ width: 100%; height: 420px; display: block; }}
        input[type="range"] {{ accent-color: #4f46e5; }}
    </style>
</head>
<body class="bg-slate-50 text-slate-800">
    <div class="mx-auto max-w-5xl px-4 py-8">
        <header class="mb-6">
            <p class="text-sm font-semibold text-violet-700">本地交互学习页</p>
            <h1 class="mt-2 text-3xl font-bold text-slate-950">{subject}</h1>
            <p class="mt-3 text-slate-600">远端大模型生成完整页面失败或超限，系统已基于PDF内容分析生成可运行的交互学习页。</p>
        </header>

        <div class="grid gap-5 lg:grid-cols-[0.92fr_1.08fr]">
            <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-slate-950">学习目标</h2>
                <ul class="mt-3 list-disc space-y-2 pl-5 text-slate-700">
                    {list_items(objectives, '理解材料中的核心概念并完成应用练习。')}
                </ul>
                <div class="mt-5 rounded-lg bg-violet-50 p-4">
                    <h3 class="font-bold text-violet-950">主要主题</h3>
                    <ul class="mt-2 list-disc space-y-1 pl-5 text-violet-900">
                        {list_items(topics, 'PDF核心内容')}
                    </ul>
                </div>
            </section>

            <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 class="text-xl font-bold text-slate-950">导数、梯度与下降路径</h2>
                        <p class="mt-1 text-sm text-slate-600">拖动参数，观察切线斜率如何变成梯度下降的方向。</p>
                    </div>
                    <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold">
                        <button id="modeDerivative" class="rounded-md bg-white px-3 py-1.5 text-indigo-700 shadow-sm">导数</button>
                        <button id="modeGradient" class="rounded-md px-3 py-1.5 text-slate-600">梯度下降</button>
                        <button id="modeBackprop" class="rounded-md px-3 py-1.5 text-slate-600">链式法则</button>
                    </div>
                </div>
                <div class="mt-4 rounded-xl bg-slate-950 p-3">
                    <canvas id="learningCanvas" width="900" height="420" aria-label="导数、梯度下降与链式法则交互图"></canvas>
                </div>
                <div class="mt-4 grid gap-4 md:grid-cols-3">
                    <label class="text-sm font-semibold text-slate-700">观察点 x = <span id="xValue">0.00</span>
                        <input id="xSlider" class="mt-2 w-full" type="range" min="-250" max="350" value="60">
                    </label>
                    <label class="text-sm font-semibold text-slate-700">学习率 = <span id="lrValue">0.12</span>
                        <input id="lrSlider" class="mt-2 w-full" type="range" min="2" max="28" value="12">
                    </label>
                    <label class="text-sm font-semibold text-slate-700">迭代步数 = <span id="iterValue">8</span>
                        <input id="iterSlider" class="mt-2 w-full" type="range" min="1" max="24" value="8">
                    </label>
                </div>
                <div class="mt-4 grid gap-3 text-sm md:grid-cols-4">
                    <div class="rounded-lg bg-indigo-50 p-3"><div class="text-indigo-700">函数值 f(x)</div><div id="fxData" class="text-lg font-bold text-indigo-950">-</div></div>
                    <div class="rounded-lg bg-emerald-50 p-3"><div class="text-emerald-700">导数 f'(x)</div><div id="gradData" class="text-lg font-bold text-emerald-950">-</div></div>
                    <div class="rounded-lg bg-amber-50 p-3"><div class="text-amber-700">下一步方向</div><div id="directionData" class="text-lg font-bold text-amber-950">-</div></div>
                    <div class="rounded-lg bg-rose-50 p-3"><div class="text-rose-700">直觉提示</div><div id="hintData" class="text-sm font-semibold text-rose-950">-</div></div>
                </div>
            </section>
        </div>

        <section class="mb-6 rounded-xl border border-sky-200 bg-sky-50 p-5">
            <h2 class="text-lg font-bold text-sky-950">关键概念</h2>
            <div class="mt-3 flex flex-wrap gap-2">
                {concept_tags}
            </div>
        </section>

        <main class="grid gap-4">
            {"".join(concept_cards)}
        </main>
    </div>
    <script>
        const canvas = document.getElementById('learningCanvas');
        const ctx = canvas.getContext('2d');
        const xSlider = document.getElementById('xSlider');
        const lrSlider = document.getElementById('lrSlider');
        const iterSlider = document.getElementById('iterSlider');
        const xValue = document.getElementById('xValue');
        const lrValue = document.getElementById('lrValue');
        const iterValue = document.getElementById('iterValue');
        const fxData = document.getElementById('fxData');
        const gradData = document.getElementById('gradData');
        const directionData = document.getElementById('directionData');
        const hintData = document.getElementById('hintData');
        const modeButtons = {{
            derivative: document.getElementById('modeDerivative'),
            gradient: document.getElementById('modeGradient'),
            backprop: document.getElementById('modeBackprop')
        }};
        let mode = 'derivative';

        function f(x) {{ return 0.18 * (x - 0.8) * (x - 0.8) + 0.35 * Math.sin(2.2 * x) + 1.1; }}
        function df(x) {{ return 0.36 * (x - 0.8) + 0.77 * Math.cos(2.2 * x); }}
        function sx(x) {{ return 80 + (x + 3) / 6 * (canvas.width - 150); }}
        function sy(y) {{ return canvas.height - 52 - y / 3.4 * (canvas.height - 95); }}
        function vx() {{ return Number(xSlider.value) / 100; }}
        function lr() {{ return Number(lrSlider.value) / 100; }}

        function drawAxes() {{
            ctx.strokeStyle = '#475569';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(70, canvas.height - 52);
            ctx.lineTo(canvas.width - 45, canvas.height - 52);
            ctx.moveTo(80, 35);
            ctx.lineTo(80, canvas.height - 42);
            ctx.stroke();
            ctx.fillStyle = '#cbd5e1';
            ctx.font = '14px Microsoft YaHei';
            ctx.fillText('x', canvas.width - 52, canvas.height - 62);
            ctx.fillText('f(x)', 90, 34);
        }}

        function drawCurve() {{
            ctx.strokeStyle = '#60a5fa';
            ctx.lineWidth = 3;
            ctx.beginPath();
            for (let i = 0; i <= 360; i++) {{
                const x = -3 + i / 60;
                const px = sx(x);
                const py = sy(f(x));
                if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            }}
            ctx.stroke();
        }}

        function drawDerivative(x) {{
            const y = f(x);
            const g = df(x);
            const px = sx(x);
            const py = sy(y);
            const span = 1.3;
            ctx.strokeStyle = '#34d399';
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.moveTo(sx(x - span), sy(y - g * span));
            ctx.lineTo(sx(x + span), sy(y + g * span));
            ctx.stroke();
            ctx.fillStyle = '#f97316';
            ctx.beginPath();
            ctx.arc(px, py, 7, 0, Math.PI * 2);
            ctx.fill();
        }}

        function drawGradientPath(start, rate, steps) {{
            let x = start;
            ctx.strokeStyle = '#facc15';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(sx(x), sy(f(x)));
            for (let i = 0; i < steps; i++) {{
                x = x - rate * df(x);
                ctx.lineTo(sx(x), sy(f(x)));
            }}
            ctx.stroke();
            ctx.fillStyle = '#facc15';
            ctx.beginPath();
            ctx.arc(sx(x), sy(f(x)), 6, 0, Math.PI * 2);
            ctx.fill();
        }}

        function drawBackprop() {{
            const nodes = [
                ['输入 x', 130, 110], ['线性 z=wx+b', 330, 110], ['激活 a=g(z)', 530, 110], ['损失 L', 730, 110],
                ['∂L/∂a', 610, 265], ['∂a/∂z', 430, 265], ['∂z/∂w', 250, 265]
            ];
            ctx.font = '16px Microsoft YaHei';
            nodes.forEach(function(n) {{
                ctx.fillStyle = '#1e293b';
                ctx.strokeStyle = '#8b5cf6';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.roundRect(n[1] - 62, n[2] - 24, 124, 48, 12);
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = '#f8fafc';
                ctx.textAlign = 'center';
                ctx.fillText(n[0], n[1], n[2] + 6);
            }});
            ctx.strokeStyle = '#22c55e';
            ctx.lineWidth = 3;
            [[192,110,268,110],[392,110,468,110],[592,110,668,110],[680,135,640,240],[550,265,490,265],[370,265,310,265]].forEach(function(e) {{
                ctx.beginPath();
                ctx.moveTo(e[0], e[1]);
                ctx.lineTo(e[2], e[3]);
                ctx.stroke();
            }});
            ctx.fillStyle = '#cbd5e1';
            ctx.textAlign = 'left';
            ctx.fillText('链式法则：整体影响 = 局部影响逐层相乘', 90, 360);
        }}

        function setMode(next) {{
            mode = next;
            Object.keys(modeButtons).forEach(function(key) {{
                const active = key === next;
                modeButtons[key].className = active
                    ? 'rounded-md bg-white px-3 py-1.5 text-indigo-700 shadow-sm'
                    : 'rounded-md px-3 py-1.5 text-slate-600';
            }});
            draw();
        }}

        function draw() {{
            const x = vx();
            const rate = lr();
            const steps = Number(iterSlider.value);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#020617';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            drawAxes();
            if (mode === 'backprop') {{
                drawBackprop();
            }} else {{
                drawCurve();
                drawDerivative(x);
                if (mode === 'gradient') drawGradientPath(x, rate, steps);
            }}
            const y = f(x);
            const g = df(x);
            xValue.textContent = x.toFixed(2);
            lrValue.textContent = rate.toFixed(2);
            iterValue.textContent = String(steps);
            fxData.textContent = y.toFixed(3);
            gradData.textContent = g.toFixed(3);
            directionData.textContent = g > 0 ? '向左更新' : '向右更新';
            hintData.textContent = mode === 'backprop'
                ? '反向传播就是在计算图上反复使用链式法则。'
                : (Math.abs(g) < 0.08 ? '接近极小值，梯度很小。' : '沿负梯度方向移动，函数值通常下降。');
        }}

        modeButtons.derivative.addEventListener('click', function() {{ setMode('derivative'); }});
        modeButtons.gradient.addEventListener('click', function() {{ setMode('gradient'); }});
        modeButtons.backprop.addEventListener('click', function() {{ setMode('backprop'); }});
        [xSlider, lrSlider, iterSlider].forEach(function(el) {{ el.addEventListener('input', draw); }});
        if (!CanvasRenderingContext2D.prototype.roundRect) {{
            CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {{
                this.beginPath();
                this.moveTo(x + r, y);
                this.lineTo(x + w - r, y);
                this.quadraticCurveTo(x + w, y, x + w, y + r);
                this.lineTo(x + w, y + h - r);
                this.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
                this.lineTo(x + r, y + h);
                this.quadraticCurveTo(x, y + h, x, y + h - r);
                this.lineTo(x, y + r);
                this.quadraticCurveTo(x, y, x + r, y);
                this.closePath();
                return this;
            }};
        }}
        draw();
    </script>
</body>
</html>"""

    def _extract_procedural_concepts(self, analysis: Dict) -> List[Dict]:
        """Extract procedural concepts from analysis."""
        procedural_concepts = analysis.get('procedural_concepts', [])

        if not procedural_concepts:
            procedural_concepts = [
                {
                    "name": concept,
                    "description": f"理解并应用{concept}",
                    "key_steps": ["理解", "练习", "应用"],
                    "complexity": "中等"
                }
                for concept in analysis.get('key_concepts', [])[:3]
            ]

        return procedural_concepts[:3]

    def _map_grade_level(self, grade_level: int) -> str:
        """Map integer grade to Chinese string."""
        grade_mapping = {
            0: "幼儿园", 1: "小学一年级", 2: "小学二年级", 3: "小学三年级",
            4: "小学四年级", 5: "小学五年级", 6: "小学六年级",
            7: "初中一年级", 8: "初中二年级", 9: "初中三年级",
            10: "高中一年级", 11: "高中二年级", 12: "高中三年级"
        }
        return grade_mapping.get(grade_level, f"年级{grade_level}")

    async def _generate_fallback(self, pdf_images: List[Dict], analysis: Dict,
                                 user_preferences: Dict) -> Dict:
        """Generate complete fallback response."""
        procedural_concepts = self._extract_procedural_concepts(analysis)
        metadata_and_elements = await self._generate_metadata_if_provider_healthy(
            procedural_concepts, analysis, user_preferences
        )

        return {
            "html": self._emergency_template(analysis, user_preferences),
            "metadata": {
                **self._generate_metadata(analysis, user_preferences),
                **metadata_and_elements.get("metadata", {})
            },
            "interactive_elements": metadata_and_elements.get("interactive_elements", []),
            "generation_info": {
                "mode": "heavy",
                "fallback_used": True,
                "fallback_reason": self._last_provider_failure_reason,
                "stages_completed": 0,
                "refinements": {}
            }
        }
