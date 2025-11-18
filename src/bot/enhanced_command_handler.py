"""
Enhanced command handler for Discord bot with image editing support.

This module extends the existing command system with natural language
command recognition, image editing capabilities, and improved user experience.
"""

import asyncio
import logging
import re
from typing import Optional, Dict, List, Tuple
from enum import Enum

import discord
from discord.ext import commands
import google.generativeai as genai

from ..models.data_models import ImageEditRequest, EditType, ImageEditResult, TokenUsage
from ..services.image_processing_service import ImageProcessingService, ProcessingStatus
from ..utils.error_manager import ErrorManager


logger = logging.getLogger(__name__)


class CommandIntent(Enum):
    """Enumeration of recognized command intents."""
    IMAGE_EDIT = "image_edit"
    IMAGE_GENERATE = "image_generate"
    STATUS = "status"
    UNKNOWN = "unknown"


class EnhancedCommandHandler:
    """
    Enhanced command handler with natural language processing and image editing support.
    
    Implements requirements 1.1, 2.1, 2.2, 5.1 for image edit command detection,
    natural language parsing, and integration with image processing service.
    """
    
    def __init__(self, bot, image_processing_service: ImageProcessingService, error_manager: ErrorManager, gemini_client):
        """
        Initialize the enhanced command handler.
        
        Uses AI router model (Gemini 2.0 Flash Lite) for ALL decisions.
        NO keyword matching or regex patterns.
        
        Args:
            bot: Discord bot instance for shared helpers (token tracking, logging)
            image_processing_service: Service for processing image edits
            error_manager: Error management service
            gemini_client: Gemini client for intent detection
        """
        self.bot = bot
        self.image_processing_service = image_processing_service
        self.error_manager = error_manager
        self.gemini_client = gemini_client
    
    async def _check_intent_and_complexity(self, message_content: str, has_images: bool) -> Tuple[CommandIntent, str]:
        """
        Use Gemini 2.0 Flash Lite router model to determine user intent and complexity.
        
        This is the ONLY decision-making function - no keyword matching is used.
        
        Args:
            message_content: The message content to analyze
            has_images: Whether the message has image attachments
            
        Returns:
            Tuple of (CommandIntent, complexity_level)
            - CommandIntent: The detected intent (IMAGE_GENERATE, IMAGE_EDIT, or UNKNOWN)
            - complexity_level: "low", "medium", or "high" for model selection
        """
        try:
            # Create a prompt for Gemini router to classify intent and complexity
            classification_prompt = f"""Analyze this user message and determine the intent and complexity.

User message: "{message_content}"
Has image attachments: {"yes" if has_images else "no"}

Respond with EXACTLY two lines:

Line 1 - Intent (choose ONE):
- "image_generate" if user wants to CREATE/GENERATE a new image
- "image_edit" if user wants to EDIT/MODIFY an existing image (only if has_images=yes)
- "text" for any other request (questions, conversations, help requests, etc.)

Line 2 - Complexity level:
- "low" for simple questions, basic info, quick facts, straightforward requests
- "medium" for moderate complexity needing reasoning, analysis, or detailed explanations
- "high" for complex tasks needing deep analysis, creative writing, coding, or advanced reasoning

Examples:
"Create an image of a sunset" (no images) -> image_generate, low
"Remove the background" (has images) -> image_edit, low
"What's 2+2?" (no images) -> text, low
"Explain quantum mechanics" (no images) -> text, medium
"Write a complex sorting algorithm" (no images) -> text, high
"Help me understand this bot" (no images) -> text, low

Your response (two lines only):"""

            # Create router model instance for classification
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-lite",
                generation_config={
                    "temperature": 0.1,  # Low temperature for consistent classification
                    "max_output_tokens": 30,  # Enough for two lines
                }
            )
            
            # Generate response
            response = await asyncio.to_thread(
                model.generate_content,
                classification_prompt
            )
            
            # Extract and normalize the response
            result = response.text.strip().lower()
            lines = [line.strip() for line in result.split('\n') if line.strip()]
            
            # Parse the response
            intent = CommandIntent.UNKNOWN
            complexity_level = "medium"  # Default to medium
            
            if len(lines) >= 1:
                intent_str = lines[0]
                if intent_str == "image_generate":
                    intent = CommandIntent.IMAGE_GENERATE
                elif intent_str == "image_edit":
                    intent = CommandIntent.IMAGE_EDIT
                else:  # "text" or anything else
                    intent = CommandIntent.UNKNOWN
            
            if len(lines) >= 2:
                # Validate complexity level
                if lines[1] in ["low", "medium", "high"]:
                    complexity_level = lines[1]
                else:
                    logger.warning(f"Invalid complexity level '{lines[1]}', defaulting to 'medium'")
            
            logger.info(f"🔍 Router decision: '{message_content[:50]}...' -> intent={intent.value}, complexity={complexity_level} (raw: '{result}')")
            return intent, complexity_level
            
        except Exception as e:
            logger.error(f"Error in router model: {e}", exc_info=True)
            # On error, default to unknown intent and medium complexity
            return CommandIntent.UNKNOWN, "medium"
    
    async def handle_message(self, message: discord.Message) -> Tuple[bool, str]:
        """
        Handle a Discord message and determine if it contains commands.
        
        All decisions are made by the router model (Gemini 2.0 Flash Lite).
        NO keyword matching is used.
        
        Args:
            message: The Discord message to process
            
        Returns:
            Tuple of (handled, complexity_level)
            - handled: True if the message was handled as a command, False otherwise
            - complexity_level: "low", "medium", or "high" for model selection
        """
        try:
            # Check for image attachments
            has_images = any(
                attachment.content_type and attachment.content_type.startswith('image/')
                for attachment in message.attachments
            )
            
            # Use router model to determine intent and complexity - NO KEYWORD MATCHING
            intent, complexity_level = await self._check_intent_and_complexity(message.content, has_images)
            
            logger.info(f"📋 Handler routing: intent={intent.value}, has_images={has_images}, complexity={complexity_level}")
            
            # Route based on router model decision
            if intent == CommandIntent.IMAGE_GENERATE:
                if has_images:
                    # User wants to generate but has images attached - might be confused
                    # Let router handle it as edit since images are present
                    await self.handle_image_edit_command(message)
                else:
                    await self.handle_image_generation_command(message)
                return True, complexity_level
            
            elif intent == CommandIntent.IMAGE_EDIT:
                if has_images:
                    await self.handle_image_edit_command(message)
                else:
                    await self._suggest_image_upload(message)
                return True, complexity_level
            
            # UNKNOWN intent - let main bot handle it with the determined complexity
            return False, complexity_level
            
        except Exception as e:
            logger.error(f"Error handling message in enhanced command handler: {e}", exc_info=True)
            error_context = self.error_manager.create_error_context(
                e, "I had trouble processing your command. Please try again!"
            )
            await self.error_manager.send_error_response(message, error_context)
            return True, "medium"  # Default to medium on error
    

    
    async def detect_edit_type(self, instruction: str) -> EditType:
        """
        Use AI router model to detect the type of image edit requested.
        
        NO keyword matching - relies entirely on Gemini 2.0 Flash Lite.
        
        Args:
            instruction: The natural language edit instruction
            
        Returns:
            The detected EditType
        """
        try:
            classification_prompt = f"""Classify this image editing instruction into ONE category.

Instruction: "{instruction}"

Categories:
- "object_removal" - removing, deleting, erasing objects or elements
- "background" - changing, replacing, or modifying backgrounds
- "style" - artistic style changes, filters, painting effects
- "color" - color adjustments, brightness, contrast, saturation
- "general" - any other editing task or unclear request

Respond with EXACTLY one word (the category name):"""

            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-lite",
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 10,
                }
            )
            
            response = await asyncio.to_thread(
                model.generate_content,
                classification_prompt
            )
            
            result = response.text.strip().lower()
            
            # Map response to EditType
            edit_type_map = {
                "object_removal": EditType.OBJECT_REMOVAL,
                "background": EditType.BACKGROUND_REPLACEMENT,
                "style": EditType.STYLE_TRANSFER,
                "color": EditType.COLOR_ADJUSTMENT,
                "general": EditType.GENERAL_EDIT
            }
            
            edit_type = edit_type_map.get(result, EditType.GENERAL_EDIT)
            logger.info(f"🎨 Edit type detection: '{instruction[:50]}...' -> {edit_type.value}")
            return edit_type
            
        except Exception as e:
            logger.error(f"Error detecting edit type with AI: {e}", exc_info=True)
            return EditType.GENERAL_EDIT
    
    async def handle_image_edit_command(self, message: discord.Message):
        """
        Handle an image editing command.
        
        Implements requirements 1.1, 2.2: Add image edit command detection
        and integrate with image processing service.
        
        Args:
            message: Discord message containing image and edit instruction
        """
        try:
            # Extract edit instruction from message
            instruction = self._extract_edit_instruction(message.content)
            
            # Get the first image attachment
            image_attachment = None
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    image_attachment = attachment
                    break
            
            if not image_attachment:
                await message.reply(
                    "I don't see any images to edit! Please attach an image and try again. 🖼️"
                )
                return
            
            # Detect edit type using AI router
            edit_type = await self.detect_edit_type(instruction)
            
            # Download image data
            try:
                image_data = await image_attachment.read()
            except discord.HTTPException as e:
                logger.error(f"Failed to download image: {e}")
                await message.reply(
                    "I couldn't download your image. Please try uploading it again! 📥"
                )
                return
            
            # Create image edit request
            edit_request = ImageEditRequest(
                user_id=str(message.author.id),
                image_data=image_data,
                instruction=instruction,
                edit_type=edit_type,
                timestamp=message.created_at,
                channel_id=str(message.channel.id)
            )
            
            # Show typing indicator while processing
            async with message.channel.typing():
                # Send initial processing message
                processing_msg = await message.reply(
                    "🎨 Processing your image edit... This may take a moment!"
                )
                
                # Submit the image edit request and get job ID
                job_id = await self.image_processing_service.process_image_edit(edit_request)
                
                # Wait for the job to complete
                result = await self._wait_for_job_completion(job_id, processing_msg)
            
            # Handle the result
            if result.success and result.edited_image:
                # Create file from edited image data (bytes)
                import io
                edited_file = discord.File(
                    fp=io.BytesIO(result.edited_image),
                    filename=f"edited_{image_attachment.filename}"
                )
                
                # Create response embed
                embed = discord.Embed(
                    title="✨ Image Edit Complete!",
                    description=f"**Edit Type:** {edit_type.value.replace('_', ' ').title()}\n"
                               f"**Processing Time:** {result.processing_time:.2f}s",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Original Request",
                    value=instruction[:100] + "..." if len(instruction) > 100 else instruction,
                    inline=False
                )
                
                await message.reply(embed=embed, file=edited_file)
                
                await self._record_token_usage(message, result.token_usage)

                logger.info(f"Successfully processed image edit for user {message.author.id}")
                
            else:
                # Handle edit failure
                error_msg = result.error_message or "Unknown error occurred during image processing"
                await message.reply(
                    f"❌ I couldn't edit your image: {error_msg}\n\n"
                    f"Please try again with a different image or instruction!"
                )
                
                logger.warning(f"Image edit failed for user {message.author.id}: {error_msg}")
        
        except Exception as e:
            logger.error(f"Error in image edit command handler: {e}", exc_info=True)
            error_context = self.error_manager.create_error_context(
                e, "I encountered an error while trying to edit your image. Please try again!"
            )
            await self.error_manager.send_error_response(message, error_context)
    
    async def handle_image_generation_command(self, message: discord.Message):
        """
        Handle an image generation command.
        
        Generates a new image from a text description using Gemini 2.5 Flash Image.
        
        Args:
            message: Discord message containing the generation prompt
        """
        try:
            # Extract generation prompt from message
            prompt = self._extract_edit_instruction(message.content)
            
            if not prompt or len(prompt.strip()) < 5:
                await message.reply(
                    "Please provide a description of what you'd like me to generate! 🎨\n"
                    "Example: `generate a picture of a cyberpunk city at sunset`"
                )
                return
            
            # Show typing indicator while processing
            async with message.channel.typing():
                # Send initial processing message
                processing_msg = await message.reply(
                    "🎨 Generating your image... This may take a moment!"
                )
                
                # Generate the image (pass None for image_data to trigger generation mode)
                from ..services.nano_banana_client import NanoBananaClient
                
                # Use the nano banana client directly for generation
                result = await self.image_processing_service.client.edit_image(
                    image_data=None,  # None triggers generation mode
                    instruction=prompt,
                    edit_type=None
                )
            
            # Handle the result
            if result.success and result.image_data:
                # Delete processing message
                try:
                    await processing_msg.delete()
                except:
                    pass
                
                # Create file from generated image data
                import io
                image_file = discord.File(
                    fp=io.BytesIO(result.image_data),
                    filename="generated_image.png"
                )
                
                # Create response embed
                embed = discord.Embed(
                    title="✨ Image Generated!",
                    description=f"**Prompt:** {prompt[:200]}\n"
                               f"**Processing Time:** {result.processing_time:.2f}s\n"
                               f"**Model:** Gemini 2.5 Flash Image",
                    color=discord.Color.blue()
                )
                embed.set_footer(text="Generated with SynthID watermark • AI-generated content")
                
                await message.reply(embed=embed, file=image_file)

                await self._record_token_usage(message, result.token_usage)
                
                logger.info(f"Successfully generated image for user {message.author.id}")
                
            else:
                # Handle generation failure
                error_msg = result.error_message or "Unknown error occurred during image generation"
                
                # Update processing message with error
                try:
                    await processing_msg.edit(content=
                        f"❌ I couldn't generate your image: {error_msg}\n\n"
                        f"Please try a different prompt or try again later!"
                    )
                except:
                    await message.reply(
                        f"❌ I couldn't generate your image: {error_msg}\n\n"
                        f"Please try a different prompt or try again later!"
                    )
                
                logger.warning(f"Image generation failed for user {message.author.id}: {error_msg}")
        
        except Exception as e:
            logger.error(f"Error in image generation command handler: {e}", exc_info=True)
            error_context = self.error_manager.create_error_context(
                e, "I encountered an error while trying to generate your image. Please try again!"
            )
            await self.error_manager.send_error_response(message, error_context)

    async def _record_token_usage(self, message: discord.Message, token_usage: Optional[TokenUsage]):
        """Forward token usage data to the main bot's tracker."""

        if not token_usage:
            return
        if not self.bot or not hasattr(self.bot, "_record_token_usage"):
            return

        try:
            await self.bot._record_token_usage(message, token_usage)
        except Exception as exc:
            logger.error(f"Failed to record token usage for image command: {exc}")
    
    def _extract_edit_instruction(self, message_content: str) -> str:
        """
        Extract the edit instruction from a message, removing bot mentions.
        
        Args:
            message_content: The full message content
            
        Returns:
            The cleaned edit instruction
        """
        # Remove bot mentions
        content = re.sub(r'<@!?\d+>', '', message_content)
        
        # Remove @everyone and @here
        content = re.sub(r'@(?:everyone|here)', '', content)
        
        # Clean up whitespace
        content = re.sub(r'\s+', ' ', content).strip()
        
        return content
    
    async def _suggest_image_upload(self, message: discord.Message):
        """
        Suggest image upload when user wants to edit but no image is attached.
        
        Args:
            message: The Discord message
        """
        embed = discord.Embed(
            title="🖼️ Image Required",
            description="I'd love to help you edit an image, but I don't see one attached!",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="How to Edit Images",
            value="1. Attach an image to your message\n"
                  "2. Mention me with your editing request\n"
                  "3. I'll process and return the edited image!",
            inline=False
        )
        
        embed.add_field(
            name="Example",
            value="`@bot remove the background` (with image attached)",
            inline=False
        )
        
        await message.reply(embed=embed)
    
    async def _wait_for_job_completion(self, job_id: str, processing_msg: discord.Message, 
                                      timeout: int = 60) -> ImageEditResult:
        """
        Wait for an image processing job to complete.
        
        Args:
            job_id: The job identifier
            processing_msg: Message to update with progress
            timeout: Maximum time to wait in seconds
            
        Returns:
            ImageEditResult with the processing result
            
        Raises:
            TimeoutError: If job doesn't complete within timeout
            RuntimeError: If job fails
        """
        import asyncio
        
        start_time = asyncio.get_event_loop().time()
        last_progress = 0.0
        
        while True:
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError(f"Image processing timed out after {timeout}s")
            
            # Get job status
            job = await self.image_processing_service.get_job_status(job_id)
            
            if not job:
                raise RuntimeError(f"Job {job_id} not found")
            
            # Update progress message if progress changed significantly
            if job.progress - last_progress >= 0.1:
                try:
                    await processing_msg.edit(
                        content=f"🎨 Processing your image edit... {int(job.progress * 100)}%"
                    )
                    last_progress = job.progress
                except:
                    pass  # Ignore edit failures
            
            # Check if job completed
            if job.status == ProcessingStatus.COMPLETED:
                # Delete processing message
                try:
                    await processing_msg.delete()
                except:
                    pass
                
                return job.result
            
            elif job.status == ProcessingStatus.FAILED:
                # Delete processing message
                try:
                    await processing_msg.delete()
                except:
                    pass
                
                # Return failed result
                return ImageEditResult(
                    success=False,
                    error_message=job.error_message or "Image processing failed",
                    processing_time=0.0,
                    metadata=None,
                    token_usage=None,
                )
            
            # Wait a bit before checking again
            await asyncio.sleep(0.5)