"""
Tests for ErrorManager class.

Tests error categorization, user message generation, and error handling
according to requirements 4.1, 4.2, and 4.3.
"""

import asyncio
import logging
import unittest
from unittest.mock import Mock, patch, AsyncMock
import discord
from src.utils.error_manager import ErrorManager, ErrorType, ErrorContext


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestErrorManager(unittest.TestCase):
    """Test cases for ErrorManager functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.error_manager = ErrorManager()
        
        # Create mock Discord message
        self.mock_message = Mock(spec=discord.Message)
        self.mock_message.author = Mock()
        self.mock_message.author.mention = "@testuser"
        self.mock_message.channel = Mock()
        self.mock_message.guild = Mock()
        self.mock_message.guild.name = "Test Guild"
        self.mock_message.channel.name = "test-channel"
        self.mock_message.reply = AsyncMock()
        self.mock_message.channel.send = AsyncMock()
        self.mock_message.add_reaction = AsyncMock()

    def test_categorize_rate_limit_error(self):
        """Test categorization of rate limit errors."""
        # Test various rate limit error messages
        rate_limit_errors = [
            Exception("Rate limit exceeded"),
            Exception("Quota exceeded for requests"),
            Exception("Too many requests")
        ]
        
        for error in rate_limit_errors:
            with self.subTest(error=str(error)):
                error_type = self.error_manager.categorize_error(error)
                self.assertEqual(error_type, ErrorType.RATE_LIMIT)

    def test_categorize_timeout_error(self):
        """Test categorization of timeout errors."""
        timeout_errors = [
            Exception("Request timed out"),
            Exception("Connection timeout"),
            Exception("Operation timed out after 30 seconds"),
            asyncio.TimeoutError("Timeout occurred")
        ]
        
        for error in timeout_errors:
            with self.subTest(error=str(error)):
                error_type = self.error_manager.categorize_error(error)
                self.assertEqual(error_type, ErrorType.TIMEOUT)

    def test_categorize_authentication_error(self):
        """Test categorization of authentication errors."""
        auth_errors = [
            Exception("Authentication failed"),
            Exception("Invalid API key provided"),
            Exception("Unauthorized access"),
            Exception("API key not found")
        ]
        
        for error in auth_errors:
            with self.subTest(error=str(error)):
                error_type = self.error_manager.categorize_error(error)
                self.assertEqual(error_type, ErrorType.AUTHENTICATION_ERROR)

    def test_categorize_discord_errors(self):
        """Test categorization of Discord-specific errors."""
        # Test Discord permission error
        forbidden_error = discord.Forbidden(Mock(), "Insufficient permissions")
        error_type = self.error_manager.categorize_error(forbidden_error)
        self.assertEqual(error_type, ErrorType.DISCORD_PERMISSION_ERROR)
        
        # Test Discord HTTP error
        http_error = discord.HTTPException(Mock(), "HTTP error occurred")
        error_type = self.error_manager.categorize_error(http_error)
        self.assertEqual(error_type, ErrorType.DISCORD_HTTP_ERROR)
        
        # Test Discord connection error (using mock since ConnectionClosed is complex)
        connection_error = Mock(spec=discord.ConnectionClosed)
        connection_error.__class__ = discord.ConnectionClosed
        error_type = self.error_manager.categorize_error(connection_error)
        self.assertEqual(error_type, ErrorType.DISCORD_CONNECTION_ERROR)

    def test_categorize_unknown_error(self):
        """Test categorization of unknown errors."""
        unknown_errors = [
            Exception("Some random error"),
            ValueError("Invalid value provided"),
            RuntimeError("Runtime error occurred")
        ]
        
        for error in unknown_errors:
            with self.subTest(error=str(error)):
                error_type = self.error_manager.categorize_error(error)
                self.assertEqual(error_type, ErrorType.UNKNOWN_ERROR)

    def test_create_error_context(self):
        """Test creation of error context from exceptions."""
        test_error = Exception("Test error message")
        
        error_context = self.error_manager.create_error_context(test_error)
        
        self.assertIsInstance(error_context, ErrorContext)
        self.assertEqual(error_context.error_type, ErrorType.UNKNOWN_ERROR)
        self.assertEqual(error_context.original_error, test_error)
        self.assertIsNotNone(error_context.user_message)
        self.assertIsNotNone(error_context.technical_details)

    def test_create_error_context_with_custom_message(self):
        """Test creation of error context with custom user message."""
        test_error = Exception("Test error")
        custom_message = "Custom error message for user"
        
        error_context = self.error_manager.create_error_context(
            test_error, 
            custom_message=custom_message
        )
        
        self.assertEqual(error_context.user_message, custom_message)

    def test_create_error_context_retryable_errors(self):
        """Test that retryable errors are marked correctly."""
        retryable_errors = [
            Exception("Rate limit exceeded"),
            Exception("Request timed out"),
            Exception("Service unavailable")
        ]
        
        for error in retryable_errors:
            with self.subTest(error=str(error)):
                error_context = self.error_manager.create_error_context(error)
                self.assertTrue(error_context.should_retry)

    def test_create_error_context_non_retryable_errors(self):
        """Test that non-retryable errors are marked correctly."""
        non_retryable_errors = [
            Exception("Authentication failed"),
            Exception("Invalid request format"),
            Exception("Configuration error")
        ]
        
        for error in non_retryable_errors:
            with self.subTest(error=str(error)):
                error_context = self.error_manager.create_error_context(error)
                self.assertFalse(error_context.should_retry)

    def test_get_user_message(self):
        """Test retrieval of user-friendly error messages."""
        # Test that all error types have user messages
        for error_type in ErrorType:
            with self.subTest(error_type=error_type):
                message = self.error_manager.get_user_message(error_type)
                self.assertIsInstance(message, str)
                self.assertGreater(len(message), 0)
                # Check that message contains emoji (user-friendly indicator)
                self.assertTrue(any(char in message for char in "🤖⏰🔧🛠️🤔❓🔒📤🌐⚙️🔑🚫📚📜🤷‍♂️✅"))

    def test_get_technical_message(self):
        """Test retrieval of technical error messages."""
        # Test that all error types have technical messages
        for error_type in ErrorType:
            with self.subTest(error_type=error_type):
                message = self.error_manager.get_technical_message(error_type)
                self.assertIsInstance(message, str)
                self.assertGreater(len(message), 0)

    def test_handle_api_error(self):
        """Test API error handling with context."""
        api_error = Exception("API rate limit exceeded")
        
        error_context = self.error_manager.handle_api_error(api_error, "Test API call")
        
        self.assertEqual(error_context.error_type, ErrorType.RATE_LIMIT)
        self.assertEqual(error_context.original_error, api_error)
        self.assertTrue(error_context.should_retry)

    def test_handle_api_error_with_retry_after(self):
        """Test API error handling with retry-after extraction."""
        api_error = Exception("Rate limit exceeded, retry after 60 seconds")
        
        error_context = self.error_manager.handle_api_error(api_error)
        
        self.assertEqual(error_context.error_type, ErrorType.RATE_LIMIT)
        self.assertEqual(error_context.retry_after, 60)

    def test_handle_discord_error(self):
        """Test Discord error handling with message context."""
        discord_error = discord.Forbidden(Mock(), "Insufficient permissions")
        
        error_context = self.error_manager.handle_discord_error(discord_error, self.mock_message)
        
        self.assertEqual(error_context.error_type, ErrorType.DISCORD_PERMISSION_ERROR)
        self.assertEqual(error_context.original_error, discord_error)

    def test_log_error(self):
        """Test error logging functionality."""
        with patch('src.utils.error_manager.logger') as mock_logger:
            test_error = Exception("Test error")
            error_context = self.error_manager.create_error_context(test_error)
            
            self.error_manager.log_error(error_context, "Additional context")
            
            # Verify that logging was called
            mock_logger.log.assert_called_once()
            call_args = mock_logger.log.call_args
            self.assertEqual(call_args[0][0], error_context.log_level)
            self.assertIn("Additional context", call_args[0][1])

    def test_send_error_response_success(self):
        """Test successful error response sending."""
        async def test_send_success():
            error_context = ErrorContext(
                error_type=ErrorType.RATE_LIMIT,
                user_message="Test error message"
            )
            
            result = await self.error_manager.send_error_response(
                self.mock_message, 
                error_context
            )
            
            self.assertTrue(result)
            self.mock_message.reply.assert_called_once_with("Test error message")

        run_async_test(test_send_success())

    def test_send_error_response_reply_fails_channel_succeeds(self):
        """Test error response when reply fails but channel message succeeds."""
        async def test_reply_fails():
            # Mock reply to fail
            self.mock_message.reply.side_effect = discord.HTTPException(Mock(), "Reply failed")
            
            error_context = ErrorContext(
                error_type=ErrorType.TIMEOUT,
                user_message="Test timeout message"
            )
            
            result = await self.error_manager.send_error_response(
                self.mock_message, 
                error_context
            )
            
            self.assertTrue(result)
            self.mock_message.channel.send.assert_called_once()
            # Check that the channel message includes user mention
            call_args = self.mock_message.channel.send.call_args[0][0]
            self.assertIn("@testuser", call_args)
            self.assertIn("Test timeout message", call_args)

        run_async_test(test_reply_fails())

    def test_send_error_response_all_methods_fail_except_reaction(self):
        """Test error response when all methods fail except reaction."""
        async def test_all_fail_except_reaction():
            # Mock all methods to fail except reaction
            self.mock_message.reply.side_effect = discord.HTTPException(Mock(), "Reply failed")
            self.mock_message.channel.send.side_effect = discord.HTTPException(Mock(), "Send failed")
            
            error_context = ErrorContext(
                error_type=ErrorType.UNKNOWN_ERROR,
                user_message="Test error message"
            )
            
            result = await self.error_manager.send_error_response(
                self.mock_message, 
                error_context,
                fallback_reaction="⚠️"
            )
            
            self.assertTrue(result)
            self.mock_message.add_reaction.assert_called_once_with("⚠️")

        run_async_test(test_all_fail_except_reaction())

    def test_send_error_response_all_methods_fail(self):
        """Test error response when all methods fail."""
        async def test_all_fail():
            # Mock all methods to fail
            self.mock_message.reply.side_effect = discord.HTTPException(Mock(), "Reply failed")
            self.mock_message.channel.send.side_effect = discord.HTTPException(Mock(), "Send failed")
            self.mock_message.add_reaction.side_effect = discord.HTTPException(Mock(), "Reaction failed")
            
            error_context = ErrorContext(
                error_type=ErrorType.UNKNOWN_ERROR,
                user_message="Test error message"
            )
            
            result = await self.error_manager.send_error_response(
                self.mock_message, 
                error_context
            )
            
            self.assertFalse(result)

        run_async_test(test_all_fail())

    def test_error_message_consistency(self):
        """Test that error messages are consistent and appropriate."""
        # Test that rate limit messages mention trying again
        rate_limit_message = self.error_manager.get_user_message(ErrorType.RATE_LIMIT)
        self.assertIn("try again", rate_limit_message.lower())
        
        # Test that timeout messages mention time-related issues
        timeout_message = self.error_manager.get_user_message(ErrorType.TIMEOUT)
        self.assertTrue(any(word in timeout_message.lower() for word in ["time", "long", "again"]))
        
        # Test that permission messages mention permissions
        permission_message = self.error_manager.get_user_message(ErrorType.DISCORD_PERMISSION_ERROR)
        self.assertIn("permission", permission_message.lower())

    def test_error_context_validation(self):
        """Test error context validation and properties."""
        # Test that error context has required fields
        error = Exception("Test error")
        context = self.error_manager.create_error_context(error)
        
        self.assertIsNotNone(context.error_type)
        self.assertIsNotNone(context.user_message)
        self.assertIsNotNone(context.technical_details)
        self.assertIsInstance(context.should_retry, bool)
        self.assertIsInstance(context.log_level, int)

    def test_logging_levels_appropriate(self):
        """Test that logging levels are appropriate for different error types."""
        # Warning level errors (less severe)
        warning_errors = [
            Exception("Rate limit exceeded"),
            Exception("Request timed out"),
            Exception("Empty response received")
        ]
        
        for error in warning_errors:
            with self.subTest(error=str(error)):
                context = self.error_manager.create_error_context(error)
                self.assertIn(context.log_level, [logging.WARNING, logging.ERROR])
        
        # Error level errors (more severe)
        error_level_errors = [
            Exception("Authentication failed"),
            Exception("Configuration error"),
            Exception("Unknown error occurred")
        ]
        
        for error in error_level_errors:
            with self.subTest(error=str(error)):
                context = self.error_manager.create_error_context(error)
                self.assertEqual(context.log_level, logging.ERROR)


if __name__ == '__main__':
    unittest.main()