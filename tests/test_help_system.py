"""
Unit tests for Help System.

Tests contextual help, command suggestions, and feature discovery according to
requirements 5.2, 5.3, 5.4, 5.5.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.config import BotConfig
from src.services.help_system import HelpSystem, HelpCategory, CommandSuggestion
from src.services.user_experience_service import UserExperienceService


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestHelpSystem(unittest.TestCase):
    """Test cases for HelpSystem."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            command_suggestion_threshold=0.7
        )
        
        # Mock UX service
        self.mock_ux_service = MagicMock(spec=UserExperienceService)
        self.mock_ux_service.create_status_embed = MagicMock(return_value=MagicMock(spec=discord.Embed))
        
        self.help_system = HelpSystem(self.config, self.mock_ux_service)
        
        # Mock Discord objects
        self.mock_author = MagicMock(spec=discord.Member)
        self.mock_author.id = 123456789
        
        self.mock_message = MagicMock(spec=discord.Message)
        self.mock_message.author = self.mock_author
        self.mock_message.content = ""

    def test_find_relevant_help_sections_image_editing(self):
        """Test finding help sections relevant to image editing."""
        query = "how do I edit images"
        
        sections = self.help_system._find_relevant_help_sections(query)
        
        # Should find image editing section
        self.assertGreater(len(sections), 0)
        
        # Check that image editing section is included
        image_section_found = any(
            section.category == HelpCategory.IMAGE_EDITING 
            for section in sections
        )
        self.assertTrue(image_section_found)

    def test_find_relevant_help_sections_commands(self):
        """Test finding help sections relevant to commands."""
        query = "what slash commands are available"
        
        sections = self.help_system._find_relevant_help_sections(query)
        
        # Should find commands section
        self.assertGreater(len(sections), 0)
        
        # Check that commands section is included
        commands_section_found = any(
            section.category == HelpCategory.COMMANDS 
            for section in sections
        )
        self.assertTrue(commands_section_found)

    def test_find_relevant_help_sections_general(self):
        """Test finding help sections for general queries."""
        query = "how do I use this bot"
        
        sections = self.help_system._find_relevant_help_sections(query)
        
        # Should find general section
        self.assertGreater(len(sections), 0)
        
        # Check that general section is included
        general_section_found = any(
            section.category == HelpCategory.GENERAL 
            for section in sections
        )
        self.assertTrue(general_section_found)

    def test_find_relevant_help_sections_no_match(self):
        """Test finding help sections when no specific match."""
        query = "random unrelated query xyz"
        
        sections = self.help_system._find_relevant_help_sections(query)
        
        # Should return empty list for unrelated queries
        self.assertEqual(len(sections), 0)

    def test_generate_command_suggestions_typo(self):
        """Test generating suggestions for common typos."""
        suggestions = self.help_system._generate_command_suggestions("hlep")
        
        # Should suggest "help"
        self.assertGreater(len(suggestions), 0)
        self.assertEqual(suggestions[0].suggestion, "help")
        self.assertGreater(suggestions[0].confidence, 0.9)

    def test_generate_command_suggestions_partial_match(self):
        """Test generating suggestions for partial matches."""
        suggestions = self.help_system._generate_command_suggestions("edit")
        
        # Should suggest image editing commands
        self.assertGreater(len(suggestions), 0)
        
        # Check that at least one suggestion contains "edit"
        edit_suggestion_found = any(
            "edit" in suggestion.suggestion.lower() 
            for suggestion in suggestions
        )
        self.assertTrue(edit_suggestion_found)

    def test_generate_command_suggestions_similar_spelling(self):
        """Test generating suggestions for similar spelling."""
        suggestions = self.help_system._generate_command_suggestions("pign")
        
        # Should suggest "ping"
        ping_suggestions = [s for s in suggestions if s.suggestion == "/ping"]
        self.assertGreater(len(ping_suggestions), 0)

    def test_generate_command_suggestions_low_confidence(self):
        """Test that low confidence suggestions are filtered out."""
        suggestions = self.help_system._generate_command_suggestions("completely_unrelated_xyz")
        
        # Should return empty list for completely unrelated input
        self.assertEqual(len(suggestions), 0)

    def test_generate_command_suggestions_threshold(self):
        """Test that suggestions respect confidence threshold."""
        # Test with high threshold
        high_threshold_config = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_key_1234567890123456789012345678901234567890",
            command_suggestion_threshold=0.9
        )
        
        high_threshold_system = HelpSystem(high_threshold_config, self.mock_ux_service)
        
        # Should have fewer suggestions with high threshold
        suggestions = high_threshold_system._generate_command_suggestions("stat")
        
        # All suggestions should meet the high threshold
        for suggestion in suggestions:
            self.assertGreaterEqual(suggestion.confidence, 0.9)

    def test_get_suggestion_reason(self):
        """Test getting appropriate reason for suggestions."""
        # Test partial match reason
        reason = self.help_system._get_suggestion_reason(0.3, 0.8, 0.2)
        self.assertEqual(reason, "Contains similar text")
        
        # Test word match reason
        reason = self.help_system._get_suggestion_reason(0.3, 0.2, 0.8)
        self.assertEqual(reason, "Similar keywords")
        
        # Test sequence match reason
        reason = self.help_system._get_suggestion_reason(0.8, 0.2, 0.2)
        self.assertEqual(reason, "Similar spelling")
        
        # Test default reason
        reason = self.help_system._get_suggestion_reason(0.3, 0.2, 0.2)
        self.assertEqual(reason, "Possible match")

    def test_provide_contextual_help_with_query(self):
        """Test providing contextual help with specific query."""
        self.mock_message.content = "help with image editing"
        
        # Test contextual help
        result = run_async_test(self.help_system.provide_contextual_help(
            self.mock_message, 
            query="image editing"
        ))
        
        # Verify embed was created
        self.mock_ux_service.create_status_embed.assert_called_once()
        
        # Verify it returns an embed
        self.assertIsInstance(result, MagicMock)

    def test_provide_contextual_help_no_query(self):
        """Test providing contextual help without specific query."""
        self.mock_message.content = "I need help with commands"
        
        # Test contextual help
        result = run_async_test(self.help_system.provide_contextual_help(self.mock_message))
        
        # Verify embed was created
        self.mock_ux_service.create_status_embed.assert_called_once()

    def test_provide_contextual_help_no_relevant_sections(self):
        """Test contextual help when no relevant sections found."""
        self.mock_message.content = "completely unrelated query xyz"
        
        # Test contextual help
        result = run_async_test(self.help_system.provide_contextual_help(self.mock_message))
        
        # Should create general help embed
        self.mock_ux_service.create_status_embed.assert_called()

    def test_suggest_similar_commands_with_suggestions(self):
        """Test suggesting similar commands when suggestions exist."""
        self.mock_message.content = "hlep"
        
        # Test command suggestions
        result = run_async_test(self.help_system.suggest_similar_commands(
            self.mock_message, 
            "hlep"
        ))
        
        # Should return an embed
        self.assertIsNotNone(result)
        self.mock_ux_service.create_status_embed.assert_called()

    def test_suggest_similar_commands_no_suggestions(self):
        """Test suggesting commands when no suggestions available."""
        self.mock_message.content = "xyz_completely_unrelated"
        
        # Test command suggestions
        result = run_async_test(self.help_system.suggest_similar_commands(
            self.mock_message, 
            "xyz_completely_unrelated"
        ))
        
        # Should return None when no suggestions
        self.assertIsNone(result)

    def test_provide_feature_discovery(self):
        """Test providing feature discovery information."""
        # Test feature discovery
        result = run_async_test(self.help_system.provide_feature_discovery(self.mock_message))
        
        # Verify embed was created
        self.mock_ux_service.create_status_embed.assert_called_once()
        
        # Verify it returns an embed
        self.assertIsInstance(result, MagicMock)

    def test_get_operation_alternatives_video(self):
        """Test getting alternatives for video operations."""
        alternatives = self.help_system._get_operation_alternatives("video editing")
        
        # Should provide video-related alternatives
        self.assertGreater(len(alternatives), 0)
        self.assertTrue(any("frames" in alt.lower() for alt in alternatives))

    def test_get_operation_alternatives_audio(self):
        """Test getting alternatives for audio operations."""
        alternatives = self.help_system._get_operation_alternatives("audio processing")
        
        # Should provide audio-related alternatives
        self.assertGreater(len(alternatives), 0)
        self.assertTrue(any("transcript" in alt.lower() for alt in alternatives))

    def test_get_operation_alternatives_default(self):
        """Test getting default alternatives for unknown operations."""
        alternatives = self.help_system._get_operation_alternatives("unknown_operation")
        
        # Should provide default alternatives
        self.assertGreater(len(alternatives), 0)
        self.assertTrue(any("rephras" in alt.lower() for alt in alternatives))

    def test_handle_unsupported_operation(self):
        """Test handling unsupported operations."""
        # Test unsupported operation handling
        result = run_async_test(self.help_system.handle_unsupported_operation(
            self.mock_message, 
            "video editing"
        ))
        
        # Verify embed was created
        self.mock_ux_service.create_status_embed.assert_called_once()
        
        # Verify it returns an embed
        self.assertIsInstance(result, MagicMock)

    def test_create_general_help_embed(self):
        """Test creating general help embed."""
        embed = self.help_system._create_general_help_embed()
        
        # Verify embed creation was called
        self.mock_ux_service.create_status_embed.assert_called()

    def test_create_error_help_embed(self):
        """Test creating error help embed."""
        embed = self.help_system._create_error_help_embed()
        
        # Verify embed creation was called
        self.mock_ux_service.create_status_embed.assert_called()

    def test_help_sections_initialization(self):
        """Test that help sections are properly initialized."""
        # Verify help sections were created
        self.assertGreater(len(self.help_system.help_sections), 0)
        
        # Verify all categories are represented
        categories = {section.category for section in self.help_system.help_sections}
        expected_categories = {
            HelpCategory.GENERAL,
            HelpCategory.IMAGE_EDITING,
            HelpCategory.COMMANDS,
            HelpCategory.FEATURES,
            HelpCategory.TROUBLESHOOTING
        }
        self.assertEqual(categories, expected_categories)

    def test_command_database_initialization(self):
        """Test that command database is properly initialized."""
        # Verify known commands were initialized
        self.assertGreater(len(self.help_system.known_commands), 0)
        
        # Verify command variations were initialized
        self.assertGreater(len(self.help_system.command_variations), 0)
        
        # Test some expected commands
        self.assertIn("/help", self.help_system.known_commands)
        self.assertIn("/ping", self.help_system.known_commands)
        self.assertIn("edit image", self.help_system.known_commands)

    def test_error_handling_in_contextual_help(self):
        """Test error handling in contextual help."""
        # Mock an exception in the help system
        with patch.object(self.help_system, '_find_relevant_help_sections', side_effect=Exception("Test error")):
            result = run_async_test(self.help_system.provide_contextual_help(self.mock_message))
            
            # Should return error help embed
            self.assertIsInstance(result, MagicMock)

    def test_error_handling_in_command_suggestions(self):
        """Test error handling in command suggestions."""
        # Mock an exception in suggestion generation
        with patch.object(self.help_system, '_generate_command_suggestions', side_effect=Exception("Test error")):
            result = run_async_test(self.help_system.suggest_similar_commands(
                self.mock_message, 
                "test_command"
            ))
            
            # Should return None on error
            self.assertIsNone(result)

    def test_error_handling_in_feature_discovery(self):
        """Test error handling in feature discovery."""
        # Mock an exception in the UX service
        self.mock_ux_service.create_status_embed.side_effect = Exception("Test error")
        
        result = run_async_test(self.help_system.provide_feature_discovery(self.mock_message))
        
        # Should handle error gracefully
        self.assertIsInstance(result, MagicMock)


if __name__ == '__main__':
    unittest.main()