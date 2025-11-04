"""
Markdown detection and parsing utilities for Discord bot message formatting.

This module provides functions to identify and parse various markdown elements
including code blocks, lists, headers, and other formatting elements to enable
intelligent message splitting while preserving formatting integrity.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any


class BlockType(Enum):
    """Types of markdown blocks that can be detected."""
    CODE_BLOCK = "code_block"
    INLINE_CODE = "inline_code"
    LIST_ITEM = "list_item"
    QUOTE_BLOCK = "quote_block"
    HEADER = "header"
    BOLD = "bold"
    ITALIC = "italic"
    STRIKETHROUGH = "strikethrough"
    SPOILER = "spoiler"
    LINK = "link"
    PLAIN_TEXT = "plain_text"


@dataclass
class MarkdownBlock:
    """Represents a detected markdown block with its properties."""
    type: BlockType
    content: str
    start_pos: int
    end_pos: int
    language: Optional[str] = None  # For code blocks
    level: Optional[int] = None     # For headers and list nesting
    metadata: Optional[Dict[str, Any]] = None


class MarkdownParser:
    """Parser for detecting and analyzing markdown elements in text."""
    
    def __init__(self):
        """Initialize the markdown parser with compiled regex patterns."""
        # Code block patterns
        self.code_block_pattern = re.compile(
            r'```(?:(\w+)\n)?(.*?)```', 
            re.DOTALL | re.MULTILINE
        )
        self.inline_code_pattern = re.compile(r'`([^`\n]+)`')
        
        # List patterns
        self.unordered_list_pattern = re.compile(r'^(\s*)[-*+]\s+(.+)$', re.MULTILINE)
        self.ordered_list_pattern = re.compile(r'^(\s*)(\d+)\.\s+(.+)$', re.MULTILINE)
        
        # Quote pattern
        self.quote_pattern = re.compile(r'^(\s*)>\s*(.+)$', re.MULTILINE)
        
        # Header patterns
        self.header_pattern = re.compile(r'^(\s*)(#{1,6})\s+(.+)$', re.MULTILINE)
        
        # Text formatting patterns
        self.bold_pattern = re.compile(r'\*\*([^*\n]+)\*\*')
        self.italic_pattern = re.compile(r'(?<!\*)\*([^*\n]+)\*(?!\*)')
        self.strikethrough_pattern = re.compile(r'~~([^~\n]+)~~')
        self.spoiler_pattern = re.compile(r'\|\|([^|\n]+)\|\|')
        
        # Link pattern
        self.link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    
    def parse_markdown(self, text: str) -> List[MarkdownBlock]:
        """
        Parse text and identify all markdown blocks.
        
        Args:
            text: The text content to parse
            
        Returns:
            List of MarkdownBlock objects representing detected markdown elements
        """
        blocks = []
        
        # Find code blocks first (they take precedence)
        blocks.extend(self._find_code_blocks(text))
        
        # Find other elements, excluding areas already covered by code blocks
        code_ranges = [(block.start_pos, block.end_pos) for block in blocks if block.type == BlockType.CODE_BLOCK]
        
        blocks.extend(self._find_headers(text, code_ranges))
        blocks.extend(self._find_lists(text, code_ranges))
        blocks.extend(self._find_quotes(text, code_ranges))
        blocks.extend(self._find_inline_code(text, code_ranges))
        blocks.extend(self._find_text_formatting(text, code_ranges))
        blocks.extend(self._find_links(text, code_ranges))
        
        # Sort blocks by start position
        blocks.sort(key=lambda x: x.start_pos)
        
        return blocks
    
    def _find_code_blocks(self, text: str) -> List[MarkdownBlock]:
        """Find code blocks in the text."""
        blocks = []
        
        for match in self.code_block_pattern.finditer(text):
            language = match.group(1) if match.group(1) else None
            content = match.group(2) if match.group(2) else ""
            
            blocks.append(MarkdownBlock(
                type=BlockType.CODE_BLOCK,
                content=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
                language=language,
                metadata={'raw_content': content}
            ))
        
        return blocks
    
    def _find_inline_code(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find inline code elements, excluding specified ranges."""
        blocks = []
        
        for match in self.inline_code_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.INLINE_CODE,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'raw_content': match.group(1)}
                ))
        
        return blocks
    
    def _find_headers(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find header elements, excluding specified ranges."""
        blocks = []
        
        for match in self.header_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                level = len(match.group(2))  # Count the # symbols
                blocks.append(MarkdownBlock(
                    type=BlockType.HEADER,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    level=level,
                    metadata={'text': match.group(3)}
                ))
        
        return blocks
    
    def _find_lists(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find list items, excluding specified ranges."""
        blocks = []
        
        # Find unordered list items
        for match in self.unordered_list_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                indent_level = len(match.group(1)) // 2  # Assume 2 spaces per level
                blocks.append(MarkdownBlock(
                    type=BlockType.LIST_ITEM,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    level=indent_level,
                    metadata={'text': match.group(2), 'ordered': False}
                ))
        
        # Find ordered list items
        for match in self.ordered_list_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                indent_level = len(match.group(1)) // 2  # Assume 2 spaces per level
                blocks.append(MarkdownBlock(
                    type=BlockType.LIST_ITEM,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    level=indent_level,
                    metadata={'text': match.group(3), 'ordered': True, 'number': int(match.group(2))}
                ))
        
        return blocks
    
    def _find_quotes(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find quote blocks, excluding specified ranges."""
        blocks = []
        
        for match in self.quote_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.QUOTE_BLOCK,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(2)}
                ))
        
        return blocks
    
    def _find_text_formatting(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find text formatting elements (bold, italic, etc.), excluding specified ranges."""
        blocks = []
        
        # Bold text
        for match in self.bold_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.BOLD,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(1)}
                ))
        
        # Italic text
        for match in self.italic_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.ITALIC,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(1)}
                ))
        
        # Strikethrough text
        for match in self.strikethrough_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.STRIKETHROUGH,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(1)}
                ))
        
        # Spoiler text
        for match in self.spoiler_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.SPOILER,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(1)}
                ))
        
        return blocks
    
    def _find_links(self, text: str, exclude_ranges: List[Tuple[int, int]]) -> List[MarkdownBlock]:
        """Find link elements, excluding specified ranges."""
        blocks = []
        
        for match in self.link_pattern.finditer(text):
            if not self._is_in_excluded_range(match.start(), match.end(), exclude_ranges):
                blocks.append(MarkdownBlock(
                    type=BlockType.LINK,
                    content=match.group(0),
                    start_pos=match.start(),
                    end_pos=match.end(),
                    metadata={'text': match.group(1), 'url': match.group(2)}
                ))
        
        return blocks
    
    def _is_in_excluded_range(self, start: int, end: int, exclude_ranges: List[Tuple[int, int]]) -> bool:
        """Check if a range overlaps with any excluded ranges."""
        for exclude_start, exclude_end in exclude_ranges:
            if not (end <= exclude_start or start >= exclude_end):
                return True
        return False
    
    def find_code_block_boundaries(self, text: str) -> List[Tuple[int, int, Optional[str]]]:
        """
        Find code block boundaries for intelligent splitting.
        
        Args:
            text: The text to analyze
            
        Returns:
            List of tuples (start_pos, end_pos, language) for each code block
        """
        boundaries = []
        
        for match in self.code_block_pattern.finditer(text):
            language = match.group(1) if match.group(1) else None
            boundaries.append((match.start(), match.end(), language))
        
        return boundaries
    
    def is_safe_split_point(self, text: str, position: int) -> bool:
        """
        Determine if a position is safe for splitting text without breaking markdown.
        
        Args:
            text: The text to analyze
            position: The position to check
            
        Returns:
            True if the position is safe for splitting, False otherwise
        """
        # Don't split inside code blocks
        code_boundaries = self.find_code_block_boundaries(text)
        for start, end, _ in code_boundaries:
            if start < position < end:
                return False
        
        # Don't split in the middle of inline formatting
        blocks = self.parse_markdown(text)
        for block in blocks:
            if block.type in [BlockType.INLINE_CODE, BlockType.BOLD, BlockType.ITALIC, 
                             BlockType.STRIKETHROUGH, BlockType.SPOILER, BlockType.LINK]:
                if block.start_pos < position < block.end_pos:
                    return False
        
        return True
    
    def get_markdown_context(self, text: str, position: int) -> Dict[str, Any]:
        """
        Get markdown context information at a specific position.
        
        Args:
            text: The text to analyze
            position: The position to get context for
            
        Returns:
            Dictionary containing context information
        """
        context = {
            'in_code_block': False,
            'code_block_language': None,
            'in_list': False,
            'list_level': 0,
            'in_quote': False,
            'active_formatting': []
        }
        
        blocks = self.parse_markdown(text)
        
        for block in blocks:
            if block.start_pos <= position <= block.end_pos:
                if block.type == BlockType.CODE_BLOCK:
                    context['in_code_block'] = True
                    context['code_block_language'] = block.language
                elif block.type == BlockType.LIST_ITEM:
                    context['in_list'] = True
                    context['list_level'] = block.level or 0
                elif block.type == BlockType.QUOTE_BLOCK:
                    context['in_quote'] = True
                elif block.type in [BlockType.BOLD, BlockType.ITALIC, BlockType.STRIKETHROUGH, 
                                   BlockType.SPOILER, BlockType.INLINE_CODE]:
                    context['active_formatting'].append(block.type.value)
        
        return context


def detect_markdown_elements(text: str) -> List[MarkdownBlock]:
    """
    Convenience function to detect markdown elements in text.
    
    Args:
        text: The text to analyze
        
    Returns:
        List of detected markdown blocks
    """
    parser = MarkdownParser()
    return parser.parse_markdown(text)


def find_safe_split_points(text: str, max_length: int = 2000) -> List[int]:
    """
    Find safe points to split text without breaking markdown formatting.
    
    Args:
        text: The text to analyze
        max_length: Maximum length for each split
        
    Returns:
        List of positions where text can be safely split
    """
    parser = MarkdownParser()
    safe_points = []
    
    # Look for natural break points (paragraphs, sentences)
    potential_points = []
    
    # Paragraph breaks (double newlines)
    for match in re.finditer(r'\n\n+', text):
        potential_points.append(match.end())
    
    # Single line breaks
    for match in re.finditer(r'\n', text):
        potential_points.append(match.end())
    
    # Sentence endings
    for match in re.finditer(r'[.!?]\s+', text):
        potential_points.append(match.end())
    
    # Sort potential points
    potential_points.sort()
    
    # Filter for safe points within length constraints
    current_pos = 0
    for point in potential_points:
        if point - current_pos >= max_length * 0.5:  # At least half the max length
            if parser.is_safe_split_point(text, point):
                safe_points.append(point)
                current_pos = point
    
    return safe_points