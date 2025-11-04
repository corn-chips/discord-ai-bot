"""
Unit tests for core data models.

Tests the MessageContext and APIResponse data classes for proper validation
and behavior according to requirements 2.3 and 4.1.
"""

import unittest
from datetime import datetime
from src.models import MessageContext, APIResponse


class TestMessageContext(unittest.TestCase):
    """Test cases for MessageContext data class."""

    def setUp(self):
        """Set up test fixtures."""
        self.valid_timestamp = datetime.now()
        self.valid_message_id = 123456789

    def test_valid_message_context_creation(self):
        """Test creating a valid MessageContext instance."""
        context = MessageContext(
            content="Hello world!",
            author="test_user",
            timestamp=self.valid_timestamp,
            message_id=self.valid_message_id
        )
        
        self.assertEqual(context.content, "Hello world!")
        self.assertEqual(context.author, "test_user")
        self.assertEqual(context.timestamp, self.valid_timestamp)
        self.assertEqual(context.message_id, self.valid_message_id)
        self.assertFalse(context.is_reply)
        self.assertIsNone(context.replied_to_id)

    def test_reply_message_context_creation(self):
        """Test creating a MessageContext for a reply message."""
        replied_to_id = 987654321
        context = MessageContext(
            content="This is a reply",
            author="test_user",
            timestamp=self.valid_timestamp,
            message_id=self.valid_message_id,
            is_reply=True,
            replied_to_id=replied_to_id
        )
        
        self.assertTrue(context.is_reply)
        self.assertEqual(context.replied_to_id, replied_to_id)

    def test_empty_content_raises_error(self):
        """Test that empty content raises ValueError."""
        with self.assertRaises(ValueError) as context:
            MessageContext(
                content="",
                author="test_user",
                timestamp=self.valid_timestamp,
                message_id=self.valid_message_id
            )
        self.assertIn("content cannot be empty", str(context.exception))

    def test_empty_author_raises_error(self):
        """Test that empty author raises ValueError."""
        with self.assertRaises(ValueError) as context:
            MessageContext(
                content="Hello world!",
                author="",
                timestamp=self.valid_timestamp,
                message_id=self.valid_message_id
            )
        self.assertIn("author cannot be empty", str(context.exception))

    def test_invalid_message_id_raises_error(self):
        """Test that invalid message ID raises ValueError."""
        with self.assertRaises(ValueError) as context:
            MessageContext(
                content="Hello world!",
                author="test_user",
                timestamp=self.valid_timestamp,
                message_id=0
            )
        self.assertIn("Message ID must be positive", str(context.exception))

    def test_reply_without_replied_to_id_raises_error(self):
        """Test that reply message without replied_to_id raises ValueError."""
        with self.assertRaises(ValueError) as context:
            MessageContext(
                content="This is a reply",
                author="test_user",
                timestamp=self.valid_timestamp,
                message_id=self.valid_message_id,
                is_reply=True
            )
        self.assertIn("Reply messages must have a replied_to_id", str(context.exception))

    def test_non_reply_with_replied_to_id_raises_error(self):
        """Test that non-reply message with replied_to_id raises ValueError."""
        with self.assertRaises(ValueError) as context:
            MessageContext(
                content="Not a reply",
                author="test_user",
                timestamp=self.valid_timestamp,
                message_id=self.valid_message_id,
                is_reply=False,
                replied_to_id=987654321
            )
        self.assertIn("Non-reply messages cannot have a replied_to_id", str(context.exception))


class TestAPIResponse(unittest.TestCase):
    """Test cases for APIResponse data class."""

    def test_successful_response_creation(self):
        """Test creating a successful APIResponse."""
        response = APIResponse(
            success=True,
            content="Generated response text"
        )
        
        self.assertTrue(response.success)
        self.assertEqual(response.content, "Generated response text")
        self.assertIsNone(response.error_type)
        self.assertIsNone(response.retry_after)

    def test_failed_response_creation(self):
        """Test creating a failed APIResponse."""
        response = APIResponse(
            success=False,
            error_type="rate_limit",
            retry_after=60
        )
        
        self.assertFalse(response.success)
        self.assertIsNone(response.content)
        self.assertEqual(response.error_type, "rate_limit")
        self.assertEqual(response.retry_after, 60)

    def test_successful_response_without_content_raises_error(self):
        """Test that successful response without content raises ValueError."""
        with self.assertRaises(ValueError) as context:
            APIResponse(success=True)
        self.assertIn("Successful responses must have content", str(context.exception))

    def test_failed_response_without_error_type_raises_error(self):
        """Test that failed response without error_type raises ValueError."""
        with self.assertRaises(ValueError) as context:
            APIResponse(success=False)
        self.assertIn("Failed responses must have an error_type", str(context.exception))

    def test_negative_retry_after_raises_error(self):
        """Test that negative retry_after raises ValueError."""
        with self.assertRaises(ValueError) as context:
            APIResponse(
                success=False,
                error_type="rate_limit",
                retry_after=-1
            )
        self.assertIn("retry_after must be non-negative", str(context.exception))

    def test_zero_retry_after_is_valid(self):
        """Test that zero retry_after is valid."""
        response = APIResponse(
            success=False,
            error_type="rate_limit",
            retry_after=0
        )
        self.assertEqual(response.retry_after, 0)


if __name__ == '__main__':
    unittest.main()