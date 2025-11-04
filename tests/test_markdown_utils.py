"""
Unit tests for markdown detection and parsing utilities.

Tests the MarkdownParser class and utility functions for proper detection
of markdown elements and safe split point identification.
"""

import unittest
from src.utils.markdown_utils import (
    MarkdownParser, 
    BlockType, 
    MarkdownBlock,
    detect_markdown_elements,
    find_safe_split_points
)


class TestMarkdownParser(unittest.TestCase):
    """Test cases for MarkdownParser class."""

    def setUp(self):
        """Set up test fixtures."""
        self.parser = MarkdownParser()

    def test_detect_code_blocks(self):
        """Test detection of code blocks."""
        text = "Here's some code:\n```python\nprint('hello')\n```\nEnd of code."
        blocks = self.parser.parse_markdown(text)
        
        code_blocks = [b for b in blocks if b.type == BlockType.CODE_BLOCK]
        self.assertEqual(len(code_blocks), 1)
        
        code_block = code_blocks[0]
        self.assertEqual(code_block.language, "python")
        self.assertIn("print('hello')", code_block.content)

    def test_detect_code_blocks_without_language(self):
        """Test detection of code blocks without language specification."""
        text = "```\nsome code\n```"
        blocks = self.parser.parse_markdown(text)
        
        code_blocks = [b for b in blocks if b.type == BlockType.CODE_BLOCK]
        self.assertEqual(len(code_blocks), 1)
        self.assertIsNone(code_blocks[0].language)

    def test_detect_inline_code(self):
        """Test detection of inline code."""
        text = "Use `print()` function to output text."
        blocks = self.parser.parse_markdown(text)
        
        inline_code_blocks = [b for b in blocks if b.type == BlockType.INLINE_CODE]
        self.assertEqual(len(inline_code_blocks), 1)
        self.assertEqual(inline_code_blocks[0].metadata['raw_content'], "print()")

    def test_detect_headers(self):
        """Test detection of headers."""
        text = "# Main Title\n## Subtitle\n### Sub-subtitle"
        blocks = self.parser.parse_markdown(text)
        
        headers = [b for b in blocks if b.type == BlockType.HEADER]
        self.assertEqual(len(headers), 3)
        
        # Check header levels
        levels = [h.level for h in headers]
        self.assertEqual(levels, [1, 2, 3])

    def test_detect_unordered_lists(self):
        """Test detection of unordered list items."""
        text = "- Item 1\n- Item 2\n  - Nested item"
        blocks = self.parser.parse_markdown(text)
        
        list_items = [b for b in blocks if b.type == BlockType.LIST_ITEM]
        self.assertEqual(len(list_items), 3)
        
        # Check nesting levels
        levels = [item.level for item in list_items]
        self.assertEqual(levels, [0, 0, 1])  # First two at level 0, third at level 1

    def test_detect_ordered_lists(self):
        """Test detection of ordered list items."""
        text = "1. First item\n2. Second item\n   3. Nested item"
        blocks = self.parser.parse_markdown(text)
        
        list_items = [b for b in blocks if b.type == BlockType.LIST_ITEM]
        ordered_items = [item for item in list_items if item.metadata.get('ordered')]
        self.assertEqual(len(ordered_items), 3)

    def test_detect_quotes(self):
        """Test detection of quote blocks."""
        text = "> This is a quote\n> Continued quote"
        blocks = self.parser.parse_markdown(text)
        
        quotes = [b for b in blocks if b.type == BlockType.QUOTE_BLOCK]
        self.assertEqual(len(quotes), 2)

    def test_detect_text_formatting(self):
        """Test detection of text formatting (bold, italic, etc.)."""
        text = "**bold text** and *italic text* and ~~strikethrough~~ and ||spoiler||"
        blocks = self.parser.parse_markdown(text)
        
        bold_blocks = [b for b in blocks if b.type == BlockType.BOLD]
        italic_blocks = [b for b in blocks if b.type == BlockType.ITALIC]
        strike_blocks = [b for b in blocks if b.type == BlockType.STRIKETHROUGH]
        spoiler_blocks = [b for b in blocks if b.type == BlockType.SPOILER]
        
        self.assertEqual(len(bold_blocks), 1)
        self.assertEqual(len(italic_blocks), 1)
        self.assertEqual(len(strike_blocks), 1)
        self.assertEqual(len(spoiler_blocks), 1)

    def test_detect_links(self):
        """Test detection of markdown links."""
        text = "Check out [this link](https://example.com) for more info."
        blocks = self.parser.parse_markdown(text)
        
        links = [b for b in blocks if b.type == BlockType.LINK]
        self.assertEqual(len(links), 1)
        
        link = links[0]
        self.assertEqual(link.metadata['text'], "this link")
        self.assertEqual(link.metadata['url'], "https://example.com")

    def test_code_block_boundaries(self):
        """Test finding code block boundaries."""
        text = "Before\n```python\ncode here\n```\nAfter"
        boundaries = self.parser.find_code_block_boundaries(text)
        
        self.assertEqual(len(boundaries), 1)
        start, end, language = boundaries[0]
        self.assertEqual(language, "python")
        self.assertIn("```python", text[start:end])

    def test_safe_split_point_detection(self):
        """Test detection of safe split points."""
        # Safe point (outside code block)
        text = "Some text\n```\ncode\n```\nMore text"
        self.assertTrue(self.parser.is_safe_split_point(text, 10))  # In "Some text"
        
        # Unsafe point (inside code block)
        code_start = text.find("```")
        code_end = text.rfind("```") + 3
        unsafe_point = (code_start + code_end) // 2
        self.assertFalse(self.parser.is_safe_split_point(text, unsafe_point))

    def test_markdown_context_detection(self):
        """Test getting markdown context at specific positions."""
        text = "Normal text\n```python\ncode here\n```\nMore text"
        
        # Context outside code block
        context = self.parser.get_markdown_context(text, 5)
        self.assertFalse(context['in_code_block'])
        
        # Context inside code block
        code_start = text.find("```python")
        context = self.parser.get_markdown_context(text, code_start + 10)
        self.assertTrue(context['in_code_block'])
        self.assertEqual(context['code_block_language'], 'python')

    def test_exclude_ranges_functionality(self):
        """Test that inline elements are not detected inside code blocks."""
        text = "```\n`inline code` inside block\n```\n`real inline code`"
        blocks = self.parser.parse_markdown(text)
        
        inline_code_blocks = [b for b in blocks if b.type == BlockType.INLINE_CODE]
        # Should only find the one outside the code block
        self.assertEqual(len(inline_code_blocks), 1)
        self.assertEqual(inline_code_blocks[0].metadata['raw_content'], "real inline code")


class TestMarkdownUtilityFunctions(unittest.TestCase):
    """Test cases for markdown utility functions."""

    def test_detect_markdown_elements_function(self):
        """Test the convenience function for detecting markdown elements."""
        text = "# Header\n**bold** text with `code`"
        blocks = detect_markdown_elements(text)
        
        # Should find header, bold, and inline code
        types = [block.type for block in blocks]
        self.assertIn(BlockType.HEADER, types)
        self.assertIn(BlockType.BOLD, types)
        self.assertIn(BlockType.INLINE_CODE, types)

    def test_find_safe_split_points_function(self):
        """Test the function for finding safe split points."""
        # Create text longer than max_length
        text = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3.\n\n```\ncode block\n```\n\nParagraph 4."
        split_points = find_safe_split_points(text, max_length=30)
        
        # Should find some split points
        self.assertGreater(len(split_points), 0)
        
        # All split points should be valid positions
        for point in split_points:
            self.assertGreaterEqual(point, 0)
            self.assertLessEqual(point, len(text))

    def test_complex_markdown_parsing(self):
        """Test parsing of complex markdown with multiple elements."""
        text = """
# Main Header

This is a paragraph with **bold** and *italic* text.

## Code Example

Here's some code:

```python
def hello():
    print("Hello, world!")
    return `inline code in block`  # This should not be detected as inline
```

- List item 1
- List item 2
  - Nested item

> This is a quote
> with multiple lines

Check out [this link](https://example.com) for more info.

||This is a spoiler|| and ~~this is strikethrough~~.
"""
        
        blocks = detect_markdown_elements(text)
        
        # Count different types of blocks
        type_counts = {}
        for block in blocks:
            type_counts[block.type] = type_counts.get(block.type, 0) + 1
        
        # Verify we found the expected elements
        self.assertGreater(type_counts.get(BlockType.HEADER, 0), 0)
        self.assertGreater(type_counts.get(BlockType.CODE_BLOCK, 0), 0)
        self.assertGreater(type_counts.get(BlockType.LIST_ITEM, 0), 0)
        self.assertGreater(type_counts.get(BlockType.QUOTE_BLOCK, 0), 0)
        self.assertGreater(type_counts.get(BlockType.BOLD, 0), 0)
        self.assertGreater(type_counts.get(BlockType.ITALIC, 0), 0)
        self.assertGreater(type_counts.get(BlockType.LINK, 0), 0)
        self.assertGreater(type_counts.get(BlockType.SPOILER, 0), 0)
        self.assertGreater(type_counts.get(BlockType.STRIKETHROUGH, 0), 0)

    def test_empty_text_handling(self):
        """Test handling of empty or whitespace-only text."""
        parser = MarkdownParser()
        
        # Empty text
        blocks = parser.parse_markdown("")
        self.assertEqual(len(blocks), 0)
        
        # Whitespace only
        blocks = parser.parse_markdown("   \n\n   ")
        self.assertEqual(len(blocks), 0)

    def test_malformed_markdown_handling(self):
        """Test handling of malformed markdown."""
        parser = MarkdownParser()
        
        # Unclosed code block
        text = "```python\ncode without closing"
        blocks = parser.parse_markdown(text)
        # Should not crash, may or may not detect as code block depending on implementation
        self.assertIsInstance(blocks, list)
        
        # Mismatched formatting
        text = "**bold without closing and *italic without closing"
        blocks = parser.parse_markdown(text)
        # Should not crash
        self.assertIsInstance(blocks, list)


if __name__ == '__main__':
    unittest.main()