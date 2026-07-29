"""
Image validation and utility functions for the Discord Grok Bot.

This module provides comprehensive image processing utilities including
validation, format conversion, compression, and basic image operations.
"""

import io
import logging
from PIL import Image
try:
    from PIL.ExifTags import ORIENTATION
except ImportError:
    # Fallback for older PIL versions
    ORIENTATION = 274

from src.models.data_models import ValidationResult

logger = logging.getLogger(__name__)

# Supported image formats
SUPPORTED_FORMATS = {
    'JPEG': ['.jpg', '.jpeg'],
    'PNG': ['.png'],
    'WEBP': ['.webp'],
    'GIF': ['.gif'],
    'BMP': ['.bmp'],
    'TIFF': ['.tiff', '.tif']
}

# Maximum dimensions for image processing
MAX_DIMENSION = 4096
MIN_DIMENSION = 32

# Quality settings for compression
DEFAULT_JPEG_QUALITY = 85
DEFAULT_WEBP_QUALITY = 80


class ImageValidationError(Exception):
    """Exception raised for image validation errors."""
    pass


class ImageProcessingError(Exception):
    """Exception raised for image processing errors."""
    pass


def validate_image(image_data: bytes, max_size_mb: float = 10.0) -> ValidationResult:
    """
    Validate image data for processing.
    
    Args:
        image_data: Raw image bytes
        max_size_mb: Maximum allowed file size in MB
        
    Returns:
        ValidationResult with validation status and metadata
    """
    try:
        # Check file size
        file_size_mb = len(image_data) / (1024 * 1024)
        if file_size_mb > max_size_mb:
            return ValidationResult(
                is_valid=False,
                error_message=f"Image size ({file_size_mb:.1f}MB) exceeds maximum allowed size ({max_size_mb}MB)",
                file_size_mb=file_size_mb
            )
        
        # Try to open and validate the image
        try:
            with Image.open(io.BytesIO(image_data)) as img:
                # Check if it's a valid image format
                if img.format not in SUPPORTED_FORMATS:
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"Unsupported image format: {img.format}. Supported formats: {', '.join(SUPPORTED_FORMATS.keys())}",
                        file_size_mb=file_size_mb,
                        format=img.format
                    )
                
                # Check dimensions
                width, height = img.size
                warnings = []
                
                if width < MIN_DIMENSION or height < MIN_DIMENSION:
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"Image dimensions ({width}x{height}) are too small. Minimum: {MIN_DIMENSION}x{MIN_DIMENSION}",
                        file_size_mb=file_size_mb,
                        format=img.format,
                        dimensions=(width, height)
                    )
                
                if width > MAX_DIMENSION or height > MAX_DIMENSION:
                    warnings.append(f"Large image dimensions ({width}x{height}) may require resizing for optimal processing")
                
                # Check for potential issues
                if img.mode not in ['RGB', 'RGBA', 'L']:
                    warnings.append(f"Image mode '{img.mode}' may require conversion for processing")
                
                return ValidationResult(
                    is_valid=True,
                    warnings=warnings if warnings else None,
                    file_size_mb=file_size_mb,
                    format=img.format,
                    dimensions=(width, height)
                )
                
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                error_message=f"Invalid or corrupted image file: {str(e)}",
                file_size_mb=file_size_mb
            )
            
    except Exception as e:
        logger.error(f"Error validating image: {e}")
        return ValidationResult(
            is_valid=False,
            error_message=f"Failed to validate image: {str(e)}"
        )


def estimate_processing_time(image_data: bytes, edit_type: str = "general") -> float:
    """
    Estimate processing time for an image based on size and edit type.
    
    Args:
        image_data: Raw image bytes
        edit_type: Type of edit operation
        
    Returns:
        Estimated processing time in seconds
    """
    try:
        # Base time factors (in seconds per megapixel)
        time_factors = {
            'object_removal': 3.0,
            'background_replacement': 4.0,
            'style_transfer': 5.0,
            'color_adjustment': 1.5,
            'general_edit': 3.0
        }
        
        with Image.open(io.BytesIO(image_data)) as img:
            megapixels = (img.width * img.height) / 1_000_000
            base_factor = time_factors.get(edit_type.lower(), 3.0)
            
            # Calculate base time
            estimated_time = megapixels * base_factor
            
            # Add overhead for large images
            if megapixels > 10:
                estimated_time *= 1.5
            elif megapixels > 5:
                estimated_time *= 1.2
            
            # Minimum processing time
            estimated_time = max(estimated_time, 5.0)
            
            # Maximum reasonable time
            estimated_time = min(estimated_time, 120.0)
            
            return round(estimated_time, 1)
            
    except Exception as e:
        logger.warning(f"Error estimating processing time: {e}")
        return 30.0  # Default fallback time
