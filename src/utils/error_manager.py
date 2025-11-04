"""
Centralized error handling for the Discord Grok Bot.

This module provides the ErrorManager class for handling different types of errors
throughout the application and generating user-friendly error messages.
"""

import logging
from enum import Enum
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

import discord


logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Enumeration of different error types that can occur in the bot."""
    
    # API-related errors
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    AUTHENTICATION_ERROR = "authentication_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    EMPTY_RESPONSE = "empty_response"
    INVALID_REQUEST = "invalid_request"
    
    # Discord-related errors
    DISCORD_PERMISSION_ERROR = "discord_permission_error"
    DISCORD_HTTP_ERROR = "discord_http_error"
    DISCORD_CONNECTION_ERROR = "discord_connection_error"
    
    # Configuration errors
    CONFIGURATION_ERROR = "configuration_error"
    MISSING_TOKEN = "missing_token"
    INVALID_TOKEN = "invalid_token"
    
    # Context collection errors
    CONTEXT_COLLECTION_ERROR = "context_collection_error"
    MESSAGE_HISTORY_ERROR = "message_history_error"
    
    # Image processing errors
    IMAGE_PROCESSING_ERROR = "image_processing_error"
    IMAGE_VALIDATION_ERROR = "image_validation_error"
    IMAGE_SIZE_ERROR = "image_size_error"
    IMAGE_FORMAT_ERROR = "image_format_error"
    NANO_BANANA_API_ERROR = "nano_banana_api_error"
    IMAGE_UPLOAD_ERROR = "image_upload_error"
    IMAGE_DOWNLOAD_ERROR = "image_download_error"
    
    # Message formatting errors
    MESSAGE_SPLIT_ERROR = "message_split_error"
    MARKDOWN_PROCESSING_ERROR = "markdown_processing_error"
    MESSAGE_TOO_LONG_ERROR = "message_too_long_error"
    
    # General errors
    UNKNOWN_ERROR = "unknown_error"
    VALIDATION_ERROR = "validation_error"


@dataclass
class ErrorContext:
    """Context information for error handling."""
    
    error_type: ErrorType
    original_error: Optional[Exception] = None
    user_message: Optional[str] = None
    technical_details: Optional[str] = None
    retry_after: Optional[int] = None
    should_retry: bool = False
    log_level: int = logging.ERROR


class ErrorManager:
    """
    Centralized error handling manager for the Discord Grok Bot.
    
    Provides methods for categorizing errors, generating user-friendly messages,
    and handling different error scenarios appropriately.
    """
    
    def __init__(self, config=None):
        """
        Initialize the ErrorManager with predefined error messages.
        
        Args:
            config: Optional BotConfig instance for accessing dev_mode_enabled
        """
        self.config = config
        self._user_messages = self._initialize_user_messages()
        self._technical_messages = self._initialize_technical_messages()
    
    def _initialize_user_messages(self) -> Dict[ErrorType, str]:
        """Initialize user-friendly error messages for each error type."""
        return {
            # API-related errors
            ErrorType.RATE_LIMIT: "⚠️ **Rate Limit Exceeded**\nI'm currently experiencing high demand. Please try again in a few moments!",
            ErrorType.TIMEOUT: "⏰ **Request Timeout**\nMy response took too long to generate. Please try asking again!",
            ErrorType.AUTHENTICATION_ERROR: "🔧 **Authentication Error**\nI'm having trouble connecting to my AI service. Please try again later!",
            ErrorType.SERVICE_UNAVAILABLE: "🛠️ **Service Unavailable**\nMy AI service is temporarily unavailable. Please try again in a few minutes!",
            ErrorType.EMPTY_RESPONSE: "🤔 **Empty Response**\nI couldn't generate a response to that. Could you try rephrasing your question?\n\n💡 **Tip:** Use `/help` to see what I can help with, or try `/features` to discover my capabilities!",
            ErrorType.INVALID_REQUEST: "❓ **Invalid Request**\nI had trouble understanding your request. Could you try asking differently?\n\n💡 **Tip:** I work best with natural language - just mention me and ask your question!",
            
            # Discord-related errors
            ErrorType.DISCORD_PERMISSION_ERROR: "🔒 **Permission Error**\nI don't have the necessary permissions to respond here. Please check my permissions!\n\n💡 **Tip:** I need 'Send Messages', 'Read Message History', and 'Add Reactions' permissions to work properly.",
            ErrorType.DISCORD_HTTP_ERROR: "📤 **Discord API Error**\nI'm having trouble sending messages right now. Please try again!",
            ErrorType.DISCORD_CONNECTION_ERROR: "🌐 **Connection Error**\nI'm experiencing connection issues with Discord. Please try again!",
            
            # Configuration errors
            ErrorType.CONFIGURATION_ERROR: "⚙️ **Configuration Error**\nI'm experiencing a configuration issue. Please try again later!",
            ErrorType.MISSING_TOKEN: "🔑 **Missing Token**\nI'm not properly configured. Please contact an administrator!",
            ErrorType.INVALID_TOKEN: "🚫 **Invalid Token**\nMy authentication credentials are invalid. Please contact an administrator!",
            
            # Context collection errors
            ErrorType.CONTEXT_COLLECTION_ERROR: "📚 **Context Collection Error**\nI had trouble reading the conversation history. Please try again!",
            ErrorType.MESSAGE_HISTORY_ERROR: "📜 **Message History Error**\nI couldn't access the message history. Please try again!",
            
            # Image processing errors
            ErrorType.IMAGE_PROCESSING_ERROR: "🖼️ **Image Processing Error**\nI encountered an issue while processing your image. Please try again with a different image!\n\n💡 **Tip:** Use `/help image_editing` to learn about supported image editing features.",
            ErrorType.IMAGE_VALIDATION_ERROR: "🚫 **Invalid Image**\nThe image you uploaded appears to be corrupted or invalid. Please try uploading a different image!\n\n💡 **Tip:** Supported formats are JPG, PNG, GIF, and WebP up to 10MB.",
            ErrorType.IMAGE_SIZE_ERROR: "📏 **Image Too Large**\nYour image is too large to process. Please upload an image smaller than 10MB!\n\n💡 **Tip:** Try compressing your image or reducing its resolution before uploading.",
            ErrorType.IMAGE_FORMAT_ERROR: "🎨 **Unsupported Format**\nI can only process JPG, PNG, GIF, and WebP images. Please convert your image and try again!\n\n💡 **Tip:** Most image editors can save in these formats.",
            ErrorType.NANO_BANANA_API_ERROR: "🍌 **Image Editing Service Error**\nThe image editing service is temporarily unavailable. Please try again in a few minutes!\n\n💡 **Tip:** You can still use me for conversations and file analysis while image editing is unavailable.",
            ErrorType.IMAGE_UPLOAD_ERROR: "📤 **Upload Error**\nI couldn't upload the edited image. Please try again!\n\n💡 **Tip:** Check your internet connection and try the edit again.",
            ErrorType.IMAGE_DOWNLOAD_ERROR: "📥 **Download Error**\nI couldn't download your image. Please make sure it's accessible and try again!\n\n💡 **Tip:** Try re-uploading the image directly to Discord.",
            
            # Message formatting errors
            ErrorType.MESSAGE_SPLIT_ERROR: "✂️ **Message Formatting Error**\nI had trouble formatting your response. The message may be incomplete!\n\n💡 **Tip:** Try asking for a shorter response or request information in parts.",
            ErrorType.MARKDOWN_PROCESSING_ERROR: "📝 **Formatting Error**\nI encountered an issue while formatting the response. Some formatting may be lost!\n\n💡 **Tip:** The content is still accurate, just with simplified formatting.",
            ErrorType.MESSAGE_TOO_LONG_ERROR: "📏 **Response Too Long**\nMy response is too long to send. I'll try to break it into smaller parts!\n\n💡 **Tip:** Use `/prompt-mode short` for more concise responses.",
            
            # General errors
            ErrorType.UNKNOWN_ERROR: "🤷‍♂️ **Unexpected Error**\nSomething unexpected happened. Please try again!",
            ErrorType.VALIDATION_ERROR: "✅ **Validation Error**\nThere was an issue with the message format. Please try again!"
        }
    
    def _initialize_technical_messages(self) -> Dict[ErrorType, str]:
        """Initialize technical error messages for logging purposes."""
        return {
            ErrorType.RATE_LIMIT: "API rate limit exceeded",
            ErrorType.TIMEOUT: "Request timeout occurred",
            ErrorType.AUTHENTICATION_ERROR: "API authentication failed",
            ErrorType.SERVICE_UNAVAILABLE: "External service unavailable",
            ErrorType.EMPTY_RESPONSE: "API returned empty response",
            ErrorType.INVALID_REQUEST: "Invalid API request format",
            ErrorType.DISCORD_PERMISSION_ERROR: "Insufficient Discord permissions",
            ErrorType.DISCORD_HTTP_ERROR: "Discord HTTP error occurred",
            ErrorType.DISCORD_CONNECTION_ERROR: "Discord connection error",
            ErrorType.CONFIGURATION_ERROR: "Configuration validation failed",
            ErrorType.MISSING_TOKEN: "Required authentication token missing",
            ErrorType.INVALID_TOKEN: "Authentication token format invalid",
            ErrorType.CONTEXT_COLLECTION_ERROR: "Failed to collect message context",
            ErrorType.MESSAGE_HISTORY_ERROR: "Failed to retrieve message history",
            ErrorType.IMAGE_PROCESSING_ERROR: "Image processing operation failed",
            ErrorType.IMAGE_VALIDATION_ERROR: "Image validation failed",
            ErrorType.IMAGE_SIZE_ERROR: "Image exceeds maximum size limit",
            ErrorType.IMAGE_FORMAT_ERROR: "Unsupported image format",
            ErrorType.NANO_BANANA_API_ERROR: "Nano-banana API request failed",
            ErrorType.IMAGE_UPLOAD_ERROR: "Failed to upload processed image",
            ErrorType.IMAGE_DOWNLOAD_ERROR: "Failed to download source image",
            ErrorType.MESSAGE_SPLIT_ERROR: "Message splitting operation failed",
            ErrorType.MARKDOWN_PROCESSING_ERROR: "Markdown processing failed",
            ErrorType.MESSAGE_TOO_LONG_ERROR: "Message exceeds maximum length",
            ErrorType.UNKNOWN_ERROR: "Unhandled exception occurred",
            ErrorType.VALIDATION_ERROR: "Data validation failed"
        }
    
    def categorize_error(self, error: Exception) -> ErrorType:
        """
        Categorize an exception into an appropriate ErrorType.
        
        Args:
            error: The exception to categorize
            
        Returns:
            The appropriate ErrorType for the exception
        """
        error_str = str(error).lower()
        error_class = type(error).__name__.lower()
        
        # Discord-specific errors
        if isinstance(error, discord.Forbidden):
            return ErrorType.DISCORD_PERMISSION_ERROR
        elif isinstance(error, discord.HTTPException):
            return ErrorType.DISCORD_HTTP_ERROR
        elif isinstance(error, discord.ConnectionClosed):
            return ErrorType.DISCORD_CONNECTION_ERROR
        
        # Image processing errors (check first for specificity)
        if any(term in error_str for term in ["image size", "file too large", "exceeds maximum size"]):
            return ErrorType.IMAGE_SIZE_ERROR
        elif any(term in error_str for term in ["unsupported format", "invalid format", "image format"]):
            return ErrorType.IMAGE_FORMAT_ERROR
        elif any(term in error_str for term in ["nano-banana", "nano_banana", "image editing service"]):
            return ErrorType.NANO_BANANA_API_ERROR
        elif any(term in error_str for term in ["image validation", "corrupted image", "invalid image"]):
            return ErrorType.IMAGE_VALIDATION_ERROR
        elif any(term in error_str for term in ["image upload", "upload failed"]):
            return ErrorType.IMAGE_UPLOAD_ERROR
        elif any(term in error_str for term in ["image download", "download failed"]):
            return ErrorType.IMAGE_DOWNLOAD_ERROR
        elif any(term in error_str for term in ["image processing", "image edit"]):
            return ErrorType.IMAGE_PROCESSING_ERROR
        
        # Message formatting errors
        elif any(term in error_str for term in ["message split", "splitting failed"]):
            return ErrorType.MESSAGE_SPLIT_ERROR
        elif any(term in error_str for term in ["markdown processing", "markdown error"]):
            return ErrorType.MARKDOWN_PROCESSING_ERROR
        elif any(term in error_str for term in ["message too long", "exceeds character limit"]):
            return ErrorType.MESSAGE_TOO_LONG_ERROR
        
        # API-related errors (check error message content)
        elif any(term in error_str for term in ["rate limit", "quota exceeded", "too many requests"]):
            return ErrorType.RATE_LIMIT
        elif any(term in error_str for term in ["timeout", "timed out"]):
            return ErrorType.TIMEOUT
        elif any(term in error_str for term in ["authentication", "unauthorized", "api key", "invalid key"]):
            return ErrorType.AUTHENTICATION_ERROR
        elif any(term in error_str for term in ["service unavailable", "server error", "503", "502", "500"]):
            return ErrorType.SERVICE_UNAVAILABLE
        elif any(term in error_str for term in ["empty response", "no content", "blank response"]):
            return ErrorType.EMPTY_RESPONSE
        elif any(term in error_str for term in ["invalid request", "bad request", "400", "malformed"]):
            return ErrorType.INVALID_REQUEST
        
        # Configuration errors
        elif any(term in error_str for term in ["configuration", "config", "missing token", "token not found"]):
            return ErrorType.CONFIGURATION_ERROR
        elif any(term in error_str for term in ["validation", "invalid format", "format error"]):
            return ErrorType.VALIDATION_ERROR
        
        # Context collection errors
        elif any(term in error_str for term in ["context", "history", "message retrieval"]):
            return ErrorType.CONTEXT_COLLECTION_ERROR
        
        # Default to unknown error
        else:
            return ErrorType.UNKNOWN_ERROR
    
    def create_error_context(
        self, 
        error: Exception, 
        custom_message: Optional[str] = None,
        retry_after: Optional[int] = None,
        include_error_details: bool = True
    ) -> ErrorContext:
        """
        Create an ErrorContext object from an exception.
        
        Args:
            error: The exception that occurred
            custom_message: Optional custom user message
            retry_after: Optional retry delay in seconds
            include_error_details: Whether to include technical error details in user message
            
        Returns:
            ErrorContext object with categorized error information
        """
        error_type = self.categorize_error(error)
        
        # Determine if this error type should be retried
        retryable_errors = {
            ErrorType.RATE_LIMIT,
            ErrorType.TIMEOUT,
            ErrorType.SERVICE_UNAVAILABLE,
            ErrorType.DISCORD_HTTP_ERROR,
            ErrorType.DISCORD_CONNECTION_ERROR,
            ErrorType.NANO_BANANA_API_ERROR,
            ErrorType.IMAGE_UPLOAD_ERROR,
            ErrorType.IMAGE_DOWNLOAD_ERROR,
            ErrorType.MESSAGE_SPLIT_ERROR
        }
        should_retry = error_type in retryable_errors
        
        # Determine appropriate log level
        log_level = logging.WARNING if error_type in {
            ErrorType.RATE_LIMIT, 
            ErrorType.TIMEOUT, 
            ErrorType.EMPTY_RESPONSE
        } else logging.ERROR
        
        # Check if developer mode is enabled
        dev_mode = self.config and getattr(self.config, 'dev_mode_enabled', False)
        
        # Build user message with error details if requested
        if custom_message:
            user_message = custom_message
        else:
            user_message = self.get_user_message(error_type)
            
            # In dev mode, always include full error details
            if dev_mode and error:
                import traceback
                error_str = str(error).strip()
                error_trace = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
                
                user_message += f"\n\n**🔧 Developer Mode - Full Error Details:**"
                user_message += f"\n**Error Type:** `{type(error).__name__}`"
                if error_str:
                    user_message += f"\n**Error Message:** `{error_str}`"
                user_message += f"\n**Stack Trace:**\n```\n{error_trace[:1500]}\n```"  # Limit to avoid message too long
            
            # In normal mode, include brief details only if requested
            elif include_error_details and error:
                error_str = str(error).strip()
                if error_str and len(error_str) < 200:  # Only include if not too long
                    user_message += f"\n\n**Details:** `{error_str}`"
        
        return ErrorContext(
            error_type=error_type,
            original_error=error,
            user_message=user_message,
            technical_details=self._technical_messages.get(error_type, str(error)),
            retry_after=retry_after,
            should_retry=should_retry,
            log_level=log_level
        )
    
    def get_user_message(self, error_type: ErrorType) -> str:
        """
        Get a user-friendly error message for the given error type.
        
        Args:
            error_type: The type of error that occurred
            
        Returns:
            User-friendly error message string
        """
        return self._user_messages.get(
            error_type, 
            "I encountered an issue. Please try again! 🤖"
        )
    
    def get_technical_message(self, error_type: ErrorType) -> str:
        """
        Get a technical error message for logging purposes.
        
        Args:
            error_type: The type of error that occurred
            
        Returns:
            Technical error message string
        """
        return self._technical_messages.get(
            error_type,
            "Unhandled error occurred"
        )
    
    def log_error(self, error_context: ErrorContext, additional_info: Optional[str] = None) -> None:
        """
        Log an error with appropriate level and context information.
        
        Args:
            error_context: The error context to log
            additional_info: Optional additional information to include
        """
        log_message = f"{error_context.technical_details}"
        
        if additional_info:
            log_message += f" - {additional_info}"
        
        if error_context.original_error:
            logger.log(
                error_context.log_level,
                log_message,
                exc_info=error_context.original_error
            )
        else:
            logger.log(error_context.log_level, log_message)
    
    def handle_api_error(self, error: Exception, context_info: Optional[str] = None) -> ErrorContext:
        """
        Handle API-related errors with specific processing.
        
        Args:
            error: The API error that occurred
            context_info: Optional context information
            
        Returns:
            ErrorContext with API-specific handling
        """
        error_context = self.create_error_context(error)
        
        # Add specific handling for rate limiting
        if error_context.error_type == ErrorType.RATE_LIMIT:
            # Try to extract retry-after information from error
            error_str = str(error).lower()
            if "retry after" in error_str:
                try:
                    # Extract number from "retry after X seconds" type messages
                    import re
                    match = re.search(r'retry after (\d+)', error_str)
                    if match:
                        error_context.retry_after = int(match.group(1))
                except (ValueError, AttributeError):
                    pass
        
        # Log the error with context
        log_info = f"API Error"
        if context_info:
            log_info += f" ({context_info})"
        
        self.log_error(error_context, log_info)
        
        return error_context
    
    def handle_discord_error(self, error: Exception, message: Optional[discord.Message] = None) -> ErrorContext:
        """
        Handle Discord-related errors with specific processing.
        
        Args:
            error: The Discord error that occurred
            message: Optional Discord message context
            
        Returns:
            ErrorContext with Discord-specific handling
        """
        error_context = self.create_error_context(error)
        
        # Add Discord-specific context information
        context_info = "Discord Error"
        if message:
            context_info += f" (Channel: {message.channel.name if hasattr(message.channel, 'name') else 'DM'}"
            if hasattr(message, 'guild') and message.guild:
                context_info += f", Guild: {message.guild.name}"
            context_info += ")"
        
        self.log_error(error_context, context_info)
        
        return error_context
    
    async def send_error_response(
        self, 
        message: discord.Message, 
        error_context: ErrorContext,
        fallback_reaction: str = "❌"
    ) -> bool:
        """
        Send an error response to Discord with fallback options.
        
        Args:
            message: The original Discord message to respond to
            error_context: The error context containing user message
            fallback_reaction: Emoji to use as fallback if message sending fails
            
        Returns:
            True if any response method succeeded, False otherwise
        """
        try:
            # Try to send a reply message
            await message.reply(error_context.user_message)
            return True
            
        except discord.HTTPException as reply_error:
            logger.warning(f"Failed to send error reply: {reply_error}")
            
            try:
                # Try to send a regular message in the channel
                await message.channel.send(f"{message.author.mention} {error_context.user_message}")
                return True
                
            except discord.HTTPException as channel_error:
                logger.warning(f"Failed to send error message to channel: {channel_error}")
                
                try:
                    # Last resort: add a reaction
                    await message.add_reaction(fallback_reaction)
                    return True
                    
                except discord.HTTPException as reaction_error:
                    logger.error(f"Failed to add error reaction: {reaction_error}")
                    return False
        
        except Exception as unexpected_error:
            logger.error(f"Unexpected error sending error response: {unexpected_error}")
            return False
    
    def handle_image_processing_error(
        self, 
        error: Exception, 
        context_info: Optional[str] = None,
        image_size: Optional[int] = None,
        image_format: Optional[str] = None
    ) -> ErrorContext:
        """
        Handle image processing errors with specific processing and recovery suggestions.
        
        Args:
            error: The image processing error that occurred
            context_info: Optional context information
            image_size: Optional image size in bytes
            image_format: Optional image format
            
        Returns:
            ErrorContext with image processing-specific handling
        """
        error_context = self.create_error_context(error)
        
        # Add actionable suggestions based on error type
        if error_context.error_type == ErrorType.IMAGE_SIZE_ERROR:
            if image_size:
                size_mb = image_size / (1024 * 1024)
                error_context.user_message += f"\n\n💡 **Tip:** Your image is {size_mb:.1f}MB. Try compressing it or using a smaller resolution."
        
        elif error_context.error_type == ErrorType.IMAGE_FORMAT_ERROR:
            if image_format:
                error_context.user_message += f"\n\n💡 **Tip:** {image_format.upper()} format is not supported. Try converting to JPG or PNG."
            else:
                error_context.user_message += f"\n\n💡 **Tip:** Supported formats are JPG, PNG, GIF, and WebP."
        
        elif error_context.error_type == ErrorType.NANO_BANANA_API_ERROR:
            error_context.user_message += f"\n\n💡 **Tip:** The image editing service may be experiencing high load. Try again in a few minutes."
            # Set retry delay for API errors
            error_context.retry_after = 120  # 2 minutes
        
        elif error_context.error_type == ErrorType.IMAGE_PROCESSING_ERROR:
            error_context.user_message += f"\n\n💡 **Tip:** Try using a simpler image or different edit instructions."
        
        # Log the error with context
        log_info = f"Image Processing Error"
        if context_info:
            log_info += f" ({context_info})"
        if image_size:
            log_info += f" - Size: {image_size} bytes"
        if image_format:
            log_info += f" - Format: {image_format}"
        
        self.log_error(error_context, log_info)
        
        return error_context
    
    def handle_message_formatting_error(
        self, 
        error: Exception, 
        message_length: Optional[int] = None,
        context_info: Optional[str] = None
    ) -> ErrorContext:
        """
        Handle message formatting errors with recovery mechanisms.
        
        Args:
            error: The message formatting error that occurred
            message_length: Optional original message length
            context_info: Optional context information
            
        Returns:
            ErrorContext with message formatting-specific handling
        """
        error_context = self.create_error_context(error)
        
        # Add recovery suggestions based on error type
        if error_context.error_type == ErrorType.MESSAGE_TOO_LONG_ERROR:
            error_context.user_message += f"\n\n💡 **Tip:** I'll try to break this into smaller messages."
            if message_length:
                error_context.user_message += f" (Original length: {message_length:,} characters)"
        
        elif error_context.error_type == ErrorType.MESSAGE_SPLIT_ERROR:
            error_context.user_message += f"\n\n💡 **Tip:** Some formatting may be simplified to ensure delivery."
        
        elif error_context.error_type == ErrorType.MARKDOWN_PROCESSING_ERROR:
            error_context.user_message += f"\n\n💡 **Tip:** The response will be sent as plain text to ensure readability."
        
        # Log the error with context
        log_info = f"Message Formatting Error"
        if context_info:
            log_info += f" ({context_info})"
        if message_length:
            log_info += f" - Length: {message_length} characters"
        
        self.log_error(error_context, log_info)
        
        return error_context
    
    def suggest_error_resolution(self, error_type: ErrorType, context: dict) -> str:
        """
        Suggest specific resolution steps for different error types.
        
        Args:
            error_type: The type of error that occurred
            context: Additional context information
            
        Returns:
            Actionable resolution suggestion string
        """
        suggestions = {
            ErrorType.IMAGE_SIZE_ERROR: [
                "Compress your image using an online tool",
                "Reduce image resolution or dimensions",
                "Convert to JPG format for smaller file size"
            ],
            ErrorType.IMAGE_FORMAT_ERROR: [
                "Convert image to JPG, PNG, GIF, or WebP format",
                "Use an online image converter",
                "Save image in a different format from your editor"
            ],
            ErrorType.NANO_BANANA_API_ERROR: [
                "Wait a few minutes and try again",
                "Check if the image editing service is operational",
                "Try with a simpler edit instruction"
            ],
            ErrorType.IMAGE_PROCESSING_ERROR: [
                "Try with a different image",
                "Use simpler edit instructions",
                "Ensure image is not corrupted"
            ],
            ErrorType.MESSAGE_SPLIT_ERROR: [
                "Request a shorter response",
                "Ask for the information in parts",
                "Use simpler formatting in your request"
            ],
            ErrorType.DISCORD_PERMISSION_ERROR: [
                "Check bot permissions in server settings",
                "Ensure bot has 'Send Messages' permission",
                "Contact server administrator for permission help"
            ]
        }
        
        error_suggestions = suggestions.get(error_type, ["Try again later", "Contact support if issue persists"])
        
        suggestion_text = "**Suggested solutions:**\n"
        for i, suggestion in enumerate(error_suggestions, 1):
            suggestion_text += f"{i}. {suggestion}\n"
        
        return suggestion_text.strip()
    
    def log_user_action_error(
        self, 
        user_id: str, 
        action: str, 
        error: Exception,
        additional_context: Optional[dict] = None
    ) -> None:
        """
        Log user action errors with context for debugging and monitoring.
        
        Args:
            user_id: ID of the user who triggered the error
            action: The action that caused the error
            error: The exception that occurred
            additional_context: Optional additional context information
        """
        error_context = self.create_error_context(error)
        
        # Build context information
        context_parts = [f"User: {user_id}", f"Action: {action}"]
        
        if additional_context:
            for key, value in additional_context.items():
                context_parts.append(f"{key}: {value}")
        
        context_info = " | ".join(context_parts)
        
        # Log with appropriate level based on error severity
        if error_context.error_type in {ErrorType.RATE_LIMIT, ErrorType.TIMEOUT}:
            log_level = logging.WARNING
        else:
            log_level = logging.ERROR
        
        logger.log(
            log_level,
            f"User action error - {error_context.technical_details}",
            extra={
                'user_id': user_id,
                'action': action,
                'error_type': error_context.error_type.value,
                'context': context_info
            },
            exc_info=error
        )
    
    async def attempt_error_recovery(
        self, 
        error_context: ErrorContext, 
        recovery_function,
        max_retries: int = 3,
        backoff_factor: float = 2.0
    ) -> Tuple[bool, Optional[Exception]]:
        """
        Attempt to recover from transient errors with exponential backoff.
        
        Args:
            error_context: The error context to recover from
            recovery_function: Async function to call for recovery
            max_retries: Maximum number of retry attempts
            backoff_factor: Multiplier for retry delay
            
        Returns:
            Tuple of (success, final_error)
        """
        if not error_context.should_retry:
            return False, error_context.original_error
        
        import asyncio
        
        retry_count = 0
        last_error = error_context.original_error
        
        while retry_count < max_retries:
            try:
                # Calculate delay with exponential backoff
                delay = (backoff_factor ** retry_count)
                if error_context.retry_after:
                    delay = max(delay, error_context.retry_after)
                
                if delay > 0:
                    logger.info(f"Retrying after {delay} seconds (attempt {retry_count + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                
                # Attempt recovery
                await recovery_function()
                logger.info(f"Error recovery successful after {retry_count + 1} attempts")
                return True, None
                
            except Exception as retry_error:
                retry_count += 1
                last_error = retry_error
                logger.warning(f"Retry attempt {retry_count} failed: {retry_error}")
        
        logger.error(f"Error recovery failed after {max_retries} attempts")
        return False, last_error