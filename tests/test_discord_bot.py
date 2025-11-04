"""
Integration tests for Discord bot event handling.

Tests the DiscordBot class event handlers, mention detection, and context
collection integration according to requirements 1.1, 1.2, and 2.1.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import discord

from src.config import BotConfig
from src.models.data_models import MessageContext


class TestDiscordBotMethods(unittest.TestCase):
    """Test cases for DiscordBot methods."""

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
        
        # Create mock Discord objects
        self.mock_user = MagicMock(spec=discord.User)
        self.mock_user.id = 123456789
        self.mock_user.name = "test_bot"
        
        self.mock_author = MagicMock(spec=discord.Member)
        self.mock_author.id = 987654321
        self.mock_author.name = "test_user"
        self.mock_author.display_name = "Test User"
        self.mock_author.bot = False

    def test_mention_detection_methods(self):
        """Test bot mention detection and prompt extraction methods."""
        from src.bot.discord_bot import DiscordBot
        
        # Create a mock bot instance for testing methods
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            
            # Test direct mention detection with mocked user property
            with patch.object(type(bot), 'user', new_callable=lambda: self.mock_user):
                mock_message = MagicMock(spec=discord.Message)
                mock_message.mentions = [self.mock_user]
                mock_message.mention_everyone = False
                mock_message.role_mentions = []
                mock_message.guild = None
                
                self.assertTrue(bot.is_bot_mentioned(mock_message))
                
                # Test @everyone mention detection
                mock_message.mentions = []
                mock_message.mention_everyone = True
                self.assertTrue(bot.is_bot_mentioned(mock_message))
                
                # Test no mention
                mock_message.mentions = []
                mock_message.mention_everyone = False
                self.assertFalse(bot.is_bot_mentioned(mock_message))
                
                # Test prompt extraction
                mock_message.content = f"<@{self.mock_user.id}> Hello bot!"
                result = bot._extract_user_prompt(mock_message)
                self.assertEqual(result, "Hello bot!")
                
                # Test empty prompt after mention removal
                mock_message.content = f"<@{self.mock_user.id}>"
                result = bot._extract_user_prompt(mock_message)
                self.assertEqual(result, "")

    def test_bot_initialization_components(self):
        """Test that bot initializes with correct components."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            
            # Verify components are initialized
            self.assertEqual(bot.config, self.config)
            self.assertIsNotNone(bot.context_collector)
            self.assertIsNotNone(bot.gemini_client)
            
            # Verify context collector configuration
            self.assertEqual(bot.context_collector.max_context_messages, 100)
            self.assertEqual(bot.context_collector.reply_context_range, 10)


class TestDiscordBotAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for DiscordBot event handlers."""

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
        
        # Create mock Discord objects
        self.mock_user = MagicMock(spec=discord.User)
        self.mock_user.id = 123456789
        self.mock_user.name = "test_bot"
        
        self.mock_author = MagicMock(spec=discord.Member)
        self.mock_author.id = 987654321
        self.mock_author.name = "test_user"
        self.mock_author.display_name = "Test User"
        self.mock_author.bot = False
        
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_channel.id = 555666777
        self.mock_channel.name = "test-channel"
        
        self.mock_guild = MagicMock(spec=discord.Guild)
        self.mock_guild.id = 111222333
        self.mock_guild.name = "Test Guild"
        self.mock_guild.member_count = 100

    async def test_message_processing_flow(self):
        """Test the complete message processing flow."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.content = f"<@{self.mock_user.id}> Hello bot!"
            mock_message.channel = self.mock_channel
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            # Mock context collection
            mock_context = [
                MessageContext(
                    content="Previous message",
                    author="other_user",
                    timestamp=datetime.now(),
                    message_id=12345
                )
            ]
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', return_value=mock_context):
                
                await bot.on_message(mock_message)
                
                # Verify reply was called
                mock_message.reply.assert_called_once()
                reply_content = mock_message.reply.call_args[0][0]
                self.assertIn("I found 1 recent messages for context", reply_content)

    async def test_message_processing_with_reply_context(self):
        """Test message processing with reply context enhancement."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock reply message
            mock_reference = MagicMock()
            mock_reference.message_id = 98765
            
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.content = f"<@{self.mock_user.id}> What about this?"
            mock_message.channel = self.mock_channel
            mock_message.reference = mock_reference
            mock_message.reply = AsyncMock()
            
            # Mock context collections
            mock_standard_context = [
                MessageContext(
                    content="Standard message",
                    author="user1",
                    timestamp=datetime.now(),
                    message_id=1
                )
            ]
            
            mock_reply_context = [
                MessageContext(
                    content="Reply context message",
                    author="user2",
                    timestamp=datetime.now(),
                    message_id=2
                )
            ]
            
            mock_combined_context = mock_reply_context + mock_standard_context
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', return_value=mock_standard_context), \
                 patch.object(bot.context_collector, 'get_reply_context', return_value=mock_reply_context), \
                 patch.object(bot.context_collector, '_remove_duplicate_messages', return_value=mock_combined_context):
                
                await bot.on_message(mock_message)
                
                # Verify reply was called with enhanced context message
                mock_message.reply.assert_called_once()
                reply_content = mock_message.reply.call_args[0][0]
                self.assertIn("I found 2 recent messages for context", reply_content)
                self.assertIn("enhanced the context since you replied", reply_content)

    async def test_error_handling_in_message_processing(self):
        """Test error handling during message processing."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.content = f"<@{self.mock_user.id}> Hello bot!"
            mock_message.channel = self.mock_channel
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', side_effect=Exception("Context error")):
                
                await bot.on_message(mock_message)
                
                # Verify error reply was sent
                mock_message.reply.assert_called_once_with(
                    "Sorry, I encountered an error processing your message. Please try again!"
                )

    def test_is_bot_mentioned_everyone_mention(self):
        """Test bot mention detection with @everyone mention."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.mentions = []
        mock_message.mention_everyone = True  # @everyone mention
        mock_message.role_mentions = []
        mock_message.guild = None
        
        result = self.bot.is_bot_mentioned(mock_message)
        self.assertTrue(result)

    def test_is_bot_mentioned_role_mention(self):
        """Test bot mention detection with role mention."""
        mock_role = MagicMock(spec=discord.Role)
        mock_role.id = 444555666
        
        mock_bot_member = MagicMock(spec=discord.Member)
        mock_bot_member.roles = [mock_role]
        
        mock_guild = MagicMock(spec=discord.Guild)
        mock_guild.me = mock_bot_member
        
        mock_message = MagicMock(spec=discord.Message)
        mock_message.mentions = []
        mock_message.mention_everyone = False
        mock_message.role_mentions = [mock_role]  # Role mention
        mock_message.guild = mock_guild
        
        result = self.bot.is_bot_mentioned(mock_message)
        self.assertTrue(result)

    def test_is_bot_mentioned_no_mention(self):
        """Test bot mention detection when bot is not mentioned."""
        other_user = MagicMock(spec=discord.User)
        other_user.id = 999888777
        
        mock_message = MagicMock(spec=discord.Message)
        mock_message.mentions = [other_user]  # Different user mentioned
        mock_message.mention_everyone = False
        mock_message.role_mentions = []
        mock_message.guild = None
        
        result = self.bot.is_bot_mentioned(mock_message)
        self.assertFalse(result)

    def test_extract_user_prompt_removes_bot_mention(self):
        """Test extracting user prompt by removing bot mentions."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = f"<@{self.mock_user.id}> What's the weather like?"
        
        result = self.bot._extract_user_prompt(mock_message)
        self.assertEqual(result, "What's the weather like?")

    def test_extract_user_prompt_removes_nickname_mention(self):
        """Test extracting user prompt by removing nickname mentions."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = f"<@!{self.mock_user.id}> Tell me a joke"
        
        result = self.bot._extract_user_prompt(mock_message)
        self.assertEqual(result, "Tell me a joke")

    def test_extract_user_prompt_removes_everyone_mention(self):
        """Test extracting user prompt by removing @everyone mentions."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = "@everyone Hello world!"
        
        result = self.bot._extract_user_prompt(mock_message)
        self.assertEqual(result, "Hello world!")

    def test_extract_user_prompt_handles_multiple_mentions(self):
        """Test extracting user prompt with multiple mentions."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = f"<@{self.mock_user.id}> @everyone What do you think?"
        
        result = self.bot._extract_user_prompt(mock_message)
        self.assertEqual(result, "What do you think?")

    def test_extract_user_prompt_empty_after_removal(self):
        """Test extracting user prompt when only mentions remain."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = f"<@{self.mock_user.id}>"
        
        result = self.bot._extract_user_prompt(mock_message)
        self.assertEqual(result, "")


class TestDiscordBotAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for DiscordBot class."""

    def setUp(self):
        """Set up test fixtures."""
        from src.bot.discord_bot import DiscordBot
        
        self.config = BotConfig(
            discord_token="test_discord_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_gemini_api_key_1234567890123456789012345678901234567890",
            max_context_messages=100,
            reply_context_range=10,
            response_timeout=30,
            max_retries=3
        )
        
        # Mock Discord intents and client initialization to avoid actual Discord connection
        with patch('discord.Intents.default') as mock_intents, \
             patch('discord.Client.__init__', return_value=None) as mock_client_init, \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            mock_intents.return_value = MagicMock()
            self.bot = DiscordBot(self.config)
        
        # Create mock Discord objects
        self.mock_user = MagicMock(spec=discord.User)
        self.mock_user.id = 123456789
        self.mock_user.name = "test_bot"
        
        self.mock_author = MagicMock(spec=discord.Member)
        self.mock_author.id = 987654321
        self.mock_author.name = "test_user"
        self.mock_author.display_name = "Test User"
        self.mock_author.bot = False
        
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_channel.id = 555666777
        self.mock_channel.name = "test-channel"
        
        self.mock_guild = MagicMock(spec=discord.Guild)
        self.mock_guild.id = 111222333
        self.mock_guild.name = "Test Guild"
        self.mock_guild.member_count = 100
        
        # Set bot user and guilds
        self.bot.user = self.mock_user
        self.bot.guilds = [self.mock_guild]

    async def test_on_ready_logs_startup_info(self):
        """Test that on_ready logs appropriate startup information."""
        with patch('src.bot.discord_bot.logger') as mock_logger:
            await self.bot.on_ready()
            
            # Verify startup logging calls
            mock_logger.info.assert_any_call(f"🤖 {self.mock_user} has connected to Discord!")
            mock_logger.info.assert_any_call(f"Bot ID: {self.mock_user.id}")
            mock_logger.info.assert_any_call("Connected to 1 guild(s):")
            mock_logger.info.assert_any_call(f"  • {self.mock_guild.name} (ID: {self.mock_guild.id}) - 100 members")
            mock_logger.info.assert_any_call("Total users across all guilds: 100")
            mock_logger.info.assert_any_call("✅ Bot is ready and listening for mentions!")

    async def test_on_ready_sets_bot_status(self):
        """Test that on_ready sets appropriate bot status."""
        with patch.object(self.bot, 'change_presence') as mock_change_presence:
            await self.bot.on_ready()
            
            # Verify change_presence was called
            mock_change_presence.assert_called_once()
            call_args = mock_change_presence.call_args[1]
            activity = call_args['activity']
            self.assertEqual(activity.type, discord.ActivityType.listening)
            self.assertEqual(activity.name, "@mentions for AI responses")

    async def test_on_message_ignores_bot_messages(self):
        """Test that on_message ignores messages from bots."""
        bot_author = MagicMock(spec=discord.Member)
        bot_author.bot = True
        
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = bot_author
        
        with patch.object(self.bot, 'is_bot_mentioned') as mock_is_mentioned:
            await self.bot.on_message(mock_message)
            
            # Should not check for mentions since it's from a bot
            mock_is_mentioned.assert_not_called()

    async def test_on_message_ignores_non_mentions(self):
        """Test that on_message ignores messages that don't mention the bot."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        
        with patch.object(self.bot, 'is_bot_mentioned', return_value=False) as mock_is_mentioned:
            await self.bot.on_message(mock_message)
            
            mock_is_mentioned.assert_called_once_with(mock_message)

    async def test_on_message_processes_mentions(self):
        """Test that on_message processes messages that mention the bot."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.content = f"<@{self.mock_user.id}> Hello bot!"
        mock_message.channel = self.mock_channel
        mock_message.reference = None
        mock_message.reply = AsyncMock()
        
        with patch.object(self.bot, 'is_bot_mentioned', return_value=True), \
             patch.object(self.bot, '_process_message_with_context') as mock_process:
            
            await self.bot.on_message(mock_message)
            
            # Should call process method with extracted prompt
            mock_process.assert_called_once_with(mock_message, "Hello bot!")

    async def test_on_message_handles_empty_prompt(self):
        """Test that on_message handles empty prompts gracefully."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.content = f"<@{self.mock_user.id}>"  # Only mention, no content
        mock_message.reply = AsyncMock()
        
        with patch.object(self.bot, 'is_bot_mentioned', return_value=True):
            await self.bot.on_message(mock_message)
            
            # Should reply with error message
            mock_message.reply.assert_called_once_with(
                "I was mentioned but didn't see a message to respond to!"
            )

    async def test_on_message_handles_processing_errors(self):
        """Test that on_message handles processing errors gracefully."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.content = f"<@{self.mock_user.id}> Hello bot!"
        mock_message.reply = AsyncMock()
        
        with patch.object(self.bot, 'is_bot_mentioned', return_value=True), \
             patch.object(self.bot, '_process_message_with_context', side_effect=Exception("Test error")):
            
            await self.bot.on_message(mock_message)
            
            # Should reply with error message
            mock_message.reply.assert_called_once_with(
                "Sorry, I encountered an error processing your message. Please try again!"
            )

    async def test_process_message_with_context_standard_message(self):
        """Test processing a standard (non-reply) message with context collection."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.channel = self.mock_channel
        mock_message.reference = None  # Not a reply
        mock_message.reply = AsyncMock()
        
        # Mock context collection
        mock_context = [
            MessageContext(
                content="Previous message",
                author="other_user",
                timestamp=datetime.now(),
                message_id=12345
            )
        ]
        
        with patch.object(self.bot.context_collector, 'get_channel_context', return_value=mock_context):
            await self.bot._process_message_with_context(mock_message, "Test prompt")
            
            # Should reply with context acknowledgment
            mock_message.reply.assert_called_once()
            reply_content = mock_message.reply.call_args[0][0]
            self.assertIn("I found 1 recent messages for context", reply_content)
            self.assertNotIn("enhanced the context", reply_content)

    async def test_process_message_with_context_reply_message(self):
        """Test processing a reply message with enhanced context collection."""
        mock_reference = MagicMock()
        mock_reference.message_id = 98765
        
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.channel = self.mock_channel
        mock_message.reference = mock_reference  # This is a reply
        mock_message.reply = AsyncMock()
        
        # Mock context collection
        mock_standard_context = [
            MessageContext(
                content="Standard message",
                author="user1",
                timestamp=datetime.now(),
                message_id=1
            )
        ]
        
        mock_reply_context = [
            MessageContext(
                content="Reply context message",
                author="user2",
                timestamp=datetime.now(),
                message_id=2
            )
        ]
        
        mock_combined_context = mock_reply_context + mock_standard_context
        
        with patch.object(self.bot.context_collector, 'get_channel_context', return_value=mock_standard_context), \
             patch.object(self.bot.context_collector, 'get_reply_context', return_value=mock_reply_context), \
             patch.object(self.bot.context_collector, '_remove_duplicate_messages', return_value=mock_combined_context):
            
            await self.bot._process_message_with_context(mock_message, "Test reply prompt")
            
            # Should reply with enhanced context acknowledgment
            mock_message.reply.assert_called_once()
            reply_content = mock_message.reply.call_args[0][0]
            self.assertIn("I found 2 recent messages for context", reply_content)
            self.assertIn("enhanced the context since you replied", reply_content)

    async def test_process_message_with_context_handles_errors(self):
        """Test that context processing handles errors gracefully."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.author = self.mock_author
        mock_message.channel = self.mock_channel
        mock_message.reference = None
        mock_message.reply = AsyncMock()
        
        with patch.object(self.bot.context_collector, 'get_channel_context', side_effect=Exception("Context error")):
            await self.bot._process_message_with_context(mock_message, "Test prompt")
            
            # Should reply with error message
            mock_message.reply.assert_called_once_with(
                "I had trouble collecting conversation context. Please try again!"
            )

    async def test_on_error_logs_discord_errors(self):
        """Test that on_error logs Discord event errors."""
        with patch('src.bot.discord_bot.logger') as mock_logger:
            await self.bot.on_error("on_message", "arg1", "arg2", kwarg1="value1")
            
            mock_logger.error.assert_called_once_with(
                "Discord event error in on_message", 
                exc_info=True
            )

    async def test_on_disconnect_logs_disconnection(self):
        """Test that on_disconnect logs disconnection events."""
        with patch('src.bot.discord_bot.logger') as mock_logger:
            await self.bot.on_disconnect()
            
            mock_logger.warning.assert_called_once_with("🔌 Bot disconnected from Discord")

    async def test_on_resumed_logs_reconnection(self):
        """Test that on_resumed logs reconnection events."""
        with patch('src.bot.discord_bot.logger') as mock_logger:
            await self.bot.on_resumed()
            
            mock_logger.info.assert_called_once_with("🔄 Bot resumed connection to Discord")


if __name__ == '__main__':
    unittest.main()   
 async def test_context_collection_error_handling(self):
        """Test error handling when context collection fails."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            # Mock context collection to fail
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', side_effect=Exception("Context collection failed")), \
                 patch.object(bot.error_manager, 'send_error_response', new_callable=AsyncMock) as mock_send_error:
                
                await bot.on_message(mock_message)
                
                # Verify error response was sent
                mock_send_error.assert_called_once()
                call_args = mock_send_error.call_args
                self.assertEqual(call_args[0][0], mock_message)  # First arg should be the message
                error_context = call_args[0][1]  # Second arg should be error context
                self.assertIn("context", error_context.user_message.lower())

    async def test_gemini_api_error_handling(self):
        """Test error handling when Gemini API fails."""
        from src.bot.discord_bot import DiscordBot
        from src.models.data_models import APIResponse
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            # Mock successful context collection but failed API response
            mock_context = [
                MessageContext(
                    content="Test context",
                    author="user1",
                    timestamp=datetime.now(),
                    message_id=1
                )
            ]
            
            failed_api_response = APIResponse(
                success=False,
                error_type="rate_limit",
                content="Rate limit exceeded"
            )
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', return_value=mock_context), \
                 patch.object(bot.gemini_client, 'generate_response', return_value=failed_api_response), \
                 patch.object(bot.error_manager, 'send_error_response', new_callable=AsyncMock) as mock_send_error:
                
                # Mock typing context manager
                mock_typing = AsyncMock()
                mock_message.channel.typing.return_value.__aenter__ = AsyncMock(return_value=mock_typing)
                mock_message.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
                
                await bot.on_message(mock_message)
                
                # Verify error response was sent
                mock_send_error.assert_called_once()

    async def test_discord_permission_error_handling(self):
        """Test handling of Discord permission errors."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            
            # Mock reply to fail with permission error
            mock_message.reply = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Insufficient permissions"))
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.error_manager, 'send_error_response', new_callable=AsyncMock) as mock_send_error:
                
                await bot.on_message(mock_message)
                
                # Verify error manager was called to handle the Discord error
                mock_send_error.assert_called()

    async def test_response_timeout_handling(self):
        """Test handling of response generation timeouts."""
        from src.bot.discord_bot import DiscordBot
        import asyncio
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            # Mock context collection
            mock_context = [
                MessageContext(
                    content="Test context",
                    author="user1",
                    timestamp=datetime.now(),
                    message_id=1
                )
            ]
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', return_value=mock_context), \
                 patch.object(bot.gemini_client, 'generate_response', side_effect=asyncio.TimeoutError()), \
                 patch.object(bot.error_manager, 'send_error_response', new_callable=AsyncMock) as mock_send_error:
                
                # Mock typing context manager
                mock_typing = AsyncMock()
                mock_message.channel.typing.return_value.__aenter__ = AsyncMock(return_value=mock_typing)
                mock_message.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
                
                await bot.on_message(mock_message)
                
                # Verify timeout error was handled
                mock_send_error.assert_called_once()
                error_context = mock_send_error.call_args[0][1]
                self.assertEqual(error_context.error_type.value, "timeout")

    async def test_performance_logging_integration(self):
        """Test integration of performance logging in message processing."""
        from src.bot.discord_bot import DiscordBot
        from src.models.data_models import APIResponse
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            # Mock successful response
            mock_context = [
                MessageContext(
                    content="Test context",
                    author="user1",
                    timestamp=datetime.now(),
                    message_id=1
                )
            ]
            
            successful_response = APIResponse(
                success=True,
                content="Test AI response"
            )
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', return_value=mock_context), \
                 patch.object(bot.gemini_client, 'generate_response', return_value=successful_response), \
                 patch.object(bot.performance_logger, 'log_message_processing') as mock_perf_log:
                
                # Mock typing context manager
                mock_typing = AsyncMock()
                mock_message.channel.typing.return_value.__aenter__ = AsyncMock(return_value=mock_typing)
                mock_message.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
                
                await bot.on_message(mock_message)
                
                # Verify performance logging was called
                mock_perf_log.assert_called_once()
                call_kwargs = mock_perf_log.call_args[1]
                self.assertIn('duration', call_kwargs)
                self.assertIn('context_messages', call_kwargs)
                self.assertIn('response_length', call_kwargs)
                self.assertEqual(call_kwargs['context_messages'], 1)
                self.assertEqual(call_kwargs['response_length'], len("Test AI response"))

    async def test_error_manager_send_error_response_fallbacks(self):
        """Test ErrorManager fallback mechanisms for sending error responses."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            
            # Mock all Discord methods to fail except reaction
            mock_message.reply = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "Reply failed"))
            mock_message.channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "Send failed"))
            mock_message.add_reaction = AsyncMock()  # This should succeed
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch.object(bot.context_collector, 'get_channel_context', side_effect=Exception("Test error")):
                
                await bot.on_message(mock_message)
                
                # Verify that reaction was added as fallback
                mock_message.add_reaction.assert_called_once_with("❌")

    async def test_structured_logging_context(self):
        """Test that structured logging includes proper context information."""
        from src.bot.discord_bot import DiscordBot
        
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.channel = self.mock_channel
            mock_message.guild = self.mock_guild
            mock_message.id = 999888777
            mock_message.content = f"<@{self.mock_user.id}> Test message"
            mock_message.mentions = [self.mock_user]
            mock_message.mention_everyone = False
            mock_message.role_mentions = []
            mock_message.reference = None
            mock_message.reply = AsyncMock()
            
            with patch.object(bot, 'is_bot_mentioned', return_value=True), \
                 patch('src.utils.logging_config.get_logger_with_context') as mock_get_logger:
                
                mock_logger = MagicMock()
                mock_get_logger.return_value = mock_logger
                
                await bot.on_message(mock_message)
                
                # Verify logger was created with proper context
                mock_get_logger.assert_called_once()
                call_args = mock_get_logger.call_args
                context = call_args[1]
                self.assertEqual(context['user_id'], self.mock_author.id)
                self.assertEqual(context['guild_id'], self.mock_guild.id)
                self.assertEqual(context['channel_id'], self.mock_channel.id)
                self.assertEqual(context['message_id'], mock_message.id)