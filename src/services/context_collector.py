"""
Context collection service for the Discord Grok Bot.

This module handles collecting and processing Discord message context
for AI response generation, including message history retrieval and
reply context enhancement.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Set
import discord
from ..models.data_models import MessageContext
from ..utils.logging_config import TimingContext


class ContextCollector:
    """
    Collects and processes Discord message context for AI processing.
    
    Handles retrieving message history, filtering by time limits,
    and enhancing context for reply-based interactions.
    """
    
    def __init__(self, max_context_messages: int = 100, reply_context_range: int = 10):
        """
        Initialize the ContextCollector.
        
        Args:
            max_context_messages: Maximum number of messages to retrieve for context
            reply_context_range: Number of messages before/after a replied-to message
        """
        self.max_context_messages = max_context_messages
        self.reply_context_range = reply_context_range
        self.logger = logging.getLogger(__name__)
    
    async def get_channel_context(self, channel: discord.TextChannel, limit: int = None, bot_user: discord.User = None) -> List[MessageContext]:
        """
        Retrieve recent message history from a Discord channel.
        
        Implements requirements 2.1, 2.2, 2.4, 2.5:
        - Retrieves past messages from the current channel
        - Excludes messages older than 24 hours
        - Limits to specified number of messages
        - Handles channels with fewer messages than the limit
        
        Args:
            channel: Discord channel to retrieve messages from
            limit: Maximum number of messages to retrieve (defaults to max_context_messages)
            bot_user: The bot user object (to include bot's own messages)
            
        Returns:
            List of MessageContext objects representing recent messages
        """
        if limit is None:
            limit = self.max_context_messages
            
        # Calculate 24-hour cutoff time
        cutoff_time = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(hours=24)
        
        messages = []
        message_count = 0
        
        try:
            with TimingContext(self.logger, f"Retrieving {limit} messages from channel", 
                             channel_id=channel.id, channel_name=channel.name):
                # Retrieve messages from the channel
                async for message in channel.history(limit=limit * 2):  # Get extra to account for filtering
                    # Skip messages older than 24 hours
                    if message.created_at < cutoff_time:
                        continue
                        
                    # Skip bot messages unless it's us
                    if message.author.bot:
                        if not bot_user or message.author.id != bot_user.id:
                            continue
                    
                    # Skip messages with no text content AND no attachments
                    if (not message.content or not message.content.strip()) and not message.attachments:
                        self.logger.debug(f"Skipping message {message.id} with no text content or attachments")
                        continue
                    
                    # Convert Discord message to MessageContext
                    message_context = MessageContext(
                        content=message.content,
                        author=message.author.display_name,
                        timestamp=message.created_at,
                        message_id=message.id,
                        is_reply=message.reference is not None,
                        replied_to_id=message.reference.message_id if message.reference else None
                    )
                    
                    messages.append(message_context)
                    message_count += 1
                    
                    # Stop when we have enough messages
                    if message_count >= limit:
                        break
                        
        except discord.Forbidden:
            # Handle case where bot doesn't have permission to read message history
            self.logger.warning(f"No permission to read message history in channel {channel.id}")
            return []
        except discord.HTTPException as e:
            # Handle other Discord API errors
            self.logger.error(f"Discord API error retrieving messages: {e}")
            return []
        
        # Return messages in chronological order (oldest first)
        return list(reversed(messages))    

    async def get_reply_context(self, message: discord.Message) -> List[MessageContext]:
        """
        Get enhanced context for reply-based interactions.
        
        Implements requirements 3.1, 3.2, 3.3, 3.5:
        - Identifies replied-to messages
        - Retrieves messages before and after the replied-to message
        - Includes reply context in addition to standard context
        - Avoids duplicate messages in context windows
        
        Args:
            message: The Discord message that contains a reply
            
        Returns:
            List of MessageContext objects representing reply context
        """
        if not message.reference or not message.reference.message_id:
            return []
        
        try:
            with TimingContext(self.logger, "Retrieving reply context", 
                             message_id=message.id, replied_to_id=message.reference.message_id):
                # Get the replied-to message
                replied_to_message = await message.channel.fetch_message(message.reference.message_id)
                
                # Get messages around the replied-to message
                reply_context = []
                
                # Get messages before the replied-to message
                before_messages = []
                async for msg in message.channel.history(
                    limit=self.reply_context_range,
                    before=replied_to_message,
                    oldest_first=False
                ):
                    if not msg.author.bot:  # Skip bot messages
                        before_messages.append(msg)
                
                # Add messages before (in chronological order)
                for msg in reversed(before_messages):
                    # Skip messages with no text content
                    if not msg.content or not msg.content.strip():
                        continue
                    reply_context.append(MessageContext(
                        content=msg.content,
                        author=msg.author.display_name,
                        timestamp=msg.created_at,
                        message_id=msg.id,
                        is_reply=msg.reference is not None,
                        replied_to_id=msg.reference.message_id if msg.reference else None
                    ))
                
                # Add the replied-to message itself (if it has text content)
                if replied_to_message.content and replied_to_message.content.strip():
                    reply_context.append(MessageContext(
                        content=replied_to_message.content,
                        author=replied_to_message.author.display_name,
                        timestamp=replied_to_message.created_at,
                        message_id=replied_to_message.id,
                        is_reply=replied_to_message.reference is not None,
                        replied_to_id=replied_to_message.reference.message_id if replied_to_message.reference else None
                    ))
                
                # Get messages after the replied-to message
                async for msg in message.channel.history(
                    limit=self.reply_context_range,
                    after=replied_to_message,
                    oldest_first=True
                ):
                    if not msg.author.bot and msg.id != message.id:  # Skip bot messages and the original message
                        # Skip messages with no text content
                        if not msg.content or not msg.content.strip():
                            continue
                        reply_context.append(MessageContext(
                            content=msg.content,
                            author=msg.author.display_name,
                            timestamp=msg.created_at,
                            message_id=msg.id,
                            is_reply=msg.reference is not None,
                            replied_to_id=msg.reference.message_id if msg.reference else None
                        ))
                
                return reply_context
            
        except discord.NotFound:
            # Replied-to message was deleted
            self.logger.warning(f"Replied-to message {message.reference.message_id} not found")
            return []
        except discord.Forbidden:
            # No permission to access the message
            self.logger.warning(f"No permission to access replied-to message {message.reference.message_id}")
            return []
        except discord.HTTPException as e:
            # Other Discord API errors
            self.logger.error(f"Discord API error retrieving reply context: {e}")
            return []
    
    def _remove_duplicate_messages(self, standard_context: List[MessageContext], 
                                 reply_context: List[MessageContext]) -> List[MessageContext]:
        """
        Remove duplicate messages between standard and reply context.
        
        Implements requirement 3.5: Avoid duplicate messages in context windows.
        
        Args:
            standard_context: Messages from standard channel context
            reply_context: Messages from reply context enhancement
            
        Returns:
            Combined context with duplicates removed, prioritizing reply context
        """
        # Create a set of message IDs from reply context
        reply_message_ids: Set[int] = {msg.message_id for msg in reply_context}
        
        # Filter out duplicates from standard context
        filtered_standard = [
            msg for msg in standard_context 
            if msg.message_id not in reply_message_ids
        ]
        
        # Combine contexts with reply context first (higher priority)
        return reply_context + filtered_standard
    
    def format_context(self, messages: List[MessageContext]) -> str:
        """
        Format message context for Gemini API consumption.
        
        Implements requirements 2.3, 3.4:
        - Structures messages chronologically with usernames and timestamps
        - Prioritizes reply context in prompt structure
        - Includes metadata for optimal AI processing
        
        Args:
            messages: List of MessageContext objects to format
            
        Returns:
            Formatted string suitable for Gemini API input
        """
        if not messages:
            return "No recent conversation context available."
        
        # Sort messages chronologically (oldest first)
        sorted_messages = sorted(messages, key=lambda msg: msg.timestamp)
        
        formatted_lines = ["=== CONVERSATION CONTEXT ==="]
        
        for msg in sorted_messages:
            # Format timestamp for readability
            time_str = msg.timestamp.strftime("%H:%M")
            
            # Add reply indicator if this is a reply
            reply_indicator = " (replying)" if msg.is_reply else ""
            
            # Format the message line
            formatted_line = f"[{time_str}] {msg.author}{reply_indicator}: {msg.content}"
            formatted_lines.append(formatted_line)
        
        formatted_lines.append("=== END CONTEXT ===")
        
        return "\n".join(formatted_lines)
    
    async def collect_full_context(self, message: discord.Message) -> str:
        """
        Collect and format complete context for a Discord message.
        
        This is the main method that combines all context collection functionality
        to provide a complete context string for AI processing.
        
        Args:
            message: The Discord message to collect context for
            
        Returns:
            Formatted context string ready for Gemini API
        """
        # Get standard channel context
        standard_context = await self.get_channel_context(message.channel)
        
        # Get enhanced reply context if this is a reply
        reply_context = []
        if message.reference:
            reply_context = await self.get_reply_context(message)
        
        # Combine contexts, removing duplicates
        if reply_context:
            combined_context = self._remove_duplicate_messages(standard_context, reply_context)
        else:
            combined_context = standard_context
        
        # Format for API consumption
        return self.format_context(combined_context)