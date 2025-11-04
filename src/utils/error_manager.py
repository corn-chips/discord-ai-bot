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
    
    def __init__(self):
        """Initialize the ErrorManager with predefined error messages."""
        self._user_messages = self._initialize_user_messages()
        self._technical_messages = self._initialize_technical_messages()
    
    def _initialize_user_messages(self) -> Dict[ErrorType, str]:
        """Initialize user-friendly error messages for each error type."""
        return {
            # API-related errors
            ErrorType.RATE_LIMIT: "I'm currently experiencing high demand. Please try again in a few moments! 🤖",
            ErrorType.TIMEOUT: "My response took too long to generate. Please try asking again! ⏰",
            ErrorType.AUTHENTICATION_ERROR: "I'm having trouble connecting to my AI service. Please try again later! 🔧",
            ErrorType.SERVICE_UNAVAILABLE: "My AI service is temporarily unavailable. Please try again in a few minutes! 🛠️",
            ErrorType.EMPTY_RESPONSE: "I couldn't generate a response to that. Could you try rephrasing your question? 🤔",
            ErrorType.INVALID_REQUEST: "I had trouble understanding your request. Could you try asking differently? ❓",
            
            # Discord-related errors
            ErrorType.DISCORD_PERMISSION_ERROR: "I don't have the necessary permissions to respond here. Please check my permissions! 🔒",
            ErrorType.DISCORD_HTTP_ERROR: "I'm having trouble sending messages right now. Please try again! 📤",
            ErrorType.DISCORD_CONNECTION_ERROR: "I'm experiencing connection issues with Discord. Please try again! 🌐",
            
            # Configuration errors
            ErrorType.CONFIGURATION_ERROR: "I'm experiencing a configuration issue. Please try again later! ⚙️",
            ErrorType.MISSING_TOKEN: "I'm not properly configured. Please contact an administrator! 🔑",
            ErrorType.INVALID_TOKEN: "My authentication credentials are invalid. Please contact an administrator! 🚫",
            
            # Context collection errors
            ErrorType.CONTEXT_COLLECTION_ERROR: "I had trouble reading the conversation history. Please try again! 📚",
            ErrorType.MESSAGE_HISTORY_ERROR: "I couldn't access the message history. Please try again! 📜",
            
            # General errors
            ErrorType.UNKNOWN_ERROR: "Something unexpected happened. Please try again! 🤷‍♂️",
            ErrorType.VALIDATION_ERROR: "There was an issue with the message format. Please try again! ✅"
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
        
        # API-related errors (check error message content)
        if any(term in error_str for term in ["rate limit", "quota exceeded", "too many requests"]):
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
        retry_after: Optional[int] = None
    ) -> ErrorContext:
        """
        Create an ErrorContext object from an exception.
        
        Args:
            error: The exception that occurred
            custom_message: Optional custom user message
            retry_after: Optional retry delay in seconds
            
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
            ErrorType.DISCORD_CONNECTION_ERROR
        }
        should_retry = error_type in retryable_errors
        
        # Determine appropriate log level
        log_level = logging.WARNING if error_type in {
            ErrorType.RATE_LIMIT, 
            ErrorType.TIMEOUT, 
            ErrorType.EMPTY_RESPONSE
        } else logging.ERROR
        
        return ErrorContext(
            error_type=error_type,
            original_error=error,
            user_message=custom_message or self.get_user_message(error_type),
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