"""
Unit tests for User Experience Service.

Tests typing indicators, rich embeds, and reaction feedback according to
requirements 4.1, 4.2, 4.3.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import discord

from src.config import BotConfig
from src.services.user_experience_service import UserExperienceService, Status, ReactionType


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestUserExperienceService(unittest.TestCase):
    """Test cases for UserExperienceService."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            show_typing_indicators=True,
            use_rich_embeds=True,
            enable_reaction_feedback=True
        )
        
        self.ux_service = UserExperienceService(self.config)
        
        # Mock Discord objects
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_channel.id = 123456789
        self.mock_channel.typing = MagicMock()
        
        self.mock_message = MagicMock(spec=discord.Message)
        self.mock_message.id = 987654321
        self.mock_message.add_reaction = AsyncMock()
        self.mock_message.remove_reaction = AsyncMock()
        
        self.mock_guild = MagicMock(spec=discord.Guild)
        self.mock_guild.me = MagicMock()
        self.mock_message.guild = self.mock_guild

    def test_create_status_embed_success(self):
        """Test creating success status embed."""
        embed = self.ux_service.create_status_embed(
            title="Test Success",
            description="Operation completed successfully",
            status=Status.SUCCESS
        )
        
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("✅", embed.title)
        self.assertEqual(embed.color, discord.Color.green())
        self.assertEqual(embed.description, "Operation completed successfully")

    def test_create_status_embed_error(self):
        """Test creating error status embed."""
        embed = self.ux_service.create_status_embed(
            title="Test Error",
            description="Operation failed",
            status=Status.ERROR
        )
        
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("❌", embed.title)
        self.assertEqual(embed.color, discord.Color.red())

    def test_create_status_embed_with_fields(self):
        """Test creating embed with custom fields."""
        fields = {
            "Field 1": "Value 1",
            "Field 2": {"value": "Value 2", "inline": True},
            "Field 3": {"value": "Value 3", "inline": False}
        }
        
        embed = self.ux_service.create_status_embed(
            title="Test Fields",
            description="Test description",
            status=Status.INFO,
            fields=fields
        )
        
        self.assertEqual(len(embed.fields), 3)
        self.assertEqual(embed.fields[0].name, "Field 1")
        self.assertEqual(embed.fields[0].value, "Value 1")
        self.assertFalse(embed.fields[0].inline)
        
        self.assertEqual(embed.fields[1].name, "Field 2")
        self.assertEqual(embed.fields[1].value, "Value 2")
        self.assertTrue(embed.fields[1].inline)

    def test_create_status_embed_with_footer(self):
        """Test creating embed with footer."""
        embed = self.ux_service.create_status_embed(
            title="Test Footer",
            description="Test description",
            status=Status.INFO,
            footer="Test footer text"
        )
        
        self.assertEqual(embed.footer.text, "Test footer text")

    def test_create_status_embed_disabled_rich_embeds(self):
        """Test embed creation when rich embeds are disabled."""
        # Create config with rich embeds disabled
        config_no_rich = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            use_rich_embeds=False
        )
        
        ux_service = UserExperienceService(config_no_rich)
        
        embed = ux_service.create_status_embed(
            title="Test Title",
            description="Test description",
            status=Status.SUCCESS
        )
        
        # Should create simple embed without status emoji
        self.assertEqual(embed.title, "Test Title")
        self.assertEqual(embed.color, discord.Color.default())

    def test_show_typing_indicator_enabled(self):
        """Test showing typing indicator when enabled."""
        # Mock typing context
        mock_typing_context = AsyncMock()
        mock_typing_context.__aenter__ = AsyncMock()
        mock_typing_context.__aexit__ = AsyncMock()
        self.mock_channel.typing.return_value = mock_typing_context
        
        # Test showing typing indicator
        run_async_test(self.ux_service.show_typing_indicator(self.mock_channel, duration=5))
        
        # Verify typing was started
        self.mock_channel.typing.assert_called_once()
        mock_typing_context.__aenter__.assert_called_once()

    def test_show_typing_indicator_disabled(self):
        """Test typing indicator when disabled in config."""
        # Create config with typing indicators disabled
        config_no_typing = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            show_typing_indicators=False
        )
        
        ux_service = UserExperienceService(config_no_typing)
        
        # Test showing typing indicator (should do nothing)
        run_async_test(ux_service.show_typing_indicator(self.mock_channel))
        
        # Verify typing was not started
        self.mock_channel.typing.assert_not_called()

    def test_stop_typing_indicator(self):
        """Test stopping typing indicator."""
        # First start typing
        mock_typing_context = AsyncMock()
        mock_typing_context.__aenter__ = AsyncMock()
        mock_typing_context.__aexit__ = AsyncMock()
        self.mock_channel.typing.return_value = mock_typing_context
        
        run_async_test(self.ux_service.show_typing_indicator(self.mock_channel))
        
        # Then stop typing
        run_async_test(self.ux_service.stop_typing_indicator(self.mock_channel))
        
        # Verify typing was stopped
        mock_typing_context.__aexit__.assert_called()

    def test_add_reaction_feedback_enabled(self):
        """Test adding reaction feedback when enabled."""
        # Test adding success reaction
        run_async_test(self.ux_service.add_reaction_feedback(
            self.mock_message, 
            ReactionType.SUCCESS
        ))
        
        # Verify reaction was added
        self.mock_message.add_reaction.assert_called_once_with("✅")

    def test_add_reaction_feedback_disabled(self):
        """Test reaction feedback when disabled in config."""
        # Create config with reaction feedback disabled
        config_no_reactions = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            enable_reaction_feedback=False
        )
        
        ux_service = UserExperienceService(config_no_reactions)
        
        # Test adding reaction (should do nothing)
        run_async_test(ux_service.add_reaction_feedback(
            self.mock_message, 
            ReactionType.SUCCESS
        ))
        
        # Verify no reaction was added
        self.mock_message.add_reaction.assert_not_called()

    def test_add_reaction_feedback_with_removal(self):
        """Test adding reaction with automatic removal."""
        # Test adding reaction with removal after 1 second
        run_async_test(self.ux_service.add_reaction_feedback(
            self.mock_message, 
            ReactionType.PROCESSING,
            remove_after=1
        ))
        
        # Verify reaction was added
        self.mock_message.add_reaction.assert_called_once_with("⏳")
        
        # Note: Testing automatic removal would require more complex async mocking

    def test_send_progress_update_new_message(self):
        """Test sending new progress update message."""
        self.mock_channel.send = AsyncMock()
        
        # Test sending progress update
        result = run_async_test(self.ux_service.send_progress_update(
            self.mock_channel,
            progress=50,
            total=100,
            operation="Processing"
        ))
        
        # Verify message was sent
        self.mock_channel.send.assert_called_once()
        
        # Verify embed was created with progress bar
        call_args = self.mock_channel.send.call_args
        embed = call_args[1]['embed']
        self.assertIn("Progress", embed.title)
        self.assertIn("50/100", embed.description)

    def test_send_progress_update_edit_existing(self):
        """Test editing existing progress message."""
        mock_existing_message = MagicMock(spec=discord.Message)
        mock_existing_message.edit = AsyncMock()
        
        # Test editing progress update
        result = run_async_test(self.ux_service.send_progress_update(
            self.mock_channel,
            progress=75,
            total=100,
            operation="Processing",
            message_to_edit=mock_existing_message
        ))
        
        # Verify message was edited
        mock_existing_message.edit.assert_called_once()
        
        # Verify correct message was returned
        self.assertEqual(result, mock_existing_message)

    def test_send_processing_notification(self):
        """Test sending processing notification."""
        self.mock_channel.send = AsyncMock()
        mock_message = MagicMock(spec=discord.Message)
        self.mock_channel.send.return_value = mock_message
        
        # Mock the add_reaction_feedback method
        with patch.object(self.ux_service, 'add_reaction_feedback', new_callable=AsyncMock) as mock_add_reaction:
            # Test sending notification
            result = run_async_test(self.ux_service.send_processing_notification(
                self.mock_channel,
                operation="Image Processing",
                estimated_time=30
            ))
            
            # Verify message was sent
            self.mock_channel.send.assert_called_once()
            
            # Verify reaction was added
            mock_add_reaction.assert_called_once_with(mock_message, ReactionType.PROCESSING)

    def test_send_completion_notification_success(self):
        """Test sending successful completion notification."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.edit = AsyncMock()
        
        # Mock the add_reaction_feedback method
        with patch.object(self.ux_service, 'add_reaction_feedback', new_callable=AsyncMock) as mock_add_reaction:
            # Test completion notification
            run_async_test(self.ux_service.send_completion_notification(
                mock_message,
                operation="Image Processing",
                success=True,
                details="Image edited successfully",
                processing_time=2.5
            ))
            
            # Verify message was edited
            mock_message.edit.assert_called_once()
            
            # Verify success reaction was added
            mock_add_reaction.assert_called_once_with(mock_message, ReactionType.COMPLETED)

    def test_send_completion_notification_failure(self):
        """Test sending failed completion notification."""
        mock_message = MagicMock(spec=discord.Message)
        mock_message.edit = AsyncMock()
        
        # Mock the add_reaction_feedback method
        with patch.object(self.ux_service, 'add_reaction_feedback', new_callable=AsyncMock) as mock_add_reaction:
            # Test completion notification
            run_async_test(self.ux_service.send_completion_notification(
                mock_message,
                operation="Image Processing",
                success=False,
                details="Processing failed due to invalid format"
            ))
            
            # Verify message was edited
            mock_message.edit.assert_called_once()
            
            # Verify error reaction was added
            mock_add_reaction.assert_called_once_with(mock_message, ReactionType.ERROR)

    def test_create_help_embed(self):
        """Test creating standardized help embed."""
        sections = {
            "Getting Started": "How to use the bot",
            "Commands": "Available commands",
            "Features": "Bot capabilities"
        }
        
        embed = self.ux_service.create_help_embed(
            title="Bot Help",
            sections=sections
        )
        
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("ℹ️", embed.title)
        self.assertEqual(len(embed.fields), 3)
        self.assertEqual(embed.footer.text, "Use /help for more detailed information")

    def test_cleanup_typing_indicators(self):
        """Test cleanup of typing indicators."""
        # Add some mock typing tasks
        mock_task = AsyncMock()
        mock_task.cancel = MagicMock()
        
        mock_context = AsyncMock()
        mock_context.__aexit__ = AsyncMock()
        
        self.ux_service.active_typing_tasks = {
            123: mock_task,
            456: mock_context
        }
        
        # Test cleanup
        run_async_test(self.ux_service.cleanup_typing_indicators())
        
        # Verify cleanup was performed
        mock_task.cancel.assert_called_once()
        mock_context.__aexit__.assert_called_once()
        self.assertEqual(len(self.ux_service.active_typing_tasks), 0)

    def test_error_handling_forbidden_reaction(self):
        """Test error handling when adding reactions is forbidden."""
        # Setup mock to raise Forbidden error
        self.mock_message.add_reaction = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Forbidden"))
        
        # Test adding reaction (should not raise exception)
        run_async_test(self.ux_service.add_reaction_feedback(
            self.mock_message, 
            ReactionType.SUCCESS
        ))
        
        # Verify the method completed without raising exception
        self.mock_message.add_reaction.assert_called_once()

    def test_error_handling_forbidden_typing(self):
        """Test error handling when typing indicator is forbidden."""
        # Setup mock to raise Forbidden error
        self.mock_channel.typing = MagicMock(side_effect=discord.Forbidden(MagicMock(), "Forbidden"))
        
        # Test showing typing (should not raise exception)
        run_async_test(self.ux_service.show_typing_indicator(self.mock_channel))
        
        # Verify the method completed without raising exception
        self.mock_channel.typing.assert_called_once()


if __name__ == '__main__':
    unittest.main()