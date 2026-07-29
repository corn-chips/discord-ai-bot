"""
Gemini API client for the Discord Grok Bot.

This module provides the GeminiClient class for interfacing with Google's Gemini API
to generate AI-powered responses based on user prompts and conversation context.
"""

import asyncio
import io
import json
import logging
import random
import re
import time
from typing import List, Optional

from google import genai
from google.genai import types

from ..config import BotConfig
from ..models.data_models import APIResponse, MessageContext, TokenUsage
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger
from ..utils.token_extraction import extract_token_usage
from .gemini_response_pipeline import (
    GeminiAttemptResult,
    GeminiRequestContent,
    GeminiRequestPlan,
    GeminiRequestRouting,
    build_error_response,
    build_success_response,
)


logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Client for interacting with Google's Gemini API.
    
    Handles API authentication, prompt formatting, response generation,
    and error handling with retry logic.
    """
    
    def __init__(self, config: BotConfig):
        """
        Initialize the Gemini client with configuration.
        
        Args:
            config: Bot configuration containing API key and settings
        """
        self.config = config
        self.error_manager = ErrorManager(config)
        self.performance_logger = PerformanceLogger("gemini_client")
        self.client = None
        self._current_model_name = config.default_model
        self._runtime_model_override = False
        self._current_complexity_level = "low"
        self._thinking_level_override: Optional[str] = None
        self._force_search = False  # Force search on/off
        self._configure_api()
        
    def _configure_api(self) -> None:
        """Configure the Gemini API with authentication and settings."""
        try:
            logger.info("=" * 80)
            logger.info("CONFIGURING GEMINI API (New SDK)")
            
            # Validate API key exists and is not empty
            if not self.config.gemini_api_key or not self.config.gemini_api_key.strip():
                logger.error("Gemini API key is missing or empty")
                self.client = None
                logger.info("=" * 80)
                return
            
            logger.info("Gemini API key configured (length=%s characters)", len(self.config.gemini_api_key))
            
            # Initialize the new SDK client
            self.client = genai.Client(api_key=self.config.gemini_api_key)
            
            logger.info(f"Model name: {self._current_model_name}")
            logger.info(f"Gemini API configured successfully")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception details: {repr(e)}")
            self.client = None
            logger.info("=" * 80)
    
    def get_current_model(self) -> str:
        """Get the name of the currently active model."""
        return self._current_model_name

    def has_runtime_model_override(self) -> bool:
        """Return whether `/config model` explicitly selected the current model."""
        return self._runtime_model_override
    
    @staticmethod
    def _normalize_thinking_level(level: Optional[str]) -> str:
        """Normalize configured/runtime thinking level labels."""
        normalized = (level or "default").strip().lower()
        alias_map = {
            "none": "default",
            "inherit": "default",
            "disabled": "off",
        }
        return alias_map.get(normalized, normalized)

    def _get_model_complexity_entry(self, complexity_level: Optional[str] = None) -> dict:
        """Return normalized model/thinking mapping for a complexity tier."""
        level = complexity_level or self._current_complexity_level
        if level not in {"low", "medium", "high"}:
            level = "low"

        raw_entry = self.config.model_complexity.get(level, {})
        if not isinstance(raw_entry, dict):
            raw_entry = {}

        model_name = str(raw_entry.get("model", self.config.default_model))
        thinking_level = self._normalize_thinking_level(str(raw_entry.get("thinking_level", "default")))

        return {
            "model": model_name,
            "thinking_level": thinking_level,
        }

    def get_thinking_level(self, model_name: Optional[str] = None) -> str:
        """
        Get effective thinking level for current complexity.

        Runtime override takes precedence over config mapping.
        """
        _ = model_name  # Backward-compatible, no longer used for resolution.
        if self._thinking_level_override is not None:
            return self._thinking_level_override
        return self._get_model_complexity_entry()["thinking_level"]

    def set_thinking_level(self, level: str) -> bool:
        """
        Set runtime thinking level override.

        Use `default` to clear the override and fall back to config.yaml routing.
        """
        normalized = self._normalize_thinking_level(level)
        valid_levels = {"default", "off", "minimal", "low", "medium", "high"}
        if normalized not in valid_levels:
            logger.error(
                "Invalid thinking level: %s. Must be one of: %s",
                level,
                ", ".join(sorted(valid_levels)),
            )
            return False

        old_level = self._thinking_level_override or "default"
        self._thinking_level_override = None if normalized == "default" else normalized
        logger.info(
            "Thinking level override changed from '%s' to '%s'",
            old_level,
            self._thinking_level_override or "default",
        )
        return True

    def set_force_search(self, enabled: bool) -> None:
        """
        Enable or disable forced Google Search for all queries.
        
        Args:
            enabled: True to force search, False to use auto-detection
        """
        self._force_search = enabled
        logger.info(f"DeepSearch mode set to: {enabled}")
    
    def get_api_usage_info(self) -> dict:
        """
        Get API usage information and rate limits.
        
        Returns:
            Dictionary containing API usage information including:
            - model_info: Current model details
            - rate_limits: Known rate limits for the API
            - pricing_tier: Free or paid tier information
        """
        try:
            usage_info = {
                "model_name": self._current_model_name,
                "api_configured": self.client is not None,
                "rate_limits": {
                    "free_tier": {
                        "requests_per_minute": 15,
                        "requests_per_day": 1500,
                        "tokens_per_minute": 1000000,
                        "tokens_per_day": None  # No daily token limit for free tier
                    },
                    "paid_tier": {
                        "requests_per_minute": 2000,
                        "requests_per_day": None,  # No daily limit for paid
                        "tokens_per_minute": 4000000,
                        "tokens_per_day": None
                    }
                },
                "model_capabilities": {
                    "max_input_tokens": 1048576,  # 1M tokens context window
                    "max_output_tokens": self.config.max_output_tokens_high,
                    "supports_images": True,
                    "supports_video": False,
                    "supports_audio": False
                },
                "pricing": {
                    "free_tier": "Free up to rate limits",
                    "paid_tier": "Pay-as-you-go pricing available"
                }
            }
            
            logger.debug(f"Retrieved API usage info for model: {self._current_model_name}")
            return usage_info
            
        except Exception as e:
            logger.error(f"Error getting API usage info: {e}")
            return {
                "error": str(e),
                "model_name": self._current_model_name,
                "api_configured": False
            }
    
    def set_model(self, model_name: str) -> bool:
        """
        Switch to a different Gemini Flash model.
        
        Args:
            model_name: Name of the model to switch to
            
        Returns:
            True if successful, False otherwise
        """
        logger.info("=" * 80)
        logger.info("SWITCHING MODEL")
        
        if model_name not in self.config.valid_models:
            logger.error(f"Invalid model name: {model_name}")
            logger.error(f"Valid models: {', '.join(self.config.valid_models)}")
            logger.info("=" * 80)
            return False
        
        old_model = self._current_model_name
        old_runtime_override = self._runtime_model_override
        try:
            logger.info(f"Old model: {old_model}")
            logger.info(f"New model: {model_name}")
            self._current_model_name = model_name
            self._runtime_model_override = True
            
            logger.info(f"Model switched successfully from {old_model} to {model_name}")
            logger.info("=" * 80)
            return True
            
        except Exception as e:
            logger.error(f"Failed to switch model to {model_name}: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception details: {repr(e)}", exc_info=True)
            # Revert to old model
            logger.warning(f"Reverting to old model: {old_model}")
            self._current_model_name = old_model
            self._runtime_model_override = old_runtime_override
            logger.info("=" * 80)
            return False
    
    def get_timeout_for_model(
        self,
        model_name: str,
        prompt_mode: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> int:
        """Get timeout for an explicit model/request override without mutating runtime state."""
        _ = model_name  # Kept for compatibility; timeout now follows effective thinking level.
        if thinking_level is not None:
            level = self._normalize_thinking_level(thinking_level)
        else:
            level = self._normalize_thinking_level(
                "high"
                if prompt_mode == "thinking"
                else ("minimal" if prompt_mode == "short" else self.get_thinking_level(model_name))
            )
        if level in {"low", "medium", "high"}:
            return self.config.extended_timeout
        return self.config.response_timeout
    
    def get_estimated_response_time(
        self,
        model_name: Optional[str] = None,
        prompt_mode: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> str:
        """
        Get a user-friendly estimated response time for the current model.
        
        Returns:
            Human-readable time estimate
        """
        effective_model_name = model_name or self._current_model_name
        if thinking_level is not None:
            effective_thinking_level = self._normalize_thinking_level(thinking_level)
        else:
            effective_thinking_level = self._normalize_thinking_level(
                "high"
                if prompt_mode == "thinking"
                else ("minimal" if prompt_mode == "short" else self.get_thinking_level(effective_model_name))
            )

        if effective_thinking_level in {"low", "medium", "high"}:
            return "30-60 seconds (using advanced model with API thinking)"
        else:
            return "5-15 seconds"
    
    def get_model_for_complexity(self, complexity_level: str) -> str:
        """Resolve the configured model for a complexity tier without mutating shared state."""
        normalized_level = complexity_level if complexity_level in ["low", "medium", "high"] else "low"
        target_model = self._get_model_complexity_entry(normalized_level)["model"]
        if target_model not in self.config.valid_models:
            logger.warning(
                "Configured model '%s' for complexity '%s' is invalid; falling back to default model '%s'",
                target_model,
                normalized_level,
                self.config.default_model,
            )
            return self.config.default_model
        return target_model

    def _get_max_output_tokens_for_complexity(
        self,
        complexity_level: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> int:
        """
        Select max output token budget from complexity tier.

        Primary routing is by complexity tier. If a request-scoped thinking level
        is provided, it can elevate the token tier for that single request.
        """
        effective_complexity = (complexity_level or self._current_complexity_level or "low").strip().lower()
        if effective_complexity == "high":
            return self.config.max_output_tokens_high
        if effective_complexity == "medium":
            return self.config.max_output_tokens_medium

        normalized_thinking = self._normalize_thinking_level(thinking_level)
        if normalized_thinking == "high":
            return self.config.max_output_tokens_high
        if normalized_thinking in {"low", "medium"}:
            return self.config.max_output_tokens_medium
        return self.config.max_output_tokens_low
    
    def _get_model_display_name(
        self,
        model_name: Optional[str] = None,
        prompt_mode: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> str:
        """
        Get a user-friendly display name for the current model and mode.
        
        Returns:
            Display name string (e.g., "Configured Model", "Configured Model + Thinking")
        """
        effective_model_name = model_name or self._current_model_name
        if thinking_level is not None:
            effective_thinking_level = self._normalize_thinking_level(thinking_level)
        else:
            effective_thinking_level = self._normalize_thinking_level(
                "high"
                if prompt_mode == "thinking"
                else ("minimal" if prompt_mode == "short" else self.get_thinking_level(effective_model_name))
            )
        display_name = self.config.model_display_names.get(effective_model_name, effective_model_name)
        
        if effective_thinking_level not in {"default"}:
            display_name += f" + Thinking:{effective_thinking_level.capitalize()}"
        
        return display_name
    
    def _extract_grounding_sources(self, response, use_search: bool) -> list:
        """
        Extract grounding sources from API response.
        
        Args:
            response: The API response object
            use_search: Whether search was used
            
        Returns:
            List of grounding source dictionaries with 'uri' and 'title'
        """
        grounding_sources = []
        
        if not use_search:
            return grounding_sources
        
        try:
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                
                # New SDK: grounding_metadata attribute
                if hasattr(candidate, 'grounding_metadata'):
                    grounding_metadata = candidate.grounding_metadata
                    
                    # Log search queries if available
                    if hasattr(grounding_metadata, 'web_search_queries'):
                        queries = grounding_metadata.web_search_queries
                        logger.info(f"🔍 Google Search queries used: {queries}")
                    
                    # Extract grounding chunks
                    if hasattr(grounding_metadata, 'grounding_chunks') and grounding_metadata.grounding_chunks:
                        for chunk in grounding_metadata.grounding_chunks:
                            if hasattr(chunk, 'web') and hasattr(chunk.web, 'uri'):
                                grounding_sources.append({
                                    'uri': chunk.web.uri,
                                    'title': chunk.web.title if hasattr(chunk.web, 'title') else None
                                })
                        
                        if grounding_sources:
                            logger.info(f"✅ Extracted {len(grounding_sources)} grounding sources:")
                            for i, source in enumerate(grounding_sources):
                                logger.info(f"  [{i+1}] {source['title']} - {source['uri']}")
        
        except Exception as e:
            logger.error(f"Error extracting grounding sources: {e}")
            logger.error(f"Exception details:", exc_info=True)
        
        return grounding_sources

    def _extract_token_usage(self, response) -> Optional[TokenUsage]:
        """Extract token usage metadata from API responses using shared utility."""
        return extract_token_usage(response)
    
    def _should_use_search(self, prompt: str) -> bool:
        """
        Determine if Google Search should be used based on the prompt content.
        
        Args:
            prompt: The user's message/prompt
            
        Returns:
            True if search should be used, False otherwise
        """
        # Check if DeepSearch is forced enabled
        if self._force_search:
            return True

        if not prompt:
            return False

        prompt_lower = re.sub(r"\s+", " ", prompt.lower()).strip()

        # Check for URLs
        if re.search(r"(https?://|www\.)", prompt_lower):
            return True

        # Check for explicit search intent
        explicit_search_patterns = [
            r"\bsearch\b",
            r"\bweb search\b",
            r"\binternet search\b",
            r"\blook ?up\b",
            r"\bcheck online\b",
            r"\bfind online\b",
            r"\bgoogle\b",
            r"\bsearch for\b",
        ]
        if any(re.search(pattern, prompt_lower) for pattern in explicit_search_patterns):
            return True

        # Check for common live/time-sensitive requests
        live_data_patterns = [
            r"\bnews\b",
            r"\bweather\b",
            r"\bforecast\b",
            r"\bstock price\b",
            r"\bcrypto price\b",
            r"\bscores?\b",
            r"\bstandings\b",
        ]
        if any(re.search(pattern, prompt_lower) for pattern in live_data_patterns):
            return True

        recency_signal = re.search(r"\b(latest|newest|most recent|breaking|today(?:'s)?|recent)\b", prompt_lower)
        recency_topic = re.search(r"\b(news|updates?|events?|prices?|scores?)\b", prompt_lower)
        return bool(recency_signal and recency_topic)

    def _resolve_thinking_level_for_request(
        self,
        model_name: str,
        prompt_mode_override: Optional[str] = None,
        complexity_level: Optional[str] = None,
    ) -> str:
        """
        Resolve effective thinking level for a request.

        Legacy prompt-mode overrides still map to native API thinking levels:
        - short -> minimal
        - thinking -> high
        """
        _ = model_name  # Kept for compatibility; thinking now follows complexity/config mapping.
        if prompt_mode_override == "short":
            return "minimal"
        if prompt_mode_override == "thinking":
            return "high"
        if self._thinking_level_override is not None:
            return self._thinking_level_override
        return self._get_model_complexity_entry(complexity_level)["thinking_level"]

    def _build_thinking_config_for_model(
        self,
        model_name: str,
        thinking_level: str,
    ) -> Optional[types.ThinkingConfig]:
        """
        Build Gemini API thinking config for the target model.

        Backend behavior comes from config.models.thinking_backend:
        - thinking_level: use native thinking_level enum
        - thinking_budget: map levels to thinking_budget values
        - off/minimal -> 0
        - low/medium/high -> -1 (dynamic)
        """
        normalized = self._normalize_thinking_level(thinking_level)
        if normalized == "default":
            return None

        backend = str(
            self.config.model_thinking_backend.get(model_name, "none")
        ).strip().lower()

        if backend == "thinking_level":
            level_map = {
                "off": types.ThinkingLevel.MINIMAL,
                "minimal": types.ThinkingLevel.MINIMAL,
                "low": types.ThinkingLevel.LOW,
                "medium": types.ThinkingLevel.MEDIUM,
                "high": types.ThinkingLevel.HIGH,
            }
            enum_value = level_map.get(normalized)
            if not enum_value:
                logger.warning(
                    "Unsupported thinking level '%s' for model '%s'; skipping thinking config",
                    thinking_level,
                    model_name,
                )
                return None
            return types.ThinkingConfig(thinking_level=enum_value)

        if backend == "thinking_budget":
            if normalized in {"off", "minimal"}:
                return types.ThinkingConfig(thinking_budget=0)
            if normalized in {"low", "medium", "high"}:
                return types.ThinkingConfig(thinking_budget=-1)
            return None

        if backend == "none":
            logger.debug(
                "Thinking backend disabled for model '%s'; using model defaults",
                model_name,
            )
            return None

        logger.warning(
            "Unknown thinking backend '%s' for model '%s'; using model defaults",
            backend,
            model_name,
        )
        return None

    @staticmethod
    def _sorted_context(messages: Optional[List[MessageContext]]) -> List[MessageContext]:
        """Return context messages in deterministic Discord conversation order."""
        if not messages:
            return []
        return sorted(messages, key=lambda msg: (msg.timestamp, msg.message_id))

    def _finalize_context_selection(
        self,
        context: List[MessageContext],
        selected_message_ids: List[int],
        max_messages: int,
        anchor_message_ids: Optional[set[int]] = None,
    ) -> List[MessageContext]:
        """Validate model-selected ids and return an ordered bounded context slice."""
        sorted_context = self._sorted_context(context)
        if not sorted_context:
            return []

        id_to_msg = {msg.message_id: msg for msg in sorted_context}
        ordered_unique_ids = []
        seen_ids = set()

        for message_id in selected_message_ids:
            if message_id not in id_to_msg or message_id in seen_ids:
                continue
            ordered_unique_ids.append(message_id)
            seen_ids.add(message_id)

        for anchor_id in anchor_message_ids or set():
            if anchor_id in id_to_msg and anchor_id not in seen_ids:
                ordered_unique_ids.append(anchor_id)
                seen_ids.add(anchor_id)

        if not ordered_unique_ids:
            anchor_ids = set(anchor_message_ids or set())
            if not anchor_ids:
                return []
            return [msg for msg in sorted_context if msg.message_id in anchor_ids][:max_messages]

        ordered_unique_ids = ordered_unique_ids[-max_messages:]

        selected_ids = set(ordered_unique_ids)
        return [msg for msg in sorted_context if msg.message_id in selected_ids]

    async def select_relevant_context(
        self,
        user_message: str,
        context: Optional[List[MessageContext]],
        max_messages: int,
        anchor_message_ids: Optional[set[int]] = None,
    ) -> List[MessageContext]:
        """
        Use the fast router model to reduce a large Discord history to relevant context.

        The final response model should see only the selected ordered slice, not the
        whole fetched channel history. On selector failure, fall back to the
        newest bounded context window.
        """
        sorted_context = self._sorted_context(context)
        max_messages = max(1, int(max_messages or 1))
        if not sorted_context:
            return []
        if len(sorted_context) <= max_messages:
            return sorted_context

        anchor_ids = set(anchor_message_ids or set())
        if not self.client:
            logger.warning("Gemini client unavailable; using recent context fallback")
            return sorted_context[-max_messages:]

        candidate_lines = []
        total = len(sorted_context)
        for idx, msg in enumerate(sorted_context, start=1):
            content = (msg.content or "").replace("\n", " ").strip()
            if len(content) > 420:
                content = content[:420] + "..."
            reply_text = f" | replied_to={msg.replied_to_id}" if msg.replied_to_id else ""
            candidate_lines.append(
                f"[CTX_MSG_{idx:03d}/{total} | message_id={msg.message_id} | "
                f"time={msg.timestamp.isoformat()}{reply_text}] {msg.author}: {content}"
            )

        selector_prompt = f"""Select the Discord messages needed to answer the current user naturally.

Current user message:
{user_message}

Candidate channel history is ordered oldest to newest. Higher CTX_MSG numbers are more recent.
Return JSON only with this schema:
{{
  "selected_message_ids": [123, 456]
}}

Selection rules:
- Pick at most {max_messages} messages.
- Include messages directly referenced by the user, replied-to messages, and relevant image/file messages.
- Include nearby messages only when they clarify what a selected message means.
- Prefer recent messages when relevance is similar.
- Do not include unrelated chatter just because it is available.
- If the request is self-contained, return an empty list.

Candidate messages:
{chr(10).join(candidate_lines)}
"""

        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.router_model_name,
                contents=selector_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=max(512, self.config.router_max_output_tokens * 6),
                    response_mime_type="application/json",
                ),
            )
            raw_text = getattr(response, "text", "") or ""
            result = json.loads(raw_text)
            if isinstance(result, list) and result:
                result = result[0]
            selected_ids_raw = result.get("selected_message_ids", []) if isinstance(result, dict) else []
            selected_ids = []
            for raw_id in selected_ids_raw:
                try:
                    selected_ids.append(int(raw_id))
                except (TypeError, ValueError):
                    continue

            selected_context = self._finalize_context_selection(
                sorted_context,
                selected_ids,
                max_messages,
                anchor_ids,
            )
            logger.info(
                "Context selector reduced %s candidate messages to %s selected messages",
                len(sorted_context),
                len(selected_context),
            )
            return selected_context

        except Exception as exc:
            logger.warning("Context selector failed; using recent context fallback: %s", exc)
            return sorted_context[-max_messages:]

    async def embed_texts(
        self,
        texts: List[str],
        *,
        model_name: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[Optional[List[float]]]:
        """Embed text for local RAG retrieval without mutating generation model state."""
        if not texts:
            return []
        if not self.client:
            logger.warning("Gemini client unavailable; embeddings skipped")
            return [None for _ in texts]

        target_model = model_name or getattr(self.config, "rag_embedding_model", "gemini-embedding-2")
        sanitized_texts = [(text or "").strip() for text in texts]
        try:
            model_id = target_model.rsplit("/", 1)[-1].lower()
            is_embedding_2 = model_id.startswith("gemini-embedding-2")
            request_texts = sanitized_texts
            if is_embedding_2 and task_type == "RETRIEVAL_DOCUMENT":
                request_texts = [
                    text
                    if text.lower().startswith("title:") and "| text:" in text.lower()
                    else f"title: none | text: {text}"
                    for text in sanitized_texts
                ]
            elif is_embedding_2 and task_type == "RETRIEVAL_QUERY":
                request_texts = [f"task: search result | query: {text}" for text in sanitized_texts]

            if is_embedding_2:
                contents = [
                    types.Content(parts=[types.Part.from_text(text=text)])
                    for text in request_texts
                ]
            else:
                contents = request_texts

            config_kwargs = {
                "output_dimensionality": int(
                    getattr(self.config, "rag_embedding_dimensions", 768)
                )
            }
            if task_type and not is_embedding_2:
                config_kwargs["task_type"] = task_type
            request_kwargs = {
                "model": target_model,
                "contents": contents,
            }
            request_kwargs["config"] = types.EmbedContentConfig(**config_kwargs)
            response = await self.client.aio.models.embed_content(**request_kwargs)
            embeddings = getattr(response, "embeddings", None) or []
            vectors: List[Optional[List[float]]] = []
            for embedding in embeddings:
                values = getattr(embedding, "values", None)
                vectors.append([float(value) for value in values] if values else None)
            while len(vectors) < len(sanitized_texts):
                vectors.append(None)
            logger.info("Generated %s/%s RAG embedding(s) with model %s", sum(1 for item in vectors if item), len(texts), target_model)
            return vectors[: len(sanitized_texts)]
        except Exception as exc:
            logger.warning("Gemini embedding request failed with model %s: %s", target_model, exc)
            return [None for _ in sanitized_texts]

    def _resolve_response_request_routing(
        self,
        prompt: str,
        model_override: Optional[str],
        prompt_mode_override: Optional[str],
        thinking_level_override: Optional[str],
        complexity_override: Optional[str],
        search_override: Optional[bool],
    ) -> GeminiRequestRouting:
        """Resolve per-request precedence without mutating shared runtime state."""
        target_model = model_override if model_override else self._current_model_name
        if complexity_override in {"low", "medium", "high"}:
            effective_complexity = complexity_override
        else:
            effective_complexity = self._current_complexity_level
            if complexity_override is not None:
                logger.warning(
                    "Invalid complexity override '%s'; using '%s'",
                    complexity_override,
                    effective_complexity,
                )

        if thinking_level_override is not None:
            thinking_level = self._normalize_thinking_level(thinking_level_override)
        else:
            thinking_level = self._resolve_thinking_level_for_request(
                target_model,
                prompt_mode_override=prompt_mode_override,
                complexity_level=effective_complexity,
            )

        logger.info("=" * 80)
        logger.info("API CALL INITIATED")
        logger.info("Model: %s", target_model)
        logger.info("Thinking level: %s", thinking_level)
        logger.info("Gemini API key configured for request")

        use_search = (
            search_override
            if search_override is not None
            else self._should_use_search(prompt)
        )
        if use_search:
            logger.info("Google Search enabled for this request")

        return GeminiRequestRouting(
            model_name=target_model,
            complexity_level=effective_complexity,
            thinking_level=thinking_level,
            use_search=use_search,
        )

    def _build_response_request_content(
        self,
        prompt: str,
        context: Optional[List[MessageContext]],
        images: Optional[List],
        image_context: Optional[List[dict]],
        audio_files: Optional[List[dict]],
        personality_prompt: Optional[str],
        language: Optional[str],
        complexity_level: str,
    ) -> GeminiRequestContent:
        """Format text and append media parts in provider-visible order."""
        normalized_image_context = self._normalize_image_context(images, image_context)
        formatted_prompt = self.format_prompt(
            prompt,
            context,
            personality_prompt=personality_prompt,
            language=language,
            image_context=normalized_image_context,
            complexity_override=complexity_level,
        )
        content_parts = [formatted_prompt]

        if images and len(images) > 0:
            for image in images:
                image_buffer = io.BytesIO()
                image.save(image_buffer, format="PNG")
                content_parts.append(
                    types.Part.from_bytes(
                        data=image_buffer.getvalue(),
                        mime_type="image/png",
                    )
                )
            logger.info("Added %s image(s) to request", len(images))

        if audio_files and len(audio_files) > 0:
            for audio in audio_files:
                content_parts.append(
                    types.Part.from_bytes(
                        data=audio["data"],
                        mime_type=audio["mime_type"],
                    )
                )
            logger.info("Added %s audio file(s) to request", len(audio_files))

        logger.info("Input prompt length: %s characters", len(formatted_prompt))
        logger.debug("Input prompt preview redacted")
        return GeminiRequestContent(
            parts=content_parts,
            formatted_prompt=formatted_prompt,
            image_context=normalized_image_context,
        )

    @staticmethod
    def _log_response_request_context(
        context: Optional[List[MessageContext]],
        image_context: Optional[List[dict]],
    ) -> None:
        """Log request context metadata without exposing prompt contents."""
        if context:
            logger.info("Context messages provided: %s", len(context))
            for index, message in enumerate(context):
                logger.info(
                    "  Context[%s]: Author=%s, Length=%s chars, Timestamp=%s",
                    index,
                    message.author,
                    len(message.content),
                    message.timestamp,
                )
        else:
            logger.info("No context messages provided")

        if image_context:
            logger.info("Image context entries provided: %s", len(image_context))
            for item in image_context:
                logger.info(
                    "  Image[%s]: source=%s, message_order=%s, message_id=%s, attachment=%s",
                    item.get("image_index"),
                    item.get("source_type"),
                    item.get("source_message_order"),
                    item.get("source_message_id"),
                    item.get("attachment_name"),
                )

    def _complete_response_request_plan(
        self,
        routing: GeminiRequestRouting,
    ) -> GeminiRequestPlan:
        """Select the per-attempt timeout after request content is prepared."""
        timeout_duration = self.get_timeout_for_model(
            routing.model_name,
            thinking_level=routing.thinking_level,
        )
        logger.info(
            "Using timeout: %ss for model %s (thinking_level: %s)",
            timeout_duration,
            routing.model_name,
            routing.thinking_level,
        )
        return GeminiRequestPlan(
            model_name=routing.model_name,
            complexity_level=routing.complexity_level,
            thinking_level=routing.thinking_level,
            use_search=routing.use_search,
            timeout_duration=timeout_duration,
        )

    def _build_successful_response(
        self,
        attempt_result: GeminiAttemptResult,
        plan: GeminiRequestPlan,
        response_text: str,
        *,
        truncated: bool,
    ) -> APIResponse:
        """Log, enrich, and construct a successful public response."""
        if truncated:
            logger.warning(
                "Gemini API response hit max tokens, returning partial response (%s chars)",
                len(response_text),
            )
        else:
            logger.info("Successfully generated response from Gemini API")
        logger.info("Output token count (estimated): %s", len(response_text.split()))
        logger.info("Output length: %s characters", len(response_text))
        logger.debug("Output preview redacted")
        logger.info("=" * 80)

        self.performance_logger.log_api_call(
            api_name="gemini_generate_content",
            duration=attempt_result.duration,
            success=True,
        )
        grounding_sources = self._extract_grounding_sources(
            attempt_result.response,
            plan.use_search,
        )
        token_usage = self._extract_token_usage(attempt_result.response)
        model_info = self._get_model_display_name(
            model_name=plan.model_name,
            thinking_level=plan.thinking_level,
        )
        return build_success_response(
            response_text,
            model_info,
            plan.use_search,
            grounding_sources,
            token_usage,
            truncated=truncated,
        )

    def _interpret_provider_response(
        self,
        attempt_result: GeminiAttemptResult,
        plan: GeminiRequestPlan,
    ) -> Optional[APIResponse]:
        """Interpret one provider response while preserving finish-reason behavior."""
        response = attempt_result.response
        duration = attempt_result.duration
        logger.info("-" * 80)
        logger.info("API RESPONSE RECEIVED (Duration: %.3fs)", duration)
        logger.info("Response object type: %s", type(response))

        if not response:
            logger.warning("Gemini API returned None response")
            logger.info("=" * 80)
            self.performance_logger.log_api_call(
                api_name="gemini_generate_content",
                duration=duration,
                success=False,
                error_type="empty_response",
            )
            return build_error_response(
                "empty_response",
                "The AI returned no response",
            )

        if not hasattr(response, "candidates") or not response.candidates:
            logger.warning("Gemini API returned response without candidates")
            logger.info("=" * 80)
            self.performance_logger.log_api_call(
                api_name="gemini_generate_content",
                duration=duration,
                success=False,
                error_type="empty_response",
            )
            return build_error_response(
                "empty_response",
                "The AI generated an empty response",
            )

        candidate = response.candidates[0]
        finish_reason_raw = candidate.finish_reason
        finish_reason = self._normalize_finish_reason(finish_reason_raw)
        logger.info("Number of candidates: %s", len(response.candidates))
        logger.info(
            "Finish reason: %s -> normalized: %s (%s)",
            finish_reason_raw,
            finish_reason,
            self._get_finish_reason_name(finish_reason_raw),
        )

        if hasattr(candidate, "safety_ratings") and candidate.safety_ratings:
            logger.info("Safety ratings:")
            for rating in candidate.safety_ratings:
                logger.info("  %s: %s", rating.category, rating.probability)

        if finish_reason == 1:
            response_text = self._get_response_text(response)
            if response_text:
                return self._build_successful_response(
                    attempt_result,
                    plan,
                    response_text,
                    truncated=False,
                )
            return None

        if finish_reason == 2:
            response_text = self._get_response_text(response)
            if response_text:
                return self._build_successful_response(
                    attempt_result,
                    plan,
                    response_text,
                    truncated=True,
                )
            logger.error("Max tokens hit but no response text available")
            logger.info("=" * 80)
            self.performance_logger.log_api_call(
                api_name="gemini_generate_content",
                duration=duration,
                success=False,
                error_type="max_tokens_no_content",
            )
            return build_error_response(
                "max_tokens",
                "The response was too complex to generate. Please try breaking your question into smaller parts.",
            )

        if finish_reason == 3:
            logger.warning("Gemini API response blocked by safety filters")
            if hasattr(candidate, "safety_ratings") and candidate.safety_ratings:
                logger.warning("Triggered safety ratings:")
                for rating in candidate.safety_ratings:
                    logger.warning("  %s: %s", rating.category, rating.probability)
            logger.info("=" * 80)
            self.performance_logger.log_api_call(
                api_name="gemini_generate_content",
                duration=duration,
                success=False,
                error_type="safety_filter",
            )
            return build_error_response(
                "safety_filter",
                "I can't respond to that due to content safety guidelines. Please rephrase your message.",
            )

        if finish_reason == 4:
            logger.warning("Gemini API response blocked due to recitation")
            logger.info("=" * 80)
            self.performance_logger.log_api_call(
                api_name="gemini_generate_content",
                duration=duration,
                success=False,
                error_type="recitation",
            )
            return build_error_response(
                "recitation",
                "I can't provide that response as it may be copyrighted content.",
            )

        logger.warning(
            "Gemini API returned unexpected finish_reason: %s",
            finish_reason,
        )
        logger.info("=" * 80)
        self.performance_logger.log_api_call(
            api_name="gemini_generate_content",
            duration=duration,
            success=False,
            error_type="unknown_finish_reason",
        )
        return build_error_response(
            "unknown_finish_reason",
            "Received an unexpected response from the AI. Please try again.",
        )

    async def _execute_response_attempt(
        self,
        request_content: GeminiRequestContent,
        plan: GeminiRequestPlan,
        on_chunk: Optional[callable],
        attempt: int,
    ) -> GeminiAttemptResult:
        """Execute one provider attempt under the request-scoped timeout."""
        logger.debug(
            "Generating response (attempt %s/%s)",
            attempt + 1,
            self.config.max_retries + 1,
        )
        start_time = time.time()
        response = await asyncio.wait_for(
            self._generate_response_async(
                request_content.parts,
                plan.use_search,
                on_chunk,
                model_override=plan.model_name,
                thinking_level=plan.thinking_level,
                complexity_level=plan.complexity_level,
            ),
            timeout=plan.timeout_duration,
        )
        return GeminiAttemptResult(
            response=response,
            duration=time.time() - start_time,
        )

    def _handle_response_timeout(
        self,
        attempt: int,
        plan: GeminiRequestPlan,
    ) -> Optional[APIResponse]:
        """Record a timed-out attempt and build the final timeout response."""
        logger.warning("Gemini API request timed out (attempt %s)", attempt + 1)
        logger.warning("Timeout duration: %ss", plan.timeout_duration)
        logger.info("=" * 80)
        self.performance_logger.log_api_call(
            api_name="gemini_generate_content",
            duration=plan.timeout_duration,
            success=False,
            error_type="timeout",
        )
        if attempt == self.config.max_retries:
            return build_error_response(
                "timeout",
                "Request timed out after multiple attempts",
            )
        return None

    async def _handle_response_exception(
        self,
        error: Exception,
        attempt: int,
    ) -> Optional[APIResponse]:
        """Classify a failed attempt, back off when retryable, or return an error."""
        logger.error(
            "Exception during API call: %s: %s",
            type(error).__name__,
            str(error),
        )
        logger.error("Exception details: %r", error, exc_info=True)
        error_context = self.error_manager.handle_api_error(
            error,
            f"Gemini API attempt {attempt + 1}",
        )
        logger.error("Error type: %s", error_context.error_type.value)
        logger.error("Should retry: %s", error_context.should_retry)
        if error_context.retry_after:
            logger.error("Retry after: %ss", error_context.retry_after)
        logger.info("=" * 80)
        self.performance_logger.log_api_call(
            api_name="gemini_generate_content",
            duration=0,
            success=False,
            error_type=error_context.error_type.value,
        )

        if error_context.should_retry and attempt < self.config.max_retries:
            wait_time = (
                error_context.retry_after
                or self._calculate_backoff_delay(attempt)
            )
            logger.info(
                "Retrying after %.2f seconds (attempt %s)",
                wait_time,
                attempt + 1,
            )
            await asyncio.sleep(wait_time)
            return None

        return build_error_response(
            error_context.error_type.value,
            error_context.user_message,
        )

    async def _run_response_attempts(
        self,
        request_content: GeminiRequestContent,
        plan: GeminiRequestPlan,
        on_chunk: Optional[callable],
    ) -> APIResponse:
        """Own retry, timeout, cancellation, and response interpretation flow."""
        for attempt in range(self.config.max_retries + 1):
            try:
                attempt_result = await self._execute_response_attempt(
                    request_content,
                    plan,
                    on_chunk,
                    attempt,
                )
                interpreted_response = self._interpret_provider_response(
                    attempt_result,
                    plan,
                )
                if interpreted_response is not None:
                    return interpreted_response
            except asyncio.TimeoutError:
                timeout_response = self._handle_response_timeout(attempt, plan)
                if timeout_response is not None:
                    return timeout_response
            except Exception as error:
                error_response = await self._handle_response_exception(error, attempt)
                if error_response is not None:
                    return error_response

        return build_error_response(
            "unknown_error",
            "An unexpected error occurred",
        )

    async def generate_response(
        self,
        prompt: str,
        context: Optional[List[MessageContext]] = None,
        images: Optional[List] = None,
        image_context: Optional[List[dict]] = None,
        audio_files: Optional[List[dict]] = None,
        on_chunk: Optional[callable] = None,
        model_override: Optional[str] = None,
        prompt_mode_override: Optional[str] = None,
        thinking_level_override: Optional[str] = None,
        complexity_override: Optional[str] = None,
        search_override: Optional[bool] = None,
        personality_prompt: Optional[str] = None,
        language: Optional[str] = None,
    ) -> APIResponse:
        """
        Generate a response using the Gemini API with retry logic.
        
        Args:
            prompt: The user's message/prompt
            context: Optional conversation context for better responses
            images: Optional list of PIL Image objects to include in the request
            image_context: Optional image metadata aligned to `images` order
            audio_files: Optional list of audio file dicts {'data': bytes, 'mime_type': str}
            on_chunk: Optional async callback for streaming response chunks
            model_override: Optional model name to use for this specific request
            prompt_mode_override: Optional prompt mode ("short" or "thinking") for this request
            thinking_level_override: Optional explicit thinking level for this request
            complexity_override: Optional complexity tier ("low" | "medium" | "high") for this request
            search_override: Optional boolean to force enable/disable search for this request

        Returns:
            APIResponse containing the generated response or error information
        """
        if not self.client:
            logger.error("Attempted to generate response but Gemini client is not configured")
            return build_error_response(
                "configuration_error",
                "Gemini API not properly configured. Please check your GEMINI_API_KEY environment variable.",
            )

        routing = self._resolve_response_request_routing(
            prompt,
            model_override,
            prompt_mode_override,
            thinking_level_override,
            complexity_override,
            search_override,
        )
        request_content = self._build_response_request_content(
            prompt,
            context,
            images,
            image_context,
            audio_files,
            personality_prompt,
            language,
            routing.complexity_level,
        )
        self._log_response_request_context(
            context,
            request_content.image_context,
        )
        plan = self._complete_response_request_plan(routing)
        return await self._run_response_attempts(
            request_content,
            plan,
            on_chunk,
        )
    
    async def _generate_response_async(
        self,
        content,
        use_search: bool = False,
        on_chunk: Optional[callable] = None,
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
        complexity_level: Optional[str] = None,
    ):
        """
        Async wrapper for Gemini API call.
        
        Args:
            content: Content to send to the API (string for text-only, list for multimodal)
            use_search: Whether to use the model with Google Search enabled
            on_chunk: Optional async callback for streaming chunks
            model_override: Optional model name to use
            thinking_level: Optional resolved thinking level for this request
            complexity_level: Optional resolved complexity tier for this request
            
        Returns:
            Generated response from Gemini API
        """
        target_model = model_override if model_override else self._current_model_name
        logger.info(f"Making API call to model: {target_model}")
        logger.info(f"Using search-enabled model: {use_search}")
        logger.info(f"Resolved thinking level: {thinking_level or 'default'}")

        has_multimodal_parts = (
            isinstance(content, list)
            and any(not isinstance(part, str) for part in content)
        )
        if use_search and has_multimodal_parts:
            logger.info("Disabling Google Search for multimodal request so media parts are preserved")
            use_search = False
        
        # Configure tools
        tools = []
        if use_search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        
        # Configure generation config from config.yaml settings
        safety_mapping = {
            'harassment': (types.HarmCategory.HARM_CATEGORY_HARASSMENT, self.config.safety_harassment),
            'hate_speech': (types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, self.config.safety_hate_speech),
            'sexually_explicit': (types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, self.config.safety_sexually_explicit),
            'dangerous_content': (types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, self.config.safety_dangerous_content),
        }
        threshold_map = {
            'BLOCK_NONE': types.HarmBlockThreshold.BLOCK_NONE,
            'BLOCK_LOW_AND_ABOVE': types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            'BLOCK_MEDIUM_AND_ABOVE': types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            'BLOCK_HIGH_AND_ABOVE': types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }
        safety_settings = [
            types.SafetySetting(
                category=cat,
                threshold=threshold_map.get(thresh, types.HarmBlockThreshold.BLOCK_NONE)
            )
            for cat, thresh in safety_mapping.values()
        ]

        max_output_tokens = self._get_max_output_tokens_for_complexity(
            complexity_level=complexity_level,
            thinking_level=thinking_level,
        )
        logger.info(
            "Using max_output_tokens=%s for complexity=%s (target_model=%s)",
            max_output_tokens,
            complexity_level or self._current_complexity_level,
            target_model,
        )

        thinking_config = self._build_thinking_config_for_model(
            target_model,
            thinking_level or "default",
        )
        if thinking_config:
            logger.info("Applying API thinking config: %s", thinking_config)

        config = types.GenerateContentConfig(
            tools=tools,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=max_output_tokens,
            safety_settings=safety_settings,
            thinking_config=thinking_config,
        )
        
        content_str = content
        
        model_name = target_model
        
        logger.info(f"Using model name: {model_name}")
        
        # Use async client
        if on_chunk:
            # Streaming mode
            logger.info("Starting streaming response...")
            response_stream = await self.client.aio.models.generate_content_stream(
                model=model_name,
                contents=content_str,
                config=config,
            )
            
            full_text = ""
            last_chunk = None
            
            async for chunk in response_stream:
                last_chunk = chunk
                if chunk.text:
                    full_text += chunk.text
                    try:
                        await on_chunk(chunk.text)
                    except Exception as e:
                        logger.error(f"Error in streaming callback: {e}")
            
            # Patch the last chunk to contain the full text so downstream code works
            # We create a wrapper object that mimics the response interface
            class StreamedResponse:
                def __init__(self, chunk, text):
                    self._chunk = chunk
                    self.text = text
                    self.candidates = chunk.candidates if hasattr(chunk, 'candidates') else []
                    self.usage_metadata = getattr(chunk, 'usage_metadata', None)
                    
                def __getattr__(self, name):
                    return getattr(self._chunk, name)
            
            return StreamedResponse(last_chunk, full_text)
            
        else:
            # Non-streaming mode (standard)
            return await self.client.aio.models.generate_content(
                model=model_name,
                contents=content_str,
                config=config,
            )
    
    def _get_response_text(self, response) -> Optional[str]:
        """
        Safely extract text from response.
        
        Args:
            response: Response object from SDK
            
        Returns:
            Text content or None if not available
        """
        try:
            # Try direct .text attribute
            if hasattr(response, 'text') and response.text:
                return response.text
            
            # Try candidates[0].content.parts[0].text
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                return part.text
        except Exception as e:
            logger.error(f"Error extracting text from response: {e}")
        
        return None
    
    def _normalize_finish_reason(self, finish_reason):
        """
        Normalize finish reason from SDK enum format.
        
        Args:
            finish_reason: Finish reason from SDK (enum)
            
        Returns:
            Integer representation: 1=STOP, 2=MAX_TOKENS, 3=SAFETY, etc.
        """
        # If it's already an integer, return it
        if isinstance(finish_reason, int):
            return finish_reason
        
        # Handle SDK enum format
        if hasattr(finish_reason, 'name'):
            reason_name = finish_reason.name.upper()
            reason_map = {
                'STOP': 1,
                'MAX_TOKENS': 2,
                'SAFETY': 3,
                'PROHIBITED_CONTENT': 3,
                'BLOCKLIST': 3,
                'SPII': 3,
                'RECITATION': 4,
                'OTHER': 5,
                'UNSPECIFIED': 0
            }
            return reason_map.get(reason_name, 0)
        
        # Try to convert string representation
        reason_str = str(finish_reason).upper()
        if 'STOP' in reason_str:
            return 1
        elif 'MAX_TOKENS' in reason_str:
            return 2
        elif 'SAFETY' in reason_str or 'PROHIBITED' in reason_str or 'BLOCK' in reason_str or 'SPII' in reason_str:
            return 3
        elif 'RECITATION' in reason_str:
            return 4
        elif 'OTHER' in reason_str:
            return 5
        
        return 0  # UNSPECIFIED
    
    def _get_finish_reason_name(self, finish_reason) -> str:
        """
        Get human-readable name for finish reason code.
        
        Args:
            finish_reason: Numeric finish reason code or enum
            
        Returns:
            Human-readable name for the finish reason
        """
        # Normalize first
        normalized = self._normalize_finish_reason(finish_reason)
        
        finish_reasons = {
            0: "UNSPECIFIED",
            1: "STOP",
            2: "MAX_TOKENS",
            3: "SAFETY",
            4: "RECITATION",
            5: "OTHER"
        }
        return finish_reasons.get(normalized, f"UNKNOWN({finish_reason})")
    
    def _get_system_instruction(
        self,
        complexity_level: Optional[str] = None,
    ) -> str:
        """
        Get the appropriate system instruction based on complexity tier.
        
        Returns:
            System instruction string tailored to the model complexity
        """
        effective_complexity = (complexity_level or self._current_complexity_level or "low").strip().lower()
        if effective_complexity == "high":
            logger.info("Using HIGH COMPLEXITY system prompt")
            return self.config.system_prompt_high_complexity.rstrip()
        elif effective_complexity == "medium":
            logger.info("Using MEDIUM COMPLEXITY system prompt")
            return self.config.system_prompt_medium_complexity.rstrip()
        logger.info("Using LOW COMPLEXITY system prompt")
        return self.config.system_prompt_low_complexity.rstrip()
    
    @staticmethod
    def _normalize_image_context(images: Optional[List], image_context: Optional[List[dict]]) -> Optional[List[dict]]:
        """Ensure image context metadata is 1:1 and in-order with provided images."""
        if not images:
            return None

        normalized: List[dict] = []
        source_context = image_context or []

        if image_context and len(image_context) != len(images):
            logger.warning(
                "Image metadata count (%s) does not match image count (%s); filling missing entries.",
                len(image_context),
                len(images),
            )

        for idx in range(len(images)):
            base = dict(source_context[idx]) if idx < len(source_context) else {}
            base["image_index"] = idx + 1
            base.setdefault("source_type", "unknown")
            base.setdefault("source_message_order", "UNKNOWN")
            base.setdefault("source_message_id", "unknown")
            base.setdefault("source_timestamp", "unknown")
            base.setdefault("attachment_name", f"image_{idx + 1}.png")
            base.setdefault("attachment_index", 1)
            normalized.append(base)

        return normalized

    def format_prompt(
        self,
        user_message: str,
        context: Optional[List[MessageContext]] = None,
        personality_prompt: Optional[str] = None,
        language: Optional[str] = None,
        image_context: Optional[List[dict]] = None,
        complexity_override: Optional[str] = None,
    ) -> str:
        """
        Format the user message and context into an optimal prompt for Gemini API.

        Args:
            user_message: The user's message/question
            context: Optional conversation context
            personality_prompt: Optional personality/tone instruction to prepend
            language: Optional language preference for the response
            image_context: Optional image metadata aligned to attached image parts
            complexity_override: Optional complexity tier for request-scoped formatting

        Returns:
            Formatted prompt string for the Gemini API
        """
        logger.debug("Formatting prompt for API call")
        logger.debug(f"User message length: {len(user_message)} characters")
        logger.debug(f"Context messages: {len(context) if context else 0}")

        prompt_parts = []

        # Add system instruction based on the current request complexity.
        system_instruction = self._get_system_instruction(
            complexity_level=complexity_override,
        )
        logger.debug(
            "Using system instruction for complexity=%s",
            complexity_override or self._current_complexity_level,
        )
        prompt_parts.append(system_instruction)

        # Add personality/tone instruction if set
        if personality_prompt:
            prompt_parts.append(f"\n[TONE INSTRUCTION]: {personality_prompt}")
            logger.debug(f"Applied personality prompt: {personality_prompt[:60]}...")

        # Add language preference if set
        if language and language != "auto":
            prompt_parts.append(f"\n[LANGUAGE INSTRUCTION]: Always respond in {language}.")

        # Add conversation context if provided
        if context and len(context) > 0:
            has_rag_metadata = any(getattr(msg, "retrieval_source", None) for msg in context)
            if has_rag_metadata:
                prompt_parts.append("\n--- Retrieved Discord Context (RAG) ---")
                prompt_parts.append(
                    "This is a compact, relevance-ranked retrieval pack from Discord history. "
                    "It may include pinned memories, direct reply anchors, recent continuity, lexical matches, and semantic matches. "
                    "Use provenance labels when helpful, ignore unrelated items, and prioritize the current user message."
                )
                pins = [msg for msg in context if getattr(msg, "is_pinned_memory", False)]
                anchors = [
                    msg for msg in context
                    if not getattr(msg, "is_pinned_memory", False)
                    and "reply_anchor" in (getattr(msg, "retrieval_source", "") or "")
                ]
                others = [
                    msg for msg in context
                    if msg not in pins and msg not in anchors
                ]
                anchors = sorted(anchors, key=lambda msg: (msg.timestamp, msg.message_id))
                # Lowest-confidence retrieved items first so the strongest item sits closest to the user prompt.
                others = sorted(
                    others,
                    key=lambda msg: (
                        getattr(msg, "retrieval_score", 0.0) or 0.0,
                        msg.timestamp,
                        msg.message_id,
                    ),
                )
                sorted_context = pins + anchors + others
            else:
                prompt_parts.append("\n--- Selected Conversation Context (oldest to newest) ---")
                prompt_parts.append(
                    "This is a relevance-selected slice of Discord history, not the whole channel. "
                    "Higher CTX_MSG numbers are more recent. Use it only when it helps answer the current user; "
                    "ignore unrelated chatter and prioritize the current user message, reply relationships, and the newest relevant messages."
                )
                # Sort context by timestamp to ensure chronological order
                sorted_context = sorted(context, key=lambda msg: (msg.timestamp, msg.message_id))
            
            for idx, msg in enumerate(sorted_context, start=1):
                # Include explicit sequence + id for deterministic ordering references
                time_str = msg.timestamp.isoformat()
                
                # Mark replied-to messages for clarity
                reply_indicator = " (replying)" if msg.is_reply else ""
                source = getattr(msg, "retrieval_source", None)
                score = getattr(msg, "retrieval_score", None)
                reason = getattr(msg, "retrieval_reason", None)
                source_suffix = ""
                if source:
                    score_text = f", score={score:.3f}" if isinstance(score, (int, float)) else ""
                    reason_text = f", reason={reason}" if reason else ""
                    source_suffix = f" | source={source}{score_text}{reason_text}"
                
                formatted_msg = (
                    f"[CTX_MSG_{idx:03d} | message_id={msg.message_id} | time={time_str}{source_suffix}] "
                    f"{msg.author}{reply_indicator}: {msg.content}"
                )
                prompt_parts.append(formatted_msg)
            
            prompt_parts.append("--- End Context ---\n")

        # Add image ordering metadata if images are provided
        if image_context and len(image_context) > 0:
            prompt_parts.append("\n--- Image Context Mapping ---")
            prompt_parts.append(
                "Images are attached after this text in the exact order listed below. "
                "Use these mappings to match each image to the correct message context."
            )

            for item in image_context:
                image_idx = item.get("image_index", "?")
                pdf_page = item.get("pdf_page_number")
                pdf_suffix = f", pdf_page={pdf_page}" if pdf_page else ""
                prompt_parts.append(
                    f"[IMG_{image_idx}] "
                    f"source={item.get('source_type')}, "
                    f"source_message_order={item.get('source_message_order')}, "
                    f"source_message_id={item.get('source_message_id')}, "
                    f"source_time={item.get('source_timestamp')}, "
                    f"attachment={item.get('attachment_name')}, "
                    f"attachment_index={item.get('attachment_index')}"
                    f"{pdf_suffix}"
                )

            prompt_parts.append("--- End Image Context Mapping ---\n")

        # Add the current user message
        prompt_parts.append(f"User: {user_message}")
        
        prompt_parts.append("\nGrok:")
        
        formatted = "\n".join(prompt_parts)
        logger.debug(f"Formatted prompt total length: {len(formatted)} characters")
        
        return formatted
    

    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay with jitter.
        
        Args:
            attempt: Current attempt number (0-based)
            
        Returns:
            Delay in seconds
        """
        # Exponential backoff: 2^attempt seconds, with jitter
        base_delay = 2 ** attempt
        jitter = random.uniform(0.1, 0.5)  # Add 0.1-0.5 seconds of jitter
        return min(base_delay + jitter, 30.0)  # Cap at 30 seconds
