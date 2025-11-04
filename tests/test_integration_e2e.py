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

    async def test_image_edit_detection_and_processing(self):
        """
        Test complete image editing workflow from detection to response.
        
        Requirements: 1.1, 1.2, 1.3, 1.4 - Image edit detection and processing.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with image processing enabled
            config_with_image = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890",
                max_image_size_mb=10,
                image_processing_timeout=60,
                max_concurrent_image_edits=3
            )
            
            bot = DiscordBot(config_with_image)
            
            # Mock image processing service
            mock_image_service = AsyncMock()
            mock_image_service.process_image_edit = AsyncMock(return_value="job_123")
            mock_image_service.get_job_status = AsyncMock()
            mock_image_service.get_user_rate_limit_info = AsyncMock(return_value={
                'requests_used': 0,
                'requests_remaining': 10,
                'reset_time': None
            })
            
            # Mock successful job completion
            from src.models.data_models import ImageEditResult
            from src.services.image_processing_service import ProcessingJob, ProcessingStatus
            
            mock_job = ProcessingJob(
                job_id="job_123",
                request=MagicMock(),
                status=ProcessingStatus.COMPLETED,
                created_at=datetime.now(),
                progress=1.0
            )
            mock_job.result = ImageEditResult(
                success=True,
                edited_image=b"fake_edited_image_data",
                processing_time=15.5,
                error_message=None,
                metadata={}
            )
            
            mock_image_service.get_job_status.return_value = mock_job
            bot.image_processing_service = mock_image_service
            
            # Create mock image attachment
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.content_type = "image/png"
            mock_attachment.filename = "test_image.png"
            mock_attachment.size = 1024 * 1024  # 1MB
            mock_attachment.read = AsyncMock(return_value=b"fake_image_data")
            
            # Create message with image and edit instruction
            image_edit_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> remove the background from this image",
                author=self.mock_human_user,
                message_id=2001,
                timestamp=datetime.now()
            )
            image_edit_message.mentions = [self.mock_bot_user]
            image_edit_message.attachments = [mock_attachment]
            
            # Mock channel typing context manager
            typing_mock = AsyncMock()
            typing_mock.__aenter__ = AsyncMock(return_value=typing_mock)
            typing_mock.__aexit__ = AsyncMock(return_value=None)
            self.mock_channel.typing.return_value = typing_mock
            
            # Process the image edit request
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop, \
                 patch('src.bot.discord_bot.Image') as mock_pil_image, \
                 patch('io.BytesIO') as mock_bytesio:
                
                mock_user_prop.return_value = self.mock_bot_user
                
                # Mock PIL Image processing
                mock_image_obj = MagicMock()
                mock_image_obj.mode = 'RGB'
                mock_image_obj.save = MagicMock()
                mock_pil_image.open.return_value = mock_image_obj
                
                # Mock BytesIO for image conversion
                mock_bio = MagicMock()
                mock_bio.getvalue.return_value = b"converted_image_data"
                mock_bytesio.return_value = mock_bio
                
                await bot.on_message(image_edit_message)
            
            # Verify image processing was initiated
            mock_image_service.process_image_edit.assert_called_once()
            
            # Verify initial response was sent
            image_edit_message.reply.assert_called()
            
            # Check that the call count is at least 2 (initial response + final response with image)
            self.assertGreaterEqual(image_edit_message.reply.call_count, 2)

    async def test_image_edit_rate_limiting(self):
        """
        Test image editing rate limiting functionality.
        
        Requirements: 6.3 - Rate limiting for image editing requests.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with image processing
            config_with_image = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890"
            )
            
            bot = DiscordBot(config_with_image)
            
            # Mock image processing service with rate limit exceeded
            mock_image_service = AsyncMock()
            mock_image_service.get_user_rate_limit_info = AsyncMock(return_value={
                'requests_used': 10,
                'requests_remaining': 0,
                'reset_time': '2024-01-01T12:00:00'
            })
            
            bot.image_processing_service = mock_image_service
            
            # Create mock image attachment
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.content_type = "image/png"
            mock_attachment.filename = "test_image.png"
            mock_attachment.size = 1024 * 1024
            mock_attachment.read = AsyncMock(return_value=b"fake_image_data")
            
            # Create message with image edit request
            rate_limited_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> edit this image",
                author=self.mock_human_user,
                message_id=2002,
                timestamp=datetime.now()
            )
            rate_limited_message.mentions = [self.mock_bot_user]
            rate_limited_message.attachments = [mock_attachment]
            
            # Process the rate-limited request
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(rate_limited_message)
            
            # Verify rate limit check was called
            mock_image_service.get_user_rate_limit_info.assert_called_once()
            
            # Verify rate limit message was sent
            rate_limited_message.reply.assert_called_once()
            response_content = rate_limited_message.reply.call_args[0][0]
            self.assertIn("limit", response_content.lower())

    async def test_image_edit_error_handling(self):
        """
        Test error handling in image editing workflow.
        
        Requirements: 6.1, 6.2 - Error handling for image processing failures.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with image processing
            config_with_image = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890"
            )
            
            bot = DiscordBot(config_with_image)
            
            # Mock image processing service with failure
            mock_image_service = AsyncMock()
            mock_image_service.get_user_rate_limit_info = AsyncMock(return_value={
                'requests_used': 0,
                'requests_remaining': 10,
                'reset_time': None
            })
            mock_image_service.process_image_edit = AsyncMock(side_effect=ValueError("Invalid image format"))
            
            bot.image_processing_service = mock_image_service
            
            # Create mock image attachment
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.content_type = "image/png"
            mock_attachment.filename = "test_image.png"
            mock_attachment.size = 1024 * 1024
            mock_attachment.read = AsyncMock(return_value=b"fake_image_data")
            
            # Create message with image edit request
            error_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> fix this broken image",
                author=self.mock_human_user,
                message_id=2003,
                timestamp=datetime.now()
            )
            error_message.mentions = [self.mock_bot_user]
            error_message.attachments = [mock_attachment]
            
            # Mock channel typing
            typing_mock = AsyncMock()
            typing_mock.__aenter__ = AsyncMock(return_value=typing_mock)
            typing_mock.__aexit__ = AsyncMock(return_value=None)
            self.mock_channel.typing.return_value = typing_mock
            
            # Process the error-prone request
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop, \
                 patch('src.bot.discord_bot.Image') as mock_pil_image, \
                 patch('io.BytesIO') as mock_bytesio:
                
                mock_user_prop.return_value = self.mock_bot_user
                
                # Mock PIL Image processing
                mock_image_obj = MagicMock()
                mock_image_obj.mode = 'RGB'
                mock_image_obj.save = MagicMock()
                mock_pil_image.open.return_value = mock_image_obj
                
                # Mock BytesIO
                mock_bio = MagicMock()
                mock_bio.getvalue.return_value = b"converted_image_data"
                mock_bytesio.return_value = mock_bio
                
                await bot.on_message(error_message)
            
            # Verify error handling was triggered
            mock_image_service.process_image_edit.assert_called_once()
            
            # Verify error response was sent
            error_message.reply.assert_called()
            # Should have at least initial response
            self.assertGreaterEqual(error_message.reply.call_count, 1)

    async def test_image_edit_without_service_configured(self):
        """
        Test image edit request when service is not configured.
        
        Requirements: 6.4, 6.5 - Graceful degradation when services unavailable.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance without image processing configured
            bot = DiscordBot(self.config)  # No nano_banana_api_key
            
            # Create mock image attachment
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.content_type = "image/png"
            mock_attachment.filename = "test_image.png"
            
            # Create message with image edit request
            no_service_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> edit this image please",
                author=self.mock_human_user,
                message_id=2004,
                timestamp=datetime.now()
            )
            no_service_message.mentions = [self.mock_bot_user]
            no_service_message.attachments = [mock_attachment]
            
            # Process the request without service
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(no_service_message)
            
            # Verify service unavailable message was sent
            no_service_message.reply.assert_called_once()
            response_content = no_service_message.reply.call_args[0][0]
            self.assertIn("not available", response_content.lower())

    async def test_slash_command_image_edit_integration(self):
        """
        Test image editing via slash command integration.
        
        Requirements: 2.2, 2.3, 2.4 - Slash command integration for image editing.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with image processing
            config_with_image = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890"
            )
            
            bot = DiscordBot(config_with_image)
            
            # Mock image processing service
            mock_image_service = AsyncMock()
            mock_image_service.get_user_rate_limit_info = AsyncMock(return_value={
                'requests_used': 0,
                'requests_remaining': 10,
                'reset_time': None
            })
            mock_image_service.process_image_edit = AsyncMock(return_value="job_456")
            
            # Mock successful job completion
            from src.models.data_models import ImageEditResult
            from src.services.image_processing_service import ProcessingJob, ProcessingStatus
            
            mock_job = ProcessingJob(
                job_id="job_456",
                request=MagicMock(),
                status=ProcessingStatus.COMPLETED,
                created_at=datetime.now(),
                progress=1.0
            )
            mock_job.result = ImageEditResult(
                success=True,
                edited_image=b"fake_edited_image_data",
                processing_time=12.3,
                error_message=None,
                metadata={}
            )
            
            mock_image_service.get_job_status.return_value = mock_job
            bot.image_processing_service = mock_image_service
            
            # Create mock interaction for slash command
            mock_interaction = AsyncMock(spec=discord.Interaction)
            mock_interaction.user = self.mock_human_user
            mock_interaction.channel = self.mock_channel
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            
            # Create mock image attachment for slash command
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.content_type = "image/jpeg"
            mock_attachment.filename = "slash_test.jpg"
            mock_attachment.size = 2 * 1024 * 1024  # 2MB
            mock_attachment.read = AsyncMock(return_value=b"fake_slash_image_data")
            
            # Import and test the slash command function
            from src.bot.commands import setup_commands
            
            # Set up commands (this would normally be done in on_ready)
            await setup_commands(bot, config_with_image, bot.gemini_client, bot.performance_logger)
            
            # Find the edit-image command
            edit_command = None
            for command in bot.tree.get_commands():
                if command.name == "edit-image":
                    edit_command = command
                    break
            
            # Verify command exists
            self.assertIsNotNone(edit_command, "edit-image slash command should be registered")
            
            # Test command execution would require more complex mocking
            # For now, verify the command was registered correctly
            self.assertEqual(edit_command.name, "edit-image")
            self.assertIn("edit", edit_command.description.lower())

    async def test_enhanced_command_handler_integration(self):
        """
        Test enhanced command handler with natural language processing.
        
        Requirements: 5.1, 5.2 - Natural language command recognition and contextual help.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with enhanced features
            enhanced_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890",
                show_typing_indicators=True,
                use_rich_embeds=True,
                enable_reaction_feedback=True,
                command_suggestion_threshold=0.7
            )
            
            bot = DiscordBot(enhanced_config)
            
            # Mock enhanced command handler
            mock_enhanced_handler = AsyncMock()
            mock_enhanced_handler.handle_message = AsyncMock(return_value=True)
            bot.enhanced_command_handler = mock_enhanced_handler
            
            # Create help request message
            help_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> how do I use image editing?",
                author=self.mock_human_user,
                message_id=3001,
                timestamp=datetime.now()
            )
            help_message.mentions = [self.mock_bot_user]
            
            # Process the help request
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(help_message)
            
            # Verify enhanced command handler was called
            mock_enhanced_handler.handle_message.assert_called_once_with(help_message)

    async def test_user_experience_service_integration(self):
        """
        Test user experience service integration with typing indicators and embeds.
        
        Requirements: 4.1, 4.2, 4.3 - Enhanced UX features integration.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up successful AI response
            mock_response = MagicMock()
            mock_response.text = "This is a test response with enhanced UX!"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance with UX enhancements
            ux_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                show_typing_indicators=True,
                use_rich_embeds=True,
                enable_reaction_feedback=True
            )
            
            bot = DiscordBot(ux_config)
            bot.gemini_client._model = mock_model
            
            # Mock UX service methods
            mock_ux_service = AsyncMock()
            mock_ux_service.show_typing_indicator = AsyncMock()
            mock_ux_service.add_reaction_feedback = AsyncMock()
            mock_ux_service.create_status_embed = MagicMock()
            bot.user_experience_service = mock_ux_service
            
            # Create test message
            ux_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Tell me something interesting",
                author=self.mock_human_user,
                message_id=3002,
                timestamp=datetime.now()
            )
            ux_message.mentions = [self.mock_bot_user]
            
            # Mock channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield
            
            self.mock_channel.history = mock_empty_history
            
            # Mock typing context
            typing_mock = AsyncMock()
            typing_mock.__aenter__ = AsyncMock(return_value=typing_mock)
            typing_mock.__aexit__ = AsyncMock(return_value=None)
            self.mock_channel.typing.return_value = typing_mock
            
            # Process the message
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(ux_message)
            
            # Verify response was sent
            ux_message.reply.assert_called_once()
            
            # Verify typing indicator was used
            self.mock_channel.typing.assert_called_once()

    async def test_message_splitter_integration(self):
        """
        Test message splitter integration with long responses.
        
        Requirements: 3.1, 3.2, 3.3 - Smart message splitting with markdown preservation.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Create very long response that needs splitting
            long_response = "This is a very long response. " * 200  # Over 2000 characters
            
            mock_response = MagicMock()
            mock_response.text = long_response
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance with message splitting enabled
            split_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                message_split_length=2000,
                preserve_code_blocks=True,
                add_continuation_indicators=True
            )
            
            bot = DiscordBot(split_config)
            bot.gemini_client._model = mock_model
            
            # Create test message
            long_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Give me a very detailed explanation",
                author=self.mock_human_user,
                message_id=3003,
                timestamp=datetime.now()
            )
            long_message.mentions = [self.mock_bot_user]
            
            # Mock channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield
            
            self.mock_channel.history = mock_empty_history
            
            # Process the message
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(long_message)
            
            # Verify multiple replies were sent (message was split)
            self.assertGreater(long_message.reply.call_count, 1)

    async def test_help_system_integration(self):
        """
        Test help system integration with contextual help and command suggestions.
        
        Requirements: 5.2, 5.3, 5.4, 5.5 - Help system functionality.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance with help system
            help_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                command_suggestion_threshold=0.7
            )
            
            bot = DiscordBot(help_config)
            
            # Mock help system
            mock_help_system = AsyncMock()
            mock_help_system.provide_contextual_help = AsyncMock()
            mock_help_system.suggest_similar_commands = AsyncMock()
            bot.help_system = mock_help_system
            
            # Create help request message
            help_request = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> help me with image editing",
                author=self.mock_human_user,
                message_id=3004,
                timestamp=datetime.now()
            )
            help_request.mentions = [self.mock_bot_user]
            
            # Mock enhanced command handler to handle help
            mock_enhanced_handler = AsyncMock()
            mock_enhanced_handler.handle_message = AsyncMock(return_value=True)
            bot.enhanced_command_handler = mock_enhanced_handler
            
            # Process the help request
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(help_request)
            
            # Verify enhanced command handler was called
            mock_enhanced_handler.handle_message.assert_called_once()

    async def test_service_health_monitoring_integration(self):
        """
        Test service health monitoring and graceful degradation.
        
        Requirements: 6.4, 6.5 - Service health monitoring and graceful degradation.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Test service health status method
            health_status = await bot.get_service_health_status()
            
            # Verify core services are reported as available
            self.assertIn('discord_connection', health_status)
            self.assertIn('gemini_client', health_status)
            self.assertIn('context_collector', health_status)
            self.assertIn('message_splitter', health_status)
            self.assertIn('user_experience', health_status)
            self.assertIn('help_system', health_status)
            
            # Verify image processing is reported as not configured
            self.assertIn('image_processing', health_status)
            self.assertIn('Not configured', health_status['image_processing'])

    async def test_configuration_validation_integration(self):
        """
        Test configuration validation for new features.
        
        Requirements: 6.4, 6.5 - Configuration validation for new features.
        """
        # Test valid configuration
        valid_config = BotConfig(
            discord_token="test_discord_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_gemini_api_key_1234567890123456789012345678901234567890",
            nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890",
            max_image_size_mb=10,
            image_processing_timeout=60,
            max_concurrent_image_edits=3,
            message_split_length=2000,
            preserve_code_blocks=True,
            add_continuation_indicators=True,
            show_typing_indicators=True,
            use_rich_embeds=True,
            enable_reaction_feedback=True,
            command_suggestion_threshold=0.7
        )
        
        # Validate configuration
        errors = valid_config.validate()
        self.assertEqual(len(errors), 0, f"Valid configuration should have no errors: {errors}")
        
        # Test feature availability
        features = valid_config.get_feature_availability()
        self.assertTrue(features['image_editing'])
        self.assertTrue(features['enhanced_ux'])
        self.assertTrue(features['message_splitting'])
        self.assertTrue(features['command_suggestions'])
        
        # Test invalid configuration
        invalid_config = BotConfig(
            discord_token="short",  # Too short
            gemini_api_key="short",  # Too short
            max_image_size_mb=0,  # Invalid
            message_split_length=3000,  # Too large
            command_suggestion_threshold=1.5  # Out of range
        )
        
        errors = invalid_config.validate()
        self.assertGreater(len(errors), 0, "Invalid configuration should have errors")

    async def test_error_recovery_integration(self):
        """
        Test error recovery mechanisms with new error types.
        
        Requirements: 6.1, 6.2, 6.3 - Enhanced error handling and recovery.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            # Create bot instance
            bot = DiscordBot(self.config)
            
            # Test image processing error handling
            from src.utils.error_manager import ErrorType
            
            # Test image size error
            image_error = ValueError("Image size exceeds maximum limit")
            error_context = bot.error_manager.handle_image_processing_error(
                image_error,
                context_info="Test image processing",
                image_size=15 * 1024 * 1024,  # 15MB
                image_format="PNG"
            )
            
            self.assertEqual(error_context.error_type, ErrorType.IMAGE_SIZE_ERROR)
            self.assertIn("15.0MB", error_context.user_message)
            self.assertIn("compress", error_context.user_message.lower())
            
            # Test message formatting error
            format_error = Exception("Message splitting failed")
            format_context = bot.error_manager.handle_message_formatting_error(
                format_error,
                message_length=5000,
                context_info="Test message formatting"
            )
            
            self.assertEqual(format_context.error_type, ErrorType.MESSAGE_SPLIT_ERROR)
            self.assertIn("5,000", format_context.user_message)

    async def test_backward_compatibility_integration(self):
        """
        Test backward compatibility with existing functionality.
        
        Requirements: All requirements - Ensure new features don't break existing functionality.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up successful AI response
            mock_response = MagicMock()
            mock_response.text = "This is a backward compatibility test response"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance with minimal configuration (like old version)
            minimal_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key
                # No new features configured
            )
            
            bot = DiscordBot(minimal_config)
            bot.gemini_client._model = mock_model
            
            # Create basic mention message (old-style usage)
            basic_message = self._create_mock_message(
                content=f"<@{self.mock_bot_user.id}> Hello bot",
                author=self.mock_human_user,
                message_id=4001,
                timestamp=datetime.now()
            )
            basic_message.mentions = [self.mock_bot_user]
            
            # Mock channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield
            
            self.mock_channel.history = mock_empty_history
            
            # Process the basic message
            with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                mock_user_prop.return_value = self.mock_bot_user
                await bot.on_message(basic_message)
            
            # Verify basic functionality still works
            basic_message.reply.assert_called_once()
            response_content = basic_message.reply.call_args[0][0]
            self.assertEqual(response_content, "This is a backward compatibility test response")
            
            # Verify new services are properly disabled when not configured
            self.assertIsNone(bot.image_processing_service)
            self.assertIsNone(bot.enhanced_command_handler)
            
            # But core enhanced services should still be available
            self.assertIsNotNone(bot.user_experience_service)
            self.assertIsNotNone(bot.help_system)
            self.assertIsNotNone(bot.message_splitter)

    async def test_performance_under_load_simulation(self):
        """
        Test performance characteristics with new features under simulated load.
        
        Requirements: All requirements - Ensure performance is maintained with new features.
        """
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel') as mock_model_class:
            
            # Set up fast AI response
            mock_response = MagicMock()
            mock_response.text = "Fast response"
            
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model
            
            # Create bot instance with all features enabled
            full_config = BotConfig(
                discord_token=self.config.discord_token,
                gemini_api_key=self.config.gemini_api_key,
                nano_banana_api_key="test_nano_banana_key_1234567890123456789012345678901234567890",
                show_typing_indicators=True,
                use_rich_embeds=True,
                enable_reaction_feedback=True,
                preserve_code_blocks=True,
                add_continuation_indicators=True
            )
            
            bot = DiscordBot(full_config)
            bot.gemini_client._model = mock_model
            
            # Mock all services for performance
            mock_image_service = AsyncMock()
            mock_image_service.get_user_rate_limit_info = AsyncMock(return_value={
                'requests_used': 0,
                'requests_remaining': 10,
                'reset_time': None
            })
            bot.image_processing_service = mock_image_service
            
            # Mock channel history
            async def mock_empty_history(limit=None, before=None, after=None, oldest_first=False):
                return
                yield
            
            self.mock_channel.history = mock_empty_history
            
            # Simulate multiple concurrent requests
            import time
            start_time = time.time()
            
            tasks = []
            for i in range(5):  # Simulate 5 concurrent requests
                message = self._create_mock_message(
                    content=f"<@{self.mock_bot_user.id}> Request {i}",
                    author=self.mock_human_user,
                    message_id=5000 + i,
                    timestamp=datetime.now()
                )
                message.mentions = [self.mock_bot_user]
                
                # Create task for concurrent processing
                with patch.object(type(bot), 'user', new_callable=PropertyMock) as mock_user_prop:
                    mock_user_prop.return_value = self.mock_bot_user
                    task = asyncio.create_task(bot.on_message(message))
                    tasks.append((task, message))
            
            # Wait for all tasks to complete
            for task, message in tasks:
                await task
                # Verify each message got a response
                message.reply.assert_called_once()
            
            end_time = time.time()
            total_time = end_time - start_time
            
            # Performance should be reasonable (less than 5 seconds for 5 concurrent requests)
            self.assertLess(total_time, 5.0, f"Performance test took too long: {total_time:.2f}s")


if __name__ == '__main__':
    unittest.main()