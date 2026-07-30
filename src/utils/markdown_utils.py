"""
Markdown detection and parsing utilities for Discord bot message formatting.

This module provides functions to identify and parse various markdown elements
including code blocks, lists, headers, and other formatting elements to enable
intelligent message splitting while preserving formatting integrity.
"""

import re
from bisect import bisect_left
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
        return self.build_split_index(text).is_safe_split_point(position)
    
    def build_split_index(self, text: str) -> "SplitIndex":
        """
        Build a reusable index of the spans that make a position unsafe to split.
        
        Callers that test many candidate positions against the *same* text should
        build this once and query it, rather than calling `is_safe_split_point`
        in a loop. See `SplitIndex` for why that matters.
        
        Args:
            text: The text to index
            
        Returns:
            A SplitIndex answering safe-split queries in O(log n)
        """
        return SplitIndex(self, text)


#: Inline spans that must not be cut through. Kept next to SplitIndex because the
#: two have to agree: this is the same set is_safe_split_point used to test inline.
_INLINE_UNSAFE_TYPES = frozenset({
    BlockType.INLINE_CODE,
    BlockType.BOLD,
    BlockType.ITALIC,
    BlockType.STRIKETHROUGH,
    BlockType.SPOILER,
    BlockType.LINK,
})


class SplitIndex:
    """One markdown parse of a text; O(log n) safe-split-point queries.
    
    `is_safe_split_point(text, p)` is False exactly when some code block or
    inline-formatting span satisfies `start < p < end`. Answering that by
    re-scanning the whole text per candidate is what made splitting quadratic:
    the splitter probes many candidate positions per part, and each probe ran a
    full regex scan plus a full markdown parse of the entire message. A 139 KB
    response took 30.1 s of blocked event loop.
    
    Sorting the spans by start and carrying a running maximum of `end` reduces
    each query to one bisect. Building the index costs a single parse.
    
    The running maximum is what makes it correct for *nested and overlapping*
    spans: a span starting earlier may still cover `position` even though a
    later-starting span does not, so comparing against the largest `end` seen up
    to that point -- rather than the immediately preceding span's `end` -- is
    required.
    """
    
    __slots__ = ("code_boundaries", "_starts", "_prefix_max_end")
    
    def __init__(self, parser: "MarkdownParser", text: str):
        self.code_boundaries: List[Tuple[int, int, Optional[str]]] = \
            parser.find_code_block_boundaries(text)
        
        intervals = [(start, end) for start, end, _ in self.code_boundaries]
        intervals.extend(
            (block.start_pos, block.end_pos)
            for block in parser.parse_markdown(text)
            if block.type in _INLINE_UNSAFE_TYPES
        )
        intervals.sort()
        
        self._starts = [start for start, _ in intervals]
        prefix_max_end: List[int] = []
        running = -1
        for _, end in intervals:
            if end > running:
                running = end
            prefix_max_end.append(running)
        self._prefix_max_end = prefix_max_end
    
    def is_safe_split_point(self, position: int) -> bool:
        """True if `position` does not fall strictly inside any unsafe span."""
        index = bisect_left(self._starts, position)
        return index == 0 or self._prefix_max_end[index - 1] <= position


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
