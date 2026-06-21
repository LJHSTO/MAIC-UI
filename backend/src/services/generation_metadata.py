from typing import Any, Dict, Optional


def _provider_label_for_model(model: Optional[str], backend: Optional[str] = None) -> Optional[str]:
    if not model:
        return None
    innospark_prefixes = ("gpt-", "claude-", "gemini-", "deepseek-", "doubao-", "kimi-")
    innospark_models = {"Qwen3.6-35B-inno", "qwen3.6-27b", "qwen3.6-35b-a3b"}
    if model.startswith(innospark_prefixes) or model in innospark_models:
        return "Innospark (OpenAI-compatible)"

    if backend == "zhipu" or model.startswith("glm-"):
        return "Zhipu GLM"
    if backend == "anthropic":
        return "Anthropic"
    if backend == "openai_compat":
        if model.startswith("gpt-"):
            return "UUAPI GPT (OpenAI-compatible)"
        if model.startswith("claude-"):
            return "UUAPI Claude (OpenAI-compatible)"
        if model.startswith("gemini-"):
            return "UUAPI Gemini (OpenAI-compatible)"
        if model.startswith("deepseek-"):
            return "DeepSeek official (OpenAI-compatible)"
        if model.startswith("kimi-"):
            return "Moonshot Kimi (OpenAI-compatible)"
        if model.startswith("minimax-") or model.startswith("qwen"):
            return "SiliconFlow (OpenAI-compatible)"
        return "OpenAI-compatible transfer"

    return None


def normalize_generation_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not metadata:
        return None

    normalized = dict(metadata)
    model = normalized.get("model") or normalized.get("requested_model") or normalized.get("text_model")
    backend = normalized.get("backend")
    provider = normalized.get("provider")

    inferred_provider = _provider_label_for_model(model, backend)
    if inferred_provider and (
        not provider
        or provider.startswith("ChineseProvider-")
        or provider.startswith("ChineseEditorProvider-")
    ):
        normalized["provider"] = inferred_provider

    return normalized


def _safe_provider_name(ai_processor: Any, provider: Any) -> Optional[str]:
    try:
        if hasattr(ai_processor, "get_provider_name"):
            return ai_processor.get_provider_name()
        if hasattr(provider, "get_provider_name"):
            return provider.get_provider_name()
    except Exception:
        return None
    return None


def build_generation_metadata(
    ai_processor: Any,
    requested_model: Optional[str] = None,
    generation_mode: Optional[str] = None,
    workflow_type: Optional[str] = None,
    generation_method: Optional[str] = None,
) -> Dict[str, Any]:
    provider = getattr(ai_processor, "provider", ai_processor)
    provider_model = getattr(provider, "model", None)
    text_model = getattr(provider, "text_model", None)
    backend = getattr(provider, "backend", None)
    effective_model = requested_model or text_model or provider_model

    metadata = {
        "model": effective_model or "default",
        "requested_model": requested_model,
        "provider": _safe_provider_name(ai_processor, provider),
        "provider_model": provider_model,
        "text_model": text_model,
        "backend": backend,
        "generation_mode": generation_mode,
        "workflow_type": workflow_type,
        "generation_method": generation_method,
    }
    return normalize_generation_metadata({key: value for key, value in metadata.items() if value not in (None, "")}) or {}


def get_generation_metadata(*sources: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for source in sources:
        if not source:
            continue

        metadata = source.get("generation_metadata")
        if isinstance(metadata, dict) and metadata:
            return normalize_generation_metadata(metadata)

        model = source.get("ai_model") or source.get("model")
        provider = source.get("ai_provider") or source.get("provider")
        if model or provider:
            inferred = {
                "model": model,
                "provider": provider,
                "generation_mode": source.get("generation_mode"),
                "workflow_type": source.get("workflow_type"),
                "generation_method": source.get("generation_method"),
            }
            return normalize_generation_metadata({key: value for key, value in inferred.items() if value not in (None, "")})

    return None
