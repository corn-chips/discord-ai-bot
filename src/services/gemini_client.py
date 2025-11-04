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
        self.error_manager = ErrorManager()
        self.performance_logger = PerformanceLogger("gemini_client")
        self._model = None
        self._current_model_name = "gemini-2.5-flash"
        self._configure_api()
        
    def _configure_api(self) -> None:
        """Configure the Gemini API with authentication and settings."""
        try:
            # Validate API key exists and is not empty
            if not self.config.gemini_api_key or not self.config.gemini_api_key.strip():
                logger.error("Gemini API key is missing or empty")
                self._model = None
                return
            
            # Check if API key looks valid (basic format check)
            if len(self.config.gemini_api_key) < 20:
                logger.error("Gemini API key appears to be invalid (too short)")
                self._model = None
                return
            
            genai.configure(api_key=self.config.gemini_api_key)
            
            # Configure the model with appropriate settings
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 1000,
            }
            
            # Configure safety settings to be less restrictive for general conversation
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            }
            
            self._model = genai.GenerativeModel(
                model_name=self._current_model_name,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            logger.info(f"Gemini API configured successfully with model: {self._current_model_name}")
            
        except ValueError as e:
            logger.error(f"Invalid Gemini API key format: {e}")
            self._model = None
        except Exception as e:
            logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
            self._model = None
    
    def get_current_model(self) -> str:
        """Get the name of the currently active model."""
        return self._current_model_name
    
    def set_model(self, model_name: str) -> bool:
        """
        Switch to a different Gemini Flash model.
        
        Args:
            model_name: Name of the model to switch to
            
        Returns:
            True if successful, False otherwise
        """
        valid_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite", 
            "gemini-2.0-flash-exp",
            "gemini-2.0-flash-lite"
        ]
        
        if model_name not in valid_models:
            logger.error(f"Invalid model name: {model_name}")
            return False
        
        try:
            old_model = self._current_model_name
            self._current_model_name = model_name
            
            # Reconfigure with new model
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 1000,
            }
            
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            }
            
            self._model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            logger.info(f"Switched model from {old_model} to {model_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to switch model to {model_name}: {e}")
            # Revert to old model
            self._current_model_name = old_model
            return False
    
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
        
        # Build content for API call
        if images and len(images) > 0:
            # Multimodal content with images
            formatted_prompt = self.format_prompt(prompt, context)
            content = [formatted_prompt] + images
            logger.info(f"Generating response with {len(images)} image(s)")
        else:
            # Text-only content
            formatted_prompt = self.format_prompt(prompt, context)
            content = formatted_prompt
        
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"Generating response (attempt {attempt + 1}/{self.config.max_retries + 1})")
                
                # Generate response with timeout and performance tracking
                import time
                start_time = time.time()
                
                response = await asyncio.wait_for(
                    self._generate_response_async(content),
                    timeout=self.config.response_timeout
                )
                
                duration = time.time() - start_time
                
                # Check if response is valid
                if not response:
                    logger.warning("Gemini API returned None response")
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
                    
                    # Handle different finish reasons
                    if finish_reason == 1:  # STOP - normal completion
                        if response.text:
                            logger.info("Successfully generated response from Gemini API")
                            self.performance_logger.log_api_call(
                                api_name="gemini_generate_content",
                                duration=duration,
                                success=True
                            )
                            return APIResponse(
                                success=True,
                                content=response.text.strip()
                            )
                    elif finish_reason == 2:  # MAX_TOKENS
                        logger.warning("Gemini API response hit max tokens")
                        self.performance_logger.log_api_call(
                            api_name="gemini_generate_content",
                            duration=duration,
                            success=False,
                            error_type="max_tokens"
                        )
                        return APIResponse(
                            success=False,
                            error_type="max_tokens",
                            content="Response was too long and was cut off. Please try a simpler question."
                        )
                    elif finish_reason == 3:  # SAFETY
                        logger.warning("Gemini API response blocked by safety filters")
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
                error_context = self.error_manager.handle_api_error(e, f"Gemini API attempt {attempt + 1}")
                
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
    
    async def _generate_response_async(self, content):
        """
        Async wrapper for Gemini API call.
        
        Args:
            content: Content to send to the API (string for text-only, list for multimodal)
            
        Returns:
            Generated response from Gemini API
        """
        # Run the synchronous API call in a thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._model.generate_content, 
            content
        )
    
    def format_prompt(self, user_message: str, context: Optional[List[MessageContext]] = None) -> str:
        """
        Format the user message and context into an optimal prompt for Gemini API.
        
        Args:
            user_message: The user's message/question
            context: Optional conversation context
            
        Returns:
            Formatted prompt string for the Gemini API
        """
        prompt_parts = []
        
        # Add system instruction for the bot's personality and behavior
        system_instruction = (
            "You are Grok, a witty and helpful AI assistant in a Discord chat. "
            "Respond naturally to conversations, be engaging and informative, "
            "but keep responses concise and conversational. "
            "Use the conversation context to provide relevant responses."
        )
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
        
        return "\n".join(prompt_parts)
    

    
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