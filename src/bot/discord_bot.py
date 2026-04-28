"""
Discord bot implementation for the Discord Grok Bot.

This module contains the main DiscordBot class that handles Discord events,
message processing, and coordinates with other services to provide AI responses.
"""

import asyncio
import io
import logging
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Tuple, Callable, Awaitable, Deque

import discord
from PIL import Image
import fitz  # PyMuPDF

from ..config import BotConfig
from ..constants import (
    SUPPORTED_TEXT_EXTENSIONS,
    RGB_WHITE_BACKGROUND,
)
from ..models.data_models import APIResponse, ImageEditRequest, EditType, TokenUsage, MessageContext
from ..services.context_collector import ContextCollector
from ..services.gemini_client import GeminiClient
from ..services.message_splitter import MessageSplitter
from ..services.image_processing_service import ImageProcessingService
from ..services.user_experience_service import UserExperienceService
from ..services.token_tracker import TokenTracker
from ..services.content_renderer import ContentRenderer
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, TimingContext, get_logger_with_context
from .commands import setup_commands
from .enhanced_command_handler import EnhancedCommandHandler


logger = logging.getLogger(__name__)


class SplitResponsePaginatorView(discord.ui.View):
    """Simple paginator for split responses with sender-priority navigation."""

    def __init__(
        self,
        pages: List[str],
        sender_user_id: int,
        title: str,
        priority_window_seconds: float = 2.0,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.sender_user_id = sender_user_id
        self.title = title
        self.priority_window_seconds = priority_window_seconds
        self.current_page_index = 0
        self.last_sender_click_time: Optional[datetime] = None
        self._interaction_lock = asyncio.Lock()
        self.message: Optional[discord.Message] = None
        self._update_button_states()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description=self.pages[self.current_page_index],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page {self.current_page_index + 1}/{len(self.pages)}")
        return embed

    def _update_button_states(self, *, disable_all: bool = False) -> None:
        self.previous_page.disabled = disable_all or self.current_page_index <= 0
        self.next_page.disabled = disable_all or self.current_page_index >= len(self.pages) - 1

    def _priority_window_remaining(self) -> float:
        if self.last_sender_click_time is None:
            return 0.0
        elapsed = (datetime.now(timezone.utc) - self.last_sender_click_time).total_seconds()
        return max(0.0, self.priority_window_seconds - elapsed)

    async def _handle_navigation(self, interaction: discord.Interaction, delta: int) -> None:
        async with self._interaction_lock:
            if interaction.user.id != self.sender_user_id:
                remaining = self._priority_window_remaining()
                if remaining > 0:
                    await interaction.response.send_message(
                        f"The message sender has priority for {remaining:.1f}s.",
                        ephemeral=True,
                    )
                    return

            new_index = self.current_page_index + delta
            if new_index < 0 or new_index >= len(self.pages):
                await interaction.response.defer()
                return

            self.current_page_index = new_index
            if interaction.user.id == self.sender_user_id:
                self.last_sender_click_time = datetime.now(timezone.utc)

            self._update_button_states()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="\u2B05\uFE0F", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_navigation(interaction, -1)

    @discord.ui.button(label="\u27A1\uFE0F", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_navigation(interaction, 1)

    async def on_timeout(self) -> None:
        self._update_button_states(disable_all=True)
        if not self.message:
            return
        try:
            await self.message.edit(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class TextRateLimiter:
    """Per-user text request limiter with minute/hour windows."""

    def __init__(self, per_minute: int, per_hour: int):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._requests: Dict[int, List[datetime]] = {}
        self._lock = asyncio.Lock()

    async def check_and_record(self, user_id: int) -> tuple[bool, Optional[str]]:
        """Validate and record a request for the given user."""
        now = datetime.now(timezone.utc)
        minute_cutoff = now - timedelta(minutes=1)
        hour_cutoff = now - timedelta(hours=1)

        async with self._lock:
            # Prune expired entries for all users to prevent unbounded growth.
            for existing_user_id, timestamps in list(self._requests.items()):
                recent = [ts for ts in timestamps if ts >= hour_cutoff]
                if recent:
                    self._requests[existing_user_id] = recent
                else:
                    del self._requests[existing_user_id]

            requests = self._requests.get(user_id, [])

            minute_count = sum(1 for ts in requests if ts >= minute_cutoff)
            hour_count = len(requests)

            if minute_count >= self.per_minute:
                oldest_minute = min(ts for ts in requests if ts >= minute_cutoff)
                retry_after = oldest_minute + timedelta(minutes=1)
                retry_seconds = max(1, int((retry_after - now).total_seconds()))
                return False, (
                    f"Text rate limit reached ({self.per_minute}/minute). "
                    f"Please wait about {retry_seconds}s and try again."
                )

            if hour_count >= self.per_hour:
                oldest_hour = min(requests)
                retry_after = oldest_hour + timedelta(hours=1)
                retry_minutes = max(1, int((retry_after - now).total_seconds() // 60) + 1)
                return False, (
                    f"Text rate limit reached ({self.per_hour}/hour). "
                    f"Please try again in about {retry_minutes} minute(s)."
                )

            requests.append(now)
            self._requests[user_id] = requests

        return True, None


class DiscordBot(discord.Client):
    """
    Main Discord bot class that handles events and coordinates services.
    
    Extends discord.Client to handle Discord events and integrates with
    ContextCollector and GeminiClient to provide AI-powered responses.
    """
    
    def __init__(self, config: BotConfig):
        """
        Initialize the Discord bot with configuration and services.
        
        Args:
            config: Bot configuration containing tokens and settings
        """
        # Configure Discord client intents
        intents = discord.Intents.default()
        intents.message_content = True  # Required to read message content
        intents.messages = True  # Required to receive message events
        
        super().__init__(intents=intents)
        
        self.config = config
        self.error_manager = ErrorManager(config)
        self.performance_logger = PerformanceLogger("discord_bot")
        self.token_tracker = None
        try:
            self.token_tracker = TokenTracker(config.token_db_path)
            logger.info("✅ Token tracker initialized")
        except Exception as exc:
            logger.error(f"Failed to initialize token tracker: {exc}", exc_info=True)
            self.token_tracker = None
        
        # Initialize core services
        self.context_collector = ContextCollector(
            max_context_messages=config.max_context_messages,
            reply_context_range=config.reply_context_range,
            cutoff_hours=config.context_cutoff_hours
        )
        self.gemini_client = GeminiClient(config)
        self.message_splitter = MessageSplitter(
            max_length=config.message_split_length,
            preserve_formatting=config.preserve_code_blocks,
            add_continuation_indicators=config.add_continuation_indicators,
            continuation_overhead=config.continuation_overhead,
        )
        
        # Initialize content renderer for LaTeX and table formatting
        self.content_renderer = ContentRenderer()

        # Initialize UX enhancement services
        self.user_experience_service = UserExperienceService(config)
        
        # Initialize image processing service if configured
        self.image_processing_service = None
        self.image_generation_enabled = False
        if hasattr(config, 'nano_banana_api_key') and config.nano_banana_api_key:
            try:
                self.image_processing_service = ImageProcessingService(config)
                self.image_generation_enabled = True
                logger.info("✅ Image processing service initialized")
            except Exception as e:
                logger.error(f"Failed to initialize image processing service: {e}")
                logger.warning("Image editing features will be disabled")

        self.text_rate_limiter = TextRateLimiter(
            per_minute=config.text_rate_limit_per_minute,
            per_hour=config.text_rate_limit_per_hour,
        )
        
        # Initialize enhanced command handler
        self.enhanced_command_handler = None
        if self.image_processing_service:
            try:
                self.enhanced_command_handler = EnhancedCommandHandler(
                    self,
                    self.image_processing_service,
                    self.error_manager,
                    self.gemini_client,
                )
                logger.info("✅ Enhanced command handler initialized")
            except Exception as e:
                logger.error(f"Failed to initialize enhanced command handler: {e}")
                logger.warning("Enhanced command features will be disabled")
        
        self.start_time = datetime.now()
        
        # Set up command tree for slash commands
        self.tree = discord.app_commands.CommandTree(self)

        # Live mode (channel-isolated, mention-free) runtime state
        self._live_model_name = config.router_model_name
        self._live_cooldown_seconds = 2.0
        self._live_reply_style_instruction = (
            "Keep replies very short and natural, like normal chatting. "
            "Use 1-2 brief sentences unless the user explicitly asks for detail."
        )
        self._live_turn_window = 6  # 6 turns => up to 12 rolling messages (user+assistant)
        self._live_channel_tasks: Dict[int, asyncio.Task] = {}
        self._live_pending_messages: Dict[int, List[discord.Message]] = {}
        self._live_channel_locks: Dict[int, asyncio.Lock] = {}
        self._live_channel_context: Dict[int, Deque[MessageContext]] = {}
        
        logger.info("DiscordBot initialized with configuration")
    
    def _convert_image_to_rgb(self, image: Image.Image) -> Image.Image:
        """
        Convert a PIL Image to RGB mode, handling RGBA transparency.
        
        Args:
            image: PIL Image object to convert
            
        Returns:
            RGB-mode PIL Image
        """
        if image.mode == 'RGBA':
            # Create white background and paste image with alpha mask
            background = Image.new('RGB', image.size, RGB_WHITE_BACKGROUND)
            background.paste(image, mask=image.split()[3])
            return background
        elif image.mode not in ['RGB', 'L']:
            # Convert other modes to RGB
            return image.convert('RGB')
        return image
    
    async def on_ready(self):
        """
        Event handler called when the bot successfully connects to Discord.
        
        Implements requirement 5.5: Log startup information including
        connected guilds and user count.
        """
        logger.info(f"🤖 {self.user} has connected to Discord!")
        logger.info(f"Bot ID: {self.user.id}")
        
        # Log guild information
        guild_count = len(self.guilds)
        logger.info(f"Connected to {guild_count} guild(s):")
        
        total_members = 0
        for guild in self.guilds:
            member_count = guild.member_count or 0
            total_members += member_count
            logger.info(f"  • {guild.name} (ID: {guild.id}) - {member_count} members")
        
        logger.info(f"Total users across all guilds: {total_members}")
        
        # Start image processing service if available
        if self.image_processing_service:
            try:
                await self.image_processing_service.start()
                logger.info("✅ Image processing service started")
            except Exception as e:
                logger.error(f"Failed to start image processing service: {e}")
                self.image_processing_service = None
                self.image_generation_enabled = False
                # Disable enhanced command handler if image service fails
                self.enhanced_command_handler = None
        
        # Log service initialization status
        logger.info("🔧 Service initialization status:")
        logger.info(f"  • Context Collector: ✅ Active")
        logger.info(f"  • Gemini Client: ✅ Active")
        logger.info(f"  • Message Splitter: ✅ Active")
        logger.info(f"  • User Experience Service: ✅ Active")
        logger.info(f"  • Image Processing: {'✅ Active' if self.image_processing_service else '❌ Disabled'}")
        logger.info(f"  • Enhanced Commands: {'✅ Active' if self.enhanced_command_handler else '❌ Disabled'}")
        
        # Set bot status
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="@mentions for AI responses"
        )
        await self.change_presence(activity=activity)
        
        # Set up slash commands
        try:
            await setup_commands(self, self.config, self.gemini_client, self.performance_logger, self.token_tracker)
            
            # Sync commands globally
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash command(s) globally")
            
            # Log each synced command for verification
            for cmd in synced:
                logger.info(f"  ✓ Command synced: /{cmd.name} - {cmd.description}")
            
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}", exc_info=True)
        
        logger.info("✅ Bot is ready and listening for mentions!")
    
    async def on_error(self, event, *args, **kwargs):
        """
        Global error handler for Discord events.
        
        Captures unhandled exceptions from event listeners and logs them
        with full context.
        """
        logger.error(f"❌ Unhandled exception in event '{event}'", exc_info=True)
        
        # If we have args, log them for context (be careful with sensitive data)
        if args:
            logger.error(f"Event args: {args}")
        if kwargs:
            logger.error(f"Event kwargs: {kwargs}")

    async def get_service_health_status(self) -> Dict[str, str]:
        """
        Get current health status of all services.
        
        Returns:
            Dictionary mapping service names to their status
        """
        status = {}
        
        # Core services (always available)
        status['discord_connection'] = "✅ Connected" if not self.is_closed() else "❌ Disconnected"
        status['gemini_client'] = "✅ Available"
        status['context_collector'] = "✅ Available"
        status['message_splitter'] = "✅ Available"
        status['user_experience'] = "✅ Available"
        
        # Optional services
        if self.image_processing_service:
            try:
                # Check if image service is responsive
                health = await self.image_processing_service.get_service_health()
                status['image_processing'] = "✅ Available" if health else "⚠️ Degraded"
            except Exception as e:
                logger.warning(f"Image processing service health check failed: {e}")
                status['image_processing'] = "❌ Unavailable"
        else:
            status['image_processing'] = "⚪ Not configured"
        
        if self.enhanced_command_handler:
            status['enhanced_commands'] = "✅ Available"
        else:
            status['enhanced_commands'] = "❌ Unavailable"
        
        return status
    
    async def on_disconnect(self):
        """Event handler called when the bot disconnects from Discord."""
        logger.warning("🔌 Bot disconnected from Discord")
    
    async def on_resumed(self):
        """Event handler called when the bot resumes connection to Discord."""
        logger.info("🔄 Bot resumed connection to Discord")
    
    async def close(self):
        """Clean up resources when the bot is shutting down."""
        logger.info("🛑 Bot shutting down, cleaning up resources...")
        
        # Stop image processing service
        if self.image_processing_service:
            try:
                await self.image_processing_service.stop()
                logger.info("✅ Image processing service stopped")
            except Exception as e:
                logger.error(f"Error stopping image processing service: {e}")
        
        # Clean up user experience service
        if self.user_experience_service:
            try:
                await self.user_experience_service.cleanup_typing_indicators()
                logger.info("✅ User experience service cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up user experience service: {e}")
        
        # Call parent close method
        live_tasks = [task for task in self._live_channel_tasks.values() if not task.done()]
        if live_tasks:
            for task in live_tasks:
                task.cancel()
            await asyncio.gather(*live_tasks, return_exceptions=True)
            logger.info("Live channel workers stopped")

        await super().close()
        logger.info("✅ Bot shutdown complete")
    
    def _get_live_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Get or create a per-channel lock for live-mode state changes."""
        lock = self._live_channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._live_channel_locks[channel_id] = lock
        return lock

    def _get_live_context_buffer(self, channel_id: int) -> Deque[MessageContext]:
        """Get or create the rolling in-memory context buffer for a live channel."""
        buffer = self._live_channel_context.get(channel_id)
        if buffer is None:
            buffer = deque(maxlen=self._live_turn_window * 2)
            self._live_channel_context[channel_id] = buffer
        return buffer

    def _is_live_mode_enabled(self, channel_id: int) -> bool:
        """Return whether mention-free live mode is enabled for this channel."""
        if not hasattr(self, "_channel_settings_service") or not self._channel_settings_service:
            return False
        try:
            return self._channel_settings_service.get_live_enabled(channel_id)
        except Exception as exc:
            logger.error(f"Failed to resolve live mode for channel {channel_id}: {exc}")
            return False

    async def _enqueue_live_message(self, message: discord.Message):
        """Enqueue a live-mode message for batched single-reply processing."""
        channel_id = message.channel.id
        lock = self._get_live_channel_lock(channel_id)
        async with lock:
            self._live_pending_messages.setdefault(channel_id, []).append(message)
            task = self._live_channel_tasks.get(channel_id)
            if task and not task.done():
                return

            self._live_channel_tasks[channel_id] = asyncio.create_task(
                self._run_live_channel_worker(channel_id),
                name=f"live-worker-{channel_id}",
            )

    async def _run_live_channel_worker(self, channel_id: int):
        """Process live-mode messages serially with post-response cooldown."""
        lock = self._get_live_channel_lock(channel_id)
        try:
            while True:
                if not self._is_live_mode_enabled(channel_id):
                    async with lock:
                        self._live_pending_messages.pop(channel_id, None)
                    break

                async with lock:
                    pending_messages = self._live_pending_messages.pop(channel_id, [])

                if not pending_messages:
                    break

                response_sent = False
                try:
                    response_sent = await self._process_live_messages(pending_messages)
                except Exception as exc:
                    first_message = pending_messages[0]
                    last_message = pending_messages[-1]
                    logger.error(
                        (
                            "Live worker error in channel "
                            f"{channel_id} for messages {first_message.id}-{last_message.id}: {exc}"
                        ),
                        exc_info=True,
                    )

                if not self._is_live_mode_enabled(channel_id):
                    async with lock:
                        self._live_pending_messages.pop(channel_id, None)
                    break

                if response_sent:
                    await asyncio.sleep(self._live_cooldown_seconds)
        finally:
            async with lock:
                self._live_channel_tasks.pop(channel_id, None)
                has_pending = bool(self._live_pending_messages.get(channel_id))
                if has_pending and self._is_live_mode_enabled(channel_id):
                    self._live_channel_tasks[channel_id] = asyncio.create_task(
                        self._run_live_channel_worker(channel_id),
                        name=f"live-worker-{channel_id}",
                    )

    def _append_live_context_entry(
        self,
        channel_id: int,
        content: str,
        author: str,
        message_id: int,
        timestamp: datetime,
        is_reply: bool = False,
        replied_to_id: Optional[int] = None,
    ):
        """Append a single message entry to the in-memory live rolling context."""
        cleaned = (content or "").strip()
        if not cleaned:
            return
        if len(cleaned) > 1200:
            cleaned = cleaned[:1200] + "..."

        self._get_live_context_buffer(channel_id).append(
            MessageContext(
                content=cleaned,
                author=author,
                timestamp=timestamp,
                message_id=message_id,
                channel_id=channel_id,
                is_reply=is_reply,
                replied_to_id=replied_to_id,
            )
        )

    async def _process_live_messages(self, messages: List[discord.Message]) -> bool:
        """Fast-path processing for mention-free live mode in a single channel."""
        if not messages:
            return False

        target_message = messages[-1]
        channel_id = target_message.channel.id

        is_allowed, rate_limit_message = await self.text_rate_limiter.check_and_record(target_message.author.id)
        if not is_allowed:
            await target_message.reply(rate_limit_message)
            return True

        has_attachments = any(msg.attachments for msg in messages)
        prompt_entries: List[Tuple[discord.Message, str]] = []
        for msg in messages:
            prompt = self._extract_user_prompt(msg)
            if prompt:
                prompt_entries.append((msg, prompt))

        # Attachment-heavy requests fall back to the full pipeline, still with live model overrides.
        if has_attachments:
            attachment_message = next(
                (msg for msg in reversed(messages) if msg.attachments),
                target_message,
            )
            attachment_prompt = self._extract_user_prompt(attachment_message)
            await self._process_message_with_context(
                attachment_message,
                attachment_prompt,
                complexity_level="low",
                routed_intent="live_mode",
                model_override=self._live_model_name,
                prompt_mode_override="short",
                search_override=False,
                show_status_message=False,
                skip_context_media=False,
                apply_user_preferences=False,
            )
            return True

        if not prompt_entries:
            return False

        if len(prompt_entries) == 1:
            user_prompt = prompt_entries[0][1]
        else:
            user_prompt_lines = [f"- {msg.author.display_name}: {prompt}" for msg, prompt in prompt_entries]
            user_prompt = (
                "New live chat messages (oldest to newest):\n"
                + "\n".join(user_prompt_lines)
                + "\nReply once to all of these in one short chat response."
            )

        rolling_context = list(self._get_live_context_buffer(channel_id))
        personality_prompt = self._live_reply_style_instruction
        if hasattr(self, "_channel_settings_service") and self._channel_settings_service:
            channel_personality = self._channel_settings_service.get_personality_prompt(channel_id)
            if channel_personality:
                personality_prompt = f"{channel_personality}\n{self._live_reply_style_instruction}"

        api_response = await self.gemini_client.generate_response(
            user_prompt,
            rolling_context,
            model_override=self._live_model_name,
            prompt_mode_override="short",
            search_override=False,
            personality_prompt=personality_prompt,
            language=None,
        )

        if not api_response.success:
            await self._handle_response_error(target_message, api_response)
            return True

        await self._record_token_usage(target_message, api_response.token_usage)
        sent_message = await self._send_response_safely(
            target_message,
            api_response.content,
            api_response.grounding_sources,
        )

        for source_message, source_prompt in prompt_entries:
            self._append_live_context_entry(
                channel_id=channel_id,
                content=source_prompt,
                author=source_message.author.display_name,
                message_id=source_message.id,
                timestamp=source_message.created_at,
                is_reply=source_message.reference is not None,
                replied_to_id=source_message.reference.message_id if source_message.reference else None,
            )

        if sent_message:
            self._append_live_context_entry(
                channel_id=channel_id,
                content=api_response.content,
                author=self.user.display_name if self.user else "Grok",
                message_id=sent_message.id,
                timestamp=datetime.now(timezone.utc),
                is_reply=True,
                replied_to_id=target_message.id,
            )
            return True

        return False

    async def on_message(self, message: discord.Message):
        """
        Event handler for processing Discord messages.
        
        Implements requirements 1.1, 1.2: Detect bot mentions and extract
        message content for processing.
        
        Args:
            message: The Discord message object
        """
        # Ignore messages from bots (including ourselves)
        if message.author.bot:
            return

        # Live mode bypasses mention requirements in opted-in channels.
        if message.guild and self._is_live_mode_enabled(message.channel.id):
            await self._enqueue_live_message(message)
            return

        # Check if the bot is mentioned in the message
        if not self.is_bot_mentioned(message):
            return
        
        # Create logger with context
        context_logger = get_logger_with_context(
            __name__,
            user_id=message.author.id,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
            message_id=message.id
        )
        
        context_logger.info(f"Bot mentioned by {message.author} in #{getattr(message.channel, 'name', 'DM')}")
        logger.debug(f"Message content: {message.content}")
        
        try:
            is_allowed, rate_limit_message = await self.text_rate_limiter.check_and_record(message.author.id)
            if not is_allowed:
                context_logger.warning(f"Text rate limit triggered for user {message.author.id}")
                await message.reply(rate_limit_message)
                return

            complexity_level = "low"
            routed_intent = "unknown"

            # Try enhanced command handler first if available
            if self.enhanced_command_handler:
                handled, complexity_level, intent = await self.enhanced_command_handler.handle_message(message)
                routed_intent = intent.value
                if handled:
                    context_logger.info("Message handled by enhanced command handler")
                    return
                
                # If not fully handled, use the complexity level to set the model
                # The router model has already determined the appropriate complexity
                context_logger.info(f"Setting model based on router complexity level: {complexity_level}")
                self.gemini_client.set_model_by_complexity(complexity_level)
            
            # Extract the user's prompt by removing bot mentions
            user_prompt = self._extract_user_prompt(message)
            if not user_prompt.strip():
                context_logger.warning("Empty prompt after removing mentions")
                try:
                    await message.reply("I was mentioned but didn't see a message to respond to! Please include a message with your mention. 📝")
                except discord.Forbidden:
                    context_logger.error(f"No permission to reply in channel {message.channel.id}")
                except discord.HTTPException as e:
                    context_logger.error(f"Failed to send empty prompt message: {e}")
                return
            
            # Process message with timing
            import time
            start_time = time.time()
            
            # Collect conversation context
            await self._process_message_with_context(
                message,
                user_prompt,
                complexity_level=complexity_level,
                routed_intent=routed_intent,
            )
            
            # Log processing completion (will be called from _generate_and_send_response)
            
        except discord.Forbidden as e:
            context_logger.error(f"Permission error processing message: {e}")
            error_context = self.error_manager.create_error_context(
                e, "I don't have the necessary permissions to respond here. Please check that I can read message history and send messages! 🔒"
            )
            await self.error_manager.send_error_response(message, error_context)
            
        except discord.HTTPException as e:
            context_logger.error(f"Discord API error processing message: {e}")
            error_context = self.error_manager.create_error_context(
                e, "I'm having trouble communicating with Discord. Please try again in a moment! 🌐"
            )
            await self.error_manager.send_error_response(message, error_context)
            
        except asyncio.CancelledError:
            context_logger.warning(f"Message processing was cancelled for message {message.id}")
            # Don't send error response for cancelled operations
            raise
            
        except KeyboardInterrupt:
            context_logger.info("Bot shutdown requested")
            raise
            
        except Exception as e:
            context_logger.error(f"Unexpected error processing message: {e}", exc_info=True)
            error_context = self.error_manager.handle_discord_error(e, message)
            await self.error_manager.send_error_response(message, error_context)
    
    async def _process_message_with_context(
        self,
        message: discord.Message,
        user_prompt: str,
        complexity_level: str = "low",
        routed_intent: str = "unknown",
        *,
        model_override: Optional[str] = None,
        prompt_mode_override: Optional[str] = None,
        search_override: Optional[bool] = None,
        show_status_message: bool = True,
        skip_context_media: bool = False,
        apply_user_preferences: bool = True,
    ):
        """
        Process a message with full context collection and response generation.
        
        Implements requirements 2.1, 3.1: Connect ContextCollector to message
        event processing and implement reply detection with enhanced context retrieval.
        
        Args:
            message: The Discord message object
            user_prompt: The extracted user prompt without mentions
            complexity_level: Router-detected complexity level
            routed_intent: Router-detected intent from the first pass
        """
        try:
            context_limit = self._get_context_limit_for_complexity(complexity_level)
            candidate_limit = max(self.config.max_context_messages, context_limit)
            logger.debug(
                "Collecting context candidates (limit=%s, selected_max=%s) for complexity=%s, intent=%s",
                candidate_limit,
                context_limit,
                complexity_level,
                routed_intent,
            )

            # Check if this is a reply and collect appropriate context
            anchor_message_ids = set()
            if message.reference:
                if message.reference.message_id:
                    anchor_message_ids.add(message.reference.message_id)
                logger.debug("Message is a reply, collecting enhanced context")
                try:
                    # Get enhanced reply context
                    reply_context = await self.context_collector.get_reply_context(message)
                except discord.NotFound:
                    logger.warning(f"Replied-to message not found: {message.reference.message_id}")
                    reply_context = []
                except discord.Forbidden:
                    logger.warning(f"No permission to fetch replied message in channel {message.channel.id}")
                    reply_context = []
                except discord.HTTPException as e:
                    logger.error(f"Failed to fetch reply context: {e}")
                    reply_context = []
                
                # Get standard channel context
                try:
                    standard_context = await self.context_collector.get_channel_context(
                        message.channel,
                        limit=candidate_limit,
                        bot_user=self.user,
                    )
                except discord.Forbidden:
                    logger.warning(f"No permission to read message history in channel {message.channel.id}")
                    standard_context = []
                except discord.HTTPException as e:
                    logger.error(f"Failed to fetch channel context: {e}")
                    standard_context = []
                
                # Combine contexts, removing duplicates
                combined_context = self.context_collector._remove_duplicate_messages(
                    standard_context, reply_context
                )
            else:
                logger.debug("Message is not a reply, collecting standard context")
                # Get standard channel context only
                try:
                    combined_context = await self.context_collector.get_channel_context(
                        message.channel,
                        limit=candidate_limit,
                        bot_user=self.user,
                    )
                except discord.Forbidden:
                    logger.warning(f"No permission to read message history in channel {message.channel.id}")
                    combined_context = []
                except discord.HTTPException as e:
                    logger.error(f"Failed to fetch channel context: {e}")
                    combined_context = []
            
            combined_context = [
                ctx_msg for ctx_msg in combined_context
                if ctx_msg.message_id != message.id
            ]
            logger.info(f"Collected {len(combined_context)} candidate messages for context")

            if routed_intent == "image_generate":
                logger.debug("Image generation intent reached context handler; using minimal context fallback")
                combined_context = []
            elif combined_context:
                original_context_count = len(combined_context)
                combined_context = await self.gemini_client.select_relevant_context(
                    user_prompt,
                    combined_context,
                    max_messages=context_limit,
                    anchor_message_ids=anchor_message_ids,
                )
                logger.info(
                    "Selected %s/%s context messages for final response model",
                    len(combined_context),
                    original_context_count,
                )

            # Generate AI response using Gemini API with collected or filtered context
            await self._generate_and_send_response(
                message,
                user_prompt,
                combined_context,
                model_override=model_override,
                prompt_mode_override=prompt_mode_override,
                search_override=search_override,
                show_status_message=show_status_message,
                skip_context_media=skip_context_media,
                apply_user_preferences=apply_user_preferences,
            )
            
        except discord.Forbidden as e:
            error_context = self.error_manager.create_error_context(
                e, "I don't have permission to read message history in this channel. Please check my permissions! 🔒"
            )
            self.error_manager.log_error(error_context, f"Permission denied in channel {message.channel.id}")
            await self.error_manager.send_error_response(message, error_context)
            
        except discord.HTTPException as e:
            error_context = self.error_manager.create_error_context(
                e, "I'm having trouble communicating with Discord. Please try again in a moment! 🌐"
            )
            self.error_manager.log_error(error_context, f"Discord API error in channel {message.channel.id}")
            await self.error_manager.send_error_response(message, error_context)
            
        except asyncio.TimeoutError as e:
            error_context = self.error_manager.create_error_context(
                e, "It's taking too long to gather context. Please try again! ⏰"
            )
            self.error_manager.log_error(error_context, f"Timeout collecting context in channel {message.channel.id}")
            await self.error_manager.send_error_response(message, error_context)
            
        except Exception as e:
            # Categorize the error for more specific handling
            error_context = self.error_manager.create_error_context(e, include_error_details=True)
            
            self.error_manager.log_error(error_context, f"Unexpected error in channel {message.channel.id}")
            await self.error_manager.send_error_response(message, error_context)

    def _get_context_limit_for_complexity(self, complexity_level: str) -> int:
        """Select context window size by complexity tier."""
        if complexity_level == "high":
            return self.config.context_messages_high
        if complexity_level == "medium":
            return self.config.context_messages_medium
        return self.config.context_messages_low
    
    def _extract_user_prompt(self, message: discord.Message) -> str:
        """
        Extract the user's prompt from a Discord message by removing bot mentions.
        
        Args:
            message: The Discord message object
            
        Returns:
            The user's prompt with bot mentions removed
        """
        content = message.content
        
        # Remove bot mentions from the content
        if self.user:
            # Remove direct @bot mentions
            content = content.replace(f'<@{self.user.id}>', '').replace(f'<@!{self.user.id}>', '')
        
        # Remove @everyone and @here if present
        content = content.replace('@everyone', '').replace('@here', '')
        
        # Clean up extra whitespace
        return content.strip()
    
    async def _convert_pdf_to_images(self, pdf_bytes: bytes, filename: str) -> List[Image.Image]:
        """
        Convert a PDF file to a list of PNG images.
        
        Args:
            pdf_bytes: The PDF file content as bytes
            filename: The name of the PDF file (for logging)
            
        Returns:
            List of PIL Image objects, one per page
        """
        images = []
        try:
            logger.info(f"=" * 80)
            logger.info(f"PDF CONVERSION STARTED: {filename}")
            logger.info(f"PDF size: {len(pdf_bytes)} bytes ({len(pdf_bytes) / 1024:.2f} KB)")
            
            # Open PDF with PyMuPDF and ensure cleanup on exceptions
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_document:
                page_count = len(pdf_document)
                
                logger.info(f"PDF has {page_count} page(s)")

                if page_count > self.config.max_pdf_pages:
                    logger.warning(f"PDF has {page_count} pages, truncating to {self.config.max_pdf_pages}")
                    page_count = self.config.max_pdf_pages
                
                # Convert each page to an image
                for page_num in range(page_count):
                    try:
                        logger.info(f"Converting page {page_num + 1}/{page_count}...")
                        
                        page = pdf_document[page_num]
                        
                        # Render page to pixmap (image) at configured resolution
                        mat = fitz.Matrix(self.config.pdf_render_scale, self.config.pdf_render_scale)
                        pix = page.get_pixmap(matrix=mat)
                        
                        # Convert pixmap to PIL Image and ensure RGB mode
                        img_data = pix.tobytes("png")
                        image = Image.open(io.BytesIO(img_data))
                        image = self._convert_image_to_rgb(image)
                        
                        images.append(image)
                        logger.info(f"✓ Page {page_num + 1} converted: {image.size[0]}x{image.size[1]} pixels")
                        
                    except Exception as e:
                        logger.error(f"Failed to convert page {page_num + 1}: {e}")
                        continue
            
            logger.info(f"✅ PDF CONVERSION COMPLETE: {len(images)} pages converted successfully")
            logger.info(f"=" * 80)
            
        except Exception as e:
            logger.error(f"Failed to convert PDF {filename}: {e}", exc_info=True)
            logger.info(f"=" * 80)
        
        return images

    @staticmethod
    def _create_image_context_entry(
        source_type: str,
        source_message: discord.Message,
        attachment_name: str,
        attachment_index: int,
        pdf_page_number: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create metadata that links an image part back to its source message."""
        entry = {
            "source_type": source_type,
            "source_message_id": source_message.id,
            "source_timestamp": source_message.created_at.isoformat(),
            "source_author": source_message.author.display_name,
            "attachment_name": attachment_name,
            "attachment_index": attachment_index,
        }
        if pdf_page_number is not None:
            entry["pdf_page_number"] = pdf_page_number
        return entry

    @staticmethod
    def _attach_image_order_metadata(
        image_context: List[Dict[str, Any]],
        context: List[MessageContext],
        current_message: discord.Message
    ) -> List[Dict[str, Any]]:
        """Annotate image metadata with explicit image and message order labels."""
        sorted_context = sorted(context, key=lambda msg: (msg.timestamp, msg.message_id))
        context_order_map = {
            msg.message_id: f"CTX_MSG_{idx:03d}"
            for idx, msg in enumerate(sorted_context, start=1)
        }
        replied_message_id = current_message.reference.message_id if current_message.reference else None

        enriched_entries = []
        for idx, entry in enumerate(image_context, start=1):
            enriched = dict(entry)
            enriched["image_index"] = idx

            source_message_id = enriched.get("source_message_id")
            if source_message_id in context_order_map:
                source_order = context_order_map[source_message_id]
            elif source_message_id == current_message.id:
                source_order = "CURRENT_USER_MESSAGE"
            elif replied_message_id and source_message_id == replied_message_id:
                source_order = "REPLIED_TO_MESSAGE"
            else:
                source_order = "NON_CONTEXT_MESSAGE"

            enriched["source_message_order"] = source_order
            enriched_entries.append(enriched)

        return enriched_entries
    
    async def _extract_images_from_message(self, message: discord.Message) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """
        Extract and download images from a Discord message.
        Also converts PDF files to images for processing.
        
        Args:
            message: The Discord message to extract images from
            
        Returns:
            Tuple containing image list and parallel image metadata list
        """
        images = []
        image_context = []
        
        # Check message attachments for images and PDFs
        for attachment_idx, attachment in enumerate(message.attachments, start=1):
            # Check if attachment is a PDF
            if attachment.content_type == 'application/pdf' or attachment.filename.lower().endswith('.pdf'):
                try:
                    logger.info(f"📄 PDF detected: {attachment.filename}")
                    # Download the PDF
                    pdf_bytes = await attachment.read()
                    
                    # Convert PDF pages to images
                    pdf_images = await self._convert_pdf_to_images(pdf_bytes, attachment.filename)
                    
                    if pdf_images:
                        for page_idx, page_image in enumerate(pdf_images, start=1):
                            images.append(page_image)
                            image_context.append(
                                self._create_image_context_entry(
                                    source_type="current_message",
                                    source_message=message,
                                    attachment_name=attachment.filename,
                                    attachment_index=attachment_idx,
                                    pdf_page_number=page_idx,
                                )
                            )
                        logger.info(f"✅ Added {len(pdf_images)} page(s) from PDF: {attachment.filename}")
                    else:
                        logger.warning(f"⚠️ No pages could be extracted from PDF: {attachment.filename}")
                    
                except Exception as e:
                    logger.error(f"Failed to process PDF {attachment.filename}: {e}", exc_info=True)
            
            # Check if attachment is an image based on content type or filename
            elif attachment.content_type and attachment.content_type.startswith('image/'):
                try:
                    # Download the image
                    image_bytes = await attachment.read()
                    
                    # Convert to PIL Image and ensure RGB mode
                    image = Image.open(io.BytesIO(image_bytes))
                    image = self._convert_image_to_rgb(image)
                    
                    images.append(image)
                    image_context.append(
                        self._create_image_context_entry(
                            source_type="current_message",
                            source_message=message,
                            attachment_name=attachment.filename,
                            attachment_index=attachment_idx,
                        )
                    )
                    logger.info(f"Loaded image from attachment: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                    
                except Exception as e:
                    logger.error(f"Failed to load image from attachment {attachment.filename}: {e}")
        
        # Also check if the message is a reply and has images/PDFs in the replied message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                for attachment_idx, attachment in enumerate(replied_message.attachments, start=1):
                    # Check for PDFs in replied message
                    if attachment.content_type == 'application/pdf' or attachment.filename.lower().endswith('.pdf'):
                        try:
                            logger.info(f"📄 PDF detected in replied message: {attachment.filename}")
                            pdf_bytes = await attachment.read()
                            pdf_images = await self._convert_pdf_to_images(pdf_bytes, attachment.filename)
                            
                            if pdf_images:
                                for page_idx, page_image in enumerate(pdf_images, start=1):
                                    images.append(page_image)
                                    image_context.append(
                                        self._create_image_context_entry(
                                            source_type="replied_message",
                                            source_message=replied_message,
                                            attachment_name=attachment.filename,
                                            attachment_index=attachment_idx,
                                            pdf_page_number=page_idx,
                                        )
                                    )
                                logger.info(f"✅ Added {len(pdf_images)} page(s) from replied PDF: {attachment.filename}")
                            
                        except Exception as e:
                            logger.error(f"Failed to process PDF from replied message {attachment.filename}: {e}")
                    
                    # Check for images in replied message
                    elif attachment.content_type and attachment.content_type.startswith('image/'):
                        try:
                            image_bytes = await attachment.read()
                            image = Image.open(io.BytesIO(image_bytes))
                            image = self._convert_image_to_rgb(image)
                            
                            images.append(image)
                            image_context.append(
                                self._create_image_context_entry(
                                    source_type="replied_message",
                                    source_message=replied_message,
                                    attachment_name=attachment.filename,
                                    attachment_index=attachment_idx,
                                )
                            )
                            logger.info(f"Loaded image from replied message: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                            
                        except Exception as e:
                            logger.error(f"Failed to load image from replied message attachment {attachment.filename}: {e}")
        
        return images, image_context

    async def _extract_context_images(
        self,
        message: discord.Message,
        context: List[MessageContext],
        exclude_message_ids: Optional[set[int]] = None
    ) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """
        Extract recent image attachments from context messages in the same channel.

        Args:
            message: The current Discord message
            context: Collected MessageContext list
            exclude_message_ids: Optional message IDs to skip (e.g., current/replied message)

        Returns:
            Tuple containing context images and parallel metadata entries
        """
        max_context_images = max(0, getattr(self.config, "max_context_images", 6))
        if max_context_images == 0 or not context:
            return [], []

        excluded_ids = exclude_message_ids or set()
        context_message_ids = {msg.message_id for msg in context if msg.message_id not in excluded_ids}
        if not context_message_ids:
            return [], []

        images = []
        image_context = []
        history_limit = max(len(context_message_ids) * 2, self.config.max_context_messages * 2)

        try:
            async for ctx_message in message.channel.history(limit=history_limit):
                if len(images) >= max_context_images:
                    break
                if ctx_message.id not in context_message_ids:
                    continue

                for attachment_idx, attachment in enumerate(ctx_message.attachments, start=1):
                    if len(images) >= max_context_images:
                        break
                    is_image = (
                        (attachment.content_type and attachment.content_type.startswith("image/"))
                        or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
                    )
                    if not is_image:
                        continue
                    try:
                        image_bytes = await attachment.read()
                        image = Image.open(io.BytesIO(image_bytes))
                        image = self._convert_image_to_rgb(image)
                        images.append(image)
                        image_context.append(
                            self._create_image_context_entry(
                                source_type="context_message",
                                source_message=ctx_message,
                                attachment_name=attachment.filename,
                                attachment_index=attachment_idx,
                            )
                        )
                        logger.info(
                            f"Loaded context image: {attachment.filename} from message {ctx_message.id} "
                            f"({image.size[0]}x{image.size[1]})"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to load context image {attachment.filename} "
                            f"from message {ctx_message.id}: {e}"
                        )
        except discord.Forbidden:
            logger.warning(f"No permission to read channel history for context images in channel {message.channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Discord API error retrieving context images: {e}")

        if len(images) > 1:
            ordered_pairs = sorted(
                zip(images, image_context),
                key=lambda pair: (
                    pair[1].get("source_timestamp", ""),
                    int(pair[1].get("source_message_id", 0) or 0),
                    int(pair[1].get("attachment_index", 0) or 0),
                    int(pair[1].get("pdf_page_number", 0) or 0),
                ),
            )
            images = [pair[0] for pair in ordered_pairs]
            image_context = [pair[1] for pair in ordered_pairs]

        return images, image_context
    
    async def _extract_audio_from_message(self, message: discord.Message) -> List[Dict[str, Any]]:
        """
        Extract and download audio files from a Discord message.
        
        Args:
            message: The Discord message to extract audio from
            
        Returns:
            List of audio file dictionaries {'data': bytes, 'mime_type': str}
        """
        audio_files = []
        
        async def process_audio_attachment(attachment, source: str = "message") -> None:
            """Process a single audio attachment."""
            is_audio = False
            mime_type = attachment.content_type
            
            # Check if it's a voice message
            if hasattr(attachment, 'is_voice_message') and attachment.is_voice_message():
                is_audio = True
                mime_type = 'audio/ogg'  # Voice messages are typically OGG
                logger.info(f"🎤 Voice message detected in {source}: {attachment.filename}")
            
            # Check content type
            elif mime_type and any(t in mime_type for t in ['audio/', 'video/mp4']):
                is_audio = True
            # Check filename extension if content type is generic
            elif attachment.filename.lower().endswith(('.mp3', '.wav', '.aac', '.m4a', '.ogg', '.mpga')):
                is_audio = True
                # Map extension to mime type
                ext_mime_map = {
                    '.mp3': 'audio/mp3',
                    '.wav': 'audio/wav',
                    '.aac': 'audio/aac',
                    '.m4a': 'audio/mp4',
                    '.ogg': 'audio/ogg',
                    '.mpga': 'audio/mpeg'
                }
                for ext, mime in ext_mime_map.items():
                    if attachment.filename.lower().endswith(ext):
                        mime_type = mime
                        break
            
            if is_audio:
                try:
                    logger.info(f"🎵 Audio detected in {source}: {attachment.filename}")
                    audio_bytes = await attachment.read()
                    
                    audio_files.append({
                        'data': audio_bytes,
                        'mime_type': mime_type or 'audio/mp3',
                        'filename': attachment.filename
                    })
                    logger.info(f"✅ Loaded audio from {source}: {attachment.filename} ({len(audio_bytes)} bytes)")
                    
                except Exception as e:
                    logger.error(f"Failed to process audio {attachment.filename}: {e}", exc_info=True)
        
        # Process attachments from the main message
        for attachment in message.attachments:
            await process_audio_attachment(attachment, "message")
        
        # Also check replied message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                for attachment in replied_message.attachments:
                    await process_audio_attachment(attachment, "reply")
                            
        return audio_files
    
    async def _extract_files_from_message(self, message: discord.Message) -> tuple[List[Dict[str, str]], List[str]]:
        """
        Extract and read non-image files from a Discord message.
        
        Args:
            message: The Discord message to extract files from
            
        Returns:
            Tuple of (list of file dictionaries with name and content, list of unsupported filenames)
        """
        files = []
        unsupported_files = []
        
        logger.info(f"Extracting files from message {message.id}")
        logger.info(f"Total attachments in message: {len(message.attachments)}")
        
        # Supported text-based file extensions (imported from constants)
        text_extensions = SUPPORTED_TEXT_EXTENSIONS
        
        # Maximum file size to read
        max_file_size = self.config.max_text_file_size_bytes
        
        async def process_attachment(attachment: discord.Attachment) -> None:
            """Process a single attachment."""
            logger.info(f"Processing attachment: {attachment.filename}")
            logger.info(f"  - Content type: {attachment.content_type}")
            logger.info(f"  - Size: {attachment.size} bytes ({attachment.size / 1024:.2f} KB)")
            
            # Skip images (handled by _extract_images_from_message)
            if attachment.content_type and attachment.content_type.startswith('image/'):
                logger.info(f"  → Skipping {attachment.filename} (image file - handled separately)")
                return
            
            # Skip PDFs (now handled by _extract_images_from_message as images)
            if attachment.content_type == 'application/pdf' or attachment.filename.lower().endswith('.pdf'):
                logger.info(f"  → Skipping {attachment.filename} (PDF file - converted to images and handled separately)")
                return
            
            # Check file size
            if attachment.size > max_file_size:
                logger.warning(f"  → REJECTED: {attachment.filename} is too large ({attachment.size} bytes = {attachment.size / (1024*1024):.1f}MB)")
                logger.warning(f"  → Maximum allowed size: {max_file_size / (1024*1024):.1f}MB")
                unsupported_files.append(f"{attachment.filename} (too large: {attachment.size / (1024*1024):.1f}MB)")
                return
            
            # Get file extension
            file_ext = None
            if '.' in attachment.filename:
                file_ext = '.' + attachment.filename.rsplit('.', 1)[1].lower()
                logger.info(f"  - File extension: {file_ext}")
            else:
                logger.info(f"  - No file extension detected")
            
            # Check if it's a supported text file
            is_supported = file_ext in text_extensions or attachment.content_type and (
                attachment.content_type.startswith('text/') or 
                'json' in attachment.content_type or
                'xml' in attachment.content_type or
                'yaml' in attachment.content_type
            )
            
            if is_supported:
                logger.info(f"  ✓ {attachment.filename} is a SUPPORTED file type")
                try:
                    logger.info(f"  → Downloading file content...")
                    # Download and decode the file
                    file_bytes = await attachment.read()
                    logger.info(f"  → Downloaded {len(file_bytes)} bytes")
                    
                    # Try multiple encodings
                    content = None
                    tried_encodings = []
                    for encoding in ['utf-8', 'latin-1', 'cp1252', 'ascii']:
                        try:
                            content = file_bytes.decode(encoding)
                            logger.info(f"  ✓ Successfully decoded {attachment.filename} with {encoding} encoding")
                            break
                        except UnicodeDecodeError:
                            tried_encodings.append(encoding)
                            logger.debug(f"  ✗ Failed to decode with {encoding}")
                            continue
                    
                    if content is None:
                        logger.error(f"  → FAILED: Could not decode file {attachment.filename} with any encoding")
                        logger.error(f"  → Tried encodings: {', '.join(tried_encodings)}")
                        unsupported_files.append(f"{attachment.filename} (encoding error)")
                        return
                    
                    files.append({
                        'name': attachment.filename,
                        'content': content,
                        'size': attachment.size
                    })
                    logger.info(f"  ✅ SUCCESS: Loaded {attachment.filename}")
                    logger.info(f"     - File size: {attachment.size} bytes ({attachment.size / 1024:.2f} KB)")
                    logger.info(f"     - Content length: {len(content)} characters")
                    logger.info(f"     - Content preview: {content[:150]}..." if len(content) > 150 else f"     - Full content: {content}")
                    
                except Exception as e:
                    logger.error(f"  → FAILED: Error loading {attachment.filename}: {e}")
                    logger.error(f"  → Exception type: {type(e).__name__}")
                    unsupported_files.append(f"{attachment.filename} (error: {str(e)})")
            else:
                # Unsupported file type
                logger.warning(f"  → REJECTED: {attachment.filename} is an UNSUPPORTED file type")
                logger.warning(f"  → Extension '{file_ext}' not in supported list")
                logger.warning(f"  → Content type '{attachment.content_type}' not recognized as text-based")
                unsupported_files.append(f"{attachment.filename} (unsupported type)")
        
        # Process attachments from the main message
        logger.info("Processing attachments from main message...")
        for idx, attachment in enumerate(message.attachments, 1):
            logger.info(f"Attachment {idx}/{len(message.attachments)}: {attachment.filename}")
            await process_attachment(attachment)
        
        # Also check if the message is a reply and has files in the replied message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                logger.info(f"Message is a reply, processing {len(replied_message.attachments)} attachments from replied message...")
                for idx, attachment in enumerate(replied_message.attachments, 1):
                    logger.info(f"Replied attachment {idx}/{len(replied_message.attachments)}: {attachment.filename}")
                    await process_attachment(attachment)
        
        # Final summary
        logger.info("=" * 40)
        logger.info(f"FILE EXTRACTION SUMMARY:")
        logger.info(f"  ✅ Successfully processed: {len(files)} file(s)")
        if files:
            for f in files:
                logger.info(f"     - {f['name']}")
        logger.info(f"  ❌ Failed/Unsupported: {len(unsupported_files)} file(s)")
        if unsupported_files:
            for f in unsupported_files:
                logger.info(f"     - {f}")
        logger.info("=" * 40)
        
        return files, unsupported_files
        
    def is_bot_mentioned(self, message: discord.Message) -> bool:
        """
        Check if the bot is directly mentioned in a Discord message or if the message is a reply to the bot.
        
        Implements requirements 1.1, 1.2: Identify bot mentions in messages.
        
        Note: Does NOT respond to @everyone or @here to avoid spam in busy servers.
        Only responds to direct @bot mentions, replies to bot messages, or role mentions
        where the bot has that role.
        
        Args:
            message: The Discord message to check
            
        Returns:
            True if the bot is directly mentioned or message is a reply to bot, False otherwise
        """
        # Always respond in private DMs (1-on-1 with bot) — no @mention needed
        if isinstance(message.channel, discord.DMChannel):
            return True

        # Check if this is a reply to a bot message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                # Check if the replied message is from the bot
                if replied_message.author == self.user:
                    return True
        
        # Check if the bot user is directly mentioned
        if self.user in message.mentions:
            return True
        
        # Note: We intentionally do NOT respond to @everyone or @here
        # to avoid spam in busy servers
        
        # Check for role mentions that the bot has (but not @everyone role)
        if message.guild and hasattr(message.guild, 'me'):
            bot_member = message.guild.me
            if bot_member:
                for role in message.role_mentions:
                    # Skip the @everyone role
                    if role.is_default():
                        continue
                    if role in bot_member.roles:
                        return True
        
        return False
    
    async def _update_progress_message(self, message: discord.Message, progress_percent: int):
        """
        Update progress message with current percentage.
        
        Args:
            message: The message to update
            progress_percent: Progress percentage (0-100)
        """
        try:
            await message.edit(content=f"🎨 Editing your image... {progress_percent}% complete")
        except Exception as e:
            logger.warning(f"Failed to update progress message: {e}")
    
    async def _add_error_reaction(self, message: discord.Message):
        """
        Add an error reaction to a message.
        
        Args:
            message: The message to add reaction to
        """
        try:
            await message.add_reaction("❌")
        except Exception as e:
            logger.debug(f"Failed to add error reaction: {e}")
    
    def _get_user_friendly_error_message(self, error_msg: str) -> str:
        """
        Convert technical error messages to user-friendly ones.
        
        Args:
            error_msg: Technical error message
            
        Returns:
            User-friendly error message
        """
        error_lower = error_msg.lower()
        
        if "rate limit" in error_lower:
            return "You're making requests too quickly. Please wait a moment and try again."
        elif "timeout" in error_lower:
            return "The image editing service timed out. Please try again with a smaller image."
        elif "invalid" in error_lower and "format" in error_lower:
            return "The image format is not supported. Please use PNG, JPEG, or GIF."
        elif "too large" in error_lower or "size" in error_lower:
            return "The image is too large. Please use an image smaller than 10MB."
        elif "service unavailable" in error_lower:
            return "The image editing service is temporarily unavailable. Please try again later."
        elif "authentication" in error_lower:
            return "There's an issue with the image editing service configuration. Please contact support."
        else:
            return f"Image editing failed: {error_msg}"
    
    async def _handle_response_error(self, message: discord.Message, api_response):
        """
        Handle API response errors with user-friendly messages.
        
        Implements requirements 4.2, 4.4: Provide user-friendly error messages
        and handle various error scenarios appropriately.
        
        Args:
            message: The original Discord message
            api_response: The failed APIResponse object
        """
        # Create error context from API response
        from ..utils.error_manager import ErrorType
        
        # Map API response error types to ErrorType enum
        error_type_mapping = {
            "rate_limit": ErrorType.RATE_LIMIT,
            "timeout": ErrorType.TIMEOUT,
            "authentication_error": ErrorType.AUTHENTICATION_ERROR,
            "service_unavailable": ErrorType.SERVICE_UNAVAILABLE,
            "empty_response": ErrorType.EMPTY_RESPONSE,
            "invalid_request": ErrorType.INVALID_REQUEST,
            "configuration_error": ErrorType.CONFIGURATION_ERROR,
            "unknown_error": ErrorType.UNKNOWN_ERROR,
            "safety_filter": ErrorType.EMPTY_RESPONSE,  # Treat safety filter as empty response
            "max_tokens": ErrorType.INVALID_REQUEST,
            "recitation": ErrorType.EMPTY_RESPONSE
        }
        
        error_type = error_type_mapping.get(api_response.error_type, ErrorType.UNKNOWN_ERROR)
        base_message = self.error_manager.get_user_message(error_type)
        
        # Add specific error details from the API response
        if api_response.content:
            user_message = f"{base_message}\n\n**Details:** {api_response.content}"
        else:
            user_message = f"{base_message}\n\n**Error Type:** `{api_response.error_type}`"
        
        # Create error context
        from ..utils.error_manager import ErrorContext
        error_context = ErrorContext(
            error_type=error_type,
            user_message=user_message,
            technical_details=api_response.content or f"API response error: {api_response.error_type}",
            retry_after=api_response.retry_after
        )
        
        # Log the error
        self.error_manager.log_error(error_context, f"API Response Error: {api_response.error_type}")
        
        # Send error response
        await self.error_manager.send_error_response(message, error_context)
    
    async def _generate_and_send_response(
        self,
        message: discord.Message,
        user_prompt: str,
        context: List,
        *,
        model_override: Optional[str] = None,
        prompt_mode_override: Optional[str] = None,
        search_override: Optional[bool] = None,
        show_status_message: bool = True,
        skip_context_media: bool = False,
        apply_user_preferences: bool = True,
    ):
        """
        Generate AI response and send it to Discord with comprehensive error handling.
        
        Implements requirements 1.4, 1.5, 4.2, 4.4:
        - Deliver responses as replies to original messages
        - Handle timeout scenarios appropriately
        - Provide user-friendly error messages
        - Continue processing other mentions during failures
        
        Args:
            message: The original Discord message
            user_prompt: The extracted user prompt
            context: The collected conversation context
        """
        # Add typing indicator to show the bot is working
        try:
            async with message.channel.typing():
                try:
                    # Track performance metrics
                    import time
                    start_time = time.time()
                    
                    # Extract images from the message
                    if skip_context_media:
                        images, image_context = [], []
                    else:
                        images, image_context = await self._extract_images_from_message(message)

                    # Also include recent channel images from the collected context
                    exclude_context_ids = {message.id}
                    if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
                        exclude_context_ids.add(message.reference.resolved.id)
                    if skip_context_media:
                        context_images, context_image_context = [], []
                    else:
                        context_images, context_image_context = await self._extract_context_images(
                            message,
                            context,
                            exclude_context_ids,
                        )
                    if context_images:
                        images.extend(context_images)
                        image_context.extend(context_image_context)
                        logger.info(
                            f"✅ Added {len(context_images)} context image(s) from recent channel history "
                            f"(total images sent: {len(images)})"
                        )
                    
                    # Extract audio files from the message
                    audio_files = await self._extract_audio_from_message(message)
                    if audio_files:
                        logger.info(f"✅ Extracted {len(audio_files)} audio file(s)")
                        
                        # Keep limited context for audio transcription (for tone/speaker identification)
                        if message.reference and message.reference.message_id:
                            replied_id = message.reference.message_id
                            # Keep the replied-to message + last 5 recent messages
                            original_context_len = len(context)
                            replied = [msg for msg in context if msg.message_id == replied_id]
                            recent = context[-5:] if len(context) > 5 else context
                            context = self.context_collector._remove_duplicate_messages(recent, replied)
                            logger.info(f"Audio transcription: Context restricted to replied message + recent (kept {len(context)}/{original_context_len} messages)")
                        else:
                            # No reply, keep last 5 messages for minimal context
                            original_context_len = len(context)
                            context = context[-5:] if len(context) > 5 else context
                            logger.info(f"Audio transcription: Context trimmed to recent messages (kept {len(context)}/{original_context_len} messages)")

                    # Recompute image/message ordering against the final context sent to the model
                    if image_context:
                        image_context = self._attach_image_order_metadata(image_context, context, message)
                    
                    # Extract files from the message
                    logger.info("=" * 80)
                    logger.info("FILE EXTRACTION STARTED")
                    files, unsupported_files = await self._extract_files_from_message(message)
                    logger.info(f"File extraction complete: {len(files)} supported, {len(unsupported_files)} unsupported")
                    
                    # Log details about successfully processed files
                    if files:
                        logger.info("✅ SUCCESSFULLY PROCESSED FILES:")
                        for idx, file_info in enumerate(files, 1):
                            logger.info(f"  {idx}. {file_info['name']}")
                            logger.info(f"     - Size: {file_info['size']} bytes ({file_info['size'] / 1024:.2f} KB)")
                            logger.info(f"     - Content length: {len(file_info['content'])} characters")
                            logger.info(f"     - First 100 chars: {file_info['content'][:100]}...")
                    else:
                        logger.info("❌ No files were successfully processed")
                    
                    # Log details about unsupported files
                    if unsupported_files:
                        logger.warning("⚠️ UNSUPPORTED/FAILED FILES:")
                        for idx, unsupported in enumerate(unsupported_files, 1):
                            logger.warning(f"  {idx}. {unsupported}")
                    
                    logger.info("=" * 80)
                    
                    # Notify user about unsupported files if any
                    if unsupported_files:
                        unsupported_msg = "ℹ️ Note: The following files could not be processed:\n" + "\n".join(f"- {f}" for f in unsupported_files)
                        logger.info(f"Sending unsupported files notification to user")
                        try:
                            await message.channel.send(unsupported_msg)
                        except Exception as e:
                            logger.error(f"Failed to send unsupported files message: {e}")
                    
                    # Validate prompt is not empty (or has images/files/audio)
                    if (not user_prompt or not user_prompt.strip()) and len(images) == 0 and len(files) == 0 and len(audio_files) == 0:
                        logger.warning("Empty user prompt and no images/files/audio provided to response generation")
                        await message.reply("Please provide a message, attach an image/audio, or upload a file for me to respond to! 📝")
                        return
                    
                    # Build enhanced prompt with file contents
                    enhanced_prompt = user_prompt
                    
                    # Add file contents to the prompt
                    if files:
                        logger.info("=" * 80)
                        logger.info("BUILDING AI PROMPT WITH FILE CONTENTS")
                        file_contents_text = "\n\n--- UPLOADED FILES ---\n"
                        for file_info in files:
                            file_contents_text += f"\n📄 **File: {file_info['name']}** (Size: {file_info['size']} bytes)\n"
                            file_contents_text += f"```\n{file_info['content']}\n```\n"
                            logger.info(f"  ✓ Added {file_info['name']} to AI prompt")
                        
                        # Prepend file contents to the prompt
                        if not enhanced_prompt or not enhanced_prompt.strip():
                            enhanced_prompt = f"I have uploaded the following file(s). Please analyze them:\n{file_contents_text}"
                            logger.info("Using auto-generated prompt for files (no user message)")
                        else:
                            enhanced_prompt = f"{file_contents_text}\n\nUser's question/request: {enhanced_prompt}"
                            logger.info(f"Combined file contents with user prompt: '{user_prompt[:100]}...'")
                        
                        logger.info(f"✅ Enhanced prompt built with {len(files)} file(s)")
                        logger.info(f"Total prompt length: {len(enhanced_prompt)} characters")
                        logger.info("=" * 80)
                    
                    # If only audio/images, provide a default prompt
                    elif not enhanced_prompt or not enhanced_prompt.strip():
                        if audio_files:
                            enhanced_prompt = "Please provide a verbatim transcription of this audio. Preserve all speech patterns including stutters, repetitions, and informal language (e.g., 'gonna', 'wanna'). Include non-verbal sounds and emotions in brackets, such as [laughter], [sigh], [unintelligible]. Do not summarize or clean up the text; transcribe exactly what is heard."
                            logger.info("Using default prompt for audio-only message")
                        elif images:
                            enhanced_prompt = "What's in this image? Please describe it in detail."
                            logger.info("Using default prompt for image-only message")
                    
                    # Generate AI response with timeout handling (including images and files if present)
                    logger.info("=" * 80)
                    logger.info("SENDING TO AI API")
                    logger.info(f"  - Prompt length: {len(enhanced_prompt)} characters")
                    logger.info(f"  - Context messages: {len(context)}")
                    logger.info(f"  - Images included: {len(images) if images else 0}")
                    logger.info(f"  - Audio files included: {len(audio_files) if audio_files else 0}")
                    logger.info(f"  - Files included in prompt: {len(files)}")
                    if files:
                        logger.info("  - Files sent to AI:")
                        for f in files:
                            logger.info(f"    • {f['name']}")
                    logger.info("=" * 80)
                    
                    # Load channel personality and user preferences
                    personality_prompt = None
                    user_language = None
                    if hasattr(self, '_channel_settings_service') and self._channel_settings_service:
                        personality_prompt = self._channel_settings_service.get_personality_prompt(message.channel.id)
                    if apply_user_preferences and hasattr(self, '_user_prefs_service') and self._user_prefs_service:
                        prefs = self._user_prefs_service.get_preferences(message.author.id)
                        if prefs.preferred_model and model_override is None:
                            self.gemini_client.set_model(prefs.preferred_model)
                        user_language = prefs.preferred_language

                    # Get estimated response time and show it to user
                    estimated_time = self.gemini_client.get_estimated_response_time(
                        model_name=model_override or self.gemini_client.get_current_model(),
                        prompt_mode=prompt_mode_override,
                    )
                    model_name = model_override or self.gemini_client.get_current_model()
                    
                    # Send status message by default so users get immediate feedback
                    status_message = None
                    if show_status_message:
                        try:
                            status_message = await message.reply(
                            f"⏳ Processing your request with {model_name}...\n"
                            f"*Estimated time: {estimated_time}*"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send status message: {e}")
                    
                    # Use dynamic timeout based on model complexity
                    api_timeout = self.gemini_client.get_timeout_for_model(
                        model_override or self.gemini_client.get_current_model(),
                        prompt_mode_override,
                    ) + 10  # Add buffer

                    # Thinking now uses Gemini API native thinking_config; no custom <thinking> parsing.
                    on_chunk = None

                    api_response = await asyncio.wait_for(
                        self.gemini_client.generate_response(
                            enhanced_prompt, context,
                            images=images if images else None,
                            image_context=image_context if image_context else None,
                            audio_files=audio_files if audio_files else None,
                            on_chunk=on_chunk,
                            model_override=model_override,
                            prompt_mode_override=prompt_mode_override,
                            search_override=search_override,
                            personality_prompt=personality_prompt,
                            language=user_language,
                        ),
                        timeout=api_timeout
                    )
                    
                    # Delete status message if it was sent
                    if status_message:
                        try:
                            await status_message.delete()
                        except Exception as e:
                            logger.debug(f"Failed to delete status message after response: {e}")
                    
                    duration = time.time() - start_time
                    
                    if api_response.success:
                        logger.info("=" * 80)
                        logger.info("✅ AI RESPONSE GENERATED SUCCESSFULLY")
                        logger.info(f"  - Response length: {len(api_response.content) if api_response.content else 0} characters")
                        logger.info(f"  - Processing duration: {duration:.2f} seconds")
                        logger.info(f"  - Files were included in request: {len(files) > 0}")
                        if files:
                            logger.info(f"  - Files that were processed by AI:")
                            for f in files:
                                logger.info(f"    ✓ {f['name']}")
                        logger.info("=" * 80)
                        
                        # Log performance metrics
                        self.performance_logger.log_message_processing(
                            duration=duration,
                            context_messages=len(context),
                            response_length=len(api_response.content) if api_response.content else 0,
                            user_id=message.author.id,
                            guild_id=message.guild.id if message.guild else None
                        )
                        
                        await self._record_token_usage(message, api_response.token_usage)

                        await self._send_response_safely(message, api_response.content, api_response.grounding_sources)
                    else:
                        logger.error(f"Failed to generate response: {api_response.error_type}")
                        await self._handle_response_error(message, api_response)
                        
                except asyncio.TimeoutError:
                    # Delete status message if it exists
                    if status_message:
                        try:
                            await status_message.delete()
                        except Exception as e:
                            logger.debug(f"Failed to delete status message after timeout: {e}")
                    
                    timeout_used = self.gemini_client.get_timeout_for_model(
                        model_override or self.gemini_client.get_current_model(),
                        prompt_mode_override,
                    )
                    logger.error(f"Response generation timed out for message {message.id} after {timeout_used}s")
                    timeout_response = APIResponse(
                        success=False,
                        error_type="timeout",
                        content=f"Response generation timed out after {timeout_used} seconds. The model may be overloaded. Please try again or use a simpler question."
                    )
                    await self._handle_response_error(message, timeout_response)
                    
                except ConnectionError as e:
                    logger.error(f"Connection error during API call: {e}")
                    error_context = self.error_manager.create_error_context(
                        e, "I'm having trouble connecting to my AI service. Please try again in a moment! 🌐"
                    )
                    self.error_manager.log_error(error_context, "API connection error")
                    await self.error_manager.send_error_response(message, error_context)
                    
                except ValueError as e:
                    logger.error(f"Invalid value provided to API: {e}")
                    error_context = self.error_manager.create_error_context(
                        e, "There was an issue with the request format. Please try rephrasing your message! 📝"
                    )
                    self.error_manager.log_error(error_context, "API value error")
                    await self.error_manager.send_error_response(message, error_context)
                    
                except Exception as e:
                    error_context = self.error_manager.create_error_context(e)
                    self.error_manager.log_error(error_context, f"Unexpected error during response generation for message {message.id}")
                    await self.error_manager.send_error_response(message, error_context)
                    
        except discord.Forbidden as e:
            logger.error(f"Missing permissions to show typing indicator in channel {message.channel.id}")
            # Continue without typing indicator
            error_context = self.error_manager.create_error_context(
                e, "I don't have permission to respond in this channel. Please check my permissions! 🔒"
            )
            self.error_manager.log_error(error_context, "Permission error showing typing indicator")
            await self.error_manager.send_error_response(message, error_context)
            
        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error showing typing indicator: {e}")
            error_context = self.error_manager.create_error_context(
                e, "I'm having trouble communicating with Discord. Please try again! 🌐"
            )
            self.error_manager.log_error(error_context, "Discord HTTP error")
            await self.error_manager.send_error_response(message, error_context)
    
    async def _record_token_usage(self, message: discord.Message, token_usage: Optional[TokenUsage]):
        """Persist token usage stats for the current request."""

        if not token_usage:
            return
        if not self.token_tracker:
            return

        username = (
            message.author.display_name
            or getattr(message.author, "global_name", None)
            or message.author.name
            or str(message.author)
        )
        username = username[:80]  # Avoid storing excessively long names

        guild_id = message.guild.id if message.guild else 0
        guild_name = message.guild.name if message.guild else "Direct Messages"

        try:
            await self.token_tracker.record_usage(
                user_id=message.author.id,
                username=username,
                guild_id=guild_id,
                guild_name=guild_name,
                input_tokens=token_usage.input_tokens,
                output_tokens=token_usage.output_tokens,
                total_tokens=token_usage.total_tokens,
            )
        except Exception as exc:
            logger.error(f"Failed to record token usage for user {message.author.id}: {exc}")

    async def _send_response_safely(self, message: discord.Message, response_content: str, grounding_sources: list = None):
        """
        Safely send a response to Discord with error handling.
        
        Implements requirement 1.4: Post generated responses as replies to original messages.
        
        Args:
            message: The original Discord message to reply to
            response_content: The AI-generated response content
            grounding_sources: Optional list of grounding sources from the API
        """
        try:
            # Validate response content
            if not response_content or not response_content.strip():
                logger.warning("Empty response content generated")
                await message.reply("I generated a response, but it appears to be empty. Could you try asking differently? 🤔")
                return

            # Render LaTeX and format tables for Discord
            rendered = self.content_renderer.process_response(response_content)
            response_content = rendered.text
            attachments = rendered.attachments
            if attachments:
                logger.info(f"Content renderer produced {len(attachments)} image attachment(s) (LaTeX)")

            # Check if response is too long for Discord (2000 character limit)
            if len(response_content) > 2000:
                logger.info(f"Response too long ({len(response_content)} chars), splitting into multiple messages")
                sent_message = await self._send_split_response(message, response_content, attachments=attachments)
            else:
                # Send the response as a reply (with any LaTeX image attachments)
                sent_message = await message.reply(response_content, files=attachments if attachments else None)
                logger.info(f"Successfully sent response to {message.author} in #{getattr(message.channel, 'name', 'DM')}")
            
            # Send grounding sources as a separate message if available
            if grounding_sources and len(grounding_sources) > 0:
                await self._send_grounding_sources(sent_message, grounding_sources)
            
            return sent_message
            
        except discord.Forbidden as e:
            logger.error(f"Permission denied sending response in channel {message.channel.id}")
            error_context = self.error_manager.create_error_context(
                e, "I don't have permission to send messages in this channel. Please check my permissions! 🔒"
            )
            await self.error_manager.send_error_response(
                message, 
                error_context, 
                fallback_reaction="🔒"
            )
            
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limit
                logger.warning(f"Rate limited sending response: {e}")
                error_context = self.error_manager.create_error_context(
                    e, "I'm being rate limited by Discord. Please try again in a moment! 🕒"
                )
            elif e.status >= 500:  # Server error
                logger.error(f"Discord server error sending response: {e}")
                error_context = self.error_manager.create_error_context(
                    e, "Discord is experiencing issues. Please try again in a moment! 🛠️"
                )
            else:
                logger.error(f"HTTP error sending response: {e}")
                error_context = self.error_manager.create_error_context(
                    e, "I had trouble sending my response. Please try again! 📤"
                )
            
            await self.error_manager.send_error_response(
                message, 
                error_context, 
                fallback_reaction="⚠️"
            )
            
        except discord.NotFound as e:
            logger.error(f"Message or channel not found: {e}")
            error_context = self.error_manager.create_error_context(
                e, "The message or channel no longer exists. Please try again! 🔍"
            )
            await self.error_manager.send_error_response(
                message, 
                error_context, 
                fallback_reaction="❓"
            )
            
        except Exception as e:
            logger.error(f"Unexpected error sending response: {e}", exc_info=True)
            error_context = self.error_manager.handle_discord_error(e, message)
            await self.error_manager.send_error_response(
                message, 
                error_context, 
                fallback_reaction="⚠️"
            )
    
    @staticmethod
    def _clean_split_part_for_embed(content: str) -> str:
        """Remove continuation markers so embed pages show clean content."""
        cleaned = re.sub(r'^\*\(continued from part \d+/\d+\)\*\n\n', '', content)
        cleaned = re.sub(r'\n\n\*\(continues in part \d+/\d+\)\*$', '', cleaned)
        cleaned = re.sub(r'^\*\(continued\.\.\.\)\*\n\n', '', cleaned)
        cleaned = re.sub(r'\n\n\*\(continues\.\.\.\)\*$', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned if cleaned else content.strip()

    async def _send_paginated_embed(
        self,
        pages: List[str],
        sender_user_id: int,
        send_page_callable: Callable[..., Awaitable[discord.Message]],
        *,
        attachments: Optional[List[discord.File]] = None,
        title: str = "Response",
    ) -> discord.Message:
        """Send split content as a single message embed with arrow navigation."""
        view = SplitResponsePaginatorView(
            pages=pages,
            sender_user_id=sender_user_id,
            title=title,
            priority_window_seconds=2.0,
            timeout=120.0,
        )

        send_kwargs = {
            "embed": view.build_embed(),
            "view": view,
        }
        if attachments:
            send_kwargs["files"] = attachments

        sent_message = await send_page_callable(**send_kwargs)
        view.message = sent_message
        return sent_message

    async def _send_split_response(self, message: discord.Message, response_content: str, attachments: list = None) -> discord.Message:
        """
        Split a long response into multiple messages and send them using intelligent splitting.

        Args:
            message: The original Discord message to reply to
            response_content: The long response content to split
            attachments: Optional list of discord.File attachments (sent with first message)

        Returns:
            The first sent message (for reply threading)
        """
        try:
            # Use the intelligent message splitter
            message_parts = self.message_splitter.split_message(response_content)

            # Log split statistics
            stats = self.message_splitter.get_split_statistics(message_parts)
            logger.info(f"Message split statistics: {stats}")

            # Validate split integrity
            if not self.message_splitter.validate_split_integrity(response_content, message_parts):
                logger.warning("Split integrity validation failed, falling back to simple split")
                return await self._send_simple_split_response(message, response_content, attachments=attachments)

            # Send the parts — first part replies to original, rest are regular messages
            if len(message_parts) <= 1:
                return await message.reply(message_parts[0].content, files=attachments if attachments else None)

            embed_pages = [self._clean_split_part_for_embed(part.content) for part in message_parts]

            async def _send_first_page(**kwargs) -> discord.Message:
                return await message.reply(**kwargs)

            sent_message = await self._send_paginated_embed(
                pages=embed_pages,
                sender_user_id=message.author.id,
                send_page_callable=_send_first_page,
                attachments=attachments,
                title="Response",
            )
            logger.info(f"Successfully sent response in paginated embed with {len(embed_pages)} pages")
            return sent_message

        except Exception as e:
            logger.error(f"Error in intelligent message splitting: {e}", exc_info=True)
            logger.info("Falling back to simple message splitting")
            return await self._send_simple_split_response(message, response_content, attachments=attachments)
    
    async def _send_simple_split_response(self, message: discord.Message, response_content: str, attachments: list = None) -> discord.Message:
        """
        Fallback method for simple message splitting when intelligent splitting fails.
        
        Args:
            message: The original Discord message to reply to
            response_content: The long response content to split
            
        Returns:
            The first sent message (for reply threading)
        """
        # Use safe split length from config
        max_length = self.config.safe_split_length
        
        # Split by paragraphs first to avoid breaking mid-sentence
        parts = []
        current_part = ""
        
        # Split by double newlines (paragraphs) or single newlines if no paragraphs
        paragraphs = response_content.split('\n\n')
        if len(paragraphs) == 1:
            paragraphs = response_content.split('\n')
        
        for paragraph in paragraphs:
            # If a single paragraph is too long, split it by sentences
            if len(paragraph) > max_length:
                sentences = paragraph.replace('. ', '.|').replace('! ', '!|').replace('? ', '?|').split('|')
                for sentence in sentences:
                    if len(current_part) + len(sentence) + 2 > max_length:
                        if current_part:
                            parts.append(current_part.strip())
                            current_part = sentence
                    else:
                        current_part += sentence + " "
            else:
                # Check if adding this paragraph exceeds the limit
                if len(current_part) + len(paragraph) + 2 > max_length:
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = paragraph
                else:
                    current_part += paragraph + "\n\n"
        
        # Add any remaining content
        if current_part.strip():
            parts.append(current_part.strip())
        
        # Send the parts — first part replies to original, rest are regular messages
        first_message = None

        for idx, part in enumerate(parts):
            # Add continuation indicator
            if idx > 0:
                part = f"*(continued...)*\n\n{part}"
            if idx < len(parts) - 1:
                part = f"{part}\n\n*(continues...)*"

            if idx == 0:
                sent = await message.reply(part, files=attachments if attachments else None)
                first_message = sent
            else:
                sent = await message.channel.send(part)

            logger.info(f"Sent message part {idx + 1}/{len(parts)}")

        logger.info(f"Successfully sent response in {len(parts)} parts using simple splitting")
        return first_message
    
    async def _send_grounding_sources(self, reply_message: discord.Message, grounding_sources: list):
        """
        Send grounding sources as a separate reply message.
        
        Args:
            reply_message: The bot's response message to reply to
            grounding_sources: List of grounding source dictionaries with 'uri' and optional 'title'
        """
        try:
            if not grounding_sources:
                return
            
            # Format sources into a message
            # Wrap URLs in <> to prevent Discord from creating embeds
            sources_text = "📚 **Sources:**\n"
            for idx, source in enumerate(grounding_sources[:10], 1):  # Limit to 10 sources
                uri = source.get('uri', '')
                title = source.get('title')
                
                if title:
                    # Use <> to suppress embed preview
                    sources_text += f"{idx}. {title}: <{uri}>\n"
                else:
                    # Use <> to suppress embed preview
                    sources_text += f"{idx}. <{uri}>\n"
            
            # Check length and truncate if needed
            if len(sources_text) > 2000:
                sources_text = sources_text[:1997] + "..."
            
            # Reply to the bot's own message with sources
            await reply_message.reply(sources_text)
            logger.info(f"Successfully sent {len(grounding_sources)} grounding sources")
            
        except discord.Forbidden:
            logger.warning("No permission to send grounding sources message")
        except discord.HTTPException as e:
            logger.error(f"Failed to send grounding sources: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending grounding sources: {e}", exc_info=True)
    

