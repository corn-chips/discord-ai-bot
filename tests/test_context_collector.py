"""
Unit tests for ContextCollector service.

Tests the context collection functionality including message retrieval,
filtering, reply context enhancement, and formatting according to
requirements 2.1, 2.2, 3.1, 3.2.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import discord
from src.services.context_collector import ContextCollector
from src.models.data_models import MessageContext


class TestContextCollector(unittest.TestCase):
    """Test cases for ContextCollector class."""

    def setUp(self):
        """Set up test fixtures."""
        self.collector = ContextCollector(max_context_messages=100, reply_context_range=10)
        self.now = datetime.now(datetime.now().astimezone().tzinfo)
        
        # Create mock Discord objects
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_message = MagicMock(spec=discord.Message)
        self.mock_user = MagicMock(spec=discord.Member)
        self.mock_user.display_name = "test_user"
        self.mock_user.bot = False

    def test_context_collector_initialization(self):
        """Test ContextCollector initialization with default and custom values."""
        # Test default initialization
        default_collector = ContextCollector()
        self.assertEqual(default_collector.max_context_messages, 100)
        self.assertEqual(default_collector.reply_context_range, 10)
        
        # Test custom initialization
        custom_collector = ContextCollector(max_context_messages=50, reply_context_range=5)
        self.assertEqual(custom_collector.max_context_messages, 50)
        self.assertEqual(custom_collector.reply_context_range, 5)

    def test_format_context_empty_messages(self):
        """Test formatting context with empty message list."""
        result = self.collector.format_context([])
        self.assertEqual(result, "No recent conversation context available.")

    def test_format_context_single_message(self):
        """Test formatting context with a single message."""
        message = MessageContext(
            content="Hello world!",
            author="test_user",
            timestamp=self.now,
            message_id=123456789
        )
        
        result = self.collector.format_context([message])
        time_str = self.now.strftime("%H:%M")
        expected = f"=== CONVERSATION CONTEXT ===\n[{time_str}] test_user: Hello world!\n=== END CONTEXT ==="
        self.assertEqual(result, expected)

    def test_format_context_multiple_messages_chronological(self):
        """Test formatting context with multiple messages in chronological order."""
        earlier_time = self.now - timedelta(minutes=10)
        later_time = self.now
        
        # Create messages in reverse chronological order
        message1 = MessageContext(
            content="Second message",
            author="user2",
            timestamp=later_time,
            message_id=123456790
        )
        message2 = MessageContext(
            content="First message",
            author="user1",
            timestamp=earlier_time,
            message_id=123456789
        )
        
        result = self.collector.format_context([message1, message2])
        
        # Should be sorted chronologically (earliest first)
        earlier_str = earlier_time.strftime("%H:%M")
        later_str = later_time.strftime("%H:%M")
        expected_lines = [
            "=== CONVERSATION CONTEXT ===",
            f"[{earlier_str}] user1: First message",
            f"[{later_str}] user2: Second message",
            "=== END CONTEXT ==="
        ]
        expected = "\n".join(expected_lines)
        self.assertEqual(result, expected)

    def test_format_context_reply_message(self):
        """Test formatting context with reply message indicator."""
        message = MessageContext(
            content="This is a reply",
            author="test_user",
            timestamp=self.now,
            message_id=123456789,
            is_reply=True,
            replied_to_id=987654321
        )
        
        result = self.collector.format_context([message])
        time_str = self.now.strftime("%H:%M")
        expected = f"=== CONVERSATION CONTEXT ===\n[{time_str}] test_user (replying): This is a reply\n=== END CONTEXT ==="
        self.assertEqual(result, expected)

    def test_remove_duplicate_messages(self):
        """Test removing duplicate messages between standard and reply context."""
        # Create some messages
        message1 = MessageContext(
            content="Message 1",
            author="user1",
            timestamp=self.now,
            message_id=1
        )
        message2 = MessageContext(
            content="Message 2",
            author="user2",
            timestamp=self.now,
            message_id=2
        )
        message3 = MessageContext(
            content="Message 3",
            author="user3",
            timestamp=self.now,
            message_id=3
        )
        
        # Standard context has messages 1, 2, 3
        standard_context = [message1, message2, message3]
        
        # Reply context has messages 2, 3 (duplicates)
        reply_context = [message2, message3]
        
        result = self.collector._remove_duplicate_messages(standard_context, reply_context)
        
        # Should have reply context first, then non-duplicate standard context
        expected = [message2, message3, message1]  # Reply context + message1 (not duplicate)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].message_id, 2)  # Reply context first
        self.assertEqual(result[1].message_id, 3)  # Reply context first
        self.assertEqual(result[2].message_id, 1)  # Non-duplicate from standard

    def test_remove_duplicate_messages_no_duplicates(self):
        """Test removing duplicates when there are no duplicates."""
        message1 = MessageContext(
            content="Message 1",
            author="user1",
            timestamp=self.now,
            message_id=1
        )
        message2 = MessageContext(
            content="Message 2",
            author="user2",
            timestamp=self.now,
            message_id=2
        )
        
        standard_context = [message1]
        reply_context = [message2]
        
        result = self.collector._remove_duplicate_messages(standard_context, reply_context)
        
        # Should have both messages, reply context first
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].message_id, 2)  # Reply context first
        self.assertEqual(result[1].message_id, 1)  # Standard context second

    def test_remove_duplicate_messages_empty_contexts(self):
        """Test removing duplicates with empty contexts."""
        message1 = MessageContext(
            content="Message 1",
            author="user1",
            timestamp=self.now,
            message_id=1
        )
        
        # Test empty reply context
        result1 = self.collector._remove_duplicate_messages([message1], [])
        self.assertEqual(result1, [message1])
        
        # Test empty standard context
        result2 = self.collector._remove_duplicate_messages([], [message1])
        self.assertEqual(result2, [message1])
        
        # Test both empty
        result3 = self.collector._remove_duplicate_messages([], [])
        self.assertEqual(result3, [])


class TestContextCollectorAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for ContextCollector class."""

    def setUp(self):
        """Set up test fixtures."""
        self.collector = ContextCollector(max_context_messages=100, reply_context_range=10)
        self.now = datetime.now(datetime.now().astimezone().tzinfo)
        
        # Create mock Discord objects
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_message = MagicMock(spec=discord.Message)
        self.mock_user = MagicMock(spec=discord.Member)
        self.mock_user.display_name = "test_user"
        self.mock_user.bot = False

    async def test_get_channel_context_empty_channel(self):
        """Test getting context from empty channel."""
        # Mock empty channel history
        async def mock_history(limit):
            return
            yield  # Make this an async generator that yields nothing
        
        self.mock_channel.history = mock_history
        
        result = await self.collector.get_channel_context(self.mock_channel)
        self.assertEqual(result, [])

    async def test_get_channel_context_filters_old_messages(self):
        """Test that messages older than 24 hours are filtered out."""
        # Create messages - one recent, one old
        recent_message = MagicMock(spec=discord.Message)
        recent_message.created_at = self.now - timedelta(hours=1)  # 1 hour ago
        recent_message.content = "Recent message"
        recent_message.author = self.mock_user
        recent_message.id = 123456789
        recent_message.reference = None
        
        old_message = MagicMock(spec=discord.Message)
        old_message.created_at = self.now - timedelta(hours=25)  # 25 hours ago
        old_message.content = "Old message"
        old_message.author = self.mock_user
        old_message.id = 123456788
        old_message.reference = None
        
        # Mock channel history to return both messages
        async def mock_history(limit):
            yield old_message
            yield recent_message
        
        self.mock_channel.history = mock_history
        
        result = await self.collector.get_channel_context(self.mock_channel)
        
        # Should only contain the recent message
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "Recent message")

    async def test_get_channel_context_filters_bot_messages(self):
        """Test that bot messages are filtered out."""
        # Create user and bot messages
        user_message = MagicMock(spec=discord.Message)
        user_message.created_at = self.now - timedelta(hours=1)
        user_message.content = "User message"
        user_message.author = self.mock_user
        user_message.id = 123456789
        user_message.reference = None
        
        bot_user = MagicMock(spec=discord.Member)
        bot_user.display_name = "bot_user"
        bot_user.bot = True
        
        bot_message = MagicMock(spec=discord.Message)
        bot_message.created_at = self.now - timedelta(minutes=30)
        bot_message.content = "Bot message"
        bot_message.author = bot_user
        bot_message.id = 123456790
        bot_message.reference = None
        
        # Mock channel history
        async def mock_history(limit):
            yield bot_message
            yield user_message
        
        self.mock_channel.history = mock_history
        
        result = await self.collector.get_channel_context(self.mock_channel)
        
        # Should only contain the user message
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "User message")

    async def test_get_reply_context_no_reference(self):
        """Test getting reply context when message has no reference."""
        self.mock_message.reference = None
        
        result = await self.collector.get_reply_context(self.mock_message)
        self.assertEqual(result, [])

    async def test_get_reply_context_handles_discord_errors(self):
        """Test that Discord API errors are handled gracefully."""
        # Mock message with reference
        mock_reference = MagicMock()
        mock_reference.message_id = 987654321
        self.mock_message.reference = mock_reference
        self.mock_message.channel = self.mock_channel
        
        # Mock fetch_message to raise NotFound error
        self.mock_channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Message not found"))
        
        result = await self.collector.get_reply_context(self.mock_message)
        self.assertEqual(result, [])

    async def test_collect_full_context_no_reply(self):
        """Test collecting full context for non-reply message."""
        self.mock_message.reference = None
        self.mock_message.channel = self.mock_channel
        
        # Mock get_channel_context to return some messages
        with patch.object(self.collector, 'get_channel_context') as mock_get_context:
            mock_context = [
                MessageContext(
                    content="Test message",
                    author="test_user",
                    timestamp=self.now,
                    message_id=123456789
                )
            ]
            mock_get_context.return_value = mock_context
            
            result = await self.collector.collect_full_context(self.mock_message)
            
            # Should contain formatted context
            self.assertIn("=== CONVERSATION CONTEXT ===", result)
            self.assertIn("test_user: Test message", result)
            self.assertIn("=== END CONTEXT ===", result)

    async def test_collect_full_context_with_reply(self):
        """Test collecting full context for reply message."""
        # Mock message with reference
        mock_reference = MagicMock()
        mock_reference.message_id = 987654321
        self.mock_message.reference = mock_reference
        self.mock_message.channel = self.mock_channel
        
        # Mock both context collection methods
        with patch.object(self.collector, 'get_channel_context') as mock_get_context, \
             patch.object(self.collector, 'get_reply_context') as mock_get_reply:
            
            mock_standard = [
                MessageContext(
                    content="Standard message",
                    author="user1",
                    timestamp=self.now,
                    message_id=1
                )
            ]
            mock_reply = [
                MessageContext(
                    content="Reply context",
                    author="user2",
                    timestamp=self.now,
                    message_id=2
                )
            ]
            
            mock_get_context.return_value = mock_standard
            mock_get_reply.return_value = mock_reply
            
            result = await self.collector.collect_full_context(self.mock_message)
            
            # Should contain both contexts
            self.assertIn("=== CONVERSATION CONTEXT ===", result)
            self.assertIn("user1: Standard message", result)
            self.assertIn("user2: Reply context", result)
            self.assertIn("=== END CONTEXT ===", result)


if __name__ == '__main__':
    unittest.main()