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

from ..models.data_models import ImageEditRequest, EditType, ImageEditResult
from ..services.image_processing_service import ImageProcessingService, ProcessingStatus
from ..utils.error_manager import ErrorManager


logger = logging.getLogger(__name__)


class CommandIntent(Enum):
    """Enumeration of recognized command intents."""
    IMAGE_EDIT = "image_edit"
    IMAGE_GENERATE = "image_generate"
    HELP = "help"
    STATUS = "status"
    UNKNOWN = "unknown"


class EnhancedCommandHandler:
    """
    Enhanced command handler with natural language processing and image editing support.
    
    Implements requirements 1.1, 2.1, 2.2, 5.1 for image edit command detection,
    natural language parsing, and integration with image processing service.
    """
    
    def __init__(self, image_processing_service: ImageProcessingService, error_manager: ErrorManager, gemini_client):
        """
        Initialize the enhanced command handler.
        
        Args:
            image_processing_service: Service for processing image edits
            error_manager: Error management service
            gemini_client: Gemini client for intent detection
        """
        self.image_processing_service = image_processing_service
        self.error_manager = error_manager
        self.gemini_client = gemini_client
        
        # Image editing keywords for natural language detection
        self.image_edit_keywords = {
            EditType.OBJECT_REMOVAL: [
                'remove', 'delete', 'erase', 'take out', 'get rid of',
                'eliminate', 'clear', 'clean up', 'cut out'
            ],
            EditType.BACKGROUND_REPLACEMENT: [
                'background', 'replace background', 'change background',
                'new background', 'backdrop', 'scene', 'setting',
                'put', 'place', 'position', 'move', 'relocate',
                'put on', 'place on', 'on top of', 'in front of',
                'behind', 'next to', 'beside', 'above', 'below',
                'add', 'insert', 'include', 'add to', 'add in'
            ],
            EditType.STYLE_TRANSFER: [
                'style', 'artistic', 'painting', 'sketch', 'cartoon',
                'art style', 'make it look like', 'transform to',
                'fly', 'flying', 'make fly', 'make it fly'
            ],
            EditType.COLOR_ADJUSTMENT: [
                'color', 'brightness', 'contrast', 'saturation', 'hue',
                'darker', 'lighter', 'brighter', 'brighten', 'more colorful', 'vibrant'
            ],
            EditType.GENERAL_EDIT: [
                'edit', 'modify', 'change', 'alter', 'adjust', 'fix',
                'improve', 'enhance', 'update'
            ]
        }
        
        # Compiled regex patterns for better performance
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns for efficient matching."""
        # Pattern for detecting image edit requests
        edit_words = []
        for edit_type, keywords in self.image_edit_keywords.items():
            edit_words.extend(keywords)
        
        # Create pattern that matches any edit keyword
        self.edit_pattern = re.compile(
            r'\b(?:' + '|'.join(re.escape(word) for word in edit_words) + r')\b',
            re.IGNORECASE
        )
        
        # Pattern for help requests (more specific)
        self.help_pattern = re.compile(
            r'\b(?:help|how\s+(?:do|to)|what\s+(?:can|commands?|is)|commands?|usage|guide|tutorial)\b',
            re.IGNORECASE
        )
    
    async def _check_image_generation_intent(self, message_content: str) -> bool:
        """
        Use Gemini Flash Lite to determine if the user is requesting image generation.
        
        Args:
            message_content: The message content to analyze
            
        Returns:
            True if the user wants image generation, False otherwise
        """
        try:
            # Create a simple prompt for Gemini to classify the intent
            classification_prompt = f"""Analyze this user message and determine if they are requesting image generation or creation.

User message: "{message_content}"

Respond with ONLY one word:
- "yes" if the user is asking to generate, create, make, draw, or produce an image/picture
- "no" if the user is NOT asking for image generation

Your response:"""

            # Create a simple model instance for classification
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-lite",
                generation_config={
                    "temperature": 0.1,  # Low temperature for consistent classification
                    "max_output_tokens": 10,  # Only need one word
                }
            )
            
            # Generate response
            response = await asyncio.to_thread(
                model.generate_content,
                classification_prompt
            )
            
            # Extract and normalize the response
            result = response.text.strip().lower()
            
            # Check if response is "yes"
            is_generation = result == "yes"
            
            logger.info(f"Image generation intent check: '{message_content[:50]}...' -> {is_generation} (raw: '{result}')")
            return is_generation
            
        except Exception as e:
            logger.error(f"Error checking image generation intent: {e}", exc_info=True)
            # On error, default to False (not image generation)
            return False
    
    async def handle_message(self, message: discord.Message) -> bool:
        """
        Handle a Discord message and determine if it contains commands.
        
        Args:
            message: The Discord message to process
            
        Returns:
            True if the message was handled as a command, False otherwise
        """
        try:
            # Check for image attachments
            has_images = any(
                attachment.content_type and attachment.content_type.startswith('image/')
                for attachment in message.attachments
            )
            
            # Parse the message for command intent
            intent, confidence = await self.parse_natural_language_command(message.content)
            
            # CONTEXT-AWARE LOGIC: If there's an image attached and ANY kind of action keyword,
            # treat it as an edit request (not generation)
            if has_images and intent in [CommandIntent.IMAGE_GENERATE, CommandIntent.IMAGE_EDIT]:
                # Image is present, so this should be an edit operation
                await self.handle_image_edit_command(message)
                return True
            
            # Handle image generation commands (no image attached)
            if intent == CommandIntent.IMAGE_GENERATE and not has_images:
                await self.handle_image_generation_command(message)
                return True
            
            # Handle image edit commands (with image attached) - redundant now but kept for clarity
            elif intent == CommandIntent.IMAGE_EDIT and has_images:
                await self.handle_image_edit_command(message)
                return True
            
            # Handle help requests
            elif intent == CommandIntent.HELP:
                await self.handle_contextual_help(message)
                return True
            
            # If image edit intent but no images, provide guidance
            elif intent == CommandIntent.IMAGE_EDIT and not has_images:
                await self._suggest_image_upload(message)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error handling message in enhanced command handler: {e}", exc_info=True)
            error_context = self.error_manager.create_error_context(
                e, "I had trouble processing your command. Please try again!"
            )
            await self.error_manager.send_error_response(message, error_context)
            return True
    
    async def parse_natural_language_command(self, message_content: str) -> Tuple[CommandIntent, float]:
        """
        Parse natural language message to determine command intent.
        
        Uses Gemini Flash Lite to detect image generation requests instead of keyword matching.
        
        Args:
            message_content: The message content to parse
            
        Returns:
            Tuple of (CommandIntent, confidence_score)
        """
        content_lower = message_content.lower().strip()
        
        # Check for help intent
        if self.help_pattern.search(content_lower):
            return CommandIntent.HELP, 0.9
        
        # Use Gemini to check for image generation intent
        is_generation = await self._check_image_generation_intent(message_content)
        if is_generation:
            return CommandIntent.IMAGE_GENERATE, 0.95
        
        # Check for image edit intent
        edit_matches = self.edit_pattern.findall(content_lower)
        if edit_matches:
            # Calculate confidence based on number of matches and message length
            confidence = min(0.9, len(edit_matches) * 0.3 + 0.4)
            return CommandIntent.IMAGE_EDIT, confidence
        
        # Check for specific image editing phrases
        image_edit_phrases = [
            'edit this image', 'modify the picture', 'change the photo',
            'can you edit', 'please edit', 'image editing'
        ]
        
        for phrase in image_edit_phrases:
            if phrase in content_lower:
                return CommandIntent.IMAGE_EDIT, 0.8
        
        return CommandIntent.UNKNOWN, 0.0
    
    def detect_edit_type(self, instruction: str) -> EditType:
        """
        Detect the type of image edit requested from the instruction.
        
        Implements requirement 2.1: Support different types of image edits.
        
        Args:
            instruction: The natural language edit instruction
            
        Returns:
            The detected EditType
        """
        instruction_lower = instruction.lower()
        
        # Score each edit type based on keyword matches
        type_scores = {}
        
        for edit_type, keywords in self.image_edit_keywords.items():
            score = 0
            for keyword in keywords:
                if keyword in instruction_lower:
                    # Longer keywords get higher scores
                    score += len(keyword.split())
            type_scores[edit_type] = score
        
        # Return the edit type with the highest score
        if type_scores:
            best_type = max(type_scores, key=type_scores.get)
            if type_scores[best_type] > 0:
                return best_type
        
        # Default to general edit if no specific type detected
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
            
            # Detect edit type
            edit_type = self.detect_edit_type(instruction)
            
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
    
    async def handle_contextual_help(self, message: discord.Message):
        """
        Provide contextual help based on the user's message.
        
        Implements requirement 5.2: Provide contextual help when users mention
        capabilities incorrectly.
        
        Args:
            message: The Discord message requesting help
        """
        try:
            # Analyze the help request for specific topics
            content_lower = message.content.lower()
            
            # Create base help embed
            embed = discord.Embed(
                title="🤖 Bot Help",
                description="Here's how you can use me:",
                color=discord.Color.blue()
            )
            
            # Check for specific help topics
            if any(word in content_lower for word in ['image', 'edit', 'photo', 'picture']):
                embed.add_field(
                    name="🖼️ Image Editing",
                    value="Upload an image and mention me with instructions like:\n"
                          "• `@bot remove the background`\n"
                          "• `@bot make it look like a painting`\n"
                          "• `@bot brighten this image`\n"
                          "• `@bot replace background with beach scene`",
                    inline=False
                )
            
            if any(word in content_lower for word in ['command', 'slash', 'function']):
                embed.add_field(
                    name="⚡ Slash Commands",
                    value="`/help` - Show detailed help\n"
                          "`/ping` - Check bot status\n"
                          "`/model` - Switch AI models\n"
                          "`/stats` - View usage statistics",
                    inline=False
                )
            
            # General usage if no specific topic
            if not any(word in content_lower for word in ['image', 'command', 'slash']):
                embed.add_field(
                    name="💬 General Usage",
                    value="Simply mention me (@bot) in any message to get an AI response!\n"
                          "I can help with questions, analysis, and image editing.",
                    inline=False
                )
                
                embed.add_field(
                    name="🖼️ Image Editing",
                    value="Upload images with natural language instructions:\n"
                          "`@bot edit this image` - General editing\n"
                          "`@bot remove the person` - Object removal\n"
                          "`@bot change background` - Background replacement",
                    inline=False
                )
            
            embed.add_field(
                name="💡 Tips",
                value="• Be specific with your requests\n"
                      "• Use natural language - no special syntax needed\n"
                      "• For image editing, attach the image to your message",
                inline=False
            )
            
            await message.reply(embed=embed)
            
        except Exception as e:
            logger.error(f"Error providing contextual help: {e}", exc_info=True)
            await message.reply(
                "I'd love to help, but I'm having trouble right now. "
                "Try using `/help` for detailed information! 🤖"
            )
    
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
                    processing_time=0.0
                )
            
            # Wait a bit before checking again
            await asyncio.sleep(0.5)