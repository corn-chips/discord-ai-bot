"""
Discord bot implementation for the Discord Grok Bot.

This module contains the main DiscordBot class that handles Discord events,
message processing, and coordinates with other services to provide AI responses.
"""

import asyncio
import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple, Callable, Awaitable

import discord
from PIL import Image
import fitz  # PyMuPDF

from ..config import BotConfig
from ..constants import (
    SUPPORTED_TEXT_EXTENSIONS,
    RGB_WHITE_BACKGROUND,
    MAX_PDF_PAGE_PIXELS,
)
from ..models.data_models import TokenUsage, MessageContext
from ..services.context_collector import ContextCollector
from ..services.gemini_client import GeminiClient
from ..services.message_splitter import MessageSplitter
from ..services.image_processing_service import ImageProcessingService
from ..services.user_experience_service import UserExperienceService
from ..services.token_tracker import TokenTracker
from ..services.content_renderer import ContentRenderer
from ..services.rate_limiter import TextRateLimiter
from ..services.report_service import ReportService
from ..services.report_web_server import ReportWebServer
from ..services.pin_service import PinService
from ..services.channel_settings_service import ChannelSettingsService
from ..services.message_visibility_service import MessageVisibilityService
from ..services.user_preferences_service import UserPreferencesService
from ..services.message_index_service import MessageIndexService
from ..services.context_pack_builder import ContextPackBuilder
from ..services.hybrid_context_retriever import HybridContextRetriever
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, get_logger_with_context
from .commands import setup_commands
from .enhanced_command_handler import EnhancedCommandHandler
from .live_message_coordinator import LiveMessageCoordinator
from .media_extraction import MediaExtractionCoordinator
from .rag_event_coordinator import RagEventCoordinator
# SplitResponsePaginatorView is an intentional re-export: it is unused in this
# module but is imported from here by tests, which pin it as part of the surface.
from .response_delivery import ResponseDeliveryCoordinator, SplitResponsePaginatorView
from .response_generation import ResponseGenerationCoordinator


logger = logging.getLogger(__name__)


def _get_accessible_rag_channels(owner: Any) -> List[Any]:
    """Return cached guild message channels whose history the bot can read."""
    bot_user = getattr(owner, "user", None)
    if bot_user is None:
        return []

    accessible = []
    seen_channel_ids = set()
    for guild in getattr(owner, "guilds", []) or []:
        member = getattr(guild, "me", None)
        if member is None:
            get_member = getattr(guild, "get_member", None)
            member = get_member(bot_user.id) if callable(get_member) else None
        if member is None:
            logger.warning(
                "Cannot determine RAG backlog permissions for guild %s",
                getattr(guild, "id", "unknown"),
            )
            continue

        candidates = [
            *(getattr(guild, "channels", []) or []),
            *(getattr(guild, "threads", []) or []),
        ]
        for channel in candidates:
            channel_id = int(channel.id)
            if channel_id in seen_channel_ids:
                continue
            if not callable(getattr(channel, "history", None)):
                continue
            try:
                permissions = channel.permissions_for(member)
            except Exception as exc:
                logger.debug(
                    "Could not inspect RAG backlog permissions for channel %s: %s",
                    channel_id,
                    exc,
                )
                continue
            if not (
                getattr(permissions, "view_channel", False)
                and getattr(permissions, "read_message_history", False)
            ):
                continue
            seen_channel_ids.add(channel_id)
            accessible.append(channel)
    return accessible


def _start_automatic_rag_backlog(owner: Any) -> int:
    """Start a background backlog pass for every accessible guild channel."""
    config = getattr(owner, "config", None)
    retriever = getattr(owner, "hybrid_context_retriever", None)
    if not getattr(config, "rag_enabled", False) or retriever is None:
        return 0

    channels = _get_accessible_rag_channels(owner)
    if not channels:
        logger.info("RAG backlog found no accessible message channels")
        return 0

    configured_limit = getattr(config, "rag_backfill_limit", 0)
    scan_limit = None if configured_limit <= 0 else configured_limit
    bot_user = getattr(owner, "user", None)
    include_bot_user_id = (
        bot_user.id
        if getattr(config, "rag_index_bot_responses", False) and bot_user
        else None
    )
    started = retriever.start_all_channel_pregeneration(
        channels,
        limit=scan_limit,
        include_bot_user_id=include_bot_user_id,
    )
    if not started:
        logger.info("RAG backlog pass is already running")
        return 0

    logger.info(
        "Started resumable RAG backlog for %s accessible channel(s)",
        len(channels),
    )
    return len(channels)


def _get_or_create_live_coordinator(owner: Any) -> LiveMessageCoordinator:
    """Build the live collaborator lazily for private-wrapper compatibility."""
    coordinator = getattr(owner, "_live_message_coordinator", None)
    if coordinator is not None:
        return coordinator

    def _state_dict(name: str) -> dict:
        value = getattr(owner, name, None)
        if value is None:
            value = {}
            setattr(owner, name, value)
        return value

    def _rag_enabled() -> bool:
        return bool(getattr(getattr(owner, "config", None), "rag_enabled", False))

    def _get_bot_user():
        return getattr(owner, "user", None)

    def _get_personality_prompt(channel_id: int) -> Optional[str]:
        service = getattr(owner, "_channel_settings_service", None)
        return service.get_personality_prompt(channel_id) if service else None

    retriever = getattr(owner, "hybrid_context_retriever", None)
    gemini_client = getattr(owner, "gemini_client", None)
    coordinator = LiveMessageCoordinator(
        rate_limiter=owner.text_rate_limiter,
        extract_user_prompt=owner._extract_user_prompt,
        process_message_with_context=owner._process_message_with_context,
        is_enabled=getattr(owner, "_is_live_mode_enabled", lambda _channel_id: False),
        model_name=owner._live_model_name,
        cooldown_seconds=getattr(owner, "_live_cooldown_seconds", 2.0),
        reply_style_instruction=getattr(
            owner,
            "_live_reply_style_instruction",
            "Keep replies very short and natural, like normal chatting.",
        ),
        turn_window=getattr(owner, "_live_turn_window", 6),
        rag_enabled=_rag_enabled,
        retrieve_context=getattr(retriever, "retrieve", None),
        generate_response=getattr(gemini_client, "generate_response", None),
        handle_response_error=getattr(owner, "_handle_response_error", None),
        record_token_usage=getattr(owner, "_record_token_usage", None),
        send_response=getattr(owner, "_send_response_safely", None),
        get_bot_user=_get_bot_user,
        get_personality_prompt=_get_personality_prompt,
        tasks=_state_dict("_live_channel_tasks"),
        pending_messages=_state_dict("_live_pending_messages"),
        locks=_state_dict("_live_channel_locks"),
        context=_state_dict("_live_channel_context"),
    )
    setattr(owner, "_live_message_coordinator", coordinator)
    return coordinator


def _get_or_create_rag_event_coordinator(owner: Any) -> RagEventCoordinator:
    """Build the RAG mutation collaborator lazily for wrapper compatibility."""
    coordinator = getattr(owner, "_rag_event_coordinator", None)
    if coordinator is not None:
        return coordinator

    def _config_flag(name: str) -> bool:
        return bool(getattr(getattr(owner, "config", None), name, False))

    coordinator = RagEventCoordinator(
        message_index_service=owner.message_index_service,
        rag_enabled=lambda: _config_flag("rag_enabled"),
        index_bot_responses=lambda: _config_flag("rag_index_bot_responses"),
        get_bot_user=lambda: getattr(owner, "user", None),
    )
    setattr(owner, "_rag_event_coordinator", coordinator)
    return coordinator


def _get_or_create_response_delivery(owner: Any) -> ResponseDeliveryCoordinator:
    """Build the response-delivery collaborator lazily for wrapper compatibility."""
    coordinator = getattr(owner, "_response_delivery", None)
    if coordinator is not None:
        return coordinator

    coordinator = ResponseDeliveryCoordinator(
        message_splitter=owner.message_splitter,
        content_renderer=owner.content_renderer,
        error_manager=owner.error_manager,
        get_split_length=lambda: owner.config.message_split_length,
        index_sent_bot_response=owner._index_sent_bot_response,
    )
    setattr(owner, "_response_delivery", coordinator)
    return coordinator


def _get_or_create_media_extraction(owner: Any) -> MediaExtractionCoordinator:
    """Build the media collaborator lazily for private-wrapper compatibility."""
    coordinator = getattr(owner, "_media_extraction", None)
    if coordinator is not None:
        return coordinator

    config = owner.config
    coordinator = MediaExtractionCoordinator(
        pdf_converter=owner._convert_pdf_to_images,
        image_to_rgb=owner._convert_image_to_rgb,
        get_max_context_images=lambda: getattr(config, "max_context_images", 6),
        get_max_context_messages=lambda: config.max_context_messages,
        get_max_text_file_size=lambda: config.max_text_file_size_bytes,
        supported_text_extensions=SUPPORTED_TEXT_EXTENSIONS,
    )
    setattr(owner, "_media_extraction", coordinator)
    return coordinator


def _get_or_create_response_generation(owner: Any) -> ResponseGenerationCoordinator:
    """Build the generation collaborator lazily for private-wrapper compatibility."""
    coordinator = getattr(owner, "_response_generation", None)
    if coordinator is not None:
        return coordinator

    async def _send_status_message(
        message: discord.Message,
        content: str,
    ) -> discord.Message:
        return await message.reply(content)

    def _get_personality_prompt(channel_id: int) -> Optional[str]:
        service = getattr(owner, "_channel_settings_service", None)
        return service.get_personality_prompt(channel_id) if service else None

    def _remove_duplicate_context(recent: List[Any], replied: List[Any]) -> List[Any]:
        collector = getattr(owner, "context_collector", None)
        if collector is None:
            return recent + [item for item in replied if item not in recent]
        return collector._remove_duplicate_messages(recent, replied)

    coordinator = ResponseGenerationCoordinator(
        gemini_client=owner.gemini_client,
        error_manager=owner.error_manager,
        performance_logger=owner.performance_logger,
        extract_images=owner._extract_images_from_message,
        extract_context_images=owner._extract_context_images,
        extract_audio=owner._extract_audio_from_message,
        extract_files=owner._extract_files_from_message,
        attach_image_order_metadata=getattr(
            owner,
            "_attach_image_order_metadata",
            MediaExtractionCoordinator.attach_image_order_metadata,
        ),
        remove_duplicate_context=_remove_duplicate_context,
        resolve_request_preferences=owner._resolve_request_preferences,
        get_personality_prompt=_get_personality_prompt,
        get_token_tracker=lambda: getattr(owner, "token_tracker", None),
        send_status_message=_send_status_message,
        deliver_response=owner._send_response_safely,
    )
    setattr(owner, "_response_generation", coordinator)
    return coordinator


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

        # PyMuPDF work is isolated to one worker because concurrent conversions
        # are not safe within this process. The executor is drained in close().
        self._pdf_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="discord-pdf",
        )
        
        self.config = config
        self._media_extraction = _get_or_create_media_extraction(self)
        self.error_manager = ErrorManager(config)
        self.performance_logger = PerformanceLogger("discord_bot")
        self.token_tracker = None
        try:
            self.token_tracker = TokenTracker(config.token_db_path)
            logger.info("✅ Token tracker initialized")
        except Exception as exc:
            logger.error(f"Failed to initialize token tracker: {exc}", exc_info=True)
            self.token_tracker = None

        self.report_service = None
        self.report_web_server = None
        try:
            self.report_service = ReportService(config.token_db_path)
            if config.report_web_enabled:
                self.report_web_server = ReportWebServer(
                    self.report_service,
                    host=config.report_web_host,
                    port=config.report_web_port,
                )
        except Exception as exc:
            logger.error(f"Failed to initialize report tracking: {exc}", exc_info=True)
        
        # Initialize core services
        self.context_collector = ContextCollector(
            max_context_messages=config.max_context_messages,
            reply_context_range=config.reply_context_range,
            cutoff_hours=config.context_cutoff_hours
        )
        self.gemini_client = GeminiClient(config)
        self.message_index_service = MessageIndexService(
            config.rag_database_path,
            embedding_model=config.rag_embedding_model,
            embedding_dimensions=config.rag_embedding_dimensions,
            embedding_min_words=config.rag_embedding_min_words,
            embedding_min_alphanumeric_chars=config.rag_embedding_min_alphanumeric_chars,
            vector_cache_enabled=config.rag_vector_cache_enabled,
            legacy_db_path=config.token_db_path,
        )
        self._pin_service = PinService(
            db_path=config.rag_database_path,
            legacy_db_path=config.token_db_path,
        )

        # Construct these here rather than during command registration (DAB-002).
        #
        # They used to be attached to the bot as a side effect of
        # register_personalization_commands. on_ready wraps setup_commands in a
        # broad `except Exception: log`, so any registrar raising left these
        # attributes absent -- and the code that reads them uses getattr/hasattr,
        # so nothing failed loudly. Live mode reported itself disabled for every
        # channel and user preferences were skipped silently, for the lifetime
        # of the process, after one log line.
        #
        # Building them in __init__ means a registration failure can no longer
        # decide whether they exist. The registrars now reuse these instances.
        self._channel_settings_service = ChannelSettingsService(
            db_path=config.token_db_path,
            personalities=config.personalities,
        )
        self._message_visibility_service = MessageVisibilityService(
            db_path=config.token_db_path,
        )
        self._user_prefs_service = UserPreferencesService(
            db_path=config.token_db_path,
            valid_models=config.valid_models,
            valid_languages=config.valid_languages,
        )

        self._rag_event_coordinator = _get_or_create_rag_event_coordinator(self)
        self.context_pack_builder = ContextPackBuilder()
        self.hybrid_context_retriever = HybridContextRetriever(
            config=config,
            message_index=self.message_index_service,
            context_collector=self.context_collector,
            gemini_client=self.gemini_client,
            pack_builder=self.context_pack_builder,
            pin_service=self._pin_service,
        )
        self.message_splitter = MessageSplitter(
            max_length=config.message_split_length,
            preserve_formatting=config.preserve_code_blocks,
            add_continuation_indicators=config.add_continuation_indicators,
            continuation_overhead=config.continuation_overhead,
        )
        
        # Initialize content renderer for LaTeX and table formatting
        self.content_renderer = ContentRenderer()
        self._response_delivery = _get_or_create_response_delivery(self)

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
        # A separate, much tighter budget for commands that fan out into many
        # model calls or feed a whole channel's history into one prompt.
        # /deepresearch and /summarize were governed only by the general text
        # limit above, which permits 60 an hour.
        self.expensive_command_limiter = TextRateLimiter(
            per_minute=config.expensive_command_limit_per_minute,
            per_hour=config.expensive_command_limit_per_hour,
        )
        
        # Initialize enhanced command handler
        self.enhanced_command_handler = None
        try:
            self.enhanced_command_handler = EnhancedCommandHandler(
                self, self.image_processing_service, self.error_manager, self.gemini_client,
            )
            logger.info("✅ Request router initialized")
        except Exception as e:
            logger.error(f"Failed to initialize request router: {e}")
        
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
        self._live_channel_context: Dict[int, Any] = {}
        self._live_message_coordinator = _get_or_create_live_coordinator(self)
        
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

        if self.report_web_server:
            try:
                await self.report_web_server.start()
            except Exception as e:
                logger.error(f"Failed to start report web UI: {e}", exc_info=True)
        
        # Log service initialization status
        logger.info("🔧 Service initialization status:")
        logger.info(f"  • Context Collector: ✅ Active")
        logger.info(f"  • Gemini Client: ✅ Active")
        logger.info(f"  • Message Splitter: ✅ Active")
        logger.info(f"  • User Experience Service: ✅ Active")
        logger.info(f"  • Image Processing: {'✅ Active' if self.image_processing_service else '❌ Disabled'}")
        logger.info(f"  • Enhanced Commands: {'✅ Active' if self.enhanced_command_handler else '❌ Disabled'}")
        logger.info(
            "  - Report Tracking: %s",
            "Active" if self.report_service else "Disabled",
        )
        logger.info(
            "  - Hybrid Message RAG: %s",
            "Active" if self.config.rag_enabled else "Disabled",
        )
        logger.info(
            "  - Report Web UI: %s",
            "Active" if self.report_web_server and self.report_web_server.is_running else "Disabled",
        )

        # Set bot status
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="@mentions for AI responses"
        )
        await self.change_presence(activity=activity)
        
        # Set up slash commands.
        #
        # Registration and syncing are separate concerns and get separate
        # handlers. They shared one `try`, so a registrar raising was reported
        # as "Failed to sync slash commands" -- sending the operator to look at
        # Discord when the fault was in their own code -- and the bot then
        # carried on serving with a partial or empty command tree.
        registered = False
        try:
            await setup_commands(self, self.config, self.gemini_client, self.performance_logger, self.token_tracker)
            registered = True
        except Exception as e:
            logger.critical(
                "Slash command registration FAILED: %s. The bot is running with an "
                "incomplete command tree; anything registered after the failure point "
                "does not exist. This is a code fault, not a Discord outage.",
                e,
                exc_info=True,
            )

        try:
            synced = await self.tree.sync()
            if registered:
                logger.info(f"Synced {len(synced)} slash command(s) globally")
            else:
                logger.critical(
                    "Synced only %d slash command(s) after a registration failure; "
                    "the command tree is incomplete.",
                    len(synced),
                )

            # Log each synced command for verification
            for cmd in synced:
                logger.info(f"  ✓ Command synced: /{cmd.name} - {cmd.description}")

        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}", exc_info=True)

        _start_automatic_rag_backlog(self)
        
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

    async def on_raw_message_delete(self, payload):
        """Delegate one Discord deletion to the RAG event collaborator."""
        await _get_or_create_rag_event_coordinator(self).handle_raw_delete(
            payload.message_id
        )

    async def on_raw_bulk_message_delete(self, payload):
        """Delegate Discord bulk deletion to the RAG event collaborator."""
        await _get_or_create_rag_event_coordinator(self).handle_raw_bulk_delete(
            payload.message_ids
        )

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Delegate an edited Discord message to the RAG event collaborator."""
        await _get_or_create_rag_event_coordinator(self).handle_message_edit(
            before,
            after,
        )

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
        status['report_tracking'] = "Available" if self.report_service else "Unavailable"
        if self.report_web_server and self.report_web_server.is_running:
            status['report_web_ui'] = "Available"
        elif self.config.report_web_enabled:
            status['report_web_ui'] = "Unavailable"
        else:
            status['report_web_ui'] = "Disabled"
        
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
        
        if self.report_web_server:
            try:
                await self.report_web_server.stop()
            except Exception as e:
                logger.error(f"Error stopping report web UI: {e}")
        
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

        rag_retriever = getattr(self, "hybrid_context_retriever", None)
        close_rag = getattr(rag_retriever, "close", None)
        if close_rag is not None:
            await close_rag()
            logger.info("RAG background workers stopped")
        
        live_coordinator = getattr(self, "_live_message_coordinator", None)
        if live_coordinator is not None:
            await live_coordinator.close()
            logger.info("Live channel workers stopped")
        else:
            # Compatibility for partially constructed instances used by lifecycle tests.
            live_tasks = [
                task
                for task in getattr(self, "_live_channel_tasks", {}).values()
                if not task.done()
            ]
            if live_tasks:
                for task in live_tasks:
                    task.cancel()
                await asyncio.gather(*live_tasks, return_exceptions=True)
                logger.info("Live channel workers stopped")

        pdf_executor = self._pdf_executor
        self._pdf_executor = None
        if pdf_executor is not None:
            try:
                await asyncio.to_thread(
                    pdf_executor.shutdown,
                    wait=True,
                    cancel_futures=True,
                )
                logger.info("PDF conversion worker stopped")
            except Exception as e:
                logger.error(f"Error stopping PDF conversion worker: {e}")

        await super().close()
        logger.info("✅ Bot shutdown complete")
    
    def _get_live_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Compatibility wrapper for the live collaborator's channel lock."""
        return _get_or_create_live_coordinator(self).get_channel_lock(channel_id)

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
        """Compatibility wrapper for live-mode queueing."""
        await _get_or_create_live_coordinator(self).enqueue(message)

    async def _process_live_messages(self, messages: List[discord.Message]) -> bool:
        """Compatibility wrapper for processing one live-mode batch."""
        return await _get_or_create_live_coordinator(self).process_messages(messages)

    @staticmethod
    def _build_live_user_prompt(prompt_entries: List[Tuple[discord.Message, str]]) -> str:
        """Compatibility wrapper for building an ordered live prompt."""
        return LiveMessageCoordinator.build_user_prompt(prompt_entries)

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

        if self.config.rag_enabled:
            try:
                indexed = await self.message_index_service.index_discord_message_async(message)
                if indexed:
                    self.hybrid_context_retriever.schedule_pending_embeddings(
                        message.channel.id
                    )
            except Exception as exc:
                logger.debug("Failed to index incoming message %s for RAG: %s", message.id, exc)

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
        logger.debug("Mention message content redacted")
        
        try:
            is_allowed, rate_limit_message = await self.text_rate_limiter.check_and_record(message.author.id)
            if not is_allowed:
                context_logger.warning(f"Text rate limit triggered for user {message.author.id}")
                await message.reply(rate_limit_message)
                return

            complexity_level = "low"
            routed_intent = "unknown"
            needs_context = True
            model_override = None

            # Try enhanced command handler first if available
            if self.enhanced_command_handler:
                handled, routing = await self.enhanced_command_handler.handle_message(message)
                complexity_level = routing.complexity
                routed_intent = routing.intent.value
                needs_context = routing.needs_context
                if handled:
                    context_logger.info("Message handled by enhanced command handler")
                    return
                
                # The router model has already determined the appropriate complexity.
                routed_model = self.gemini_client.get_model_for_complexity(complexity_level)
                context_logger.info(
                    "Router complexity %s recommends model %s; final model follows request/user/global precedence",
                    complexity_level,
                    routed_model,
                )
            
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
                needs_context=needs_context,
                model_override=model_override,
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
        needs_context: bool = True,
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
            if self.config.rag_enabled and routed_intent != "image_generate":
                rag_context = None
                try:
                    rag_context = await self.hybrid_context_retriever.retrieve(
                        message=message,
                        user_prompt=user_prompt,
                        complexity_level=complexity_level,
                        bot_user_id=self.user.id if self.user else None,
                        needs_context=needs_context,
                        force_full_context=bool(message.reference) or routed_intent == "live_mode",
                    )
                except Exception as exc:
                    logger.warning(
                        "Hybrid RAG failed for message %s; using legacy context fallback: %s",
                        message.id,
                        exc,
                    )

                # Only a *retrieval* failure may fall through to the legacy path.
                # Generation and delivery used to sit inside this try, so anything
                # that failed after the model had already answered re-entered the
                # legacy path and answered again: a second reply and a second
                # Gemini charge for one inbound message.
                #
                # The gate is `is not None`, not truthiness. A retrieval that
                # legitimately returns an empty context has succeeded, and
                # re-running it buys a duplicate generation for nothing.
                if rag_context is not None:
                    await self._generate_and_send_response(
                        message,
                        user_prompt,
                        rag_context,
                        model_override=model_override,
                        prompt_mode_override=prompt_mode_override,
                        search_override=search_override,
                        show_status_message=show_status_message,
                        skip_context_media=skip_context_media,
                        apply_user_preferences=apply_user_preferences,
                        complexity_level=complexity_level,
                    )
                    return

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
                complexity_level=complexity_level,
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
            # Categorize the error for more specific handling.
            #
            # No include_error_details=True here. This is the highest-traffic
            # error path in the bot, and str(exc) routinely carries absolute
            # paths with the OS username in them. The operator still gets the
            # full text -- with a stack trace -- through dev_mode_enabled; a
            # passer-by in the channel should not.
            error_context = self.error_manager.create_error_context(e)
            
            self.error_manager.log_error(error_context, f"Unexpected error in channel {message.channel.id}")
            await self.error_manager.send_error_response(message, error_context)

    def _get_context_limit_for_complexity(self, complexity_level: str) -> int:
        """Select context window size by complexity tier."""
        if complexity_level == "high":
            return self.config.context_messages_high
        if complexity_level == "medium":
            return self.config.context_messages_medium
        return self.config.context_messages_low

    def _resolve_request_preferences(
        self,
        *,
        user_id: int,
        request_model_override: Optional[str],
        apply_user_preferences: bool,
        complexity_level: str,
    ) -> Tuple[str, Optional[str]]:
        """Resolve request model precedence and the user's language preference."""
        preferred_model = None
        preferred_language = None
        user_prefs_service = getattr(self, "_user_prefs_service", None)
        if apply_user_preferences and user_prefs_service:
            prefs = user_prefs_service.get_preferences(user_id)
            preferred_model = prefs.preferred_model
            preferred_language = prefs.preferred_language

        if request_model_override:
            model_name = request_model_override
        elif preferred_model:
            model_name = preferred_model
        elif self.gemini_client.has_runtime_model_override():
            model_name = self.gemini_client.get_current_model()
        else:
            model_name = self.gemini_client.get_model_for_complexity(complexity_level)

        return model_name, preferred_language
    
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
        pdf_executor = self._pdf_executor
        if pdf_executor is None:
            raise RuntimeError("PDF conversion worker is shut down")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            pdf_executor,
            self._convert_pdf_to_images_sync,
            pdf_bytes,
            filename,
        )

    def _pdf_render_scale_for_page(self, page) -> float:
        """
        Render scale for one PDF page, reduced if it would rasterise too large.

        The page geometry is taken from the uploaded file, so a small PDF can
        declare an enormous page and drive an arbitrarily large allocation. This
        returns the configured scale, or the largest scale that keeps the page
        under MAX_PDF_PAGE_PIXELS.

        Args:
            page: The PyMuPDF page about to be rendered

        Returns:
            The render scale to use, never greater than the configured one
        """
        scale = self.config.pdf_render_scale
        rect = page.rect
        pixels = (rect.width * scale) * (rect.height * scale)

        if pixels <= MAX_PDF_PAGE_PIXELS or pixels <= 0:
            return scale

        # Aim slightly under the ceiling. MuPDF rounds each dimension up to a
        # whole pixel, so landing exactly on the cap overshoots it: an
        # 8000x8000 pt page came out at 6325x6325 = 40,005,625 against a
        # 40,000,000 ceiling.
        reduced = scale * math.sqrt((MAX_PDF_PAGE_PIXELS * 0.99) / pixels)
        logger.warning(
            "PDF page is %.0fx%.0f pt; rendering at scale %.3f instead of %.2f to stay "
            "under %d pixels (would have been %.0f MP)",
            rect.width, rect.height, reduced, scale,
            MAX_PDF_PAGE_PIXELS, pixels / 1e6,
        )
        return reduced

    def _convert_pdf_to_images_sync(self, pdf_bytes: bytes, filename: str) -> List[Image.Image]:
        """Synchronously render PDF pages for the async worker-thread wrapper."""
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
                        
                        # Render page to pixmap (image) at configured resolution,
                        # reducing the scale if this page would rasterise to an
                        # unreasonable number of pixels.
                        #
                        # The page geometry comes from the uploaded file, so the
                        # render size is attacker-chosen. Clamping has to happen
                        # BEFORE get_pixmap: that call is where the allocation
                        # happens (792 MB on the 256 MP case), and it succeeds.
                        scale = self._pdf_render_scale_for_page(page)
                        mat = fitz.Matrix(scale, scale)
                        pix = page.get_pixmap(matrix=mat)
                        
                        # Build the PIL image straight from the pixmap buffer.
                        #
                        # This used to PNG-encode the pixmap and immediately
                        # decode it back, though pix.samples is already the raw
                        # buffer -- pure waste, measured at 4-10x depending on
                        # the fixture, and pixel-identical either way.
                        #
                        # The mode must be derived, not assumed. A pixmap's
                        # sample count depends on both its colourspace and
                        # whether it carries alpha, and Image.frombytes with the
                        # wrong mode misreads the buffer *silently* rather than
                        # raising -- a white CMYK page comes out solid black.
                        #
                        # Anything that is not a plain gray or RGB base is
                        # converted through MuPDF first, which is what
                        # tobytes("png") used to do internally. Alpha is kept
                        # and flattened onto white by _convert_image_to_rgb
                        # below, the same as before.
                        if pix.colorspace is None or pix.colorspace.n not in (1, 3):
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        if pix.alpha:
                            mode = "LA" if pix.n == 2 else "RGBA"
                        else:
                            mode = "L" if pix.n == 1 else "RGB"
                        image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                        if image.mode == "LA":
                            # _convert_image_to_rgb only composites RGBA; an LA
                            # image would fall through to .convert("RGB") and
                            # turn every transparent pixel black.
                            image = image.convert("RGBA")
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
    def _attach_image_order_metadata(
        image_context: List[Dict[str, Any]],
        context: List[MessageContext],
        current_message: discord.Message
    ) -> List[Dict[str, Any]]:
        """Compatibility wrapper for explicit image/message ordering metadata."""
        return MediaExtractionCoordinator.attach_image_order_metadata(
            image_context,
            context,
            current_message,
        )
    
    async def _extract_images_from_message(self, message: discord.Message) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """Compatibility wrapper for current/replied image and PDF extraction."""
        return await _get_or_create_media_extraction(self).extract_images_from_message(
            message
        )

    async def _extract_context_images(
        self,
        message: discord.Message,
        context: List[MessageContext],
        exclude_message_ids: Optional[set[int]] = None
    ) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """Compatibility wrapper for bounded context-image extraction."""
        return await _get_or_create_media_extraction(self).extract_context_images(
            message,
            context,
            exclude_message_ids,
        )
    
    async def _extract_audio_from_message(self, message: discord.Message) -> List[Dict[str, Any]]:
        """Compatibility wrapper for current/replied audio extraction."""
        return await _get_or_create_media_extraction(self).extract_audio_from_message(
            message
        )
    
    async def _extract_files_from_message(self, message: discord.Message) -> tuple[List[Dict[str, str]], List[str]]:
        """Compatibility wrapper for current/replied text-file extraction."""
        return await _get_or_create_media_extraction(self).extract_files_from_message(
            message
        )
        
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
    
    async def _handle_response_error(self, message: discord.Message, api_response):
        """Compatibility wrapper for shared API-response error handling."""
        await _get_or_create_response_generation(self).handle_response_error(
            message,
            api_response,
        )
    
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
        complexity_level: str = "low",
    ):
        """Compatibility wrapper for AI response generation orchestration."""
        await _get_or_create_response_generation(self).generate_and_send_response(
            message,
            user_prompt,
            context,
            model_override=model_override,
            prompt_mode_override=prompt_mode_override,
            search_override=search_override,
            show_status_message=show_status_message,
            skip_context_media=skip_context_media,
            apply_user_preferences=apply_user_preferences,
            complexity_level=complexity_level,
        )

    async def _record_token_usage(self, message: discord.Message, token_usage: Optional[TokenUsage]):
        """Compatibility wrapper for best-effort token usage persistence."""
        await _get_or_create_response_generation(self).record_token_usage(
            message,
            token_usage,
        )

    async def _index_sent_bot_response(
        self,
        *,
        source_message: discord.Message,
        sent_message: Optional[discord.Message],
        response_content: str,
    ) -> None:
        """Persist this bot's response so future RAG retrieval can recall it."""
        if not self.config.rag_enabled or not self.config.rag_index_bot_responses:
            return
        if not sent_message or not response_content:
            return
        try:
            indexed = await self.message_index_service.index_bot_response_async(
                message_id=sent_message.id,
                channel_id=sent_message.channel.id,
                guild_id=source_message.guild.id if source_message.guild else None,
                author_id=self.user.id if self.user else None,
                author_name=self.user.display_name if self.user else "Grok",
                content_text=response_content,
                reply_to_message_id=source_message.id,
                created_at=getattr(sent_message, "created_at", datetime.now(timezone.utc)),
            )
            if indexed:
                self.hybrid_context_retriever.schedule_pending_embeddings(
                    sent_message.channel.id
                )
        except Exception as exc:
            logger.debug("Failed to index bot response %s for RAG: %s", getattr(sent_message, "id", None), exc)

    async def _send_response_safely(self, message: discord.Message, response_content: str, grounding_sources: list = None):
        """Compatibility wrapper for rendered and indexed response delivery."""
        return await _get_or_create_response_delivery(self).send_response_safely(
            message,
            response_content,
            grounding_sources,
        )
    
    async def _send_paginated_embed(
        self,
        pages: List[str],
        sender_user_id: int,
        send_page_callable: Callable[..., Awaitable[discord.Message]],
        *,
        attachments: Optional[List[discord.File]] = None,
        title: str = "Response",
    ) -> discord.Message:
        """Compatibility wrapper for paginator delivery."""
        return await _get_or_create_response_delivery(self).send_paginated_embed(
            pages,
            sender_user_id,
            send_page_callable,
            attachments=attachments,
            title=title,
        )
