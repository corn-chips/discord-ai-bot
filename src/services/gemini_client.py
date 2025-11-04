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
        self._configure_api()
        self._model = None
        
    def _configure_api(self) -> None:
        """Configure the Gemini API with authentication and settings."""
        try:
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
                model_name="gemini-1.5-flash",
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            logger.info("Gemini API configured successfully")
            
        except Exception as e:
            logger.error(f"Failed to configure Gemini API: {e}")
            raise
    
    async def generate_response(self, prompt: str, context: Optional[List[MessageContext]] = None) -> APIResponse:
        """
        Generate a response using the Gemini API with retry logic.
        
        Args:
            prompt: The user's message/prompt
            context: Optional conversation context for better responses
            
        Returns:
            APIResponse containing the generated response or error information
        """
        if not self._model:
            return APIResponse(
                success=False,
                error_type="configuration_error",
                content="Gemini API not properly configured"
            )
        
        formatted_prompt = self.format_prompt(prompt, context)
        
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"Generating response (attempt {attempt + 1}/{self.config.max_retries + 1})")
                
                # Generate response with timeout and performance tracking
                import time
                start_time = time.time()
                
                response = await asyncio.wait_for(
                    self._generate_response_async(formatted_prompt),
                    timeout=self.config.response_timeout
                )
                
                duration = time.time() - start_time
                
                if response and response.text:
                    logger.info("Successfully generated response from Gemini API")
                    
                    # Log performance metrics
                    self.performance_logger.log_api_call(
                        api_name="gemini_generate_content",
                        duration=duration,
                        success=True
                    )
                    
                    return APIResponse(
                        success=True,
                        content=response.text.strip()
                    )
                else:
                    logger.warning("Gemini API returned empty response")
                    
                    # Log failed API call
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
    
    async def _generate_response_async(self, prompt: str):
        """
        Async wrapper for Gemini API call.
        
        Args:
            prompt: Formatted prompt to send to the API
            
        Returns:
            Generated response from Gemini API
        """
        # Run the synchronous API call in a thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._model.generate_content, 
            prompt
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