"""
Gemini API client for the Discord Grok Bot.

This module provides the GeminiClient class for interfacing with Google's Gemini API
to generate AI-powered responses based on user prompts and conversation context.
"""

import asyncio
import logging
import random
from typing import List, Optional

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from ..config import BotConfig
from ..models.data_models import APIResponse, MessageContext
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, TimingContext


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
        self._model = None
        self._model_with_search = None
        self._current_model_name = "gemini-flash-latest"
        self._prompt_mode = "short"  # Default to short mode, can be "short" or "thinking"
        self._thinking_single_use = True  # Thinking mode auto-reverts to short after one use
        self._configure_api()
        
    def _configure_api(self) -> None:
        """Configure the Gemini API with authentication and settings."""
        try:
            logger.info("=" * 80)
            logger.info("CONFIGURING GEMINI API")
            
            # Validate API key exists and is not empty
            if not self.config.gemini_api_key or not self.config.gemini_api_key.strip():
                logger.error("Gemini API key is missing or empty")
                self._model = None
                logger.info("=" * 80)
                return
            
            # Check if API key looks valid (basic format check)
            if len(self.config.gemini_api_key) < 20:
                logger.error("Gemini API key appears to be invalid (too short)")
                logger.error(f"API key length: {len(self.config.gemini_api_key)}")
                self._model = None
                logger.info("=" * 80)
                return
            
            logger.info(f"API Key length: {len(self.config.gemini_api_key)} characters")
            logger.info(f"API Key (first 8 chars): {self.config.gemini_api_key[:8]}...")
            logger.info(f"API Key (last 4 chars): ...{self.config.gemini_api_key[-4:]}")
            
            genai.configure(api_key=self.config.gemini_api_key)
            
            # Configure the model with appropriate settings
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 65536,  # Maximum token limit for longest possible responses
            }
            
            # Configure safety settings - all filters disabled
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
            
            # Configure model (Google Search grounding not available in current API version)
            self._model = genai.GenerativeModel(
                model_name=self._current_model_name,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Set model_with_search to same model for now (search feature unavailable)
            self._model_with_search = self._model
            
            logger.info(f"Model name: {self._current_model_name}")
            logger.info(f"Generation config: temperature={generation_config['temperature']}, "
                       f"top_p={generation_config['top_p']}, top_k={generation_config['top_k']}, "
                       f"max_output_tokens={generation_config['max_output_tokens']}")
            logger.info(f"Safety settings: All filters set to BLOCK_NONE")
            logger.info(f"Gemini API configured successfully")
            logger.info("=" * 80)
            
        except ValueError as e:
            logger.error(f"Invalid Gemini API key format: {e}")
            logger.error(f"ValueError details: {repr(e)}", exc_info=True)
            self._model = None
            logger.info("=" * 80)
        except Exception as e:
            logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception details: {repr(e)}")
            self._model = None
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
                "api_configured": self._model is not None,
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
                    "max_output_tokens": 65536,   # 64K tokens output
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
        
        valid_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite", 
            "gemini-2.0-flash-exp",
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-flash-lite-latest"
        ]
        
        if model_name not in valid_models:
            logger.error(f"Invalid model name: {model_name}")
            logger.error(f"Valid models: {', '.join(valid_models)}")
            logger.info("=" * 80)
            return False
        
        try:
            old_model = self._current_model_name
            logger.info(f"Old model: {old_model}")
            logger.info(f"New model: {model_name}")
            self._current_model_name = model_name
            
            # Reconfigure with new model
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 65536,  # Increased to allow longer responses (will be split if needed)
            }
            
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
            
            # Configure model (Google Search grounding not available in current API version)
            self._model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Set model_with_search to same model for now (search feature unavailable)
            self._model_with_search = self._model
            
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
    
    def _should_use_search(self, prompt: str) -> bool:
        """
        Determine if Google Search should be used based on the prompt content.
        
        Args:
            prompt: The user's message/prompt
            
        Returns:
            True if search should be used, False otherwise
        """
        prompt_lower = prompt.lower()
        
        # Check for URLs
        if "http://" in prompt_lower or "https://" in prompt_lower or "www." in prompt_lower:
            return True
        
        # Check for explicit search keywords
        search_keywords = [
            "search", "look up", "lookup", "find online", "google", 
            "search for", "look for", "check online", "find information",
            "what's new", "latest", "current", "recent news", "today's",
            "web search", "internet"
        ]
        
        return any(keyword in prompt_lower for keyword in search_keywords)
    
    async def generate_response(self, prompt: str, context: Optional[List[MessageContext]] = None, images: Optional[List] = None) -> APIResponse:
        """
        Generate a response using the Gemini API with retry logic.
        
        Args:
            prompt: The user's message/prompt
            context: Optional conversation context for better responses
            images: Optional list of PIL Image objects to include in the request
            
        Returns:
            APIResponse containing the generated response or error information
        """
        if not self._model:
            logger.error("Attempted to generate response but Gemini model is not configured")
            return APIResponse(
                success=False,
                error_type="configuration_error",
                content="Gemini API not properly configured. Please check your GEMINI_API_KEY environment variable."
            )
        
        # Log API call initiation
        logger.info("=" * 80)
        logger.info("API CALL INITIATED")
        logger.info(f"Model: {self._current_model_name}")
        logger.info(f"API Key (last 4 chars): ...{self.config.gemini_api_key[-4:]}")
        
        # Determine if Google Search should be used
        use_search = self._should_use_search(prompt)
        if use_search:
            logger.info("Google Search enabled for this request")
        
        # Build content for API call
        if images and len(images) > 0:
            # Multimodal content with images
            formatted_prompt = self.format_prompt(prompt, context)
            content = [formatted_prompt] + images
            logger.info(f"Generating response with {len(images)} image(s)")
            logger.info(f"Input prompt length: {len(formatted_prompt)} characters")
            logger.info(f"Input prompt (first 200 chars): {formatted_prompt[:200]}")
        else:
            # Text-only content
            formatted_prompt = self.format_prompt(prompt, context)
            content = formatted_prompt
            logger.info(f"Input prompt length: {len(formatted_prompt)} characters")
            logger.info(f"Input prompt (first 200 chars): {formatted_prompt[:200]}")
        
        # Log context details
        if context:
            logger.info(f"Context messages provided: {len(context)}")
            for i, msg in enumerate(context):
                logger.info(f"  Context[{i}]: Author={msg.author}, Length={len(msg.content)} chars, Timestamp={msg.timestamp}")
        else:
            logger.info("No context messages provided")
        
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"Generating response (attempt {attempt + 1}/{self.config.max_retries + 1})")
                
                # Generate response with timeout and performance tracking
                import time
                start_time = time.time()
                
                response = await asyncio.wait_for(
                    self._generate_response_async(content, use_search),
                    timeout=self.config.response_timeout
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
                    finish_reason = candidate.finish_reason
                    
                    # Log candidate details
                    logger.info(f"Number of candidates: {len(response.candidates)}")
                    logger.info(f"Finish reason: {finish_reason} ({self._get_finish_reason_name(finish_reason)})")
                    
                    # Log safety ratings if available
                    if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                        logger.info("Safety ratings:")
                        for rating in candidate.safety_ratings:
                            logger.info(f"  {rating.category}: {rating.probability}")
                    
                    # Handle different finish reasons
                    if finish_reason == 1:  # STOP - normal completion
                        if response.text:
                            logger.info("Successfully generated response from Gemini API")
                            logger.info(f"Output token count (estimated): {len(response.text.split())}")
                            logger.info(f"Output length: {len(response.text)} characters")
                            logger.info(f"Output (first 200 chars): {response.text[:200]}")
                            logger.info("=" * 80)
                            
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=True
                            )
                            
                            # Extract grounding sources if available
                            grounding_sources = []
                            if use_search and hasattr(candidate, 'grounding_metadata'):
                                grounding_metadata = candidate.grounding_metadata
                                if hasattr(grounding_metadata, 'grounding_chunks'):
                                    for chunk in grounding_metadata.grounding_chunks:
                                        if hasattr(chunk, 'web') and hasattr(chunk.web, 'uri'):
                                            grounding_sources.append({
                                                'uri': chunk.web.uri,
                                                'title': chunk.web.title if hasattr(chunk.web, 'title') else None
                                            })
                                    logger.info(f"Extracted {len(grounding_sources)} grounding sources:")
                                    for i, source in enumerate(grounding_sources):
                                        logger.info(f"  Source[{i}]: {source['title']} - {source['uri']}")
                            
                            # Add grounding indicator if search was used
                            response_text = response.text.strip()
                            if use_search:
                                response_text = f"🌐 *[Grounding: Online Search Enabled]*\n\n{response_text}"
                            
                            # Auto-revert thinking mode to short after single use
                            if self._prompt_mode == "thinking" and self._thinking_single_use:
                                logger.info("Auto-reverting from 'thinking' mode to 'short' mode (single-use feature)")
                                self._prompt_mode = "short"
                            
                            return APIResponse(
                                success=True,
                                content=response_text,
                                grounding_sources=grounding_sources if grounding_sources else None
                            )
                    elif finish_reason == 2:  # MAX_TOKENS
                        # Response hit max tokens but we still got partial content
                        if response.text:
                            logger.warning(f"Gemini API response hit max tokens, returning partial response ({len(response.text)} chars)")
                            logger.info(f"Output token count (estimated): {len(response.text.split())}")
                            logger.info(f"Output length: {len(response.text)} characters")
                            logger.info(f"Output (first 200 chars): {response.text[:200]}")
                            logger.info("=" * 80)
                            
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=True  # Still consider it successful since we got content
                            )
                            
                            # Extract grounding sources if available
                            grounding_sources = []
                            if use_search and hasattr(candidate, 'grounding_metadata'):
                                grounding_metadata = candidate.grounding_metadata
                                if hasattr(grounding_metadata, 'grounding_chunks'):
                                    for chunk in grounding_metadata.grounding_chunks:
                                        if hasattr(chunk, 'web') and hasattr(chunk.web, 'uri'):
                                            grounding_sources.append({
                                                'uri': chunk.web.uri,
                                                'title': chunk.web.title if hasattr(chunk.web, 'title') else None
                                            })
                            
                            # Add grounding indicator and note about truncation
                            response_text = response.text.strip()
                            if use_search:
                                response_text = f"🌐 *[Grounding: Online Search Enabled]*\n\n{response_text}"
                            
                            # Add note that response was truncated
                            response_text += "\n\n*[Note: Response was very long and may have been truncated. You can ask for specific parts or a summary.]*"
                            
                            # Auto-revert thinking mode to short after single use
                            if self._prompt_mode == "thinking" and self._thinking_single_use:
                                logger.info("Auto-reverting from 'thinking' mode to 'short' mode (single-use feature)")
                                self._prompt_mode = "short"
                            
                            return APIResponse(
                                success=True,
                                content=response_text,
                                grounding_sources=grounding_sources if grounding_sources else None
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
                logger.warning(f"Timeout duration: {self.config.response_timeout}s")
                logger.info("=" * 80)
                
                # Log timeout performance
                self.performance_logger.log_api_call(
                    api_name="gemini_generate_content",
                    duration=self.config.response_timeout,
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
    
    async def _generate_response_async(self, content, use_search: bool = False):
        """
        Async wrapper for Gemini API call.
        
        Args:
            content: Content to send to the API (string for text-only, list for multimodal)
            use_search: Whether to use the model with Google Search enabled
            
        Returns:
            Generated response from Gemini API
        """
        # Select the appropriate model based on whether search is needed
        model = self._model_with_search if use_search else self._model
        
        logger.info(f"Making API call to model: {model._model_name if hasattr(model, '_model_name') else 'unknown'}")
        logger.info(f"Using search-enabled model: {use_search}")
        
        # Run the synchronous API call in a thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            model.generate_content, 
            content
        )
    
    def _get_finish_reason_name(self, finish_reason: int) -> str:
        """
        Get human-readable name for finish reason code.
        
        Args:
            finish_reason: Numeric finish reason code
            
        Returns:
            Human-readable name for the finish reason
        """
        finish_reasons = {
            0: "UNSPECIFIED",
            1: "STOP",
            2: "MAX_TOKENS",
            3: "SAFETY",
            4: "RECITATION",
            5: "OTHER"
        }
        return finish_reasons.get(finish_reason, f"UNKNOWN({finish_reason})")
    
    def format_prompt(self, user_message: str, context: Optional[List[MessageContext]] = None) -> str:
        """
        Format the user message and context into an optimal prompt for Gemini API.
        
        Args:
            user_message: The user's message/question
            context: Optional conversation context
            
        Returns:
            Formatted prompt string for the Gemini API
        """
        logger.debug("Formatting prompt for API call")
        logger.debug(f"User message length: {len(user_message)} characters")
        logger.debug(f"Context messages: {len(context) if context else 0}")
        
        prompt_parts = []
        
        # Add system instruction based on the current prompt mode
        if self._prompt_mode == "thinking":
            # Detailed, comprehensive analysis mode (long output)
            system_instruction = '''
### **System Prompt: The Grounded Expert**

**[CORE IDENTITY]**

You are a grounded, well-informed expert AI assistant. Your primary function is to provide users with accurate, verifiable, and comprehensive information sourced from reliable data. You operate as a subject-matter expert across a wide range of fields, with an absolute commitment to factual integrity and intellectual honesty.

**[CORE DIRECTIVES & PRINCIPLES]**

1.  **Factuality is Paramount:** Your primary output must be factual and verifiable. Prioritize objective data, established scientific consensus, and documented evidence over speculation or unconfirmed information.

2.  **Mandatory Grounding & Sourcing:** You **MUST** use your web search and grounding capabilities for every query that requires external, real-world knowledge. Do not answer from memory alone. Verify all claims, statistics, dates, and proper nouns. Provide clear citations or links to your primary, high-quality sources (e.g., academic journals, reputable news organizations, government reports, expert publications) at the end of your response.

3.  **Strictly Distinguish Fact from Opinion/Analysis:**
    *   **Default Mode (Facts):** By default, you will only state established facts. Report what is known and documented.
    *   **On-Request Mode (Analysis):** If a user explicitly asks for an "opinion," "analysis," "interpretation," "perspective," or "projection," you must clearly label it as such. For example: "As an analysis of the presented facts..." or "Based on the available data, a possible interpretation is...".
    *   **Evidence-Based Analysis:** Any opinion or analysis you provide **MUST** be directly and explicitly supported by the factual evidence you have already presented in your response. Your opinion is a logical synthesis of the data, not a personal belief.

4.  **Professional & Direct Tone:**
    *   Communicate in a clear, precise, and professional manner.
    *   **Avoid conversational filler,** platitudes ("I'd be happy to help!"), personal anecdotes (as an AI), or overly enthusiastic language. Your tone is that of a trusted, objective researcher or a university professor.
    *   Your goal is to inform, not to entertain or please. Be respectful but direct.

5.  **Acknowledge Limits and Nuance:**
    *   If information is contested, uncertain, or unavailable after a thorough search, state this clearly. For example: "The available data on this topic is inconclusive," or "There are several competing theories on this matter, and no single one has achieved consensus."
    *   Acknowledge nuance and avoid presenting complex topics as simple binaries. It is better to state that a definitive answer is not available than to provide an inaccurate or oversimplified one.

6.  **Structure and Clarity:** Organize your responses logically. Use headings, bullet points, and bold text to improve readability for complex topics. Define technical terms when first used.

7.  **Strive for Comprehensive Depth:**
    *   **Your primary goal is to provide thorough, well-reasoned, and in-depth answers.** Avoid superficial or cursory responses. A "well thought out" answer is one that demonstrates a deep and holistic understanding of the subject.
    *   **Provide Context:** Always frame your answer with relevant historical, scientific, or social context. Explain *why* the information is significant.
    *   **Synthesize, Don't Just List:** Do not simply list disparate facts. Synthesize information from multiple sources to build a coherent and comprehensive narrative or explanation. Explore the key components, contributing factors, and implications related to the user's query.
    *   **Anticipate and Elaborate:** Go beyond the surface-level question. Anticipate logical follow-up questions and incorporate those explanations into your main response. A detailed, exhaustive explanation is always preferred over a brief summary.

**[IN ESSENCE]**

You are an objective conduit for verified information. Your value lies in your accuracy, your sourcing, and your ability to separate established fact from reasoned analysis. You do not have personal feelings or beliefs. You have access to information, and your purpose is to convey it with clarity, depth, and integrity.
'''
        else:  # short mode
            # Concise, direct response mode (short output)
            system_instruction = '''
### **System Prompt: The Concise Expert**

**[CORE IDENTITY]**

You are a helpful AI assistant focused on providing clear, accurate, and concise responses. You communicate efficiently while maintaining accuracy and relevance.

**[CORE DIRECTIVES]**

1.  **Be Concise:** Keep responses brief and to the point. Avoid unnecessary elaboration unless specifically requested.

2.  **Prioritize Clarity:** Use simple, direct language. Get straight to the answer without lengthy preambles.

3.  **Stay Accurate:** Provide factual information. If you're uncertain about something, acknowledge it briefly.

4.  **Be Conversational:** Maintain a friendly, helpful tone while remaining professional.

5.  **Format for Readability:** Use short paragraphs, bullet points when appropriate, and clear structure.

6.  **Answer Directly:** Start with the core answer, then provide brief supporting details if needed.

**[IN ESSENCE]**

You provide quick, accurate, and helpful responses without unnecessary verbosity. You respect the user's time by being efficient and direct.
'''
        
        prompt_parts.append(system_instruction)
        
        # Add conversation context if provided
        if context and len(context) > 0:
            prompt_parts.append("\n--- Recent Conversation Context ---")
            
            # Sort context by timestamp to ensure chronological order
            sorted_context = sorted(context, key=lambda msg: msg.timestamp)
            
            for msg in sorted_context:
                # Format timestamp for readability
                time_str = msg.timestamp.strftime("%H:%M")
                
                # Mark replied-to messages for clarity
                reply_indicator = " (replying)" if msg.is_reply else ""
                
                formatted_msg = f"[{time_str}] {msg.author}{reply_indicator}: {msg.content}"
                prompt_parts.append(formatted_msg)
            
            prompt_parts.append("--- End Context ---\n")
        
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