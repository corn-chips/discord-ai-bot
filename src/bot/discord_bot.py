"""
Discord bot implementation for the Discord Grok Bot.

This module contains the main DiscordBot class that handles Discord events,
message processing, and coordinates with other services to provide AI responses.
"""

import asyncio
import io
import logging
from datetime import datetime
from typing import List, Optional

import discord
from discord.ext import commands
from PIL import Image

from ..config import BotConfig
from ..models.data_models import APIResponse
from ..services.context_collector import ContextCollector
from ..services.gemini_client import GeminiClient
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, TimingContext, get_logger_with_context
from .commands import setup_commands


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
        self.error_manager = ErrorManager()
        self.performance_logger = PerformanceLogger("discord_bot")
        self.context_collector = ContextCollector(
            max_context_messages=config.max_context_messages,
            reply_context_range=config.reply_context_range
        )
        self.gemini_client = GeminiClient(config)
        self.start_time = datetime.now()
        
        # Set up command tree for slash commands
        self.tree = discord.app_commands.CommandTree(self)
        
        logger.info("DiscordBot initialized with configuration")
    
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
        
        # Set bot status
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="@mentions for AI responses"
        )
        await self.change_presence(activity=activity)
        
        # Set up slash commands
        try:
            await setup_commands(self, self.config, self.gemini_client, self.performance_logger)
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash command(s)")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}", exc_info=True)
        
        logger.info("✅ Bot is ready and listening for mentions!")
    
    async def on_error(self, event: str, *args, **kwargs):
        """
        Global error handler for Discord events.
        
        Args:
            event: Name of the event that caused the error
            *args: Event arguments
            **kwargs: Event keyword arguments
        """
        logger.error(f"Discord event error in {event}", exc_info=True)
    
    async def on_disconnect(self):
        """Event handler called when the bot disconnects from Discord."""
        logger.warning("🔌 Bot disconnected from Discord")
    
    async def on_resumed(self):
        """Event handler called when the bot resumes connection to Discord."""
        logger.info("🔄 Bot resumed connection to Discord")
    
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
        
        context_logger.info(f"Bot mentioned by {message.author} in #{message.channel.name}")
        logger.debug(f"Message content: {message.content}")
        
        try:
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
                    standard_context = await self.context_collector.get_channel_context(message.channel)
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
                    combined_context = await self.context_collector.get_channel_context(message.channel)
                except discord.Forbidden:
                    logger.warning(f"No permission to read message history in channel {message.channel.id}")
                    combined_context = []
                except discord.HTTPException as e:
                    logger.error(f"Failed to fetch channel context: {e}")
                    combined_context = []
            
            logger.info(f"Collected {len(combined_context)} messages for context")
            
            # Generate AI response using Gemini API with collected context
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
    
    async def _extract_images_from_message(self, message: discord.Message) -> List[Image.Image]:
        """
        Extract and download images from a Discord message.
        
        Args:
            message: The Discord message to extract images from
            
        Returns:
            List of PIL Image objects from the message attachments
        """
        images = []
        
        # Check message attachments for images
        for attachment in message.attachments:
            # Check if attachment is an image based on content type or filename
            if attachment.content_type and attachment.content_type.startswith('image/'):
                try:
                    # Download the image
                    image_bytes = await attachment.read()
                    
                    # Convert to PIL Image
                    image = Image.open(io.BytesIO(image_bytes))
                    
                    # Convert RGBA to RGB if necessary (Gemini prefers RGB)
                    if image.mode == 'RGBA':
                        # Create white background
                        background = Image.new('RGB', image.size, (255, 255, 255))
                        background.paste(image, mask=image.split()[3])  # Use alpha channel as mask
                        image = background
                    elif image.mode not in ['RGB', 'L']:
                        # Convert other modes to RGB
                        image = image.convert('RGB')
                    
                    images.append(image)
                    logger.info(f"Loaded image from attachment: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                    
                except Exception as e:
                    logger.error(f"Failed to load image from attachment {attachment.filename}: {e}")
        
        # Also check if the message is a reply and has images in the replied message
        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                for attachment in replied_message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        try:
                            image_bytes = await attachment.read()
                            image = Image.open(io.BytesIO(image_bytes))
                            
                            if image.mode == 'RGBA':
                                background = Image.new('RGB', image.size, (255, 255, 255))
                                background.paste(image, mask=image.split()[3])
                                image = background
                            elif image.mode not in ['RGB', 'L']:
                                image = image.convert('RGB')
                            
                            images.append(image)
                            logger.info(f"Loaded image from replied message: {attachment.filename} ({image.size[0]}x{image.size[1]})")
                            
                        except Exception as e:
                            logger.error(f"Failed to load image from replied message attachment {attachment.filename}: {e}")
        
        return images
        
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
                    
                    # Validate prompt is not empty (or has images)
                    if (not user_prompt or not user_prompt.strip()) and len(images) == 0:
                        logger.warning("Empty user prompt and no images provided to response generation")
                        await message.reply("Please provide a message or attach an image for me to respond to! 📝")
                        return
                    
                    # If only images, provide a default prompt
                    if not user_prompt or not user_prompt.strip():
                        user_prompt = "What's in this image? Please describe it in detail."
                        logger.info("Using default prompt for image-only message")
                    
                    # Generate AI response with timeout handling (including images if present)
                    api_response = await asyncio.wait_for(
                        self.gemini_client.generate_response(user_prompt, context, images=images if images else None),
                        timeout=self.config.response_timeout + 5  # Add buffer to client timeout
                    )
                    
                    duration = time.time() - start_time
                    
                    if api_response.success:
                        logger.info("Successfully generated AI response")
                        
                        # Log performance metrics
                        self.performance_logger.log_message_processing(
                            duration=duration,
                            context_messages=len(context),
                            response_length=len(api_response.content) if api_response.content else 0,
                            user_id=message.author.id,
                            guild_id=message.guild.id if message.guild else None
                        )
                        
                        await self._send_response_safely(message, api_response.content, api_response.grounding_sources)
                    else:
                        logger.error(f"Failed to generate response: {api_response.error_type}")
                        await self._handle_response_error(message, api_response)
                        
                except asyncio.TimeoutError:
                    logger.error(f"Response generation timed out for message {message.id} after {self.config.response_timeout}s")
                    timeout_response = APIResponse(
                        success=False,
                        error_type="timeout",
                        content=f"Response generation timed out after {self.config.response_timeout} seconds"
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
        Split a long response into multiple messages and send them.
        
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
        
        logger.info(f"Successfully sent response in {len(parts)} parts")
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
            sources_text = "📚 **Sources:**\n"
            for idx, source in enumerate(grounding_sources[:10], 1):  # Limit to 10 sources
                uri = source.get('uri', '')
                title = source.get('title')
                
                if title:
                    sources_text += f"{idx}. [{title}]({uri})\n"
                else:
                    sources_text += f"{idx}. {uri}\n"
            
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
        Determine if a response indicates the bot will provide follow-up information.
        
        Args:
            response: The bot's initial response content
            
        Returns:
            True if a follow-up should be generated
        """
        response_lower = response.lower()
        
        # Keywords/phrases that indicate the bot is going to do something and report back
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