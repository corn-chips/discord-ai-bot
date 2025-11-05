"""
Unit tests for image utility functions.

Tests image validation, compression, format conversion, and other utility functions
according to requirements 1.5 and 6.4.
"""

import unittest
import io
from unittest.mock import patch, Mock
from PIL import Image
from src.utils.image_utils import (
    validate_image, get_supported_formats, get_supported_extensions,
    normalize_image, compress_image, convert_format, get_image_info,
    estimate_processing_time, ImageValidationError, ImageProcessingError
)
from src.models.data_models import ValidationResult


class TestImageValidation(unittest.TestCase):
    """Test cases for image validation functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a small test image
        self.test_image = Image.new('RGB', (100, 100), color='red')
        self.test_image_bytes = self._image_to_bytes(self.test_image)
        
        # Create a large test image
        self.large_image = Image.new('RGB', (2000, 2000), color='blue')
        self.large_image_bytes = self._image_to_bytes(self.large_image)

    def _image_to_bytes(self, img: Image.Image, format='PNG') -> bytes:
        """Convert PIL Image to bytes."""
        output = io.BytesIO()
        img.save(output, format=format)
        return output.getvalue()

    def test_validate_valid_image(self):
        """Test validation of a valid image."""
        result = validate_image(self.test_image_bytes, max_size_mb=10.0)
        
        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.is_valid)
        self.assertIsNone(result.error_message)
        self.assertEqual(result.format, 'PNG')
        self.assertEqual(result.dimensions, (100, 100))
        self.assertIsNotNone(result.file_size_mb)

    def test_validate_oversized_image(self):
        """Test validation of an oversized image."""
        # Create a very large image data
        large_data = b'x' * (11 * 1024 * 1024)  # 11MB of dummy data
        
        result = validate_image(large_data, max_size_mb=10.0)
        
        self.assertFalse(result.is_valid)
        self.assertIn("exceeds maximum allowed size", result.error_message)
        self.assertIsNotNone(result.file_size_mb)

    def test_validate_corrupted_image(self):
        """Test validation of corrupted image data."""
        corrupted_data = b'not_an_image_file'
        
        result = validate_image(corrupted_data)
        
        self.assertFalse(result.is_valid)
        self.assertIn("Invalid or corrupted image", result.error_message)

    def test_validate_small_image_dimensions(self):
        """Test validation of image with too small dimensions."""
        small_image = Image.new('RGB', (10, 10), color='green')
        small_image_bytes = self._image_to_bytes(small_image)
        
        result = validate_image(small_image_bytes)
        
        self.assertFalse(result.is_valid)
        self.assertIn("too small", result.error_message)
        self.assertEqual(result.dimensions, (10, 10))

    def test_validate_large_image_warning(self):
        """Test validation warning for large image dimensions."""
        result = validate_image(self.large_image_bytes, max_size_mb=50.0)
        
        self.assertTrue(result.is_valid)
        self.assertIsNotNone(result.warnings)
        self.assertTrue(any("Large image dimensions" in warning for warning in result.warnings))

    def test_get_supported_formats(self):
        """Test getting supported image formats."""
        formats = get_supported_formats()
        
        self.assertIsInstance(formats, list)
        self.assertIn('JPEG', formats)
        self.assertIn('PNG', formats)
        self.assertIn('WEBP', formats)

    def test_get_supported_extensions(self):
        """Test getting supported file extensions."""
        extensions = get_supported_extensions()
        
        self.assertIsInstance(extensions, list)
        self.assertIn('.jpg', extensions)
        self.assertIn('.png', extensions)
        self.assertIn('.webp', extensions)


class TestImageProcessing(unittest.TestCase):
    """Test cases for image processing functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_image = Image.new('RGB', (200, 200), color='red')
        self.test_image_bytes = self._image_to_bytes(self.test_image)

    def _image_to_bytes(self, img: Image.Image, format='PNG') -> bytes:
        """Convert PIL Image to bytes."""
        output = io.BytesIO()
        img.save(output, format=format)
        return output.getvalue()

    def test_normalize_image_success(self):
        """Test successful image normalization."""
        normalized_bytes = normalize_image(self.test_image_bytes)
        
        self.assertIsInstance(normalized_bytes, bytes)
        self.assertGreater(len(normalized_bytes), 0)
        
        # Verify the normalized image can be opened
        with Image.open(io.BytesIO(normalized_bytes)) as img:
            self.assertEqual(img.size, (200, 200))

    def test_normalize_image_with_exif(self):
        """Test normalization of image with EXIF orientation data."""
        # Create JPEG image (which can have EXIF data)
        jpeg_bytes = self._image_to_bytes(self.test_image, 'JPEG')
        
        normalized_bytes = normalize_image(jpeg_bytes)
        
        self.assertIsInstance(normalized_bytes, bytes)
        # Verify image is still valid after normalization
        with Image.open(io.BytesIO(normalized_bytes)) as img:
            self.assertEqual(img.size, (200, 200))

    def test_normalize_invalid_image(self):
        """Test normalization of invalid image data."""
        invalid_data = b'not_an_image'
        
        with self.assertRaises(ImageProcessingError):
            normalize_image(invalid_data)

    def test_compress_image_under_target(self):
        """Test compression when image is already under target size."""
        # Small image should not be compressed
        compressed_bytes = compress_image(self.test_image_bytes, target_size_mb=10.0)
        
        # Should return original data since it's already small
        self.assertEqual(compressed_bytes, self.test_image_bytes)

    def test_compress_image_over_target(self):
        """Test compression when image exceeds target size."""
        # Create a larger image
        large_image = Image.new('RGB', (1000, 1000), color='blue')
        large_image_bytes = self._image_to_bytes(large_image)
        
        compressed_bytes = compress_image(large_image_bytes, target_size_mb=0.1)
        
        self.assertIsInstance(compressed_bytes, bytes)
        self.assertLess(len(compressed_bytes), len(large_image_bytes))
        
        # Verify compressed image is still valid
        with Image.open(io.BytesIO(compressed_bytes)) as img:
            self.assertIsNotNone(img.size)

    def test_convert_format_to_jpeg(self):
        """Test format conversion to JPEG."""
        jpeg_bytes = convert_format(self.test_image_bytes, 'JPEG', quality=90)
        
        self.assertIsInstance(jpeg_bytes, bytes)
        
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            self.assertEqual(img.format, 'JPEG')
            self.assertEqual(img.size, (200, 200))

    def test_convert_format_to_webp(self):
        """Test format conversion to WebP."""
        webp_bytes = convert_format(self.test_image_bytes, 'WEBP', quality=80)
        
        self.assertIsInstance(webp_bytes, bytes)
        
        with Image.open(io.BytesIO(webp_bytes)) as img:
            self.assertEqual(img.format, 'WEBP')
            self.assertEqual(img.size, (200, 200))

    def test_convert_unsupported_format(self):
        """Test conversion to unsupported format."""
        with self.assertRaises(ImageProcessingError):
            convert_format(self.test_image_bytes, 'UNSUPPORTED')

    def test_get_image_info(self):
        """Test getting image information."""
        info = get_image_info(self.test_image_bytes)
        
        self.assertIsInstance(info, dict)
        self.assertEqual(info['format'], 'PNG')
        self.assertEqual(info['mode'], 'RGB')
        self.assertEqual(info['size'], (200, 200))
        self.assertEqual(info['width'], 200)
        self.assertEqual(info['height'], 200)
        self.assertIn('file_size_bytes', info)
        self.assertIn('file_size_mb', info)
        self.assertIn('has_transparency', info)
        self.assertIn('animated', info)

    def test_get_image_info_invalid_data(self):
        """Test getting info from invalid image data."""
        with self.assertRaises(ImageProcessingError):
            get_image_info(b'invalid_image_data')

    def test_estimate_processing_time(self):
        """Test processing time estimation."""
        time_estimate = estimate_processing_time(self.test_image_bytes, 'object_removal')
        
        self.assertIsInstance(time_estimate, float)
        self.assertGreater(time_estimate, 0)
        self.assertLessEqual(time_estimate, 120.0)  # Should be capped at 120 seconds

    def test_estimate_processing_time_different_types(self):
        """Test processing time estimation for different edit types."""
        removal_time = estimate_processing_time(self.test_image_bytes, 'object_removal')
        style_time = estimate_processing_time(self.test_image_bytes, 'style_transfer')
        color_time = estimate_processing_time(self.test_image_bytes, 'color_adjustment')
        
        # Style transfer should take longer than color adjustment
        self.assertGreater(style_time, color_time)
        
        # All should be reasonable values
        for time_val in [removal_time, style_time, color_time]:
            self.assertGreater(time_val, 0)
            self.assertLessEqual(time_val, 120.0)

    def test_estimate_processing_time_invalid_image(self):
        """Test processing time estimation with invalid image."""
        time_estimate = estimate_processing_time(b'invalid', 'general_edit')
        
        # Should return default fallback time
        self.assertEqual(time_estimate, 30.0)


if __name__ == '__main__':
    unittest.main()