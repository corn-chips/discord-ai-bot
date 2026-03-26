"""
User Experience Service for enhanced Discord bot interactions.

This module provides enhanced user feedback including typing indicators,
rich embeds, reaction-based feedback, and progress updates.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Dict, Any, Tuple

import discord

from ..config import BotConfig


logger = logging.getLogger(__name__)


class Status(Enum):
    """Status types for embed creation."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PROCESSING = "processing"


class ReactionType(Enum):
    """Types of reaction feedback."""
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    PROCESSING = "⏳"
    THINKING = "🤔"
    COMPLETED = "✨"


class UserExperienceService:
    """
    Service for providing enhanced user experience features.
    
    Implements requirements 4.1, 4.2, 4.3 for typing indicators,
    rich Discord embeds, and reaction-based feedback.
    """
    
    def __init__(self, config: BotConfig):
        """
        Initialize the user experience service.
        
        Args:
            config: Bot configuration containing UX settings
        """
        self.config = config
        self.active_typing_tasks: Dict[int, Tuple[Any, datetime]] = {}
        self._typing_entry_ttl = timedelta(minutes=5)
        
        # Color mapping for different status types
        self.status_colors = {
            Status.SUCCESS: discord.Color.green(),
            Status.ERROR: discord.Color.red(),
            Status.WARNING: discord.Color.orange(),
            Status.INFO: discord.Color.blue(),
            Status.PROCESSING: discord.Color.purple()
        }
        
        # Emoji mapping for status types
        self.status_emojis = {
            Status.SUCCESS: "✅",
            Status.ERROR: "❌",
            Status.WARNING: "⚠️",
            Status.INFO: "ℹ️",
            Status.PROCESSING: "⏳"
        }
    
    def _task_error_handler(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from detached tasks."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception as callback_exc:
            logger.error(f"Failed to inspect detached task exception: {callback_exc}")
            return
        if exc:
            logger.error(
                f"Unhandled detached task exception in UX service: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def _close_typing_entry(self, entry: Any) -> None:
        """Close or cancel a stored typing entry."""
        if isinstance(entry, asyncio.Task):
            entry.cancel()
            try:
                await entry
            except asyncio.CancelledError:
                pass
        else:
            await entry.__aexit__(None, None, None)

    async def _sweep_stale_typing_entries(self) -> None:
        """Remove stale typing entries to avoid unbounded growth."""
        cutoff = datetime.now(timezone.utc) - self._typing_entry_ttl
        stale_channel_ids = [
            channel_id
            for channel_id, (_entry, created_at) in self.active_typing_tasks.items()
            if created_at < cutoff
        ]

        for channel_id in stale_channel_ids:
            entry, _created_at = self.active_typing_tasks.pop(channel_id, (None, None))
            if entry is None:
                continue
            try:
                await self._close_typing_entry(entry)
            except Exception as e:
                logger.debug(f"Failed to clean stale typing indicator for channel {channel_id}: {e}")

    async def show_typing_indicator(self, channel: discord.abc.Messageable, duration: int = 0):
        """
        Show typing indicator for long operations.
        
        Implements requirement 4.1: Add typing indicators for long operations.
        
        Args:
            channel: Discord channel to show typing in
            duration: Duration in seconds (0 for indefinite)
        """
        if not self.config.show_typing_indicators:
            return
        
        try:
            channel_id = channel.id
            await self._sweep_stale_typing_entries()
            
            # Cancel any existing typing task for this channel
            if channel_id in self.active_typing_tasks:
                existing_entry, _created_at = self.active_typing_tasks.pop(channel_id)
                await self._close_typing_entry(existing_entry)
            
            # Start typing
            typing_context = channel.typing()
            await typing_context.__aenter__()
            
            if duration > 0:
                # Create task to stop typing after duration
                async def stop_typing_after_delay():
                    try:
                        await asyncio.sleep(duration)
                        await typing_context.__aexit__(None, None, None)
                        if channel_id in self.active_typing_tasks:
                            del self.active_typing_tasks[channel_id]
                    except asyncio.CancelledError:
                        await typing_context.__aexit__(None, None, None)
                        raise
                
                task = asyncio.create_task(stop_typing_after_delay())
                task.add_done_callback(self._task_error_handler)
                self.active_typing_tasks[channel_id] = (task, datetime.now(timezone.utc))
            else:
                # Store the typing context for manual stopping
                self.active_typing_tasks[channel_id] = (typing_context, datetime.now(timezone.utc))
            
            logger.debug(f"Started typing indicator in channel {channel_id}")
            
        except discord.Forbidden:
            logger.warning(f"No permission to show typing indicator in channel {channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to show typing indicator: {e}")
        except Exception as e:
            logger.error(f"Unexpected error showing typing indicator: {e}", exc_info=True)
    
    async def stop_typing_indicator(self, channel: discord.abc.Messageable):
        """
        Stop typing indicator for a channel.
        
        Args:
            channel: Discord channel to stop typing in
        """
        channel_id = channel.id
        
        if channel_id in self.active_typing_tasks:
            try:
                task_or_context, _created_at = self.active_typing_tasks.pop(channel_id)
                await self._close_typing_entry(task_or_context)
                logger.debug(f"Stopped typing indicator in channel {channel_id}")
                
            except Exception as e:
                logger.error(f"Error stopping typing indicator: {e}", exc_info=True)
    
    def create_status_embed(
        self,
        title: str,
        description: str,
        status: Status,
        fields: Optional[Dict[str, Any]] = None,
        footer: Optional[str] = None,
        thumbnail_url: Optional[str] = None
    ) -> discord.Embed:
        """
        Create rich Discord embed for status updates and help.
        
        Implements requirement 4.2: Create rich Discord embeds for status updates and help.
        
        Args:
            title: Embed title
            description: Embed description
            status: Status type for color and emoji
            fields: Optional dictionary of field names to values
            footer: Optional footer text
            thumbnail_url: Optional thumbnail URL
            
        Returns:
            Configured Discord embed
        """
        if not self.config.use_rich_embeds:
            # Return a simple embed if rich embeds are disabled
            return discord.Embed(
                title=title,
                description=description,
                color=discord.Color.default()
            )
        
        # Get color and emoji for status
        color = self.status_colors.get(status, discord.Color.default())
        emoji = self.status_emojis.get(status, "")
        
        # Create embed with status emoji in title
        embed_title = f"{emoji} {title}" if emoji else title
        embed = discord.Embed(
            title=embed_title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Add fields if provided
        if fields:
            for field_name, field_data in fields.items():
                if isinstance(field_data, dict):
                    value = field_data.get('value', 'N/A')
                    inline = field_data.get('inline', False)
                else:
                    value = str(field_data)
                    inline = False
                
                embed.add_field(
                    name=field_name,
                    value=value[:1024],  # Discord field value limit
                    inline=inline
                )
        
        # Add footer if provided
        if footer:
            embed.set_footer(text=footer)
        
        # Add thumbnail if provided
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        
        return embed
    
    async def add_reaction_feedback(
        self,
        message: discord.Message,
        reaction_type: ReactionType,
        remove_after: Optional[int] = None
    ):
        """
        Add reaction-based feedback for operations.
        
        Implements requirement 4.3: Implement reaction-based feedback for operations.
        
        Args:
            message: Message to add reaction to
            reaction_type: Type of reaction to add
            remove_after: Optional seconds after which to remove reaction
        """
        if not self.config.enable_reaction_feedback:
            return
        
        try:
            # Add the reaction
            await message.add_reaction(reaction_type.value)
            logger.debug(f"Added reaction {reaction_type.value} to message {message.id}")
            
            # Remove reaction after delay if specified
            if remove_after:
                async def remove_reaction_after_delay():
                    try:
                        await asyncio.sleep(remove_after)
                        bot_member = None
                        if message.guild and message.guild.me:
                            bot_member = message.guild.me
                        else:
                            bot_member = getattr(getattr(message, "_state", None), "user", None)

                        if bot_member is not None:
                            await message.remove_reaction(reaction_type.value, bot_member)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        # Reaction might already be removed or no permission
                        pass
                    except Exception as e:
                        logger.error(f"Error removing reaction: {e}")
                
                task = asyncio.create_task(remove_reaction_after_delay())
                task.add_done_callback(self._task_error_handler)
        
        except discord.Forbidden:
            logger.warning(f"No permission to add reaction to message {message.id}")
        except discord.NotFound:
            logger.warning(f"Message {message.id} not found for reaction")
        except discord.HTTPException as e:
            logger.error(f"Failed to add reaction: {e}")
        except Exception as e:
            logger.error(f"Unexpected error adding reaction: {e}", exc_info=True)
    
    async def send_progress_update(
        self,
        channel: discord.abc.Messageable,
        progress: int,
        total: int,
        operation: str = "Processing",
        message_to_edit: Optional[discord.Message] = None
    ) -> Optional[discord.Message]:
        """
        Send or update a progress indicator message.
        
        Args:
            channel: Channel to send progress update to
            progress: Current progress value
            total: Total progress value
            operation: Description of the operation
            message_to_edit: Optional existing message to edit instead of sending new
            
        Returns:
            The progress message (new or edited)
        """
        try:
            # Calculate percentage
            percentage = min(100, (progress / total) * 100) if total > 0 else 0
            
            # Create progress bar
            bar_length = 20
            filled_length = int(bar_length * percentage / 100)
            bar = "█" * filled_length + "░" * (bar_length - filled_length)
            
            # Create embed
            embed = self.create_status_embed(
                title=f"{operation} Progress",
                description=f"**Progress:** {progress}/{total} ({percentage:.1f}%)\n"
                           f"```{bar}```",
                status=Status.PROCESSING if percentage < 100 else Status.SUCCESS
            )
            
            if message_to_edit:
                # Edit existing message
                await message_to_edit.edit(embed=embed)
                return message_to_edit
            else:
                # Send new message
                return await channel.send(embed=embed)
        
        except discord.Forbidden:
            logger.warning(f"No permission to send progress update in channel {channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send progress update: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending progress update: {e}", exc_info=True)
        
        return None
    
    async def send_processing_notification(
        self,
        channel: discord.abc.Messageable,
        operation: str,
        estimated_time: Optional[int] = None
    ) -> Optional[discord.Message]:
        """
        Send a processing notification with estimated time.
        
        Args:
            channel: Channel to send notification to
            operation: Description of the operation
            estimated_time: Estimated time in seconds
            
        Returns:
            The notification message
        """
        try:
            description = f"Starting {operation.lower()}..."
            
            if estimated_time:
                if estimated_time < 60:
                    time_str = f"{estimated_time} seconds"
                else:
                    minutes = estimated_time // 60
                    seconds = estimated_time % 60
                    time_str = f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
                
                description += f"\n⏱️ Estimated time: {time_str}"
            
            embed = self.create_status_embed(
                title="Processing Request",
                description=description,
                status=Status.PROCESSING
            )
            
            message = await channel.send(embed=embed)
            
            # Add processing reaction
            await self.add_reaction_feedback(message, ReactionType.PROCESSING)
            
            return message
        
        except Exception as e:
            logger.error(f"Error sending processing notification: {e}", exc_info=True)
            return None
    
    async def send_completion_notification(
        self,
        message_to_edit: discord.Message,
        operation: str,
        success: bool,
        details: Optional[str] = None,
        processing_time: Optional[float] = None
    ):
        """
        Update a processing message with completion status.
        
        Args:
            message_to_edit: Message to edit with completion status
            operation: Description of the operation
            success: Whether the operation was successful
            details: Optional additional details
            processing_time: Optional processing time in seconds
        """
        try:
            status = Status.SUCCESS if success else Status.ERROR
            title = f"{operation} {'Complete' if success else 'Failed'}"
            
            description_parts = []
            if success:
                description_parts.append(f"✅ {operation} completed successfully!")
            else:
                description_parts.append(f"❌ {operation} failed.")
            
            if details:
                description_parts.append(f"\n{details}")
            
            if processing_time:
                description_parts.append(f"\n⏱️ Processing time: {processing_time:.2f}s")
            
            embed = self.create_status_embed(
                title=title,
                description="".join(description_parts),
                status=status
            )
            
            await message_to_edit.edit(embed=embed)
            
            # Update reaction
            reaction_type = ReactionType.COMPLETED if success else ReactionType.ERROR
            await self.add_reaction_feedback(message_to_edit, reaction_type)
        
        except Exception as e:
            logger.error(f"Error sending completion notification: {e}", exc_info=True)
    
    def create_help_embed(
        self,
        title: str = "Bot Help",
        sections: Optional[Dict[str, str]] = None
    ) -> discord.Embed:
        """
        Create a standardized help embed.
        
        Args:
            title: Help embed title
            sections: Dictionary of section names to content
            
        Returns:
            Formatted help embed
        """
        embed = self.create_status_embed(
            title=title,
            description="Here's how you can use this bot:",
            status=Status.INFO
        )
        
        if sections:
            for section_name, content in sections.items():
                embed.add_field(
                    name=section_name,
                    value=content[:1024],  # Discord field limit
                    inline=False
                )
        
        embed.set_footer(text="Use /help for more detailed information")
        
        return embed
    
    async def cleanup_typing_indicators(self):
        """Clean up any remaining typing indicators."""
        for channel_id, (task_or_context, _created_at) in list(self.active_typing_tasks.items()):
            try:
                await self._close_typing_entry(task_or_context)
            except Exception as e:
                logger.error(f"Error cleaning up typing indicator: {e}")
        
        self.active_typing_tasks.clear()
