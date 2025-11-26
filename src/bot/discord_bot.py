"""
Discord bot implementation for the Discord Grok Bot.

This module contains the main DiscordBot class that handles Discord events,
message processing, and coordinates with other services to provide AI responses.
"""

import asyncio
import io
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

import discord
from discord.ext import commands
from PIL import Image
import fitz  # PyMuPDF

from ..config import BotConfig
from ..constants import (
    SUPPORTED_TEXT_EXTENSIONS,
    MAX_TEXT_FILE_SIZE_BYTES,
    PDF_RENDER_SCALE,
    RGB_WHITE_BACKGROUND,
)
from ..models.data_models import APIResponse, ImageEditRequest, EditType, TokenUsage, MessageContext
from ..services.context_collector import ContextCollector
from ..services.gemini_client import GeminiClient
from ..services.message_splitter import MessageSplitter
from ..services.image_processing_service import ImageProcessingService
from ..services.user_experience_service import UserExperienceService
from ..services.token_tracker import TokenTracker
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, TimingContext, get_logger_with_context
from .commands import setup_commands
from .enhanced_command_handler import EnhancedCommandHandler


logger = logging.getLogger(__name__)


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
            reply_context_range=config.reply_context_range
        )
        self.gemini_client = GeminiClient(config)
        self.message_splitter = MessageSplitter(
            max_length=config.message_split_length,
            preserve_formatting=config.preserve_code_blocks
        )
        
        # Initialize UX enhancement services
        self.user_experience_service = UserExperienceService(config)
        
        # Initialize image processing service if configured
        self.image_processing_service = None
        if hasattr(config, 'nano_banana_api_key') and config.nano_banana_api_key:
            try:
                self.image_processing_service = ImageProcessingService(config)
                logger.info("✅ Image processing service initialized")
            except Exception as e:
                logger.error(f"Failed to initialize image processing service: {e}")
                logger.warning("Image editing features will be disabled")
        
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
            except Exception:
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
        await super().close()
        logger.info("✅ Bot shutdown complete")
    
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
            # Try enhanced command handler first if available
            if self.enhanced_command_handler:
                handled, complexity_level = await self.enhanced_command_handler.handle_message(message)
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
            await self._process_message_with_context(message, user_prompt)
            
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
    
    async def _process_message_with_context(self, message: discord.Message, user_prompt: str):
        """
        Process a message with full context collection and response generation.
        
        Implements requirements 2.1, 3.1: Connect ContextCollector to message
        event processing and implement reply detection with enhanced context retrieval.
        
        Args:
            message: The Discord message object
            user_prompt: The extracted user prompt without mentions
        """
        try:
            # Check if this is a reply and collect appropriate context
            if message.reference:
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
                    standard_context = await self.context_collector.get_channel_context(message.channel, bot_user=self.user)
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
                    combined_context = await self.context_collector.get_channel_context(message.channel, bot_user=self.user)
                except discord.Forbidden:
                    logger.warning(f"No permission to read message history in channel {message.channel.id}")
                    combined_context = []
                except discord.HTTPException as e:
                    logger.error(f"Failed to fetch channel context: {e}")
                    combined_context = []
            
            logger.info(f"Collected {len(combined_context)} messages for context")

            # If router thinks this is an image generation request, we must avoid passing the full conversation context
            # to the text model (previously a text model might say "I cannot generate images" and pollute history).
            try:
                if self.enhanced_command_handler:
                    has_images = any(
                        attachment.content_type and attachment.content_type.startswith('image/')
                        for attachment in message.attachments
                    )
                    intent, _complexity = await self.enhanced_command_handler._check_intent_and_complexity(message.content, has_images)
                    from .enhanced_command_handler import CommandIntent
                    if intent == CommandIntent.IMAGE_GENERATE:
                        logger.info("Routing detected IMAGE_GENERATE while processing context; clearing context except reply/current message")
                        # Keep only the replied-to message context and the current message as per user's request
                        filtered_context: list[MessageContext] = []
                        if message.reference:
                            reply_context = await self.context_collector.get_reply_context(message)
                            # reply_context contains several messages around the replied-to message; keep only those
                            filtered_context.extend(reply_context)

                        # Also add a context entry for the current message (so the model knows the prompt in context)
                        filtered_context.append(MessageContext(
                            content=message.content,
                            author=message.author.display_name,
                            timestamp=message.created_at,
                            message_id=message.id,
                            is_reply=message.reference is not None,
                            replied_to_id=message.reference.message_id if message.reference else None
                        ))

                        # Either directly invoke image generation handler (preferred) or proceed with a filtered context
                        try:
                            await self.enhanced_command_handler.handle_image_generation_command(message)
                            return
                        except Exception as exc:
                            logger.error(f"Failed to handle image generation via enhanced handler: {exc}", exc_info=True)
                            # Fallback: continue with filtered context and let the text model handle it (safer than full context)
                            combined_context = filtered_context

            except Exception as e:
                logger.error(f"Failed to re-check router intent in _process_message_with_context: {e}", exc_info=True)

            # Generate AI response using Gemini API with collected or filtered context
            await self._generate_and_send_response(message, user_prompt, combined_context)
            
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
            
            # Open PDF with PyMuPDF
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = len(pdf_document)
            
            logger.info(f"PDF has {page_count} page(s)")
            
            # Convert each page to an image
            for page_num in range(page_count):
                try:
                    logger.info(f"Converting page {page_num + 1}/{page_count}...")
                    
                    page = pdf_document[page_num]
                    
                    # Render page to pixmap (image) at 2x resolution for better quality
                    mat = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
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
            
            pdf_document.close()
            
            logger.info(f"✅ PDF CONVERSION COMPLETE: {len(images)} pages converted successfully")
            logger.info(f"=" * 80)
            
        except Exception as e:
            logger.error(f"Failed to convert PDF {filename}: {e}", exc_info=True)
            logger.info(f"=" * 80)
        
        return images
    
    async def _extract_images_from_message(self, message: discord.Message) -> List[Image.Image]:
        """
        Extract and download images from a Discord message.
        Also converts PDF files to images for processing.
        
        Args:
            message: The Discord message to extract images from
            
        Returns:
            List of PIL Image objects from the message attachments
        """
        images = []
        
        # Check message attachments for images and PDFs
        for attachment in message.attachments:
            # Check if attachment is a PDF
            if attachment.content_type == 'application/pdf' or attachment.filename.lower().endswith('.pdf'):
                try:
                    logger.info(f"📄 PDF detected: {attachment.filename}")
                    # Download the PDF
                    pdf_bytes = await attachment.read()
                    
                    # Convert PDF pages to images
                    pdf_images = await self._convert_pdf_to_images(pdf_bytes, attachment.filename)
                    
                    if pdf_images:
                        images.extend(pdf_images)
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
                    logger.info(f"Loaded image from attachment: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                    
                except Exception as e:
                    logger.error(f"Failed to load image from attachment {attachment.filename}: {e}")
        
        # Also check if the message is a reply and has images/PDFs in the replied message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                for attachment in replied_message.attachments:
                    # Check for PDFs in replied message
                    if attachment.content_type == 'application/pdf' or attachment.filename.lower().endswith('.pdf'):
                        try:
                            logger.info(f"📄 PDF detected in replied message: {attachment.filename}")
                            pdf_bytes = await attachment.read()
                            pdf_images = await self._convert_pdf_to_images(pdf_bytes, attachment.filename)
                            
                            if pdf_images:
                                images.extend(pdf_images)
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
                            logger.info(f"Loaded image from replied message: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                            
                        except Exception as e:
                            logger.error(f"Failed to load image from replied message attachment {attachment.filename}: {e}")
        
        return images
    
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
        max_file_size = MAX_TEXT_FILE_SIZE_BYTES
        
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
        Check if the bot is mentioned in a Discord message or if the message is a reply to the bot.
        
        Implements requirements 1.1, 1.2: Identify bot mentions in messages.
        
        Args:
            message: The Discord message to check
            
        Returns:
            True if the bot is mentioned or message is a reply to bot, False otherwise
        """
        # Check if this is a reply to a bot message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                # Check if the replied message is from the bot
                if replied_message.author == self.user:
                    return True
        
        # Check if the bot user is in the message mentions
        if self.user in message.mentions:
            return True
        
        # Check for @everyone or @here mentions if the bot has appropriate permissions
        if message.mention_everyone:
            return True
        
        # Check for role mentions that the bot has
        if message.guild and hasattr(message.guild, 'me'):
            bot_member = message.guild.me
            if bot_member:
                for role in message.role_mentions:
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
        except Exception:
            pass  # Ignore reaction failures
    
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
    
    async def _generate_and_send_response(self, message: discord.Message, user_prompt: str, context: List):
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
                    images = await self._extract_images_from_message(message)
                    
                    # Extract audio files from the message
                    audio_files = await self._extract_audio_from_message(message)
                    if audio_files:
                        logger.info(f"✅ Extracted {len(audio_files)} audio file(s)")
                        
                        # Filter context for audio transcription to only include the replied-to message
                        if message.reference and message.reference.message_id:
                            replied_id = message.reference.message_id
                            # Keep only the immediate parent message in context
                            original_context_len = len(context)
                            context = [msg for msg in context if msg.message_id == replied_id]
                            logger.info(f"Audio transcription: Context restricted to replied message (kept {len(context)}/{original_context_len} messages)")
                        else:
                            # No reply, clear context completely
                            context = []
                            logger.info("Audio transcription: Context cleared (no reply)")
                    
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
                    
                    # Get estimated response time and show it to user
                    estimated_time = self.gemini_client.get_estimated_response_time()
                    model_name = self.gemini_client.get_current_model()
                    
                    # Send status message for longer processing times
                    status_message = None
                    if self.gemini_client.get_timeout_for_current_model() > 30:
                        try:
                            status_message = await message.reply(
                                f"⏳ Processing your request with {model_name}...\n"
                                f"*Estimated time: {estimated_time}*"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send status message: {e}")
                    
                    # Use dynamic timeout based on model complexity
                    api_timeout = self.gemini_client.get_timeout_for_current_model() + 10  # Add buffer
                    
                    # Setup streaming callback for thinking mode
                    accumulated_text = ""
                    last_edit_time = 0
                    
                    async def on_chunk(chunk_text):
                        nonlocal accumulated_text, last_edit_time, status_message
                        accumulated_text += chunk_text
                        
                        # Debug logging for streaming
                        # logger.debug(f"Stream chunk received: {len(chunk_text)} chars")
                        
                        if not status_message:
                            return
                            
                        start_tag = "<thinking>"
                        end_tag = "</thinking>"
                        
                        if start_tag in accumulated_text:
                            start_idx = accumulated_text.find(start_tag) + len(start_tag)
                            end_idx = accumulated_text.find(end_tag)
                            
                            if end_idx != -1:
                                content = accumulated_text[start_idx:end_idx].strip()
                            else:
                                content = accumulated_text[start_idx:].strip()
                                
                            import time
                            current_time = time.time()
                            if current_time - last_edit_time > 1.5 and content:
                                try:
                                    # Show last 1500 chars to keep it dynamic
                                    display_content = content
                                    if len(display_content) > 1500:
                                        display_content = "..." + display_content[-1500:]
                                    
                                    await status_message.edit(content=f"🧠 **Thinking Process:**\n{display_content}")
                                    last_edit_time = current_time
                                except Exception:
                                    pass
                        else:
                            # If we are in thinking mode but no tag yet, maybe show the raw text if it looks like thinking?
                            # But safer to wait for tag.
                            pass

                    api_response = await asyncio.wait_for(
                        self.gemini_client.generate_response(enhanced_prompt, context, images=images if images else None, audio_files=audio_files if audio_files else None, on_chunk=on_chunk),
                        timeout=api_timeout
                    )
                    
                    # Delete status message if it was sent
                    if status_message:
                        try:
                            await status_message.delete()
                        except Exception:
                            pass  # Ignore deletion errors
                    
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
                        except Exception:
                            pass
                    
                    timeout_used = self.gemini_client.get_timeout_for_current_model()
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
        if not message.guild:
            return

        username = (
            message.author.display_name
            or getattr(message.author, "global_name", None)
            or message.author.name
            or str(message.author)
        )
        username = username[:80]  # Avoid storing excessively long names

        try:
            await self.token_tracker.record_usage(
                user_id=message.author.id,
                username=username,
                guild_id=message.guild.id,
                guild_name=message.guild.name,
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
            
            # Check if response is too long for Discord (2000 character limit)
            if len(response_content) > 2000:
                logger.info(f"Response too long ({len(response_content)} chars), splitting into multiple messages")
                sent_message = await self._send_split_response(message, response_content)
            else:
                # Send the response as a reply
                sent_message = await message.reply(response_content)
                logger.info(f"Successfully sent response to {message.author} in #{message.channel.name}")
            
            # Send grounding sources as a separate message if available
            if grounding_sources and len(grounding_sources) > 0:
                await self._send_grounding_sources(sent_message, grounding_sources)
            
            # Check if the response indicates the bot will provide more information
            if self._should_generate_followup(response_content):
                logger.info("Response indicates follow-up needed, generating continuation...")
                await self._generate_and_send_followup(message, sent_message)
            
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
    
    async def _send_split_response(self, message: discord.Message, response_content: str) -> discord.Message:
        """
        Split a long response into multiple messages and send them using intelligent splitting.
        
        Args:
            message: The original Discord message to reply to
            response_content: The long response content to split
            
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
                return await self._send_simple_split_response(message, response_content)
            
            # Send the parts
            first_message = None
            last_message = message
            
            for part in message_parts:
                # Reply to the last message to create a thread
                sent = await last_message.reply(part.content)
                
                if part.part_number == 1:
                    first_message = sent
                last_message = sent
                
                logger.info(f"Sent message part {part.part_number}/{part.total_parts} ({len(part.content)} chars)")
            
            logger.info(f"Successfully sent response in {len(message_parts)} parts with intelligent splitting")
            return first_message
            
        except Exception as e:
            logger.error(f"Error in intelligent message splitting: {e}", exc_info=True)
            logger.info("Falling back to simple message splitting")
            return await self._send_simple_split_response(message, response_content)
    
    async def _send_simple_split_response(self, message: discord.Message, response_content: str) -> discord.Message:
        """
        Fallback method for simple message splitting when intelligent splitting fails.
        
        Args:
            message: The original Discord message to reply to
            response_content: The long response content to split
            
        Returns:
            The first sent message (for reply threading)
        """
        # Discord limit is 2000 chars, we'll use 1900 to be safe and leave room for continuation indicators
        max_length = 1900
        
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
        
        # Send the parts
        first_message = None
        last_message = message
        
        for idx, part in enumerate(parts):
            # Add continuation indicator
            if idx > 0:
                part = f"*(continued...)*\n\n{part}"
            if idx < len(parts) - 1:
                part = f"{part}\n\n*(continues...)*"
            
            # Reply to the last message to create a thread
            sent = await last_message.reply(part)
            
            if idx == 0:
                first_message = sent
            last_message = sent
            
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
    
    def _should_generate_followup(self, response: str) -> bool:
        """
        Simple heuristic to detect if bot promised a follow-up.
        
        Note: This is a lightweight UX enhancement, NOT a core routing decision.
        Core routing decisions are made by the AI router model.
        
        Args:
            response: The bot's initial response content
            
        Returns:
            True if a follow-up should be generated
        """
        response_lower = response.lower()
        
        # Simple keyword check for common "I'll get back to you" phrases
        # This is just a UX enhancement and doesn't affect routing
        followup_indicators = [
            "give me a sec",
            "let me check",
            "let me search",
            "let me look",
            "one moment",
            "just a moment",
            "hold on",
            "searching for",
            "looking up",
            "checking on",
            "dig up",
            "find out",
            "i'll search",
            "i'll check",
            "i'll look",
            "i will search",
            "i will check",
            "i will look"
        ]
        
        return any(indicator in response_lower for indicator in followup_indicators)
    
    async def _generate_and_send_followup(self, original_message: discord.Message, initial_response: discord.Message):
        """
        Generate and send a follow-up response when the initial response indicated more info would come.
        
        Args:
            original_message: The user's original message
            initial_response: The bot's initial response message
        """
        try:
            # Wait a moment to simulate "working on it"
            await asyncio.sleep(2)
            
            # Show typing indicator
            async with original_message.channel.typing():
                # Extract the original user prompt
                user_prompt = self._extract_user_prompt(original_message)
                
                # Create a modified prompt that asks for the actual information
                followup_prompt = f"Now provide the actual detailed information for: {user_prompt}"
                
                # Collect fresh context including the initial response
                try:
                    context = await self.context_collector.get_channel_context(original_message.channel)
                except Exception as e:
                    logger.error(f"Failed to collect context for follow-up: {e}")
                    context = []
                
                # Generate the follow-up response
                api_response = await asyncio.wait_for(
                    self.gemini_client.generate_response(followup_prompt, context),
                    timeout=self.config.response_timeout + 5
                )
                
                if api_response.success and api_response.content:
                    # Send as a reply to the initial response
                    if len(api_response.content) > 2000:
                        api_response.content = api_response.content[:1997] + "..."
                    
                    await initial_response.reply(api_response.content)
                    logger.info("Successfully sent follow-up response")
                else:
                    logger.error(f"Failed to generate follow-up: {api_response.error_type}")
                    # Don't send an error for follow-up failures, just log them
                    
        except asyncio.TimeoutError:
            logger.error("Follow-up response generation timed out")
        except Exception as e:
            logger.error(f"Error generating follow-up response: {e}", exc_info=True)