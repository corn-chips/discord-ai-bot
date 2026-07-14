"""SDK-independent data and response builders for Gemini generation."""

from dataclasses import dataclass
from typing import Any, List, Optional

from ..models.data_models import APIResponse, TokenUsage


@dataclass(frozen=True)
class GeminiRequestRouting:
    """Resolved request-scoped model, thinking, and search choices."""

    model_name: str
    complexity_level: str
    thinking_level: str
    use_search: bool


@dataclass(frozen=True)
class GeminiRequestPlan(GeminiRequestRouting):
    """Complete request plan after content formatting and timeout selection."""

    timeout_duration: float


@dataclass(frozen=True)
class GeminiRequestContent:
    """Formatted prompt and ordered provider content parts."""

    parts: List[Any]
    formatted_prompt: str
    image_context: Optional[List[dict]]


@dataclass(frozen=True)
class GeminiAttemptResult:
    """Provider result paired with its measured attempt duration."""

    response: Any
    duration: float


def build_error_response(error_type: str, content: str) -> APIResponse:
    """Create a failed public response without coupling to provider objects."""
    return APIResponse(
        success=False,
        error_type=error_type,
        content=content,
    )


def build_success_response(
    response_text: str,
    model_display_name: str,
    use_search: bool,
    grounding_sources: List[dict],
    token_usage: Optional[TokenUsage],
    *,
    truncated: bool = False,
) -> APIResponse:
    """Apply public headers and metadata to successful provider text."""
    model_header = f"🤖 *[Model: {model_display_name}]*"
    content = response_text.strip()
    if use_search:
        content = (
            f"{model_header}\n"
            "🌐 *[Grounding: Online Search Enabled]*\n\n"
            f"{content}"
        )
    else:
        content = f"{model_header}\n\n{content}"

    if truncated:
        content += (
            "\n\n*[Note: Response was very long and may have been truncated. "
            "You can ask for specific parts or a summary.]*"
        )

    return APIResponse(
        success=True,
        content=content,
        grounding_sources=grounding_sources if grounding_sources else None,
        token_usage=token_usage,
    )
