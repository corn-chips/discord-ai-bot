"""
Unit tests for message splitting service.

Tests the MessageSplitter class for proper message splitting with markdown
preservation, continuation indicators, and split integrity validation.
"""

import unittest
from src.services.message_splitter import MessageSplitter, MessagePart
from src.utils.markdown_utils import BlockType


class TestMessageSplitter(unittest.TestCase):
    """Test cases for MessageSplitter class."""

    def setUp(self):
        """Set up test fixtures."""
        self.splitter = MessageSplitter(max_length=100, preserve_formatting=True)
        self.simple_splitter = MessageSplitter(max_length=100, preserve_formatting=False)

    def test_short_message_no_split(self):
        """Test that short messages are not split."""
        content = "This is a short message."
        parts = self.splitter.split_message(content)
        
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].content, content)
        self.assertEqual(parts[0].part_number, 1)
        self.assertEqual(parts[0].total_parts, 1)
        self.assertFalse(parts[0].has_continuation)

    def test_long_message_split(self):
        """Test that long messages are properly split."""
        # Create a message longer than max_length
        content = "This is a very long message. " * 10  # Should exceed 100 chars
        parts = self.splitter.split_message(content)
        
        self.assertGreater(len(parts), 1)
        
        # Check part numbering
        for i, part in enumerate(parts):
            self.assertEqual(part.part_number, i + 1)
            self.assertEqual(part.total_parts, len(parts))

    def test_continuation_indicators(self):
        """Test that continuation indicators are properly added."""
        content = "Part one content. " * 10  # Long enough to split
        parts = self.splitter.split_message(content)
        
        if len(parts) > 1:
            # First part should have "continues" indicator
            self.assertIn("continues in part", parts[0].content)
            
            # Middle parts should have both indicators
            if len(parts) > 2:
                middle_part = parts[1]
                self.assertIn("continued from part", middle_part.content)
                self.assertIn("continues in part", middle_part.content)
            
            # Last part should have "continued" indicator
            self.assertIn("continued from part", parts[-1].content)
            self.assertNotIn("continues in part", parts[-1].content)

    def test_code_block_preservation(self):
        """Test that code blocks are preserved across splits."""
        content = """Here's some text before the code.

```python
def long_function():
    # This is a long code block that might get split
    for i in range(100):
        print(f"Processing item {i}")
        if i % 10 == 0:
            print("Checkpoint reached")
    return "Done"
```

And some text after the code."""
        
        parts = self.splitter.split_message(content)
        
        # If split occurred, check for code block preservation
        if len(parts) > 1:
            # Look for code block markers in the parts
            has_opening_marker = any("```python" in part.content for part in parts)
            has_closing_marker = any(part.content.rstrip().endswith("```") for part in parts)
            
            # Should have proper code block markers
            self.assertTrue(has_opening_marker or has_closing_marker)

    def test_split_integrity_validation(self):
        """Test that split integrity validation works correctly."""
        content = "This is test content for validation. " * 5
        parts = self.splitter.split_message(content)
        
        # Should validate successfully for proper splits
        is_valid = self.splitter.validate_split_integrity(content, parts)
        self.assertTrue(is_valid)

    def test_markdown_block_detection_in_parts(self):
        """Test that markdown blocks are detected in message parts."""
        content = "# Header\n\n**Bold text** and `inline code`. " * 3
        parts = self.splitter.split_message(content)
        
        # Check that markdown blocks are detected
        total_blocks = sum(len(part.markdown_blocks) for part in parts)
        self.assertGreater(total_blocks, 0)

    def test_split_statistics(self):
        """Test that split statistics are calculated correctly."""
        content = "Test content for statistics. " * 10
        parts = self.splitter.split_message(content)
        stats = self.splitter.get_split_statistics(parts)
        
        self.assertEqual(stats['total_parts'], len(parts))
        self.assertGreater(stats['total_length'], 0)
        self.assertGreater(stats['average_length'], 0)

    def test_preserve_formatting_disabled(self):
        """Test behavior when formatting preservation is disabled."""
        content = "# Header\n\n**Bold** text with `code`. " * 5
        parts = self.simple_splitter.split_message(content)
        
        # Should still split, but without markdown analysis
        if len(parts) > 1:
            # Markdown blocks should be empty when preservation is disabled
            for part in parts:
                self.assertEqual(len(part.markdown_blocks), 0)

    def test_empty_content_handling(self):
        """Test handling of empty or whitespace content."""
        # Empty content
        parts = self.splitter.split_message("")
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].content, "")
        
        # Whitespace only
        parts = self.splitter.split_message("   \n\n   ")
        self.assertEqual(len(parts), 1)

    def test_optimal_split_points(self):
        """Test that optimal split points are found."""
        # Content with clear paragraph breaks
        content = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3.\n\nParagraph 4."
        parts = self.splitter.split_message(content)
        
        # Should split at paragraph boundaries when possible
        if len(parts) > 1:
            # Check that splits don't break in the middle of words
            for part in parts:
                # Remove continuation indicators for checking
                clean_content = part.content
                clean_content = clean_content.replace("*(continued from part", "")
                clean_content = clean_content.replace("*(continues in part", "")
                clean_content = clean_content.strip()
                
                if clean_content:
                    # Should not end with partial words (basic check)
                    self.assertFalse(clean_content.endswith("-"))

    def test_convenience_function(self):
        """Test the convenience function for simple message splitting."""
        from src.services.message_splitter import split_long_message
        
        content = "Simple test content. " * 10
        parts = split_long_message(content, max_length=50)
        
        self.assertIsInstance(parts, list)
        self.assertGreater(len(parts), 0)
        
        # All parts should be strings
        for part in parts:
            self.assertIsInstance(part, str)


class TestMessagePart(unittest.TestCase):
    """Test cases for MessagePart data class."""

    def test_message_part_creation(self):
        """Test creating a MessagePart instance."""
        part = MessagePart(
            content="Test content",
            part_number=1,
            total_parts=3,
            has_continuation=True,
            markdown_blocks=[],
            metadata={'test': 'value'}
        )
        
        self.assertEqual(part.content, "Test content")
        self.assertEqual(part.part_number, 1)
        self.assertEqual(part.total_parts, 3)
        self.assertTrue(part.has_continuation)
        self.assertEqual(part.metadata['test'], 'value')


if __name__ == '__main__':
    unittest.main()