"""
Discord bot implementation for the Discord Grok Bot.

This module contains the main DiscordBot class that handles Discord events,
message processing, and coordinates with other services to provide AI responses.
"""

import asyncio
import logging
from typing import List, Optional

import discord
from discord.ext import commands

from ..config import BotConfig
from ..models.data_models import APIResponse
from ..services.context_collector import ContextCollector
from ..services.gemini_client import GeminiClient
from ..utils.error_manager import ErrorManager
from ..utils.logging_config import PerformanceLogger, TimingContext, get_logger_with_context


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
                await message.reply("I was mentioned but didn't see a message to respond to!")
                return
            
            # Process message with timing
            import time
            start_time = time.time()
            
            # Collect conversation context
            await self._process_message_with_context(message, user_prompt)
            
            # Log processing completion (will be called from _generate_and_send_response)
            
        except Exception as e:
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
                # Get enhanced reply context
                reply_context = await self.context_collector.get_reply_context(message)
                # Get standard channel context
                standard_context = await self.context_collector.get_channel_context(message.channel)
                # Combine contexts, removing duplicates
                combined_context = self.context_collector._remove_duplicate_messages(
                    standard_context, reply_context
                )
            else:
                logger.debug("Message is not a reply, collecting standard context")
                # Get standard channel context only
                combined_context = await self.context_collector.get_channel_context(message.channel)
            
            logger.info(f"Collected {len(combined_context)} messages for context")
            
            # Generate AI response using Gemini API with collected context
            await self._generate_and_send_response(message, user_prompt, combined_context)
            
        except Exception as e:
            error_context = self.error_manager.create_error_context(
                e, "I had trouble collecting conversation context. Please try again!"
            )
            self.error_manager.log_error(error_context, "Context collection failed")
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
        
    def is_bot_mentioned(self, message: discord.Message) -> bool:
        """
        Check if the bot is mentioned in a Discord message.
        
        Implements requirements 1.1, 1.2: Identify bot mentions in messages.
        
        Args:
            message: The Discord message to check
            
        Returns:
            True if the bot is mentioned, False otherwise
        """
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
            "unknown_error": ErrorType.UNKNOWN_ERROR
        }
        
        error_type = error_type_mapping.get(api_response.error_type, ErrorType.UNKNOWN_ERROR)
        user_message = self.error_manager.get_user_message(error_type)
        
        # Create error context
        from ..utils.error_manager import ErrorContext
        error_context = ErrorContext(
            error_type=error_type,
            user_message=user_message,
            technical_details=api_response.content or "API response error",
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
        async with message.channel.typing():
            try:
                # Track performance metrics
                import time
                start_time = time.time()
                
                # Generate AI response with timeout handling
                api_response = await asyncio.wait_for(
                    self.gemini_client.generate_response(user_prompt, context),
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
                    
                    await self._send_response_safely(message, api_response.content)
                else:
                    logger.error(f"Failed to generate response: {api_response.error_type}")
                    await self._handle_response_error(message, api_response)
                    
            except asyncio.TimeoutError:
                logger.error(f"Response generation timed out for message {message.id}")
                timeout_response = APIResponse(
                    success=False,
                    error_type="timeout",
                    content="Response generation timed out"
                )
                await self._handle_response_error(message, timeout_response)
                
            except Exception as e:
                error_context = self.error_manager.create_error_context(e)
                self.error_manager.log_error(error_context, "Unexpected error during response generation")
                await self.error_manager.send_error_response(message, error_context)
    
    async def _send_response_safely(self, message: discord.Message, response_content: str):
        """
        Safely send a response to Discord with error handling.
        
        Implements requirement 1.4: Post generated responses as replies to original messages.
        
        Args:
            message: The original Discord message to reply to
            response_content: The AI-generated response content
        """
        try:
            # Check if response is too long for Discord (2000 character limit)
            if len(response_content) > 2000:
                logger.warning(f"Response too long ({len(response_content)} chars), truncating")
                response_content = response_content[:1997] + "..."
            
            # Send the response as a reply
            await message.reply(response_content)
            logger.info(f"Successfully sent response to {message.author} in #{message.channel.name}")
            
        except Exception as e:
            error_context = self.error_manager.handle_discord_error(e, message)
            await self.error_manager.send_error_response(
                message, 
                error_context, 
                fallback_reaction="⚠️"
            )