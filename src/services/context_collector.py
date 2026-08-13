"""
Context collection service for the Discord Grok Bot.

This module handles collecting and processing Discord message context
for AI response generation, including message history retrieval and
reply context enhancement.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Set
import discord
from ..models.data_models import MessageContext
from ..utils.logging_config import TimingContext

# `<@id>`, the legacy nickname form `<@!id>`, roles `<@&id>` and channels `<#id>`.
_MENTION_MARKUP = re.compile(r"<(@[!&]?|#)(\d+)>")


def _entity_name(entity) -> str:
    return getattr(entity, "display_name", None) or getattr(entity, "name", None) or ""


def _resolve_mentions(message, text: str) -> str:
    """Rewrite mention markup as `@name (id)` so both forms tokenize.

    Indexed text kept mentions raw, so FTS held only the bare snowflake and a
    question naming the person matched none of it; the id stays because that is
    what a mention in a *query* still tokenizes to. Runs against SimpleNamespace
    fakes too, so an unresolved mention keeps its markup rather than raising.
    """
    if not text or "<" not in text:
        return text

    def by_id(entities) -> dict:
        return {
            str(getattr(entity, "id", "")): _entity_name(entity)
            for entity in (entities or [])
            if getattr(entity, "id", None) is not None
        }

    try:
        users = by_id(getattr(message, "mentions", None))
        roles = by_id(getattr(message, "role_mentions", None))
        channels = by_id(getattr(message, "channel_mentions", None))
        guild = getattr(message, "guild", None)

        def from_guild(getter_name: str, raw_id: str) -> str:
            getter = getattr(guild, getter_name, None)
            return _entity_name(getter(int(raw_id))) if callable(getter) else ""

        def replace(match) -> str:
            kind, raw_id = match.group(1), match.group(2)
            if kind == "#":
                name, prefix = channels.get(raw_id) or from_guild("get_channel", raw_id), "#"
            elif kind == "@&":
                name, prefix = roles.get(raw_id) or from_guild("get_role", raw_id), "@"
            else:
                name, prefix = users.get(raw_id) or from_guild("get_member", raw_id), "@"
            return f"{prefix}{name} ({raw_id})" if name else match.group(0)

        return _MENTION_MARKUP.sub(replace, text)
    except Exception:
        return text


class ContextCollector:
    """
    Collects and processes Discord message context for AI processing.
    
    Handles retrieving message history, filtering by time limits,
    and enhancing context for reply-based interactions.
    """
    
    def __init__(self, max_context_messages: int = 100, reply_context_range: int = 10,
                 cutoff_hours: int = 24):
        """
        Initialize the ContextCollector.

        Args:
            max_context_messages: Maximum number of messages to retrieve for context
            reply_context_range: Number of messages before/after a replied-to message
            cutoff_hours: Exclude messages older than this many hours
        """
        self.max_context_messages = max_context_messages
        self.reply_context_range = reply_context_range
        self.cutoff_hours = cutoff_hours
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _build_message_content(message: discord.Message) -> str:
        """
        Build message content with attachment metadata for AI context.

        Adds metadata so the model can reference non-text, forwarded, and system messages.
        """
        base_content = (message.content or "").strip()
        if not base_content:
            # For system/forwarded messages, Discord may populate system_content but not content.
            base_content = (getattr(message, "system_content", "") or "").strip()
        base_content = _resolve_mentions(message, base_content)

        image_names = []
        other_names = []
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                image_names.append(attachment.filename)
            else:
                other_names.append(attachment.filename)

        suffix_parts = [f"Message type: {getattr(message.type, 'name', str(message.type))}"]
        if image_names:
            suffix_parts.append(f"Attached images: {', '.join(image_names)}")
        if other_names:
            suffix_parts.append(f"Attached files: {', '.join(other_names)}")

        # Include embed/sticker hints for non-text messages.
        if message.embeds:
            suffix_parts.append(f"Embeds: {len(message.embeds)}")
        if message.stickers:
            sticker_names = [sticker.name for sticker in message.stickers if getattr(sticker, "name", None)]
            if sticker_names:
                suffix_parts.append(f"Stickers: {', '.join(sticker_names)}")
            else:
                suffix_parts.append(f"Stickers: {len(message.stickers)}")

        # Include forwarded message snapshots so the model can read forwarded context.
        snapshots = getattr(message, "message_snapshots", None) or []
        if snapshots:
            snapshot_summaries = []
            for idx, snapshot in enumerate(snapshots, start=1):
                snapshot_text = (getattr(snapshot, "content", "") or "").strip()
                if snapshot_text:
                    if len(snapshot_text) > 180:
                        snapshot_text = f"{snapshot_text[:180]}..."
                    snapshot_summaries.append(f"Forwarded[{idx}]: {snapshot_text}")
                else:
                    snap_attachments = getattr(snapshot, "attachments", None) or []
                    snap_embeds = getattr(snapshot, "embeds", None) or []
                    snap_stickers = getattr(snapshot, "stickers", None) or []
                    snapshot_summaries.append(
                        f"Forwarded[{idx}] type={getattr(getattr(snapshot, 'type', None), 'name', 'unknown')}, "
                        f"attachments={len(snap_attachments)}, embeds={len(snap_embeds)}, stickers={len(snap_stickers)}"
                    )
            if snapshot_summaries:
                suffix_parts.extend(snapshot_summaries)

        metadata_suffix = f" [{' | '.join(suffix_parts)}]" if suffix_parts else ""
        return f"{base_content}{metadata_suffix}" if base_content else metadata_suffix.strip()

    def _to_message_context(self, message: discord.Message) -> MessageContext:
        """Convert a Discord message to MessageContext including attachment metadata."""
        return MessageContext(
            content=self._build_message_content(message),
            author=message.author.display_name,
            timestamp=message.created_at,
            message_id=message.id,
            channel_id=message.channel.id,
            is_reply=message.reference is not None,
            replied_to_id=message.reference.message_id if message.reference else None
        )
    
    async def get_channel_context(self, channel: discord.TextChannel, limit: int = None, bot_user: discord.User = None) -> List[MessageContext]:
        """
        Retrieve recent message history from a Discord channel.
        
        Implements requirements 2.1, 2.2, 2.4, 2.5:
        - Retrieves past messages from the current channel
        - Excludes messages older than 24 hours
        - Limits to specified number of messages
        - Handles channels with fewer messages than the limit
        - Excludes all bot messages (including our own)
        
        Args:
            channel: Discord channel to retrieve messages from
            limit: Maximum number of messages to retrieve (defaults to max_context_messages)
            bot_user: The bot user object (unused, kept for backwards compatibility)
            
        Returns:
            List of MessageContext objects representing recent messages
        """
        if limit is None:
            limit = self.max_context_messages
            
        # Calculate 24-hour cutoff time using timezone-aware datetime
        from datetime import timezone
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=self.cutoff_hours)
        
        messages = []
        message_count = 0
        
        try:
            with TimingContext(self.logger, f"Retrieving {limit} messages from channel",
                             channel_id=channel.id, channel_name=getattr(channel, 'name', 'DM')):
                # Retrieve messages from the channel
                async for message in channel.history(limit=limit + 20):  # Small buffer to account for filtering
                    # Skip messages older than 24 hours
                    if message.created_at < cutoff_time:
                        continue
                        
                    # Skip ALL bot messages (including our own) to avoid context pollution
                    if message.author.bot:
                        continue
                    
                    # Convert Discord message to MessageContext
                    message_context = self._to_message_context(message)
                    
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
                    reply_context.append(self._to_message_context(msg))
                
                # Add the replied-to message itself
                reply_context.append(self._to_message_context(replied_to_message))
                
                # Get messages after the replied-to message
                async for msg in message.channel.history(
                    limit=self.reply_context_range,
                    after=replied_to_message,
                    oldest_first=True
                ):
                    if not msg.author.bot and msg.id != message.id:  # Skip bot messages and the original message
                        reply_context.append(self._to_message_context(msg))
                
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
