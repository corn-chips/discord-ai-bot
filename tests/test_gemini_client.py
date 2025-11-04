"""
Integration tests for GeminiClient.

Tests the Gemini API client integration with mock responses and error handling
according to requirements 4.1, 4.2, and 4.3.
"""

import asyncio
import unittest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock
from src.config import BotConfig
from src.models import MessageContext, APIResponse
from src.services import GeminiClient


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGeminiClient(unittest.TestCase):
    """Test cases for GeminiClient integration."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = BotConfig(
            discord_token="test_discord_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_gemini_api_key_1234567890123456789012345678901234567890",
            max_context_messages=100,
            reply_context_range=10,
            response_timeout=30,
            max_retries=3
        )
        
        self.sample_context = [
            MessageContext(
                content="Hello everyone!",
                author="user1",
                timestamp=datetime(2024, 1, 1, 10, 0, 0),
                message_id=1001
            ),
            MessageContext(
                content="How's everyone doing?",
                author="user2",
                timestamp=datetime(2024, 1, 1, 10, 1, 0),
                message_id=1002
            ),
            MessageContext(
                content="I'm doing great, thanks!",
                author="user1",
                timestamp=datetime(2024, 1, 1, 10, 2, 0),
                message_id=1003,
                is_reply=True,
                replied_to_id=1002
            )
        ]

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_client_initialization(self, mock_model_class, mock_configure):
        """Test GeminiClient initialization and configuration."""
        mock_model = Mock()
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        # Verify API configuration was called
        mock_configure.assert_called_once_with(api_key=self.config.gemini_api_key)
        
        # Verify model was created with correct parameters
        mock_model_class.assert_called_once()
        call_args = mock_model_class.call_args
        self.assertEqual(call_args[1]['model_name'], 'gemini-1.5-flash')
        self.assertIn('generation_config', call_args[1])
        self.assertIn('safety_settings', call_args[1])

    def test_format_prompt_without_context(self):
        """Test prompt formatting without conversation context."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            prompt = client.format_prompt("What's the weather like?")
            
            self.assertIn("You are Grok", prompt)
            self.assertIn("What's the weather like?", prompt)
            self.assertNotIn("Recent Conversation Context", prompt)

    def test_format_prompt_with_context(self):
        """Test prompt formatting with conversation context."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            prompt = client.format_prompt("What did user1 say?", self.sample_context)
            
            self.assertIn("You are Grok", prompt)
            self.assertIn("Recent Conversation Context", prompt)
            self.assertIn("user1: Hello everyone!", prompt)
            self.assertIn("user2: How's everyone doing?", prompt)
            self.assertIn("user1 (replying): I'm doing great, thanks!", prompt)
            self.assertIn("What did user1 say?", prompt)

    def test_format_prompt_chronological_ordering(self):
        """Test that context messages are ordered chronologically."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            # Create context with messages in reverse chronological order
            reverse_context = list(reversed(self.sample_context))
            prompt = client.format_prompt("Test message", reverse_context)
            
            # Find the positions of the messages in the prompt
            pos_hello = prompt.find("user1: Hello everyone!")
            pos_how = prompt.find("user2: How's everyone doing?")
            pos_great = prompt.find("user1 (replying): I'm doing great, thanks!")
            
            # Verify chronological order (earlier messages appear first)
            self.assertLess(pos_hello, pos_how)
            self.assertLess(pos_how, pos_great)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_successful_response_generation(self, mock_model_class, mock_configure):
        """Test successful response generation from Gemini API."""
        # Mock the API response
        mock_response = Mock()
        mock_response.text = "This is a test response from Gemini"
        
        mock_model = Mock()
        mock_model.generate_content.return_value = mock_response
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        # Test the response generation
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertTrue(result.success)
        self.assertEqual(result.content, "This is a test response from Gemini")
        self.assertIsNone(result.error_type)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_empty_response_handling(self, mock_model_class, mock_configure):
        """Test handling of empty responses from Gemini API."""
        # Mock empty response
        mock_response = Mock()
        mock_response.text = ""
        
        mock_model = Mock()
        mock_model.generate_content.return_value = mock_response
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "empty_response")

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_timeout_handling(self, mock_model_class, mock_configure):
        """Test timeout handling in response generation."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = asyncio.TimeoutError()
        mock_model_class.return_value = mock_model
        
        # Use a very short timeout for testing
        short_timeout_config = BotConfig(
            discord_token=self.config.discord_token,
            gemini_api_key=self.config.gemini_api_key,
            response_timeout=0.1,  # Very short timeout
            max_retries=1  # Reduce retries for faster test
        )
        
        client = GeminiClient(short_timeout_config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "timeout")

    def test_error_categorization(self):
        """Test error categorization logic."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            # Test rate limit error
            rate_limit_error = Exception("Rate limit exceeded")
            self.assertEqual(client._categorize_error(rate_limit_error), "rate_limit")
            
            # Test authentication error
            auth_error = Exception("Invalid API key")
            self.assertEqual(client._categorize_error(auth_error), "authentication_error")
            
            # Test invalid request error
            invalid_error = Exception("Bad request format")
            self.assertEqual(client._categorize_error(invalid_error), "invalid_request")
            
            # Test timeout error
            timeout_error = Exception("Request timeout")
            self.assertEqual(client._categorize_error(timeout_error), "timeout")
            
            # Test unknown error
            unknown_error = Exception("Something unexpected happened")
            self.assertEqual(client._categorize_error(unknown_error), "unknown_error")

    def test_backoff_delay_calculation(self):
        """Test exponential backoff delay calculation."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            # Test that delays increase exponentially
            delay_0 = client._calculate_backoff_delay(0)
            delay_1 = client._calculate_backoff_delay(1)
            delay_2 = client._calculate_backoff_delay(2)
            
            # Delays should generally increase (accounting for jitter)
            self.assertGreater(delay_1, delay_0 - 0.5)  # Allow for jitter
            self.assertGreater(delay_2, delay_1 - 0.5)  # Allow for jitter
            
            # Test maximum delay cap
            delay_large = client._calculate_backoff_delay(10)
            self.assertLessEqual(delay_large, 30.0)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_retry_logic_with_rate_limiting(self, mock_model_class, mock_configure):
        """Test retry logic when encountering rate limiting."""
        mock_model = Mock()
        
        # First call fails with rate limit, second succeeds
        mock_response = Mock()
        mock_response.text = "Success after retry"
        
        mock_model.generate_content.side_effect = [
            Exception("Rate limit exceeded"),
            mock_response
        ]
        mock_model_class.return_value = mock_model
        
        # Use shorter delays for testing
        client = GeminiClient(self.config)
        
        # Mock the backoff delay to be very short for testing
        with patch.object(client, '_calculate_backoff_delay', return_value=0.01):
            result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertTrue(result.success)
        self.assertEqual(result.content, "Success after retry")
        
        # Verify generate_content was called twice (initial + 1 retry)
        self.assertEqual(mock_model.generate_content.call_count, 2)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_max_retries_exceeded(self, mock_model_class, mock_configure):
        """Test behavior when max retries are exceeded."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("Rate limit exceeded")
        mock_model_class.return_value = mock_model
        
        # Use config with only 1 retry for faster testing
        retry_config = BotConfig(
            discord_token=self.config.discord_token,
            gemini_api_key=self.config.gemini_api_key,
            max_retries=1
        )
        
        client = GeminiClient(retry_config)
        
        # Mock the backoff delay to be very short for testing
        with patch.object(client, '_calculate_backoff_delay', return_value=0.01):
            result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "rate_limit")
        
        # Verify generate_content was called max_retries + 1 times
        self.assertEqual(mock_model.generate_content.call_count, 2)  # 1 initial + 1 retry

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_non_retryable_error_immediate_return(self, mock_model_class, mock_configure):
        """Test that non-retryable errors return immediately without retries."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("Invalid API key")
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "authentication_error")
        
        # Verify generate_content was called only once (no retries for auth errors)
        self.assertEqual(mock_model.generate_content.call_count, 1)


if __name__ == '__main__':
    # Run async tests
    unittest.main()  
  @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_rate_limit_error_with_retry(self, mock_model_class, mock_configure):
        """Test rate limit error handling with retry logic."""
        mock_model = Mock()
        # First call fails with rate limit, second succeeds
        mock_response = Mock()
        mock_response.text = "Success after retry"
        mock_model.generate_content.side_effect = [
            Exception("Rate limit exceeded"),
            mock_response
        ]
        mock_model_class.return_value = mock_model
        
        # Use config with retries
        retry_config = BotConfig(
            discord_token=self.config.discord_token,
            gemini_api_key=self.config.gemini_api_key,
            max_retries=2,
            response_timeout=30
        )
        
        client = GeminiClient(retry_config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertTrue(result.success)
        self.assertEqual(result.content, "Success after retry")

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_authentication_error_no_retry(self, mock_model_class, mock_configure):
        """Test that authentication errors are not retried."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("Authentication failed - invalid API key")
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "authentication_error")
        
        # Verify generate_content was only called once (no retries)
        self.assertEqual(mock_model.generate_content.call_count, 1)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_service_unavailable_with_retries(self, mock_model_class, mock_configure):
        """Test service unavailable error with retry attempts."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("Service unavailable - 503 error")
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertIsInstance(result, APIResponse)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "service_unavailable")
        
        # Verify retries were attempted (max_retries + 1 total calls)
        expected_calls = self.config.max_retries + 1
        self.assertEqual(mock_model.generate_content.call_count, expected_calls)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_performance_logging_on_success(self, mock_model_class, mock_configure):
        """Test that performance metrics are logged on successful API calls."""
        mock_response = Mock()
        mock_response.text = "Test response"
        
        mock_model = Mock()
        mock_model.generate_content.return_value = mock_response
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        with patch.object(client.performance_logger, 'log_api_call') as mock_log:
            result = run_async_test(client.generate_response("Test prompt"))
            
            self.assertTrue(result.success)
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            self.assertEqual(call_args[1]['api_name'], 'gemini_generate_content')
            self.assertTrue(call_args[1]['success'])
            self.assertIsInstance(call_args[1]['duration'], float)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_performance_logging_on_error(self, mock_model_class, mock_configure):
        """Test that performance metrics are logged on API errors."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("API error")
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        with patch.object(client.performance_logger, 'log_api_call') as mock_log:
            result = run_async_test(client.generate_response("Test prompt"))
            
            self.assertFalse(result.success)
            mock_log.assert_called()
            # Check that error was logged
            call_args = mock_log.call_args
            self.assertEqual(call_args[1]['api_name'], 'gemini_generate_content')
            self.assertFalse(call_args[1]['success'])
            self.assertIsNotNone(call_args[1]['error_type'])

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_error_manager_integration(self, mock_model_class, mock_configure):
        """Test integration with ErrorManager for error handling."""
        mock_model = Mock()
        mock_model.generate_content.side_effect = Exception("Test API error")
        mock_model_class.return_value = mock_model
        
        client = GeminiClient(self.config)
        
        with patch.object(client.error_manager, 'handle_api_error') as mock_handle_error:
            # Mock error context
            from src.utils.error_manager import ErrorContext, ErrorType
            mock_error_context = ErrorContext(
                error_type=ErrorType.UNKNOWN_ERROR,
                user_message="Test error message",
                should_retry=False
            )
            mock_handle_error.return_value = mock_error_context
            
            result = run_async_test(client.generate_response("Test prompt"))
            
            self.assertFalse(result.success)
            mock_handle_error.assert_called()
            # Verify error context was used
            self.assertEqual(result.error_type, ErrorType.UNKNOWN_ERROR.value)
            self.assertEqual(result.content, "Test error message")

    def test_backoff_delay_calculation(self):
        """Test exponential backoff delay calculation."""
        with patch('google.generativeai.configure'), \
             patch('google.generativeai.GenerativeModel'):
            client = GeminiClient(self.config)
            
            # Test that delays increase exponentially
            delay_0 = client._calculate_backoff_delay(0)
            delay_1 = client._calculate_backoff_delay(1)
            delay_2 = client._calculate_backoff_delay(2)
            
            # Delays should increase (accounting for jitter)
            self.assertGreater(delay_1, delay_0)
            self.assertGreater(delay_2, delay_1)
            
            # Should be capped at 30 seconds
            delay_large = client._calculate_backoff_delay(10)
            self.assertLessEqual(delay_large, 30.0)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_configuration_error_handling(self, mock_model_class, mock_configure):
        """Test handling of configuration errors."""
        # Mock configuration failure
        mock_configure.side_effect = Exception("Invalid API key format")
        
        with self.assertRaises(Exception):
            GeminiClient(self.config)

    @patch('src.services.gemini_client.genai.configure')
    @patch('src.services.gemini_client.genai.GenerativeModel')
    def test_model_not_configured_error(self, mock_model_class, mock_configure):
        """Test error when model is not properly configured."""
        client = GeminiClient(self.config)
        client._model = None  # Simulate configuration failure
        
        result = run_async_test(client.generate_response("Test prompt"))
        
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "configuration_error")
        self.assertIn("not properly configured", result.content)