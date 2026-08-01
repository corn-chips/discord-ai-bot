"""
Enhanced command handler for Discord bot with image editing support.

This module extends the existing command system with natural language
command recognition, image editing capabilities, and improved user experience.
"""

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum

import discord
from google.genai import types

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


@dataclass(frozen=True)
class RoutingDecision:
    intent: CommandIntent = CommandIntent.UNKNOWN
    complexity: str = "low"
    edit_type: Optional[EditType] = None
    needs_context: bool = True


class EnhancedCommandHandler:
    """
    Enhanced command handler with natural language processing and image editing support.
    
    Implements requirements 1.1, 2.1, 2.2, 5.1 for image edit command detection,
    natural language parsing, and integration with image processing service.
    """
    
    def __init__(self, bot, image_processing_service: ImageProcessingService, error_manager: ErrorManager, gemini_client):
        """
        Initialize the enhanced command handler.
        
        Uses the configured router model for all decisions.
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
        self._router_cache: "OrderedDict[Tuple[str, bool], Tuple[float, RoutingDecision]]" = OrderedDict()
        self._router_cache_size = max(1, bot.config.router_cache_size)
        self._router_cache_ttl = max(1, bot.config.router_cache_ttl)

    @staticmethod
    def _normalize_router_content(message_content: str) -> str:
        """Normalize content for router cache lookups."""
        return re.sub(r"\s+", " ", (message_content or "").strip().lower())[:200]

    @staticmethod
    def _map_router_edit_type(edit_type_value: Optional[str]) -> Optional[EditType]:
        """Convert router edit_type string to EditType enum."""
        if not edit_type_value:
            return None
        edit_type_map = {
            "object_removal": EditType.OBJECT_REMOVAL,
            "background": EditType.BACKGROUND_REPLACEMENT,
            "style": EditType.STYLE_TRANSFER,
            "color": EditType.COLOR_ADJUSTMENT,
            "general": EditType.GENERAL_EDIT,
        }
        return edit_type_map.get(edit_type_value.strip().lower())

    def _get_cached_router_result(
        self, cache_key: Tuple[str, bool]
    ) -> Optional[RoutingDecision]:
        """Return cached router decision if fresh."""
        now = time.monotonic()
        cached = self._router_cache.get(cache_key)
        if not cached:
            return None

        cached_at, decision = cached
        if now - cached_at > self._router_cache_ttl:
            self._router_cache.pop(cache_key, None)
            return None

        self._router_cache.move_to_end(cache_key)
        return decision

    def _set_cached_router_result(
        self,
        cache_key: Tuple[str, bool],
        decision: RoutingDecision,
    ) -> None:
        """Store router decision in bounded LRU cache."""
        self._router_cache[cache_key] = (time.monotonic(), decision)
        self._router_cache.move_to_end(cache_key)

        while len(self._router_cache) > self._router_cache_size:
            self._router_cache.popitem(last=False)
    
    async def _check_intent_and_complexity(self, message_content: str, has_images: bool) -> RoutingDecision:
        """
        Use Gemini router model to determine user intent and complexity.
        
        This is the ONLY decision-making function - no keyword matching is used.
        
        Args:
            message_content: The message content to analyze
            has_images: Whether the message has image attachments
            
        Returns:
            Tuple of (CommandIntent, complexity_level, edit_type)
            - CommandIntent: The detected intent (IMAGE_GENERATE, IMAGE_EDIT, or UNKNOWN)
            - complexity_level: "low", "medium", or "high" for model selection
            - edit_type: Optional edit category for IMAGE_EDIT intents
        """
        try:
            cache_key = (self._normalize_router_content(message_content), has_images)
            cached_decision = self._get_cached_router_result(cache_key)
            if cached_decision:
                logger.debug("Router cache hit for %s", cache_key)
                return cached_decision

            # Optimized prompt for Gemini router to classify intent and complexity
            classification_prompt = f"""Classify the user's intent and task complexity.

User message: "{message_content}"
Has image attachments: {"yes" if has_images else "no"}

Return JSON with this schema:
{{
  "intent": "image_generate" | "image_edit" | "text",
  "complexity": "low" | "medium" | "high",
  "edit_type": "object_removal" | "background" | "style" | "color" | "general" | null,
  "needs_context": true | false
}}

Intent rules:
- "image_generate": User wants to CREATE/GENERATE a NEW image from text description (e.g., "generate a picture of...", "create an image of...", "draw me...", "make a picture of...")
- "image_edit": User wants to MODIFY an EXISTING attached image (only valid if Has image attachments=yes)
- "text": Any other request (questions, conversations, coding, analysis, etc.)

Complexity rules (BE CONSERVATIVE - favor lower complexity):
- "low": DEFAULT for most questions. Simple facts, greetings, short answers, casual conversation, basic questions, quick lookups, simple explanations, memes, jokes
- "medium": Only for tasks needing moderate reasoning: multi-step explanations, code debugging, summarizing documents, comparative analysis
- "high": ONLY for genuinely complex tasks requiring deep expertise: advanced mathematics/proofs, complex system architecture, novel research questions, multi-file code refactoring, PhD-level analysis

IMPORTANT: When in doubt, choose "low" or "medium". Reserve "high" for truly exceptional complexity.
Most everyday questions should be "low". Most work-related tasks should be "medium".

Edit type rules:
- Set "edit_type" only when intent is "image_edit".
- For image edits choose one of: "object_removal", "background", "style", "color", "general".
- For non-image intents return null.

Context rule: needs_context=false only when the request is fully self-contained and
does not benefit from conversation history. When uncertain, return true.
"""

            # Create router model instance for classification.
            # Router model always comes from config.
            router_model = self.bot.config.router_model_name

            if not self.gemini_client.client:
                logger.warning("Gemini client not initialized, skipping router")
                return RoutingDecision()

            # The async SDK, deliberately, and NOT asyncio.to_thread.
            #
            # This used to call the synchronous SDK through asyncio.to_thread,
            # which runs on the loop's DEFAULT ThreadPoolExecutor -- the pool
            # every other to_thread in the process shares, including all ~20
            # SQLite calls in message_index_service, token_tracker and the pin
            # loads. It is sized min(32, cpu_count + 4), so 6-8 threads on a
            # typical small host. A hung provider call held a worker for as long
            # as the TCP connection lasted, and this call had no deadline at all,
            # so a handful of them starved every database operation in the bot.
            # Measured: with the pool saturated, an unrelated to_thread was still
            # unscheduled after 1.5 s.
            #
            # Wrapping the to_thread in asyncio.wait_for does NOT fix that.
            # Cancelling the await abandons the coroutine and leaves the worker
            # running -- measured, 32/32 threads still alive after every await
            # was cancelled. Only leaving the executor removes the hazard.
            #
            # This is already the house idiom: gemini_client.py:680 makes the
            # identical call, same options and the same JSON response_mime_type,
            # for the context selector. The error surface is unchanged -- the
            # SDK's raise_for_response and raise_for_async_response construct the
            # same ClientError/ServerError from google.genai.errors -- and in any
            # case the handler below classifies nothing and never retries, so a
            # differing transport exception cannot change behaviour here.
            #
            # The deadline is what the executor could never give us: cancelling
            # this await genuinely aborts the request. asyncio.TimeoutError is an
            # Exception, so it lands in the same handler and degrades to the
            # default RoutingDecision like any other router failure.
            response = await asyncio.wait_for(
                self.gemini_client.client.aio.models.generate_content(
                    model=router_model,
                    contents=classification_prompt,
                    config=types.GenerateContentConfig(
                        temperature=self.bot.config.router_temperature,
                        max_output_tokens=self.bot.config.router_max_output_tokens,
                        response_mime_type="application/json"
                    )
                ),
                timeout=self.bot.config.response_timeout,
            )
            
            # Extract and normalize the response
            import json
            try:
                result = json.loads(response.text)
                # Handle case where model returns a list instead of a dict
                if isinstance(result, list) and len(result) > 0:
                    logger.debug(f"Router returned list, extracting first element: {result}")
                    result = result[0]  # Take the first element
                if isinstance(result, dict):
                    intent_str = result.get("intent", "text").lower()
                    complexity_level = result.get("complexity", "low").lower()
                    edit_type_str = result.get("edit_type")
                    needs_context = result.get("needs_context")
                    logger.debug(f"Router raw response: intent={intent_str}, complexity={complexity_level}")
                else:
                    logger.warning(f"Unexpected router response type: {type(result)}, value: {result}")
                    intent_str = "text"
                    complexity_level = "low"
                    edit_type_str = None
                    needs_context = True
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse router JSON response: {response.text}")
                intent_str = "text"
                complexity_level = "low"
                edit_type_str = None
                needs_context = True
            
            # Parse the response
            intent = CommandIntent.UNKNOWN
            
            if intent_str == "image_generate":
                intent = CommandIntent.IMAGE_GENERATE
            elif intent_str == "image_edit":
                intent = CommandIntent.IMAGE_EDIT
            else:  # "text" or anything else
                intent = CommandIntent.UNKNOWN
            
            # Validate complexity level
            if complexity_level not in ["low", "medium", "high"]:
                logger.warning(f"Invalid complexity level '{complexity_level}', defaulting to 'low'")
                complexity_level = "low"
            
            edit_type = self._map_router_edit_type(edit_type_str) if intent == CommandIntent.IMAGE_EDIT else None
            if intent == CommandIntent.IMAGE_EDIT and has_images and not edit_type:
                edit_type = EditType.GENERAL_EDIT

            if not isinstance(needs_context, bool):
                needs_context = True
            decision = RoutingDecision(intent, complexity_level, edit_type, needs_context)
            self._set_cached_router_result(cache_key, decision)

            logger.info(
                "Router decision: '%s...' -> intent=%s, complexity=%s, edit_type=%s",
                message_content[:50],
                intent.value,
                complexity_level,
                edit_type.value if edit_type else "n/a",
            )
            return decision
            
        except Exception as e:
            logger.error(f"Error in router model: {e}", exc_info=True)
            # On error, default to unknown intent and low complexity (safer/faster fallback)
            return RoutingDecision()
    
    async def handle_message(self, message: discord.Message) -> Tuple[bool, RoutingDecision]:
        """
        Handle a Discord message and determine if it contains commands.
        
        All decisions are made by the configured router model.
        NO keyword matching is used.
        
        Args:
            message: The Discord message to process
            
        Returns:
            Tuple of (handled, complexity_level, intent)
            - handled: True if the message was handled as a command, False otherwise
            - complexity_level: "low", "medium", or "high" for model selection
            - intent: Router-detected intent for downstream processing
        """
        try:
            # Check for image attachments
            has_images = any(
                attachment.content_type and attachment.content_type.startswith('image/')
                for attachment in message.attachments
            )
            
            # Use router model to determine intent and complexity - NO KEYWORD MATCHING
            decision = await self._check_intent_and_complexity(message.content, has_images)
            intent, complexity_level, edit_type = decision.intent, decision.complexity, decision.edit_type
            
            logger.info(f"📋 Handler routing: intent={intent.value}, has_images={has_images}, complexity={complexity_level}")
            
            # Route based on router model decision
            if intent == CommandIntent.IMAGE_GENERATE:
                if not getattr(self.bot, "image_generation_enabled", True):
                    await message.reply(
                        "Image generation is currently disabled by server configuration."
                    )
                    return True, decision

                if has_images:
                    # User wants to generate but has images attached - might be confused
                    # Let router handle it as edit since images are present
                    await self.handle_image_edit_command(message, detected_edit_type=edit_type)
                else:
                    await self.handle_image_generation_command(message)
                return True, decision
            
            elif intent == CommandIntent.IMAGE_EDIT:
                if not self.image_processing_service:
                    await message.reply("Image editing is currently disabled by server configuration.")
                    return True, decision
                if has_images:
                    await self.handle_image_edit_command(message, detected_edit_type=edit_type)
                else:
                    await self._suggest_image_upload(message)
                return True, decision
            
            # UNKNOWN intent - let main bot handle it with the determined complexity
            return False, decision
            
        except Exception as e:
            logger.error(f"Error handling message in enhanced command handler: {e}", exc_info=True)
            error_context = self.error_manager.create_error_context(
                e, "I had trouble processing your command. Please try again!"
            )
            await self.error_manager.send_error_response(message, error_context)
            return True, RoutingDecision(complexity="medium")
    

    async def handle_image_edit_command(self, message: discord.Message, detected_edit_type: Optional[EditType] = None):
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
            
            # Use router-provided edit type, default to GENERAL_EDIT
            edit_type = detected_edit_type or EditType.GENERAL_EDIT
            
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
        
        Generates a new image from a text description using the configured image model.
        
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
                except discord.HTTPException:
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
                               f"**Model:** {self.image_processing_service.client.model_name}",
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
                except discord.HTTPException:
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
                                      timeout: int = None) -> ImageEditResult:
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
        if timeout is None:
            timeout = self.bot.config.job_timeout

        job = await self.image_processing_service.get_job_status(job_id)
        if not job:
            raise RuntimeError(f"Job {job_id} not found")

        try:
            await asyncio.wait_for(job.completion_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Image processing timed out after {timeout}s") from exc

        final_job = await self.image_processing_service.get_job_status(job_id) or job

        try:
            await processing_msg.delete()
        except discord.HTTPException:
            pass

        if final_job.status == ProcessingStatus.COMPLETED and final_job.result:
            return final_job.result

        return ImageEditResult(
            success=False,
            error_message=final_job.error_message or "Image processing failed",
            processing_time=0.0,
            metadata=None,
            token_usage=None,
        )
