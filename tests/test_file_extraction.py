"""
Tests for file extraction functionality in Discord bot.

Tests the ability to extract and process various file types from Discord messages.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord

from src.config import BotConfig
from src.bot.discord_bot import DiscordBot


class TestFileExtraction(unittest.TestCase):
    """Test cases for file extraction from Discord messages."""

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

    async def test_extract_text_file(self):
        """Test extraction of a text file from a message."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with text file attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for a text file
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "test.txt"
            mock_attachment.content_type = "text/plain"
            mock_attachment.size = 100
            mock_attachment.read = AsyncMock(return_value=b"Hello, this is a test file!")
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 1)
            self.assertEqual(len(unsupported), 0)
            self.assertEqual(files[0]['name'], "test.txt")
            self.assertEqual(files[0]['content'], "Hello, this is a test file!")
            self.assertEqual(files[0]['size'], 100)

    async def test_extract_json_file(self):
        """Test extraction of a JSON file from a message."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with JSON file attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for a JSON file
            json_content = '{"name": "test", "value": 123}'
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "config.json"
            mock_attachment.content_type = "application/json"
            mock_attachment.size = len(json_content)
            mock_attachment.read = AsyncMock(return_value=json_content.encode('utf-8'))
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 1)
            self.assertEqual(len(unsupported), 0)
            self.assertEqual(files[0]['name'], "config.json")
            self.assertEqual(files[0]['content'], json_content)

    async def test_skip_image_files(self):
        """Test that image files are skipped (handled separately)."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with image attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for an image file
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "image.png"
            mock_attachment.content_type = "image/png"
            mock_attachment.size = 1000
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results - image should be skipped
            self.assertEqual(len(files), 0)
            self.assertEqual(len(unsupported), 0)

    async def test_unsupported_file_type(self):
        """Test handling of unsupported file types."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with unsupported file attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for an unsupported file
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "document.pdf"
            mock_attachment.content_type = "application/pdf"
            mock_attachment.size = 1000
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 0)
            self.assertEqual(len(unsupported), 1)
            self.assertIn("document.pdf", unsupported[0])

    async def test_file_size_limit(self):
        """Test that files exceeding size limit are rejected."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with large file attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for a large file (over 5MB)
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "large_file.txt"
            mock_attachment.content_type = "text/plain"
            mock_attachment.size = 6 * 1024 * 1024  # 6MB
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 0)
            self.assertEqual(len(unsupported), 1)
            self.assertIn("large_file.txt", unsupported[0])
            self.assertIn("too large", unsupported[0])

    async def test_multiple_files(self):
        """Test extraction of multiple files from a single message."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with multiple file attachments
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create multiple mock attachments
            mock_attachment1 = MagicMock(spec=discord.Attachment)
            mock_attachment1.filename = "file1.txt"
            mock_attachment1.content_type = "text/plain"
            mock_attachment1.size = 50
            mock_attachment1.read = AsyncMock(return_value=b"Content of file 1")
            
            mock_attachment2 = MagicMock(spec=discord.Attachment)
            mock_attachment2.filename = "file2.md"
            mock_attachment2.content_type = "text/markdown"
            mock_attachment2.size = 60
            mock_attachment2.read = AsyncMock(return_value=b"# Content of file 2")
            
            mock_message.attachments = [mock_attachment1, mock_attachment2]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 2)
            self.assertEqual(len(unsupported), 0)
            self.assertEqual(files[0]['name'], "file1.txt")
            self.assertEqual(files[1]['name'], "file2.md")

    async def test_code_file_extraction(self):
        """Test extraction of code files (Python, JavaScript, etc.)."""
        with patch('discord.Intents.default'), \
             patch('discord.Client.__init__', return_value=None), \
             patch('src.services.gemini_client.genai.configure'), \
             patch('src.services.gemini_client.genai.GenerativeModel'):
            
            bot = DiscordBot(self.config)
            bot.user = self.mock_user
            
            # Create mock message with code file attachment
            mock_message = MagicMock(spec=discord.Message)
            mock_message.author = self.mock_author
            mock_message.reference = None
            
            # Create mock attachment for a Python file
            code_content = 'def hello():\n    print("Hello, World!")'
            mock_attachment = MagicMock(spec=discord.Attachment)
            mock_attachment.filename = "script.py"
            mock_attachment.content_type = "text/x-python"
            mock_attachment.size = len(code_content)
            mock_attachment.read = AsyncMock(return_value=code_content.encode('utf-8'))
            
            mock_message.attachments = [mock_attachment]
            
            # Extract files
            files, unsupported = await bot._extract_files_from_message(mock_message)
            
            # Verify results
            self.assertEqual(len(files), 1)
            self.assertEqual(len(unsupported), 0)
            self.assertEqual(files[0]['name'], "script.py")
            self.assertEqual(files[0]['content'], code_content)


# Create async test runner
def async_test_runner():
    """Run async tests."""
    import asyncio
    
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFileExtraction)
    
    # Run each test
    for test in suite:
        if asyncio.iscoroutinefunction(getattr(test, test._testMethodName)):
            asyncio.run(test.debug())


if __name__ == '__main__':
    # For running with unittest
    unittest.main()
