"""
Shared token usage extraction utilities.

This module provides a centralized function for extracting token usage
metadata from Gemini API responses, eliminating duplication across clients.
"""

import logging
from typing import Optional

from ..models.data_models import TokenUsage


logger = logging.getLogger(__name__)


def extract_token_usage(response) -> Optional[TokenUsage]:
    """
    Extract token usage metadata from Gemini API responses.
    
    This is a shared utility used by both GeminiClient and NanoBananaClient
    to avoid code duplication.
    
    Args:
        response: The API response object from Gemini
        
    Returns:
        TokenUsage object if extraction successful, None otherwise
    """
    usage = getattr(response, 'usage_metadata', None)
    if not usage:
        return None

    def _read_field(obj, names):
        for name in names:
            value = None
            if isinstance(obj, dict):
                value = obj.get(name)
            else:
                value = getattr(obj, name, None)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return None

    prompt_tokens = _read_field(usage, [
        'prompt_token_count', 'prompt_tokens', 'input_tokens', 'promptTokenCount'
    ])
    candidate_tokens = _read_field(usage, [
        'candidates_token_count', 'output_tokens', 'candidatesTokenCount'
    ])
    total_tokens = _read_field(usage, [
        'total_token_count', 'total_tokens', 'totalTokenCount'
    ])

    if prompt_tokens is None and candidate_tokens is None and total_tokens is None:
        return None

    prompt_tokens = prompt_tokens or 0
    candidate_tokens = candidate_tokens or 0
    total_tokens = total_tokens or (prompt_tokens + candidate_tokens)

    try:
        token_usage = TokenUsage(
            input_tokens=prompt_tokens,
            output_tokens=candidate_tokens,
            total_tokens=total_tokens
        )
        logger.info(
            "Token usage extracted: input=%s output=%s total=%s",
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.total_tokens
        )
        return token_usage
    except Exception as exc:
        logger.warning(f"Failed to parse token usage metadata: {exc}")
        return None
