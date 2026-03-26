"""
Gemini 2.5 Flash Image (Nano-Banana) client for image generation and editing operations.

This module provides a client interface for Google's Gemini 2.5 Flash Image model,
which supports image generation and prompt-based editing. The model is accessed through
the Google GenAI SDK.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any
from io import BytesIO
import PIL.Image

try:
    from google import genai
    from google.genai import types
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    genai = None
    types = None
    logging.warning("Google GenAI SDK not available. Install: pip install google-generativeai")

from ..models.data_models import EditType, TokenUsage
from ..utils.image_utils import validate_image
from ..utils.token_extraction import extract_token_usage

logger = logging.getLogger(__name__)


class ServiceStatus(Enum):
    """Enumeration of service status states."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ParsedInstruction:
    """Represents a parsed image edit instruction."""
    
    def __init__(self, original_text: str, edit_type: EditType, 
                 processed_text: str, confidence: float = 1.0):
        self.original_text = original_text
        self.edit_type = edit_type
        self.processed_text = processed_text
        self.confidence = confidence
        self.keywords = []
        self.parameters = {}


class EditResponse:
    """Represents a response from the nano-banana API."""
    
    def __init__(self, success: bool, image_data: Optional[bytes] = None,
                 error_message: Optional[str] = None, processing_time: float = 0.0,
                 metadata: Optional[Dict[str, Any]] = None,
                 token_usage: Optional[TokenUsage] = None):
        self.success = success
        self.image_data = image_data
        self.error_message = error_message
        self.processing_time = processing_time
        self.metadata = metadata or {}
        self.token_usage = token_usage


class NanoBananaClientError(Exception):
    """Exception raised for nano-banana client errors."""
    pass


class NanoBananaClient:
    """
    Client for interacting with Google's Gemini 2.5 Flash Image model (codename: nano-banana).
    
    Provides methods for image generation and editing using Google's GenAI SDK,
    with instruction parsing, service monitoring, and robust error handling.
    """
    
    def __init__(self, api_key: str, base_url: str = None, timeout: int = 60,
                 max_retries: int = 3, retry_delay: float = 1.0,
                 max_requests_per_minute: int = 30):
        """
        Initialize the Gemini 2.5 Flash Image client.
        
        Args:
            api_key: Google API key for authentication
            base_url: Unused (kept for compatibility)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Base delay between retries in seconds
        """
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Configure GenAI client
        try:
            if not SDK_AVAILABLE or genai is None:
                logger.warning("Google GenAI SDK is not available. Image generation will be disabled.")
                logger.warning("Please install: pip install google-generativeai")
                self.client = None
                self.model_name = "gemini-2.5-flash-image"
                return
            
            # Initialize the new google.genai client
            self.client = genai.Client(api_key=api_key)
            self.model_name = "gemini-2.5-flash-image"  # Official image generation model
            
            logger.info(f"✅ Gemini 2.5 Flash Image client initialized (model: {self.model_name})")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini image client: {e}")
            self.client = None
        
        # Service monitoring
        self._last_health_check = None
        self._service_status = ServiceStatus.HEALTHY if self.client else ServiceStatus.UNAVAILABLE
        self._consecutive_failures = 0
        
        # Rate limiting
        self._request_times = []
        self._max_requests_per_minute = max_requests_per_minute
        
        # Edit type patterns for instruction parsing
        self._edit_patterns = {
            EditType.OBJECT_REMOVAL: [
                r'\b(remove|delete|erase|eliminate|take\s+out)\b.*\b(object|person|thing|item)\b',
                r'\b(get\s+rid\s+of|clear\s+away)\b',
                r'\bremove\s+the\s+\w+',
                r'\bdelete\s+\w+'
            ],
            EditType.BACKGROUND_REPLACEMENT: [
                r'\b(change|replace|swap|switch)\s+(the\s+)?background\b',
                r'\bbackground\s+(to|with|as)\b',
                r'\bput\s+.*\s+(in|on|at|to)\b',
                r'\bplace\s+.*\s+(in|on|at|to)\b',
                r'\bmake\s+the\s+background\b',
                r'\bon\s+top\s+of\b',
                r'\bin\s+front\s+of\b',
                r'\b(move|relocate|position)\s+\w+\s+to\b'
            ],
            EditType.STYLE_TRANSFER: [
                r'\b(style|artistic|art)\s+(of|like|as)\b',
                r'\bmake\s+it\s+(look\s+like|in\s+the\s+style\s+of)\b',
                r'\b(convert|transform)\s+to\s+\w+\s+style\b',
                r'\bapply\s+.*\s+style\b'
            ],
            EditType.COLOR_ADJUSTMENT: [
                r'\b(adjust|change|modify)\s+(color|brightness|contrast|saturation)\b',
                r'\bmake\s+it\s+(brighter|darker|more\s+colorful|less\s+colorful)\b',
                r'\b(increase|decrease|enhance)\s+(brightness|contrast|saturation)\b',
                r'\bcolor\s+(correction|grading)\b'
            ]
        }
    
    async def edit_image(self, image_data: bytes, instruction: str, 
                        edit_type: Optional[EditType] = None) -> EditResponse:
        """
        Edit or generate an image using Gemini 2.5 Flash Image model.
        
        For editing: Provide the original image with editing instructions.
        For generation: Provide None as image_data with generation instructions.
        
        Args:
            image_data: Raw image bytes (or None for generation)
            instruction: Natural language edit/generation instruction
            edit_type: Optional specific edit type (auto-detected if None)
            
        Returns:
            EditResponse with the result
            
        Raises:
            NanoBananaClientError: If the request fails after retries
        """
        start_time = datetime.now()
        
        try:
            # Validate image if provided (editing mode)
            if image_data:
                validation = validate_image(image_data)
                if not validation.is_valid:
                    return EditResponse(
                        success=False,
                        error_message=f"Image validation failed: {validation.error_message}"
                    )
            
            # Parse instruction if edit_type not provided
            if edit_type is None:
                parsed = self.parse_edit_instruction(instruction)
                edit_type = parsed.edit_type
                processed_instruction = parsed.processed_text
            else:
                processed_instruction = instruction.strip()
            
            # Check rate limiting
            await self._check_rate_limit()
            
            # Build prompt for image editing/generation
            if image_data:
                # Editing mode: Be more explicit that we want image output
                prompt = f"{processed_instruction}\n\nReturn the edited image as the output."
                logger.info(f"Image editing mode - instruction: {processed_instruction}")
            else:
                # Generation mode: Be explicit that we want to generate an image
                prompt = f"Generate an image: {processed_instruction}"
                logger.info(f"Image generation mode - prompt: {prompt}")
            
            # Make request with retry logic using Gemini SDK
            result_image_data, token_usage = await self._make_gemini_request(prompt, image_data)
            
            response_metadata = {'edit_type': edit_type.value, 'model': self.model_name}
            if token_usage:
                response_metadata['token_usage'] = token_usage

            if result_image_data:
                processing_time = (datetime.now() - start_time).total_seconds()
                return EditResponse(
                    success=True,
                    image_data=result_image_data,
                    processing_time=processing_time,
                    metadata=response_metadata,
                    token_usage=token_usage,
                )
            else:
                return EditResponse(
                    success=False,
                    error_message="Failed to generate/edit image - no image data returned",
                    processing_time=(datetime.now() - start_time).total_seconds(),
                    metadata=response_metadata,
                    token_usage=token_usage,
                )
                
        except Exception as e:
            logger.error(f"Error editing/generating image: {e}", exc_info=True)
            self._consecutive_failures += 1
            return EditResponse(
                success=False,
                error_message=f"Image operation failed: {str(e)}",
                processing_time=(datetime.now() - start_time).total_seconds()
            )
    
    async def _make_gemini_request(self, prompt: str, image_data: Optional[bytes] = None) -> tuple[Optional[bytes], Optional[TokenUsage]]:
        """
        Make a request to Gemini 2.5 Flash Image model.
        
        Args:
            prompt: The text prompt for image generation/editing
            image_data: Optional image bytes for editing operations
            
        Returns:
            Tuple of (image bytes, token usage metadata)
        """
        last_exception = None
        token_usage: Optional[TokenUsage] = None
        
        for attempt in range(self.max_retries + 1):
            try:
                token_usage = None
                # Build content list
                contents = []
                
                # Add image if provided (for editing)
                if image_data:
                    try:
                        # Load image using PIL to validate, then convert to bytes Part for SDK
                        image = PIL.Image.open(BytesIO(image_data))
                        logger.info(f"Loaded image for editing: {image.size}, {image.format}")
                        
                        # Convert to PNG bytes for the SDK
                        img_buffer = BytesIO()
                        image.save(img_buffer, format='PNG')
                        img_bytes = img_buffer.getvalue()
                        contents.append(types.Part.from_bytes(data=img_bytes, mime_type='image/png'))
                    except Exception as e:
                        logger.error(f"Failed to load image: {e}")
                        raise NanoBananaClientError(f"Invalid image data: {e}")
                
                # Add prompt
                contents.append(prompt)
                
                logger.info(f"Making Gemini request with {len(contents)} content items")
                
                # Make request using the new google.genai SDK
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=['Image']  # Request only image output
                    )
                )
                token_usage = self._extract_token_usage(response)
                
                # Extract image from response
                if response:
                    logger.info(f"Response received. Has candidates: {hasattr(response, 'candidates')}")
                    
                    if hasattr(response, 'candidates') and response.candidates:
                        logger.info(f"Number of candidates: {len(response.candidates)}")
                        
                        for idx, candidate in enumerate(response.candidates):
                            logger.info(f"Candidate {idx}: has content: {hasattr(candidate, 'content')}")
                            
                            if hasattr(candidate, 'content') and candidate.content:
                                logger.info(f"Content has parts: {hasattr(candidate.content, 'parts')}")
                                
                                if hasattr(candidate.content, 'parts'):
                                    logger.info(f"Number of parts: {len(candidate.content.parts)}")
                                    
                                    for part_idx, part in enumerate(candidate.content.parts):
                                        logger.info(f"Part {part_idx} attributes: {dir(part)}")
                                        
                                        # Check for inline_data (legacy SDK format)
                                        if hasattr(part, 'inline_data') and part.inline_data is not None:
                                            logger.info("Found inline_data in part")
                                            self._consecutive_failures = 0
                                            return part.inline_data.data, token_usage
                                        
                                        # Check for blob (new SDK format)
                                        if hasattr(part, 'blob') and part.blob is not None:
                                            logger.info("Found blob in part")
                                            self._consecutive_failures = 0
                                            # blob might have 'data' or be the data itself
                                            if hasattr(part.blob, 'data'):
                                                return part.blob.data, token_usage
                                            else:
                                                return part.blob, token_usage
                                        
                                        # Check for image_url or other image formats
                                        if hasattr(part, 'image_url'):
                                            logger.warning("Part has image_url, which is not supported yet")
                                        
                                        # Log part type for debugging
                                        if hasattr(part, 'text'):
                                            logger.info(f"Part {part_idx} is text: {part.text[:100] if len(part.text) > 100 else part.text}")
                    
                    # Check if response has text (error case)
                    if hasattr(response, 'text'):
                        logger.warning(f"Response has text instead of image: {response.text[:200]}")
                
                # No image in response
                logger.warning("No image data found in response")
                logger.warning(f"Response structure: {type(response)}, attributes: {dir(response) if response else 'None'}")
                return None, token_usage
                
            except Exception as e:
                last_exception = e
                logger.warning(f"Gemini request attempt {attempt + 1} failed: {e}")
                
                if attempt < self.max_retries:
                    # Exponential backoff
                    delay = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                else:
                    break
        
        # All retries failed
        self._consecutive_failures += 1
        raise NanoBananaClientError(f"Gemini request failed after {self.max_retries + 1} attempts: {last_exception}")

    def _extract_token_usage(self, response) -> Optional[TokenUsage]:
        """Extract token usage metadata from Gemini API responses using shared utility."""
        return extract_token_usage(response)
    
    async def check_service_status(self) -> ServiceStatus:
        """
        Check the health status of the Gemini image service.
        
        Returns:
            ServiceStatus indicating current service health
        """
        try:
            # For Gemini SDK, service is available if client is initialized
            if self.client:
                if self._consecutive_failures < 3:
                    self._service_status = ServiceStatus.HEALTHY
                elif self._consecutive_failures < 5:
                    self._service_status = ServiceStatus.DEGRADED
                else:
                    self._service_status = ServiceStatus.UNAVAILABLE
            else:
                self._service_status = ServiceStatus.UNAVAILABLE
            
            self._last_health_check = datetime.now()
            return self._service_status
            
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            self._service_status = ServiceStatus.UNAVAILABLE
            self._last_health_check = datetime.now()
            return self._service_status 
   
    def parse_edit_instruction(self, instruction: str) -> ParsedInstruction:
        """
        Parse natural language instruction to determine edit type and extract parameters.
        
        Args:
            instruction: Natural language edit instruction
            
        Returns:
            ParsedInstruction with detected edit type and processed text
        """
        instruction_lower = instruction.lower().strip()
        
        # Score each edit type based on pattern matches
        type_scores = {}
        
        for edit_type, patterns in self._edit_patterns.items():
            score = 0
            matched_patterns = []
            
            for pattern in patterns:
                matches = re.findall(pattern, instruction_lower)
                if matches:
                    score += len(matches)
                    matched_patterns.extend(matches)
            
            if score > 0:
                type_scores[edit_type] = score
        
        # Determine best match
        if type_scores:
            best_type = max(type_scores, key=type_scores.get)
            confidence = min(type_scores[best_type] / 3.0, 1.0)  # Normalize confidence
        else:
            # Default to general edit if no specific patterns match
            best_type = EditType.GENERAL_EDIT
            confidence = 0.5
        
        # Clean up instruction text
        processed_text = self._clean_instruction_text(instruction)
        
        return ParsedInstruction(
            original_text=instruction,
            edit_type=best_type,
            processed_text=processed_text,
            confidence=confidence
        )
    
    def _clean_instruction_text(self, instruction: str) -> str:
        """
        Clean and normalize instruction text for API consumption.
        
        Args:
            instruction: Raw instruction text
            
        Returns:
            Cleaned instruction text
        """
        # Remove extra whitespace
        cleaned = re.sub(r'\s+', ' ', instruction.strip())
        
        # Remove common filler words that don't add meaning
        filler_words = ['please', 'can you', 'could you', 'i want', 'i need', 'help me']
        for filler in filler_words:
            cleaned = re.sub(rf'\b{re.escape(filler)}\b', '', cleaned, flags=re.IGNORECASE)
        
        # Clean up extra spaces again
        cleaned = re.sub(r'\s+', ' ', cleaned.strip())
        
        # Ensure it starts with a capital letter
        if cleaned and cleaned[0].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
        
        return cleaned
    

    
    async def _check_rate_limit(self):
        """
        Check and enforce rate limiting.
        
        Raises:
            NanoBananaClientError: If rate limit would be exceeded
        """
        now = datetime.now()
        
        # Remove requests older than 1 minute
        self._request_times = [
            req_time for req_time in self._request_times 
            if now - req_time < timedelta(minutes=1)
        ]
        
        # Check if we're at the limit
        if len(self._request_times) >= self._max_requests_per_minute:
            oldest_request = min(self._request_times)
            wait_time = 60 - (now - oldest_request).total_seconds()
            
            if wait_time > 0:
                logger.warning(f"Rate limit reached. Waiting {wait_time:.1f} seconds")
                await asyncio.sleep(wait_time)
        
        # Record this request
        self._request_times.append(now)
    
    def get_service_health_info(self) -> Dict[str, Any]:
        """
        Get detailed service health information.
        
        Returns:
            Dictionary with service health metrics
        """
        return {
            'status': self._service_status.value,
            'last_health_check': self._last_health_check.isoformat() if self._last_health_check else None,
            'consecutive_failures': self._consecutive_failures,
            'requests_in_last_minute': len(self._request_times),
            'rate_limit_remaining': max(0, self._max_requests_per_minute - len(self._request_times))
        }
    
    def is_service_available(self) -> bool:
        """
        Check if the service is currently available for requests.
        
        Returns:
            True if service is available, False otherwise
        """
        return self._service_status in [ServiceStatus.HEALTHY, ServiceStatus.DEGRADED]