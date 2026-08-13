"""
Core data models for the Discord Grok Bot.

This module contains the primary data structures used throughout the application
for handling message context and API responses.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


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
    channel_id: Optional[int] = None
    is_reply: bool = False
    replied_to_id: Optional[int] = None
    retrieval_source: Optional[str] = None
    retrieval_score: Optional[float] = None
    retrieval_reason: Optional[str] = None
    is_pinned_memory: bool = False
    # Parent-document retrieval: a hit is expanded into its surrounding conversation.
    conversation_id: Optional[int] = None  # None = standalone, not part of an expanded block
    is_conversation_filler: bool = False  # True = surrounding context, not itself a hit

    def __post_init__(self):
        """Validate the message context after initialization."""
        # Note: content can be empty for messages with only attachments, embeds, or stickers
        if not self.author:
            raise ValueError("Message author cannot be empty")
        if self.message_id <= 0:
            raise ValueError("Message ID must be positive")
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("Channel ID must be positive when provided")
        if self.is_reply and self.replied_to_id is None:
            raise ValueError("Reply messages must have a replied_to_id")
        if not self.is_reply and self.replied_to_id is not None:
            raise ValueError("Non-reply messages cannot have a replied_to_id")


@dataclass
class TokenUsage:
    """Represents concrete token usage numbers returned by the LLM."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self):
        if self.input_tokens < 0 or self.output_tokens < 0 or self.total_tokens < 0:
            raise ValueError("Token counts must be non-negative")
        # Guard against obviously incorrect totals
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must be at least the sum of input and output tokens")


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
    grounding_sources: Optional[list] = None
    token_usage: Optional[TokenUsage] = None

    def __post_init__(self):
        """Validate the API response after initialization."""
        if self.success and not self.content:
            raise ValueError("Successful responses must have content")
        if not self.success and not self.error_type:
            raise ValueError("Failed responses must have an error_type")
        if self.retry_after is not None and self.retry_after < 0:
            raise ValueError("retry_after must be non-negative")
        if self.token_usage and not self.success:
            raise ValueError("token_usage may only be attached to successful responses")


class EditType(Enum):
    """Enumeration of supported image edit types."""
    OBJECT_REMOVAL = "object_removal"
    BACKGROUND_REPLACEMENT = "background_replacement"
    STYLE_TRANSFER = "style_transfer"
    COLOR_ADJUSTMENT = "color_adjustment"
    GENERAL_EDIT = "general_edit"


@dataclass
class ImageEditRequest:
    """
    Represents a request to edit an image using AI.
    
    Contains all necessary information for processing an image edit request,
    including the image data, edit instructions, and metadata.
    """
    user_id: str
    image_data: bytes
    instruction: str
    edit_type: EditType
    timestamp: datetime
    channel_id: str
    original_filename: Optional[str] = None
    
    def __post_init__(self):
        """Validate the image edit request after initialization."""
        if not self.user_id:
            raise ValueError("User ID cannot be empty")
        if not self.image_data:
            raise ValueError("Image data cannot be empty")
        if not self.instruction.strip():
            raise ValueError("Edit instruction cannot be empty")
        if not self.channel_id:
            raise ValueError("Channel ID cannot be empty")


@dataclass
class ImageEditResult:
    """
    Represents the result of an image editing operation.
    
    Contains the edited image data, processing metadata, and any error information.
    """
    success: bool
    edited_image: Optional[bytes] = None
    processing_time: float = 0.0
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    token_usage: Optional[TokenUsage] = None
    
    def __post_init__(self):
        """Validate the image edit result after initialization."""
        if self.success and not self.edited_image:
            raise ValueError("Successful results must have edited image data")
        if not self.success and not self.error_message:
            raise ValueError("Failed results must have an error message")
        if self.processing_time < 0:
            raise ValueError("Processing time must be non-negative")


@dataclass
class ValidationResult:
    """
    Represents the result of image validation.
    
    Contains validation status and any error messages or warnings.
    """
    is_valid: bool
    error_message: Optional[str] = None
    warnings: Optional[list[str]] = None
    file_size_mb: Optional[float] = None
    format: Optional[str] = None
    dimensions: Optional[tuple[int, int]] = None
    
    def __post_init__(self):
        """Validate the validation result after initialization."""
        if not self.is_valid and not self.error_message:
            raise ValueError("Invalid results must have an error message")
        if self.file_size_mb is not None and self.file_size_mb < 0:
            raise ValueError("File size must be non-negative")
        if self.dimensions is not None:
            width, height = self.dimensions
            if width <= 0 or height <= 0:
                raise ValueError("Image dimensions must be positive")
