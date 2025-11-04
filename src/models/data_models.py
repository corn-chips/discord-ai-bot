"""
Core data models for the Discord Grok Bot.

This module contains the primary data structures used throughout the application
for handling message context and API responses.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class MessageContext:
    """
    Represents a Discord message with relevant context information.
    
    Used to store message information for context collection and processing.
    """
    content: str
    author: str
    timestamp: datetime
    message_id: int
    is_reply: bool = False
    replied_to_id: Optional[int] = None

    def __post_init__(self):
        """Validate the message context after initialization."""
        # Note: content can be empty for messages with only attachments, embeds, or stickers
        if not self.author:
            raise ValueError("Message author cannot be empty")
        if self.message_id <= 0:
            raise ValueError("Message ID must be positive")
        if self.is_reply and self.replied_to_id is None:
            raise ValueError("Reply messages must have a replied_to_id")
        if not self.is_reply and self.replied_to_id is not None:
            raise ValueError("Non-reply messages cannot have a replied_to_id")


@dataclass
class APIResponse:
    """
    Represents a response from the Gemini API.
    
    Used to handle both successful responses and error conditions
    from the Gemini API in a structured way.
    """
    success: bool
    content: Optional[str] = None
    error_type: Optional[str] = None
    retry_after: Optional[int] = None

    def __post_init__(self):
        """Validate the API response after initialization."""
        if self.success and not self.content:
            raise ValueError("Successful responses must have content")
        if not self.success and not self.error_type:
            raise ValueError("Failed responses must have an error_type")
        if self.retry_after is not None and self.retry_after < 0:
            raise ValueError("retry_after must be non-negative")