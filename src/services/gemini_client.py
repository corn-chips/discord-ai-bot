"""
Gemini API client for the Discord Grok Bot.

This module provides the GeminiClient class for interfacing with Google's Gemini API
to generate AI-powered responses based on user prompts and conversation context.
"""

import asyncio
import io
import logging
import random
import re
from typing import List, Optional

from google import genai
from google.genai import types

from ..config import BotConfig
from ..models.data_models import APIResponse, MessageContext, TokenUsage
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger
from ..utils.token_extraction import extract_token_usage


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
        self._current_complexity_level = "low"
        self._prompt_mode = "short"  # Default to short mode, can be "short" or "thinking"
        self._thinking_single_use = False  # Changed to False for persistent mode via command
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
            
            logger.info(f"API Key length: {len(self.config.gemini_api_key)} characters")
            logger.info(f"API Key (first 8 chars): {self.config.gemini_api_key[:8]}...")
            logger.info(f"API Key (last 4 chars): ...{self.config.gemini_api_key[-4:]}")
            
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
    
    def get_prompt_mode(self) -> str:
        """Get the current prompt mode (short or thinking)."""
        return self._prompt_mode
    
    def set_prompt_mode(self, mode: str) -> bool:
        """
        Set the prompt mode for system instructions.
        
        Args:
            mode: Either "short" for concise responses or "thinking" for detailed analysis
            
        Returns:
            True if successful, False otherwise
        """
        if mode not in ["short", "thinking"]:
            logger.error(f"Invalid prompt mode: {mode}. Must be 'short' or 'thinking'")
            return False
        
        old_mode = self._prompt_mode
        self._prompt_mode = mode
        logger.info(f"Prompt mode changed from '{old_mode}' to '{mode}'")
        return True

    def set_force_search(self, enabled: bool) -> None:
        """
        Enable or disable forced Google Search for all queries.
        
        Args:
            enabled: True to force search, False to use auto-detection
        """
        self._force_search = enabled
        logger.info(f"DeepSearch mode set to: {enabled}")
    
    def get_force_search(self) -> bool:
        """Get the current DeepSearch mode state."""
        return self._force_search
    
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
        try:
            logger.info(f"Old model: {old_model}")
            logger.info(f"New model: {model_name}")
            self._current_model_name = model_name
            
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
            logger.info("=" * 80)
            return False
    
    def get_timeout_for_current_model(self) -> int:
        """
        Get the appropriate timeout duration based on current model and mode.
        
        Returns:
            Timeout in seconds
        """
        return self.get_timeout_for_model(self._current_model_name, self._prompt_mode)

    def get_timeout_for_model(self, model_name: str, prompt_mode: Optional[str] = None) -> int:
        """Get timeout for an explicit model/prompt mode without mutating runtime state."""
        mode = prompt_mode if prompt_mode in {"short", "thinking"} else self._prompt_mode
        if model_name == "gemini-3.1-pro-preview" or mode == "thinking":
            return self.config.extended_timeout
        return self.config.response_timeout
    
    def get_estimated_response_time(self) -> str:
        """
        Get a user-friendly estimated response time for the current model.
        
        Returns:
            Human-readable time estimate
        """
        if self._current_model_name == "gemini-3.1-pro-preview" or self._prompt_mode == "thinking":
            return "30-60 seconds (using advanced model with extended thinking)"
        else:
            return "5-15 seconds"
    
    def set_model_by_complexity(self, complexity_level: str) -> bool:
        """
        Select and switch to the appropriate model based on complexity level.
        
        Args:
            complexity_level: "low", "medium", or "high"
            
        Returns:
            True if successful, False otherwise
        """
        # If thinking mode is manually enabled (persistent), don't downgrade it
        # But we can still upgrade the model if needed
        
        logger.info(f"Setting model based on complexity level: {complexity_level}")
        self._current_complexity_level = complexity_level if complexity_level in ["low", "medium", "high"] else "low"
        
        if complexity_level == "low":
            # Only downgrade to short mode if not manually set to thinking
            if self._prompt_mode != "thinking":
                self.set_prompt_mode("short")
            return self.set_model("gemini-3-flash-preview")

        elif complexity_level == "medium":
            # Keep medium on default Flash behavior; avoid auto-enabling thinking.
            # Manual /config thinking remains respected.
            return self.set_model("gemini-3-flash-preview")

        elif complexity_level == "high":
            # High complexity uses Pro model
            # Pro model is smart enough without explicit thinking prompt, but we can keep it if set
            success = self.set_model("gemini-3.1-pro-preview")
            if not success:
                logger.warning("gemini-3.1-pro-preview not available, falling back to gemini-3-flash-preview with thinking")
                self.set_prompt_mode("thinking")
                return self.set_model("gemini-3-flash-preview")
            return success
        else:
            logger.error(f"Invalid complexity level: {complexity_level}. Must be 'low', 'medium', or 'high'")
            return False

    def _get_max_output_tokens_for_complexity(self, target_model: Optional[str] = None) -> int:
        """
        Select max output token budget from complexity tier.

        Pro model overrides always use the high tier.
        """
        if target_model == "gemini-3.1-pro-preview":
            return self.config.max_output_tokens_high

        if self._current_complexity_level == "high":
            return self.config.max_output_tokens_high
        if self._current_complexity_level == "medium":
            return self.config.max_output_tokens_medium
        return self.config.max_output_tokens_low
    
    def _get_model_display_name(
        self,
        model_name: Optional[str] = None,
        prompt_mode: Optional[str] = None,
    ) -> str:
        """
        Get a user-friendly display name for the current model and mode.
        
        Returns:
            Display name string (e.g., "Gemini 2.5 Flash", "Gemini 2.5 Flash + Thinking")
        """
        effective_model_name = model_name or self._current_model_name
        effective_prompt_mode = prompt_mode if prompt_mode in {"short", "thinking"} else self._prompt_mode
        display_name = self.config.model_display_names.get(effective_model_name, effective_model_name)
        
        # Add thinking mode indicator if enabled
        if effective_prompt_mode == "thinking":
            display_name += " + Extended Thinking"
        
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
            search_override: Optional boolean to force enable/disable search for this request
            
        Returns:
            APIResponse containing the generated response or error information
        """
        if not self.client:
            logger.error("Attempted to generate response but Gemini client is not configured")
            return APIResponse(
                success=False,
                error_type="configuration_error",
                content="Gemini API not properly configured. Please check your GEMINI_API_KEY environment variable."
            )
        
        # Use request overrides without mutating global runtime state
        target_model = model_override if model_override else self._current_model_name
        target_prompt_mode = (
            prompt_mode_override
            if prompt_mode_override in {"short", "thinking"}
            else self._prompt_mode
        )
        
        # Log API call initiation
        logger.info("=" * 80)
        logger.info("API CALL INITIATED")
        logger.info(f"Model: {target_model}")
        logger.info(f"Prompt mode: {target_prompt_mode}")
        logger.info(f"API Key (last 4 chars): ...{self.config.gemini_api_key[-4:]}")
        
        # Determine if Google Search should be used
        if search_override is not None:
            use_search = search_override
        else:
            use_search = self._should_use_search(prompt)
            
        if use_search:
            logger.info("Google Search enabled for this request")
        
        # Build content for API call
        content_parts = []

        # Normalize image metadata to ensure strict alignment with image order
        normalized_image_context = self._normalize_image_context(images, image_context)

        # Add text prompt (with personality and language if provided)
        formatted_prompt = self.format_prompt(
            prompt,
            context,
            personality_prompt=personality_prompt,
            language=language,
            image_context=normalized_image_context,
            model_override=target_model,
            prompt_mode_override=target_prompt_mode,
        )
        content_parts.append(formatted_prompt)
        
        # Add images - convert PIL Images to bytes for the Gemini SDK
        if images and len(images) > 0:
            for img in images:
                # Convert PIL Image to bytes for the SDK
                img_buffer = io.BytesIO()
                img.save(img_buffer, format='PNG')
                img_bytes = img_buffer.getvalue()
                content_parts.append(types.Part.from_bytes(data=img_bytes, mime_type='image/png'))
            logger.info(f"Added {len(images)} image(s) to request")
            
        # Add audio files
        if audio_files and len(audio_files) > 0:
            for audio in audio_files:
                content_parts.append(types.Part.from_bytes(data=audio['data'], mime_type=audio['mime_type']))
            logger.info(f"Added {len(audio_files)} audio file(s) to request")
            
        logger.info(f"Input prompt length: {len(formatted_prompt)} characters")
        logger.info(f"Input prompt (first 200 chars): {formatted_prompt[:200]}")
        
        # Log context details
        if context:
            logger.info(f"Context messages provided: {len(context)}")
            for i, msg in enumerate(context):
                logger.info(f"  Context[{i}]: Author={msg.author}, Length={len(msg.content)} chars, Timestamp={msg.timestamp}")
        else:
            logger.info("No context messages provided")

        if normalized_image_context:
            logger.info(f"Image context entries provided: {len(normalized_image_context)}")
            for item in normalized_image_context:
                logger.info(
                    "  Image[%s]: source=%s, message_order=%s, message_id=%s, attachment=%s",
                    item.get("image_index"),
                    item.get("source_type"),
                    item.get("source_message_order"),
                    item.get("source_message_id"),
                    item.get("attachment_name"),
                )
        
        # Get dynamic timeout based on effective request model/mode
        timeout_duration = self.get_timeout_for_model(target_model, target_prompt_mode)
        # Adjust timeout if using Pro model via override
        if target_model == "gemini-3.1-pro-preview":
            timeout_duration = max(timeout_duration, 120)
            
        logger.info(f"Using timeout: {timeout_duration}s for model {target_model} (mode: {target_prompt_mode})")
        
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"Generating response (attempt {attempt + 1}/{self.config.max_retries + 1})")
                
                # Generate response with timeout and performance tracking
                import time
                start_time = time.time()
                
                response = await asyncio.wait_for(
                    self._generate_response_async(content_parts, use_search, on_chunk, model_override=target_model),
                    timeout=timeout_duration
                )
                
                duration = time.time() - start_time
                
                # Log API response details
                logger.info("-" * 80)
                logger.info(f"API RESPONSE RECEIVED (Duration: {duration:.3f}s)")
                logger.info(f"Response object type: {type(response)}")
                
                # Check if response is valid
                if not response:
                    logger.warning("Gemini API returned None response")
                    logger.info("=" * 80)
                    self.performance_logger.log_api_call(
                        api_name="gemini_generate_content",
                        duration=duration,
                        success=False,
                        error_type="empty_response"
                    )
                    return APIResponse(
                        success=False,
                        error_type="empty_response",
                        content="The AI returned no response"
                    )
                
                # Check finish_reason before accessing text
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    finish_reason_raw = candidate.finish_reason
                    finish_reason = self._normalize_finish_reason(finish_reason_raw)
                    
                    # Log candidate details
                    logger.info(f"Number of candidates: {len(response.candidates)}")
                    logger.info(f"Finish reason: {finish_reason_raw} -> normalized: {finish_reason} ({self._get_finish_reason_name(finish_reason_raw)})")
                    
                    # Log safety ratings if available
                    if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                        logger.info("Safety ratings:")
                        for rating in candidate.safety_ratings:
                            logger.info(f"  {rating.category}: {rating.probability}")
                    
                    # Handle different finish reasons
                    if finish_reason == 1:  # STOP - normal completion
                        response_text_content = self._get_response_text(response)
                        if response_text_content:
                            logger.info("Successfully generated response from Gemini API")
                            logger.info(f"Output token count (estimated): {len(response_text_content.split())}")
                            logger.info(f"Output length: {len(response_text_content)} characters")
                            logger.info(f"Output (first 200 chars): {response_text_content[:200]}")
                            logger.info("=" * 80)
                            
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=True
                            )
                            
                            # Extract grounding sources using unified helper method
                            grounding_sources = self._extract_grounding_sources(response, use_search)
                            token_usage = self._extract_token_usage(response)
                            
                            # Add grounding indicator if search was used
                            response_text = response_text_content.strip()
                            
                            # Add model information header
                            model_info = self._get_model_display_name(
                                model_name=target_model,
                                prompt_mode=target_prompt_mode,
                            )
                            model_header = f"🤖 *[Model: {model_info}]*"
                            
                            if use_search:
                                response_text = f"{model_header}\n🌐 *[Grounding: Online Search Enabled]*\n\n{response_text}"
                            else:
                                response_text = f"{model_header}\n\n{response_text}"
                            
                            # Auto-revert thinking mode to short after single use
                            if (
                                prompt_mode_override is None
                                and self._prompt_mode == "thinking"
                                and self._thinking_single_use
                            ):
                                logger.info("Auto-reverting from 'thinking' mode to 'short' mode (single-use feature)")
                                self._prompt_mode = "short"
                            
                            return APIResponse(
                                success=True,
                                content=response_text,
                                grounding_sources=grounding_sources if grounding_sources else None,
                                token_usage=token_usage
                            )
                    elif finish_reason == 2:  # MAX_TOKENS
                        # Response hit max tokens but we still got partial content
                        response_text_content = self._get_response_text(response)
                        if response_text_content:
                            logger.warning(f"Gemini API response hit max tokens, returning partial response ({len(response_text_content)} chars)")
                            logger.info(f"Output token count (estimated): {len(response_text_content.split())}")
                            logger.info(f"Output length: {len(response_text_content)} characters")
                            logger.info(f"Output (first 200 chars): {response_text_content[:200]}")
                            logger.info("=" * 80)
                            
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=True  # Still consider it successful since we got content
                            )
                            
                            # Extract grounding sources using unified helper method
                            grounding_sources = self._extract_grounding_sources(response, use_search)
                            token_usage = self._extract_token_usage(response)
                            
                            # Add grounding indicator and note about truncation
                            response_text = response_text_content.strip()
                            
                            # Add model information header
                            model_info = self._get_model_display_name(
                                model_name=target_model,
                                prompt_mode=target_prompt_mode,
                            )
                            model_header = f"🤖 *[Model: {model_info}]*"
                            
                            if use_search:
                                response_text = f"{model_header}\n🌐 *[Grounding: Online Search Enabled]*\n\n{response_text}"
                            else:
                                response_text = f"{model_header}\n\n{response_text}"
                            
                            # Add note that response was truncated
                            response_text += "\n\n*[Note: Response was very long and may have been truncated. You can ask for specific parts or a summary.]*"
                            
                            # Auto-revert thinking mode to short after single use
                            if (
                                prompt_mode_override is None
                                and self._prompt_mode == "thinking"
                                and self._thinking_single_use
                            ):
                                logger.info("Auto-reverting from 'thinking' mode to 'short' mode (single-use feature)")
                                self._prompt_mode = "short"
                            
                            return APIResponse(
                                success=True,
                                content=response_text,
                                grounding_sources=grounding_sources if grounding_sources else None,
                                token_usage=token_usage
                            )
                        else:
                            # No text but hit max tokens (shouldn't happen, but handle it)
                            logger.error("Max tokens hit but no response text available")
                            logger.info("=" * 80)
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=False,
                                error_type="max_tokens_no_content"
                            )
                            return APIResponse(
                                success=False,
                                error_type="max_tokens",
                                content="The response was too complex to generate. Please try breaking your question into smaller parts."
                            )
                    elif finish_reason == 3:  # SAFETY
                        logger.warning("Gemini API response blocked by safety filters")
                        if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                            logger.warning("Triggered safety ratings:")
                            for rating in candidate.safety_ratings:
                                logger.warning(f"  {rating.category}: {rating.probability}")
                        logger.info("=" * 80)
                        
                        self.performance_logger.log_api_call(
                            api_name="gemini_generate_content",
                            duration=duration,
                            success=False,
                            error_type="safety_filter"
                        )
                        return APIResponse(
                            success=False,
                            error_type="safety_filter",
                            content="I can't respond to that due to content safety guidelines. Please rephrase your message."
                        )
                    elif finish_reason == 4:  # RECITATION
                        logger.warning("Gemini API response blocked due to recitation")
                        logger.info("=" * 80)
                        
                        self.performance_logger.log_api_call(
                            api_name="gemini_generate_content",
                            duration=duration,
                            success=False,
                            error_type="recitation"
                        )
                        return APIResponse(
                            success=False,
                            error_type="recitation",
                            content="I can't provide that response as it may be copyrighted content."
                        )
                    else:
                        logger.warning(f"Gemini API returned unexpected finish_reason: {finish_reason}")
                        logger.info("=" * 80)
                        
                        self.performance_logger.log_api_call(
                            api_name="gemini_generate_content",
                            duration=duration,
                            success=False,
                            error_type="unknown_finish_reason"
                        )
                        return APIResponse(
                            success=False,
                            error_type="unknown_finish_reason",
                            content="Received an unexpected response from the AI. Please try again."
                        )
                else:
                    logger.warning("Gemini API returned response without candidates")
                    logger.info("=" * 80)
                    
                    self.performance_logger.log_api_call(
                        api_name="gemini_generate_content",
                        duration=duration,
                        success=False,
                        error_type="empty_response"
                    )
                    return APIResponse(
                        success=False,
                        error_type="empty_response",
                        content="The AI generated an empty response"
                    )
                    
            except asyncio.TimeoutError:
                logger.warning(f"Gemini API request timed out (attempt {attempt + 1})")
                logger.warning(f"Timeout duration: {timeout_duration}s")
                logger.info("=" * 80)
                
                # Log timeout performance
                self.performance_logger.log_api_call(
                    api_name="gemini_generate_content",
                    duration=timeout_duration,
                    success=False,
                    error_type="timeout"
                )
                
                if attempt == self.config.max_retries:
                    return APIResponse(
                        success=False,
                        error_type="timeout",
                        content="Request timed out after multiple attempts"
                    )
                    
            except Exception as e:
                logger.error(f"Exception during API call: {type(e).__name__}: {str(e)}")
                logger.error(f"Exception details: {repr(e)}", exc_info=True)
                
                error_context = self.error_manager.handle_api_error(e, f"Gemini API attempt {attempt + 1}")
                
                logger.error(f"Error type: {error_context.error_type.value}")
                logger.error(f"Should retry: {error_context.should_retry}")
                if error_context.retry_after:
                    logger.error(f"Retry after: {error_context.retry_after}s")
                logger.info("=" * 80)
                
                # Log failed API call
                self.performance_logger.log_api_call(
                    api_name="gemini_generate_content",
                    duration=0,  # Unknown duration for exceptions
                    success=False,
                    error_type=error_context.error_type.value
                )
                
                # Handle rate limiting with exponential backoff
                if error_context.should_retry and attempt < self.config.max_retries:
                    wait_time = error_context.retry_after or self._calculate_backoff_delay(attempt)
                    logger.info(f"Retrying after {wait_time:.2f} seconds (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    return APIResponse(
                        success=False,
                        error_type=error_context.error_type.value,
                        content=error_context.user_message
                    )
        
        # This should never be reached, but included for completeness
        return APIResponse(
            success=False,
            error_type="unknown_error",
            content="An unexpected error occurred"
        )
    
    async def _generate_response_async(self, content, use_search: bool = False, on_chunk: Optional[callable] = None, model_override: Optional[str] = None):
        """
        Async wrapper for Gemini API call.
        
        Args:
            content: Content to send to the API (string for text-only, list for multimodal)
            use_search: Whether to use the model with Google Search enabled
            on_chunk: Optional async callback for streaming chunks
            model_override: Optional model name to use
            
        Returns:
            Generated response from Gemini API
        """
        target_model = model_override if model_override else self._current_model_name
        logger.info(f"Making API call to model: {target_model}")
        logger.info(f"Using search-enabled model: {use_search}")
        
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

        max_output_tokens = self._get_max_output_tokens_for_complexity(target_model=target_model)
        logger.info(
            "Using max_output_tokens=%s for complexity=%s (target_model=%s)",
            max_output_tokens,
            self._current_complexity_level,
            target_model,
        )

        config = types.GenerateContentConfig(
            tools=tools,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=max_output_tokens,
            safety_settings=safety_settings,
        )
        
        # Convert content to string if it's a list (multimodal not supported with search yet)
        # Note: The new SDK supports multimodal content differently, but for now we'll stick to text if search is on
        if use_search and isinstance(content, list):
            content_str = content[0] if content else ""
            logger.warning("⚠️ Multimodal input detected with search - using text only")
        else:
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
        model_name: Optional[str] = None,
        prompt_mode: Optional[str] = None,
    ) -> str:
        """
        Get the appropriate system instruction based on current model and mode.
        
        Returns:
            System instruction string tailored to the model complexity
        """
        effective_model_name = model_name or self._current_model_name
        effective_prompt_mode = prompt_mode if prompt_mode in {"short", "thinking"} else self._prompt_mode

        # Determine which system prompt to use based on model and thinking mode
        if effective_model_name == "gemini-3.1-pro-preview" or effective_prompt_mode == "thinking":
            logger.info("Using HIGH COMPLEXITY system prompt")

            thinking_instruction = ""
            if effective_prompt_mode == "thinking":
                thinking_instruction = "\n" + self.config.system_prompt_thinking_addon

            return self.config.system_prompt_high_complexity.rstrip() + thinking_instruction

        elif effective_model_name in ["gemini-3-flash-preview"]:
            logger.info("Using LOW COMPLEXITY system prompt")
            return self.config.system_prompt_low_complexity.rstrip()

        else:
            logger.info("Using MEDIUM COMPLEXITY system prompt")
            return self.config.system_prompt_medium_complexity.rstrip()
    
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
        model_override: Optional[str] = None,
        prompt_mode_override: Optional[str] = None,
    ) -> str:
        """
        Format the user message and context into an optimal prompt for Gemini API.

        Args:
            user_message: The user's message/question
            context: Optional conversation context
            personality_prompt: Optional personality/tone instruction to prepend
            language: Optional language preference for the response
            image_context: Optional image metadata aligned to attached image parts
            model_override: Optional model name for request-scoped formatting
            prompt_mode_override: Optional prompt mode for request-scoped formatting

        Returns:
            Formatted prompt string for the Gemini API
        """
        logger.debug("Formatting prompt for API call")
        logger.debug(f"User message length: {len(user_message)} characters")
        logger.debug(f"Context messages: {len(context) if context else 0}")

        effective_model_name = model_override or self._current_model_name
        effective_prompt_mode = (
            prompt_mode_override if prompt_mode_override in {"short", "thinking"} else self._prompt_mode
        )

        prompt_parts = []

        # Add system instruction based on the current model and mode
        system_instruction = self._get_system_instruction(
            model_name=effective_model_name,
            prompt_mode=effective_prompt_mode,
        )
        logger.debug(
            "Using system instruction for model: %s, mode: %s",
            effective_model_name,
            effective_prompt_mode,
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
            prompt_parts.append("\n--- Recent Conversation Context (oldest to newest) ---")
            
            # Sort context by timestamp to ensure chronological order
            sorted_context = sorted(context, key=lambda msg: (msg.timestamp, msg.message_id))
            
            for idx, msg in enumerate(sorted_context, start=1):
                # Include explicit sequence + id for deterministic ordering references
                time_str = msg.timestamp.isoformat()
                
                # Mark replied-to messages for clarity
                reply_indicator = " (replying)" if msg.is_reply else ""
                
                formatted_msg = (
                    f"[CTX_MSG_{idx:03d} | message_id={msg.message_id} | time={time_str}] "
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
        
        # Reinforce thinking mode if enabled
        if effective_prompt_mode == "thinking":
            prompt_parts.append("\nIMPORTANT: You are in THINKING MODE. You MUST start your response with a <thinking> block containing your step-by-step reasoning, followed by </thinking>, and then your final answer.")
            
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
