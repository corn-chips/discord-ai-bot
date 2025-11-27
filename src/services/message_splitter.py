"""
Intelligent message splitting service for Discord bot responses.

This service handles splitting long messages while preserving markdown formatting,
maintaining code block integrity, and providing continuation indicators to show
message relationships across splits.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
import logging

from ..constants import DISCORD_MESSAGE_LIMIT, CONTINUATION_INDICATOR_OVERHEAD
from ..utils.markdown_utils import MarkdownParser, BlockType, MarkdownBlock


logger = logging.getLogger(__name__)


@dataclass
class MessagePart:
    """Represents a part of a split message with metadata."""
    content: str
    part_number: int
    total_parts: int
    has_continuation: bool
    markdown_blocks: List[MarkdownBlock]
    metadata: Optional[Dict[str, Any]] = None


class MessageSplitter:
    """
    Service for intelligently splitting long messages while preserving markdown formatting.
    
    This class implements smart boundary detection, code block preservation across splits,
    and continuation indicators for message relationship tracking.
    """
    
    def __init__(self, max_length: int = DISCORD_MESSAGE_LIMIT, preserve_formatting: bool = True):
        """
        Initialize the message splitter.
        
        Args:
            max_length: Maximum length for each message part (Discord limit is 2000)
            preserve_formatting: Whether to preserve markdown formatting across splits
        """
        self.max_length = max_length
        self.preserve_formatting = preserve_formatting
        self.markdown_parser = MarkdownParser()
        
        # Reserve space for continuation indicators
        self.continuation_overhead = CONTINUATION_INDICATOR_OVERHEAD
        self.effective_max_length = max_length - self.continuation_overhead
        
        logger.info(f"MessageSplitter initialized with max_length={max_length}, preserve_formatting={preserve_formatting}")
    
    def split_message(self, content: str) -> List[MessagePart]:
        """
        Split a message into multiple parts while preserving markdown formatting.
        
        Args:
            content: The message content to split
            
        Returns:
            List of MessagePart objects representing the split message
        """
        # Handle empty or whitespace-only content
        if not content or not content.strip():
            return [MessagePart(
                content=content or "",
                part_number=1,
                total_parts=1,
                has_continuation=False,
                markdown_blocks=[]
            )]
        
        # Strip the content for length check but preserve for processing
        stripped_content = content.strip()
        
        if len(stripped_content) <= self.max_length:
            # No splitting needed
            blocks = self.markdown_parser.parse_markdown(stripped_content) if self.preserve_formatting else []
            return [MessagePart(
                content=stripped_content,
                part_number=1,
                total_parts=1,
                has_continuation=False,
                markdown_blocks=blocks
            )]
        
        logger.info(f"Splitting message of {len(stripped_content)} characters")
        
        # Find optimal split points
        split_points = self._find_optimal_split_points(stripped_content)
        
        # Create message parts (filters out empty parts)
        parts = self._create_message_parts(stripped_content, split_points)
        
        # If all parts were empty, return a single empty part
        if not parts:
            return [MessagePart(
                content="",
                part_number=1,
                total_parts=1,
                has_continuation=False,
                markdown_blocks=[]
            )]
        
        # Add continuation indicators
        parts = self._add_continuation_indicators(parts)
        
        # Handle code block preservation
        if self.preserve_formatting:
            parts = self._preserve_code_blocks(parts, stripped_content)
        
        logger.info(f"Message split into {len(parts)} parts")
        return parts
    
    def _find_optimal_split_points(self, content: str) -> List[int]:
        """
        Find optimal points to split the content.
        
        Args:
            content: The content to analyze
            
        Returns:
            List of character positions where splits should occur
        """
        split_points = [0]  # Always start at the beginning
        current_pos = 0
        
        while current_pos < len(content):
            # Find the next split point
            next_split = self._find_next_split_point(content, current_pos)
            
            if next_split > current_pos:
                split_points.append(next_split)
                current_pos = next_split
            else:
                # Fallback: force split at max length to ensure progress
                forced_split = min(current_pos + self.effective_max_length, len(content))
                if forced_split <= current_pos:
                    # Emergency fallback: advance by at least 1 character
                    forced_split = min(current_pos + 1, len(content))
                split_points.append(forced_split)
                current_pos = forced_split
        
        # Ensure we end at the content length
        if split_points[-1] != len(content):
            split_points.append(len(content))
        
        return split_points
    
    def _find_next_split_point(self, content: str, start_pos: int) -> int:
        """
        Find the next optimal split point from the given position.
        
        Args:
            content: The content to analyze
            start_pos: Starting position to search from
            
        Returns:
            The position of the next split point
        """
        max_end = min(start_pos + self.effective_max_length, len(content))
        
        if max_end >= len(content):
            return len(content)
        
        # Minimum content threshold - don't split if it would create a part smaller than this
        min_part_length = 100  # Ensure at least 100 chars of actual content per part
        
        # Look for natural break points in order of preference
        search_text = content[start_pos:max_end]
        
        # Helper function to check if a split point creates a part with enough content
        def has_enough_content(split_pos: int) -> bool:
            """Check if the split would create a part with enough non-whitespace content."""
            part_content = content[start_pos:split_pos].strip()
            return len(part_content) >= min_part_length
        
        # 1. Paragraph breaks (double newlines) - highest priority
        paragraph_breaks = list(re.finditer(r'\n\n+', search_text))
        if paragraph_breaks:
            # Try breaks from last to first, preferring those that create substantial parts
            for match in reversed(paragraph_breaks):
                split_pos = start_pos + match.end()
                if has_enough_content(split_pos) and self.markdown_parser.is_safe_split_point(content, split_pos):
                    return split_pos
        
        # 2. End of code blocks
        code_boundaries = self.markdown_parser.find_code_block_boundaries(content)
        for start, end, _ in code_boundaries:
            if start_pos < end <= max_end:
                if has_enough_content(end) and self.markdown_parser.is_safe_split_point(content, end):
                    return end
        
        # 3. Single line breaks
        line_breaks = list(re.finditer(r'\n', search_text))
        if line_breaks:
            # Prefer line breaks that are not inside code blocks
            for match in reversed(line_breaks):
                split_pos = start_pos + match.end()
                if has_enough_content(split_pos) and self.markdown_parser.is_safe_split_point(content, split_pos):
                    return split_pos
        
        # 4. Sentence endings
        sentence_endings = list(re.finditer(r'[.!?]\s+', search_text))
        if sentence_endings:
            for match in reversed(sentence_endings):
                split_pos = start_pos + match.end()
                if has_enough_content(split_pos) and self.markdown_parser.is_safe_split_point(content, split_pos):
                    return split_pos
        
        # 5. Word boundaries
        word_boundaries = list(re.finditer(r'\s+', search_text))
        if word_boundaries:
            # Take the last word boundary that's safe and makes progress
            for match in reversed(word_boundaries):
                split_pos = start_pos + match.start()
                if split_pos > start_pos and has_enough_content(split_pos) and self.markdown_parser.is_safe_split_point(content, split_pos):
                    return split_pos
        
        # 6. Fallback: force split at character boundary (avoid breaking UTF-8)
        return max_end
    
    def _create_message_parts(self, content: str, split_points: List[int]) -> List[MessagePart]:
        """
        Create MessagePart objects from split points.
        
        Args:
            content: The original content
            split_points: List of split positions
            
        Returns:
            List of MessagePart objects
        """
        # First pass: collect non-empty parts
        raw_parts = []
        for i in range(len(split_points) - 1):
            start = split_points[i]
            end = split_points[i + 1]
            part_content = content[start:end].strip()
            
            # Skip empty parts
            if not part_content:
                continue
            
            raw_parts.append({
                'content': part_content,
                'original_start': start,
                'original_end': end
            })
        
        # If no valid parts, return empty list
        if not raw_parts:
            return []
        
        # Second pass: create MessagePart objects with correct numbering
        parts = []
        total_parts = len(raw_parts)
        
        for i, raw_part in enumerate(raw_parts):
            # Parse markdown blocks for this part
            blocks = []
            if self.preserve_formatting:
                blocks = self.markdown_parser.parse_markdown(raw_part['content'])
            
            part = MessagePart(
                content=raw_part['content'],
                part_number=i + 1,
                total_parts=total_parts,
                has_continuation=i < total_parts - 1,  # All parts except the last have continuation
                markdown_blocks=blocks,
                metadata={'original_start': raw_part['original_start'], 'original_end': raw_part['original_end']}
            )
            
            parts.append(part)
        
        return parts
    
    def _add_continuation_indicators(self, parts: List[MessagePart]) -> List[MessagePart]:
        """
        Add continuation indicators to message parts.
        
        Args:
            parts: List of message parts
            
        Returns:
            List of message parts with continuation indicators added
        """
        if len(parts) <= 1:
            return parts
        
        updated_parts = []
        
        for i, part in enumerate(parts):
            content = part.content
            
            # Add "continued" indicator to parts after the first
            if i > 0:
                content = f"*(continued from part {i}/{part.total_parts})*\n\n{content}"
            
            # Add "continues" indicator to parts before the last
            if i < len(parts) - 1:
                content = f"{content}\n\n*(continues in part {i + 2}/{part.total_parts})*"
            
            # Create updated part
            updated_part = MessagePart(
                content=content,
                part_number=part.part_number,
                total_parts=part.total_parts,
                has_continuation=part.has_continuation,
                markdown_blocks=part.markdown_blocks,
                metadata=part.metadata
            )
            
            updated_parts.append(updated_part)
        
        return updated_parts
    
    def _preserve_code_blocks(self, parts: List[MessagePart], original_content: str) -> List[MessagePart]:
        """
        Preserve code block formatting across message splits.
        
        Args:
            parts: List of message parts
            original_content: The original unsplit content
            
        Returns:
            List of message parts with code block preservation applied
        """
        # Find all code blocks in the original content
        code_boundaries = self.markdown_parser.find_code_block_boundaries(original_content)
        
        if not code_boundaries:
            return parts
        
        updated_parts = []
        
        for part in parts:
            content = part.content
            start_pos = part.metadata.get('original_start', 0)
            end_pos = part.metadata.get('original_end', len(original_content))
            
            # Check if this part intersects with any code blocks
            for code_start, code_end, language in code_boundaries:
                # Code block starts before this part and ends within or after it
                if code_start < start_pos < code_end:
                    # This part starts inside a code block
                    lang_spec = f"{language}\n" if language else ""
                    content = f"```{lang_spec}{content}"
                
                # Code block starts within this part and ends after it
                if start_pos <= code_start < end_pos < code_end:
                    # This part ends inside a code block
                    content = f"{content}\n```"
                
                # Code block is entirely within this part - no action needed
                # Code block spans multiple parts - handle opening/closing
                if code_start < start_pos and code_end > end_pos:
                    # This part is entirely within a code block
                    lang_spec = f"{language}\n" if language else ""
                    content = f"```{lang_spec}{content}\n```"
            
            # Create updated part
            updated_part = MessagePart(
                content=content,
                part_number=part.part_number,
                total_parts=part.total_parts,
                has_continuation=part.has_continuation,
                markdown_blocks=part.markdown_blocks,
                metadata=part.metadata
            )
            
            updated_parts.append(updated_part)
        
        return updated_parts
    
    def validate_split_integrity(self, original: str, parts: List[MessagePart]) -> bool:
        """
        Validate that the split maintains content integrity.
        
        Args:
            original: The original content
            parts: The split message parts
            
        Returns:
            True if the split maintains integrity, False otherwise
        """
        try:
            # Remove continuation indicators and reconstruct content
            reconstructed = ""
            
            for part in parts:
                content = part.content
                
                # Remove continuation indicators
                content = re.sub(r'\*\(continued from part \d+/\d+\)\*\n\n', '', content)
                content = re.sub(r'\n\n\*\(continues in part \d+/\d+\)\*', '', content)
                
                # Remove code block preservation artifacts if they were added
                # This is a simplified check - in practice, we'd need more sophisticated logic
                
                reconstructed += content
            
            # Compare lengths (allowing for some whitespace differences)
            original_clean = re.sub(r'\s+', ' ', original.strip())
            reconstructed_clean = re.sub(r'\s+', ' ', reconstructed.strip())
            
            # Check if content is substantially the same
            return abs(len(original_clean) - len(reconstructed_clean)) <= 10
            
        except Exception as e:
            logger.error(f"Error validating split integrity: {e}")
            return False
    
    def get_split_statistics(self, parts: List[MessagePart]) -> Dict[str, Any]:
        """
        Get statistics about the message split.
        
        Args:
            parts: The split message parts
            
        Returns:
            Dictionary containing split statistics
        """
        if not parts:
            return {}
        
        lengths = [len(part.content) for part in parts]
        
        return {
            'total_parts': len(parts),
            'total_length': sum(lengths),
            'average_length': sum(lengths) / len(lengths),
            'min_length': min(lengths),
            'max_length': max(lengths),
            'parts_with_code_blocks': sum(1 for part in parts 
                                        if any(block.type == BlockType.CODE_BLOCK 
                                              for block in part.markdown_blocks)),
            'parts_with_continuation': sum(1 for part in parts if part.has_continuation)
        }


def split_long_message(content: str, max_length: int = 2000) -> List[str]:
    """
    Convenience function to split a long message into parts.
    
    Args:
        content: The message content to split
        max_length: Maximum length for each part
        
    Returns:
        List of message content strings
    """
    splitter = MessageSplitter(max_length=max_length)
    parts = splitter.split_message(content)
    return [part.content for part in parts]