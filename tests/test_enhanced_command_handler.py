"""
Unit tests for Enhanced Command Handler.

Tests command parsing, help system, and user feedback according to
requirements 5.1, 5.2, 5.3.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import io

import discord

from src.bot.enhanced_command_handler import EnhancedCommandHandler, CommandIntent
from src.models.data_models import ImageEditRequest, ImageEditResult, EditType
from src.services.image_processing_service import ImageProcessingService
from src.utils.error_manager import ErrorManager


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestEnhancedCommandHandler(unittest.TestCase):
    """Test cases for EnhancedCommandHandler."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock services
        self.mock_image_service = MagicMock(spec=ImageProcessingService)
        self.mock_error_manager = MagicMock(spec=ErrorManager)
        self.mock_gemini_client = MagicMock()
        
        # Create handler instance
        self.handler = EnhancedCommandHandler(
            image_processing_service=self.mock_image_service,
            error_manager=self.mock_error_manager,
            gemini_client=self.mock_gemini_client
        )
        
        # Mock Discord objects
        self.mock_user = MagicMock(spec=discord.User)
        self.mock_user.id = 123456789
        
        self.mock_author = MagicMock(spec=discord.Member)
        self.mock_author.id = 987654321
        self.mock_author.bot = False
        
        self.mock_channel = MagicMock(spec=discord.TextChannel)
        self.mock_channel.id = 555666777
        # Create a proper async context manager mock
        typing_context = AsyncMock()
        typing_context.__aenter__ = AsyncMock()
        typing_context.__aexit__ = AsyncMock()
        self.mock_channel.typing = MagicMock(return_value=typing_context)
        
        self.mock_message = MagicMock(spec=discord.Message)
        self.mock_message.author = self.mock_author
        self.mock_message.channel = self.mock_channel
        self.mock_message.created_at = datetime.now()
        self.mock_message.reply = AsyncMock()
        self.mock_message.attachments = []

    def test_parse_natural_language_command_image_edit(self):
        """Test parsing natural language for image edit commands."""
        # Test cases for image editing
        test_cases = [
            ("remove the background from this image", CommandIntent.IMAGE_EDIT),
            ("can you edit this photo", CommandIntent.IMAGE_EDIT),
            ("make this look artistic", CommandIntent.IMAGE_EDIT),
            ("brighten this picture", CommandIntent.IMAGE_EDIT),
            ("change the background to a beach", CommandIntent.IMAGE_EDIT),
            ("delete the person in red", CommandIntent.IMAGE_EDIT),
        ]
        
        async def test_case(message_content, expected_intent):
            # Mock the image generation check to return False (not image generation)
            with patch.object(self.handler, '_check_image_generation_intent', return_value=False):
                intent, confidence = await self.handler.parse_natural_language_command(message_content)
                self.assertEqual(intent, expected_intent)
                self.assertGreater(confidence, 0.0)
        
        for message_content, expected_intent in test_cases:
            with self.subTest(message=message_content):
                run_async_test(test_case(message_content, expected_intent))

    def test_parse_natural_language_command_help(self):
        """Test parsing natural language for help commands."""
        test_cases = [
            ("help me with this", CommandIntent.HELP),
            ("how do I use this bot", CommandIntent.HELP),
            ("what commands are available", CommandIntent.HELP),
            ("show me the tutorial", CommandIntent.HELP),
            ("usage guide please", CommandIntent.HELP),
        ]
        
        async def test_case(message_content, expected_intent):
            # Mock the image generation check to return False
            with patch.object(self.handler, '_check_image_generation_intent', return_value=False):
                intent, confidence = await self.handler.parse_natural_language_command(message_content)
                self.assertEqual(intent, expected_intent)
                self.assertGreater(confidence, 0.0)
        
        for message_content, expected_intent in test_cases:
            with self.subTest(message=message_content):
                run_async_test(test_case(message_content, expected_intent))

    def test_parse_natural_language_command_unknown(self):
        """Test parsing natural language for unknown commands."""
        test_cases = [
            "just a regular message",
            "hello there",
            "what's the weather like",
            "random conversation",
        ]
        
        async def test_case(message_content):
            # Mock the image generation check to return False
            with patch.object(self.handler, '_check_image_generation_intent', return_value=False):
                intent, confidence = await self.handler.parse_natural_language_command(message_content)
                self.assertEqual(intent, CommandIntent.UNKNOWN)
                self.assertEqual(confidence, 0.0)
        
        for message_content in test_cases:
            with self.subTest(message=message_content):
                run_async_test(test_case(message_content))

    def test_parse_natural_language_command_image_generation(self):
        """Test parsing natural language for image generation commands."""
        test_cases = [
            "generate an image of a cat",
            "create a picture of a sunset",
            "make me an image of a robot",
            "draw a picture of mountains",
        ]
        
        async def test_case(message_content):
            # Mock the image generation check to return True
            with patch.object(self.handler, '_check_image_generation_intent', return_value=True):
                intent, confidence = await self.handler.parse_natural_language_command(message_content)
                self.assertEqual(intent, CommandIntent.IMAGE_GENERATE)
                self.assertGreater(confidence, 0.0)
        
        for message_content in test_cases:
            with self.subTest(message=message_content):
                run_async_test(test_case(message_content))

    def test_detect_edit_type_object_removal(self):
        """Test detection of object removal edit type."""
        test_cases = [
            "remove the person",
            "delete the car",
            "erase the background",
            "take out the building",
            "get rid of the text",
        ]
        
        for instruction in test_cases:
            with self.subTest(instruction=instruction):
                edit_type = self.handler.detect_edit_type(instruction)
                self.assertEqual(edit_type, EditType.OBJECT_REMOVAL)

    def test_detect_edit_type_background_replacement(self):
        """Test detection of background replacement edit type."""
        test_cases = [
            "change the background",
            "replace background with beach",
            "new background please",
            "different backdrop",
            "change the scene",
        ]
        
        for instruction in test_cases:
            with self.subTest(instruction=instruction):
                edit_type = self.handler.detect_edit_type(instruction)
                self.assertEqual(edit_type, EditType.BACKGROUND_REPLACEMENT)

    def test_detect_edit_type_style_transfer(self):
        """Test detection of style transfer edit type."""
        test_cases = [
            "make it look like a painting",
            "artistic style please",
            "turn into a sketch",
            "cartoon style",
            "make it look like Van Gogh",
        ]
        
        for instruction in test_cases:
            with self.subTest(instruction=instruction):
                edit_type = self.handler.detect_edit_type(instruction)
                self.assertEqual(edit_type, EditType.STYLE_TRANSFER)

    def test_detect_edit_type_color_adjustment(self):
        """Test detection of color adjustment edit type."""
        test_cases = [
            "make it brighter",
            "increase the contrast",
            "more colorful please",
            "adjust the saturation",
            "darker image",
        ]
        
        for instruction in test_cases:
            with self.subTest(instruction=instruction):
                edit_type = self.handler.detect_edit_type(instruction)
                self.assertEqual(edit_type, EditType.COLOR_ADJUSTMENT)

    def test_detect_edit_type_general(self):
        """Test detection of general edit type as fallback."""
        test_cases = [
            "improve this image",
            "make it better",
            "fix the photo",
            "enhance please",
        ]
        
        for instruction in test_cases:
            with self.subTest(instruction=instruction):
                edit_type = self.handler.detect_edit_type(instruction)
                self.assertEqual(edit_type, EditType.GENERAL_EDIT)

    def test_extract_edit_instruction(self):
        """Test extraction of edit instructions from messages."""
        test_cases = [
            ("<@123456789> remove the background", "remove the background"),
            ("<@!123456789> make it artistic   ", "make it artistic"),
            ("@everyone brighten this image", "brighten this image"),
            ("  <@123456789>   change background   ", "change background"),
            ("multiple   spaces   between   words", "multiple spaces between words"),
        ]
        
        for message_content, expected_instruction in test_cases:
            with self.subTest(message=message_content):
                instruction = self.handler._extract_edit_instruction(message_content)
                self.assertEqual(instruction, expected_instruction)

    def test_handle_message_with_image_edit_intent(self):
        """Test handling message with image edit intent and attachments."""
        # Setup message with image attachment
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.content_type = "image/png"
        mock_attachment.filename = "test.png"
        mock_attachment.read = AsyncMock(return_value=b"fake_image_data")
        
        self.mock_message.content = "remove the background"
        self.mock_message.attachments = [mock_attachment]
        
        # Setup successful image processing result
        mock_result = ImageEditResult(
            success=True,
            edited_image=io.BytesIO(b"edited_image_data"),
            processing_time=2.5,
            error_message=None,
            metadata={}
        )
        self.mock_image_service.process_image_edit = AsyncMock(return_value=mock_result)
        
        # Test the handler
        result = run_async_test(self.handler.handle_message(self.mock_message))
        
        # Verify it was handled as a command
        self.assertTrue(result)
        
        # Verify image service was called
        self.mock_image_service.process_image_edit.assert_called_once()

    def test_handle_message_with_help_intent(self):
        """Test handling message with help intent."""
        self.mock_message.content = "help me with this bot"
        
        # Test the handler
        result = run_async_test(self.handler.handle_message(self.mock_message))
        
        # Verify it was handled as a command
        self.assertTrue(result)
        
        # Verify reply was sent
        self.mock_message.reply.assert_called_once()

    def test_handle_message_image_edit_without_attachment(self):
        """Test handling image edit intent without image attachment."""
        self.mock_message.content = "edit this image"
        self.mock_message.attachments = []  # No attachments
        
        # Test the handler
        result = run_async_test(self.handler.handle_message(self.mock_message))
        
        # Verify it was handled (should suggest image upload)
        self.assertTrue(result)
        
        # Verify reply was sent
        self.mock_message.reply.assert_called_once()

    def test_handle_message_unknown_intent(self):
        """Test handling message with unknown intent."""
        self.mock_message.content = "just a regular conversation"
        
        # Test the handler
        result = run_async_test(self.handler.handle_message(self.mock_message))
        
        # Verify it was not handled as a command
        self.assertFalse(result)

    def test_handle_image_edit_command_success(self):
        """Test successful image edit command handling."""
        # Setup message and attachment
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.content_type = "image/jpeg"
        mock_attachment.filename = "photo.jpg"
        mock_attachment.read = AsyncMock(return_value=b"image_data")
        
        self.mock_message.content = "brighten this image"
        self.mock_message.attachments = [mock_attachment]
        
        # Setup successful result
        mock_result = ImageEditResult(
            success=True,
            edited_image=io.BytesIO(b"edited_data"),
            processing_time=1.8,
            error_message=None,
            metadata={}
        )
        self.mock_image_service.process_image_edit = AsyncMock(return_value=mock_result)
        
        # Test the command
        run_async_test(self.handler.handle_image_edit_command(self.mock_message))
        
        # Verify service was called with correct parameters
        call_args = self.mock_image_service.process_image_edit.call_args
        self.assertEqual(call_args[1]['image_data'], b"image_data")
        self.assertEqual(call_args[1]['edit_instruction'], "brighten this image")
        self.assertEqual(call_args[1]['user_id'], str(self.mock_author.id))

    def test_handle_image_edit_command_failure(self):
        """Test failed image edit command handling."""
        # Setup message and attachment
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.content_type = "image/png"
        mock_attachment.filename = "test.png"
        mock_attachment.read = AsyncMock(return_value=b"image_data")
        
        self.mock_message.content = "remove background"
        self.mock_message.attachments = [mock_attachment]
        
        # Setup failed result
        mock_result = ImageEditResult(
            success=False,
            edited_image=None,
            processing_time=0.5,
            error_message="Image processing failed",
            metadata={}
        )
        self.mock_image_service.process_image_edit = AsyncMock(return_value=mock_result)
        
        # Test the command
        run_async_test(self.handler.handle_image_edit_command(self.mock_message))
        
        # Verify error reply was sent
        self.mock_message.reply.assert_called_once()
        reply_content = self.mock_message.reply.call_args[0][0]
        self.assertIn("couldn't edit", reply_content.lower())

    def test_handle_image_edit_command_no_image(self):
        """Test image edit command with no image attachment."""
        self.mock_message.content = "edit this image"
        self.mock_message.attachments = []
        
        # Test the command
        run_async_test(self.handler.handle_image_edit_command(self.mock_message))
        
        # Verify appropriate reply was sent
        self.mock_message.reply.assert_called_once()
        reply_content = self.mock_message.reply.call_args[0][0]
        self.assertIn("don't see any images", reply_content)

    def test_handle_contextual_help_general(self):
        """Test contextual help for general requests."""
        self.mock_message.content = "help me please"
        
        # Test the help handler
        run_async_test(self.handler.handle_contextual_help(self.mock_message))
        
        # Verify reply with embed was sent
        self.mock_message.reply.assert_called_once()
        call_args = self.mock_message.reply.call_args
        self.assertIn('embed', call_args[1])

    def test_handle_contextual_help_image_specific(self):
        """Test contextual help for image-specific requests."""
        self.mock_message.content = "help with image editing"
        
        # Test the help handler
        run_async_test(self.handler.handle_contextual_help(self.mock_message))
        
        # Verify reply with embed was sent
        self.mock_message.reply.assert_called_once()
        call_args = self.mock_message.reply.call_args
        self.assertIn('embed', call_args[1])

    def test_suggest_image_upload(self):
        """Test image upload suggestion."""
        # Test the suggestion method
        run_async_test(self.handler._suggest_image_upload(self.mock_message))
        
        # Verify reply with embed was sent
        self.mock_message.reply.assert_called_once()
        call_args = self.mock_message.reply.call_args
        self.assertIn('embed', call_args[1])

    def test_error_handling_in_handle_message(self):
        """Test error handling in handle_message method."""
        # Setup message that will cause an error
        self.mock_message.content = "edit image"
        
        # Make image service raise an exception
        self.mock_image_service.process_image_edit = AsyncMock(side_effect=Exception("Test error"))
        
        # Setup error manager
        self.mock_error_manager.create_error_context = MagicMock()
        self.mock_error_manager.send_error_response = AsyncMock()
        
        # Test error handling
        result = run_async_test(self.handler.handle_message(self.mock_message))
        
        # Verify error was handled
        self.assertTrue(result)  # Should return True even on error
        self.mock_error_manager.send_error_response.assert_called_once()


if __name__ == '__main__':
    unittest.main()