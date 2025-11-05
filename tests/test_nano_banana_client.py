"""
Unit tests for NanoBananaClient.

Tests the nano-banana API client with mocked responses and error handling
according to requirements 1.1, 1.2, 6.2, and 6.5.
"""

import unittest
import asyncio
import json
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime
from src.services.nano_banana_client import (
    NanoBananaClient, ServiceStatus, ParsedInstruction, EditResponse,
    NanoBananaClientError
)
from src.models.data_models import EditType


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestNanoBananaClient(unittest.TestCase):
    """Test cases for NanoBananaClient."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = NanoBananaClient(
            api_key="test_api_key",
            base_url="https://api.test-nano-banana.com/v1",
            timeout=30,
            max_retries=2,
            retry_delay=0.1  # Short delay for testing
        )
        
        # Sample image data (small PNG)
        import io
        from PIL import Image
        test_image = Image.new('RGB', (100, 100), color='red')
        output = io.BytesIO()
        test_image.save(output, format='PNG')
        self.test_image_data = output.getvalue()

    def test_client_initialization(self):
        """Test client initialization with configuration."""
        self.assertEqual(self.client.api_key, "test_api_key")
        self.assertEqual(self.client.base_url, "https://api.test-nano-banana.com/v1")
        self.assertEqual(self.client.timeout, 30)
        self.assertEqual(self.client.max_retries, 2)
        self.assertEqual(self.client._service_status, ServiceStatus.UNKNOWN)

    def test_parse_object_removal_instruction(self):
        """Test parsing object removal instructions."""
        instructions = [
            "remove the person from the image",
            "delete the car in the background",
            "erase the object on the left",
            "get rid of the tree"
        ]
        
        for instruction in instructions:
            parsed = self.client.parse_edit_instruction(instruction)
            
            self.assertIsInstance(parsed, ParsedInstruction)
            self.assertEqual(parsed.edit_type, EditType.OBJECT_REMOVAL)
            self.assertEqual(parsed.original_text, instruction)
            self.assertGreater(parsed.confidence, 0)

    def test_parse_background_replacement_instruction(self):
        """Test parsing background replacement instructions."""
        instructions = [
            "change the background to a beach",
            "replace background with mountains",
            "put a forest in the background",
            "make the background a sunset"
        ]
        
        for instruction in instructions:
            parsed = self.client.parse_edit_instruction(instruction)
            
            self.assertEqual(parsed.edit_type, EditType.BACKGROUND_REPLACEMENT)
            self.assertGreater(parsed.confidence, 0)

    def test_parse_style_transfer_instruction(self):
        """Test parsing style transfer instructions."""
        instructions = [
            "make it look like a Van Gogh painting",
            "apply impressionist style",
            "convert to watercolor style",
            "artistic style of Picasso"
        ]
        
        for instruction in instructions:
            parsed = self.client.parse_edit_instruction(instruction)
            
            self.assertEqual(parsed.edit_type, EditType.STYLE_TRANSFER)
            self.assertGreater(parsed.confidence, 0)

    def test_parse_color_adjustment_instruction(self):
        """Test parsing color adjustment instructions."""
        instructions = [
            "make it brighter",
            "increase the contrast",
            "adjust the saturation",
            "color correction needed"
        ]
        
        for instruction in instructions:
            parsed = self.client.parse_edit_instruction(instruction)
            
            self.assertEqual(parsed.edit_type, EditType.COLOR_ADJUSTMENT)
            self.assertGreater(parsed.confidence, 0)

    def test_parse_general_instruction(self):
        """Test parsing instructions that don't match specific patterns."""
        instruction = "make this image look better somehow"
        parsed = self.client.parse_edit_instruction(instruction)
        
        self.assertEqual(parsed.edit_type, EditType.GENERAL_EDIT)
        self.assertEqual(parsed.confidence, 0.5)  # Default confidence for general edits

    def test_clean_instruction_text(self):
        """Test instruction text cleaning."""
        dirty_instruction = "  please   can you  remove the   person  "
        cleaned = self.client._clean_instruction_text(dirty_instruction)
        
        self.assertEqual(cleaned, "Remove the person")
        self.assertFalse(cleaned.startswith(" "))
        self.assertFalse(cleaned.endswith(" "))

    @patch('aiohttp.ClientSession')
    def test_successful_image_edit(self, mock_session_class):
        """Test successful image editing request."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            'success': True,
            'image': 'base64_encoded_image_data',
            'metadata': {'processing_time': 5.2}
        })
        
        mock_session = Mock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        # Mock base64 encoding/decoding
        with patch('base64.b64encode') as mock_b64encode, \
             patch('base64.b64decode') as mock_b64decode:
            
            mock_b64encode.return_value = b'encoded_image'
            mock_b64decode.return_value = b'decoded_edited_image'
            
            result = run_async_test(
                self.client.edit_image(
                    self.test_image_data,
                    "remove the person",
                    EditType.OBJECT_REMOVAL
                )
            )
        
        self.assertIsInstance(result, EditResponse)
        self.assertTrue(result.success)
        self.assertEqual(result.image_data, b'decoded_edited_image')
        self.assertGreater(result.processing_time, 0)

    @patch('aiohttp.ClientSession')
    def test_api_error_response(self, mock_session_class):
        """Test handling of API error responses."""
        # Mock error response
        mock_response = Mock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={
            'success': False,
            'error': 'Invalid image format'
        })
        
        mock_session = Mock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        with patch('base64.b64encode'):
            with self.assertRaises(NanoBananaClientError):
                run_async_test(
                    self.client.edit_image(
                        self.test_image_data,
                        "remove the person"
                    )
                )

    @patch('aiohttp.ClientSession')
    def test_rate_limit_handling(self, mock_session_class):
        """Test rate limit error handling."""
        # Mock rate limit response
        mock_response = Mock()
        mock_response.status = 429
        mock_response.headers = {'Retry-After': '60'}
        mock_response.json = AsyncMock(return_value={
            'error': 'Rate limit exceeded'
        })
        
        mock_session = Mock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        with patch('base64.b64encode'):
            with self.assertRaises(NanoBananaClientError) as context:
                run_async_test(
                    self.client.edit_image(
                        self.test_image_data,
                        "remove the person"
                    )
                )
            
            self.assertIn("Rate limited", str(context.exception))

    @patch('aiohttp.ClientSession')
    def test_authentication_error(self, mock_session_class):
        """Test authentication error handling."""
        # Mock authentication error
        mock_response = Mock()
        mock_response.status = 401
        mock_response.json = AsyncMock(return_value={
            'error': 'Invalid API key'
        })
        
        mock_session = Mock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        with patch('base64.b64encode'):
            with self.assertRaises(NanoBananaClientError) as context:
                run_async_test(
                    self.client.edit_image(
                        self.test_image_data,
                        "remove the person"
                    )
                )
            
            self.assertIn("Authentication failed", str(context.exception))

    @patch('aiohttp.ClientSession')
    def test_retry_logic_success_after_failure(self, mock_session_class):
        """Test retry logic with success after initial failure."""
        # Mock first failure, then success
        mock_response_fail = Mock()
        mock_response_fail.status = 500
        mock_response_fail.json = AsyncMock(return_value={'error': 'Server error'})
        
        mock_response_success = Mock()
        mock_response_success.status = 200
        mock_response_success.json = AsyncMock(return_value={
            'success': True,
            'image': 'base64_encoded_image_data'
        })
        
        mock_session = Mock()
        mock_session.post = AsyncMock(side_effect=[
            mock_response_fail,
            mock_response_success
        ])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        with patch('base64.b64encode'), \
             patch('base64.b64decode', return_value=b'edited_image'), \
             patch('asyncio.sleep'):  # Mock sleep to speed up test
            
            result = run_async_test(
                self.client.edit_image(
                    self.test_image_data,
                    "remove the person"
                )
            )
        
        self.assertTrue(result.success)
        self.assertEqual(mock_session.post.call_count, 2)

    @patch('aiohttp.ClientSession')
    def test_max_retries_exceeded(self, mock_session_class):
        """Test behavior when max retries are exceeded."""
        # Mock consistent failures
        mock_response = Mock()
        mock_response.status = 500
        mock_response.json = AsyncMock(return_value={'error': 'Server error'})
        
        mock_session = Mock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        with patch('base64.b64encode'), \
             patch('asyncio.sleep'):  # Mock sleep to speed up test
            
            with self.assertRaises(NanoBananaClientError):
                run_async_test(
                    self.client.edit_image(
                        self.test_image_data,
                        "remove the person"
                    )
                )
        
        # Should have tried max_retries + 1 times
        self.assertEqual(mock_session.post.call_count, self.client.max_retries + 1)

    @patch('aiohttp.ClientSession')
    def test_check_service_status_healthy(self, mock_session_class):
        """Test service health check returning healthy status."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'status': 'healthy'})
        
        mock_session = Mock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        status = run_async_test(self.client.check_service_status())
        
        self.assertEqual(status, ServiceStatus.HEALTHY)
        self.assertEqual(self.client._service_status, ServiceStatus.HEALTHY)
        self.assertEqual(self.client._consecutive_failures, 0)

    @patch('aiohttp.ClientSession')
    def test_check_service_status_unavailable(self, mock_session_class):
        """Test service health check returning unavailable status."""
        mock_response = Mock()
        mock_response.status = 503
        
        mock_session = Mock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session
        
        status = run_async_test(self.client.check_service_status())
        
        self.assertEqual(status, ServiceStatus.UNAVAILABLE)
        self.assertEqual(self.client._service_status, ServiceStatus.UNAVAILABLE)
        self.assertGreater(self.client._consecutive_failures, 0)

    def test_service_availability_check(self):
        """Test service availability checking."""
        # Initially unknown
        self.assertFalse(self.client.is_service_available())
        
        # Set to healthy
        self.client._service_status = ServiceStatus.HEALTHY
        self.assertTrue(self.client.is_service_available())
        
        # Set to degraded (still available)
        self.client._service_status = ServiceStatus.DEGRADED
        self.assertTrue(self.client.is_service_available())
        
        # Set to unavailable
        self.client._service_status = ServiceStatus.UNAVAILABLE
        self.assertFalse(self.client.is_service_available())

    def test_get_service_health_info(self):
        """Test getting service health information."""
        health_info = self.client.get_service_health_info()
        
        self.assertIsInstance(health_info, dict)
        self.assertIn('status', health_info)
        self.assertIn('consecutive_failures', health_info)
        self.assertIn('requests_in_last_minute', health_info)
        self.assertIn('rate_limit_remaining', health_info)

    def test_image_validation_failure(self):
        """Test handling of image validation failure."""
        # Mock validation failure
        with patch('src.utils.image_utils.validate_image') as mock_validate:
            from src.models.data_models import ValidationResult
            mock_validate.return_value = ValidationResult(
                is_valid=False,
                error_message="Image too large"
            )
            
            result = run_async_test(
                self.client.edit_image(
                    self.test_image_data,
                    "remove the person"
                )
            )
        
        self.assertFalse(result.success)
        self.assertIn("Image validation failed", result.error_message)

    def test_rate_limiting_enforcement(self):
        """Test client-side rate limiting enforcement."""
        # Fill up the rate limit
        for _ in range(self.client._max_requests_per_minute):
            self.client._request_times.append(datetime.now())
        
        # Next request should trigger rate limiting
        with patch('asyncio.sleep') as mock_sleep:
            run_async_test(self.client._check_rate_limit())
            mock_sleep.assert_called_once()


if __name__ == '__main__':
    unittest.main()