"""
End-to-end integration tests for Discord Grok Bot.

Tests the complete mention-to-response flow including context collection,
AI response generation, and error handling according to requirements
1.3, 1.4, 1.5, 4.2, and 4.4.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timedelta
import discord

from src.config import BotConfig
from src.bot.discord_bot import DiscordBot
from src.models.data_models import MessageContext, APIResponse


class TestEndToEndIntegration(unittest.IsolatedAsyncioTestCase):
    """End-to-end integration tests for the complete bot workflow."""

    def setUp(self):
        """Set up test fixtures for integration tests."""
        self.config = BotConfig(
            discord_token="test_discord_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_gemini_api_key_1234567890123456789012345678901234567890",
            max_context_messages=100,
            reply_context_range=10,
            response_timeout=30,
            max_retries=3
        )
        
        # Create mock Discord objects
        self.mock_bot_user = MagicMock(spec=discord.User)
        self.mock_bot_user.id = 123456789
        self.mock_bot_user.name = "grok_bot"
        
        self.mock_human_user = MagicMock(spec=discord.Member)
        self.mock_human_user.id = 987654321
        self.mock_human_user.name = "human_user"
        self.mock_human_user.display_name = "Human User"
        self.mock_human_user.bot = False
        
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_channel.id = 555666777
        self.mock_channel.name = "general"

    def _create_mock_message(self, content: str, author: MagicMock, message_id: int, 
                           timestamp: datetime, reference=None) -> MagicMock:
        """Create a mock Discord message."""
        message = MagicMock(spec=discord.Message)
        message.id = message_id
        message.content = content
        message.author = author
        message.created_at = timestamp
        message.channel = self.mock_channel
        message.reference = reference
        message.mentions = []
        message.mention_everyone = False
        message.role_mentions = []
        message.reply = AsyncMock()
        return message

    async def test_complete_mention_to_response_flow(self):
        """
        Test the complete flow from mention detection to AI response delivery.
        
        Requirements: 1.3, 1.4 - Generate and deliver AI responses within timeout.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up successful AI response
            mock_response = MagicMock()
            mock_response.text = "Hello! I can see you're discussing a coding project. That sounds interesting!"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Set the model on the gemini client to avoid configuration error
            bot.gemini_client._model = mock_model
            
            # Mock the async response generation method
            async def mock_generate_async(prompt):
                return mock_response
            
            bot.gemini_client._generate_response_async = mock_generate_async
            
            # Create mention message
            mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> What do you think about this conversation?",
                author=self.mock_human_user,
                message_id=1004,
                timestamp=datetime.now()
            )
            mention_message.mentions = [self.mock_bot_user]
            
            # Create conversation history with timezone-aware timestamps
            now = datetime.now().astimezone()
            conversation_history = [
                self._create_mock_message(
                    content="Hey everyone, how's it going?",
                    author=self.mock_human_user,
                    message_id=1001,
                    timestamp=now - timedelta(minutes=10)
                ),
                self._create_mock_message(
                    content="Pretty good! Working on some code.",
                    author=self.mock_human_user,
                    message_id=1002,
                    timestamp=now - timedelta(minutes=8)
                )
            ]
            
            # Mock channel history for context collection
            async def mock_history(limit=None, before=None, after=None, oldest_first=False):
                for msg in reversed(conversation_history):
                    yield msg
            
            self.mock_channel.history = mock_history
            
            # Process the mention with mocked bot user
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(mention_message)
            
            # Verify response was sent
            mention_message.reply.assert_called_once()
            response_content = mention_message.reply.call_args[0][0]
            
            # Verify AI response content
            self.assertEqual(response_content, "Hello! I can see you're discussing a coding project. That sounds interesting!")

    async def test_timeout_error_handling(self):
        """
        Test timeout error handling in the complete flow.
        
        Requirements: 1.5, 4.2, 4.4 - Handle timeouts with user-friendly messages.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up timeout error
            mock_model = MagicMock()
            mock_model.generate_content.side_effect = asyncio.TimeoutError("Request timed out")
            mock_model_class.return_value = mock_model
            
            # Create bot instance with short timeout for testing
            timeout_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                response_timeout=1,  # Very short timeout
                max_retries=1
            )
            
            bot = DiscordBot(timeout_config)
            
            # Create mention message
            mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Tell me a long story",
                author=self.mock_human_user,
                message_id=1008,
                timestamp=datetime.now()
            )
            mention_message.mentions = [self.mock_bot_user]
            
            # Mock empty channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield  # Make this an async generator that yields nothing
            
            self.mock_channel.history = mock_empty_history
            
            # Process the mention with mocked bot user and timeout categorization
            with patch.object(bot.gemini_client, '_categorize_error', return_value="timeout"), \
                 patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(mention_message)
            
            # Verify timeout error response was sent
            mention_message.reply.assert_called_once()
            response_content = mention_message.reply.call_args[0][0]
            
            # Verify user-friendly timeout message
            self.assertIn("took too long", response_content.lower())
            self.assertIn("try again", response_content.lower())

    async def test_api_rate_limit_error_handling(self):
        """
        Test API rate limiting error handling in the complete flow.
        
        Requirements: 4.2, 4.3, 4.4 - Handle rate limiting with retries and user feedback.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up rate limit error
            mock_model = MagicMock()
            mock_model.generate_content.side_effect = Exception("Rate limit exceeded")
            mock_model_class.return_value = mock_model
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Create mention message
            mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Quick question",
                author=self.mock_human_user,
                message_id=1009,
                timestamp=datetime.now()
            )
            mention_message.mentions = [self.mock_bot_user]
            
            # Mock empty channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield  # Make this an async generator that yields nothing
            
            self.mock_channel.history = mock_empty_history
            
            # Mock the backoff delay to be very short for testing
            with patch.object(bot.gemini_client, '_calculate_backoff_delay', return_value=0.01), \
                 patch.object(bot.gemini_client, '_categorize_error', return_value="rate_limit"), \
                 patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(mention_message)
            
            # Verify rate limit error response was sent
            mention_message.reply.assert_called_once()
            response_content = mention_message.reply.call_args[0][0]
            
            # Verify user-friendly rate limit message
            self.assertIn("high demand", response_content.lower())
            self.assertIn("try again", response_content.lower())

    async def test_context_collection_error_handling(self):
        """
        Test error handling when context collection fails.
        
        Requirements: 4.2, 4.4 - Handle context collection errors gracefully.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Create mention message
            mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Hello there",
                author=self.mock_human_user,
                message_id=1011,
                timestamp=datetime.now()
            )
            mention_message.mentions = [self.mock_bot_user]
            
            # Mock channel history to raise an error
            def mock_failing_history(limit=None, before=None, after=None, oldest_first=False):
                raise Exception("Discord API error")
            
            self.mock_channel.history = mock_failing_history
            
            # Process the mention with mocked bot user
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(mention_message)
            
            # Verify context collection error response was sent
            mention_message.reply.assert_called_once()
            response_content = mention_message.reply.call_args[0][0]
            
            # Verify user-friendly context error message
            self.assertIn("trouble collecting", response_content.lower())
            self.assertIn("try again", response_content.lower())

    async def test_empty_prompt_handling(self):
        """
        Test handling of empty prompts after mention removal.
        
        Requirements: 1.2 - Handle edge cases in mention detection and prompt extraction.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Create mention message with only the mention (no additional content)
            empty_mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}>",  # Only the mention
                author=self.mock_human_user,
                message_id=1013,
                timestamp=datetime.now()
            )
            empty_mention_message.mentions = [self.mock_bot_user]
            
            # Process the mention with mocked bot user
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(empty_mention_message)
            
            # Verify empty prompt error response was sent
            empty_mention_message.reply.assert_called_once()
            response_content = empty_mention_message.reply.call_args[0][0]
            
            # Verify appropriate empty prompt message
            self.assertIn("didn't see a message", response_content.lower())

    async def test_reply_context_enhancement_flow(self):
        """
        Test the complete flow with reply context enhancement.
        
        Requirements: 3.1, 3.2, 3.3 - Enhanced context for reply messages.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up successful AI response
            mock_response = MagicMock()
            mock_response.text = "Based on the conversation, it looks like they're working on a coding project!"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Create the original message being replied to
            original_message = self._create_mock_message(
                content="Pretty good! Working on some code.",
                author=self.mock_human_user,
                message_id=1002,
                timestamp=datetime.now() - timedelta(minutes=8)
            )
            
            # Create reply message with reference
            mock_reference = MagicMock()
            mock_reference.message_id = original_message.id
            
            reply_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Can you tell me more about what they're working on?",
                author=self.mock_human_user,
                message_id=1005,
                timestamp=datetime.now(),
                reference=mock_reference
            )
            reply_message.mentions = [self.mock_bot_user]
            
            # Mock channel history and fetch_message for reply context
            async def mock_history(limit=None, before=None, after=None, oldest_first=False):
                yield original_message
            
            self.mock_channel.history = mock_history
            self.mock_channel.fetch_message = AsyncMock(return_value=original_message)
            
            # Process the reply mention with mocked bot user
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(reply_message)
            
            # Verify response was sent
            reply_message.reply.assert_called_once()
            response_content = reply_message.reply.call_args[0][0]
            
            # Verify AI response content
            self.assertEqual(response_content, "Based on the conversation, it looks like they're working on a coding project!")
            
            # Verify Gemini API was called
            mock_model.generate_content.assert_called_once()

    async def test_discord_response_sending_error_handling(self):
        """
        Test error handling when Discord response sending fails.
        
        Requirements: 1.4, 4.4 - Handle Discord API errors during response delivery.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up successful AI response
            mock_response = MagicMock()
            mock_response.text = "This is a successful AI response"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Create mention message
            mention_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Test message",
                author=self.mock_human_user,
                message_id=1012,
                timestamp=datetime.now()
            )
            mention_message.mentions = [self.mock_bot_user]
            
            # Mock reply to fail, then succeed on fallback
            mention_message.reply = AsyncMock(side_effect=[
                discord.HTTPException(MagicMock(), "Failed to send message"),
                None  # Fallback message succeeds
            ])
            
            # Mock empty channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield  # Make this an async generator that yields nothing
            
            self.mock_channel.history = mock_empty_history
            
            # Process the mention with mocked bot user
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(mention_message)
            
            # Verify both the original attempt and fallback were called
            self.assertEqual(mention_message.reply.call_count, 2)
            
            # Verify fallback message was sent
            fallback_content = mention_message.reply.call_args_list[1][0][0]
            self.assertIn("trouble sending", fallback_content.lower())


if __name__ == '__main__':
    unittest.main()