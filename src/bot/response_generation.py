"""AI response generation orchestration for Discord messages."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import discord

from ..models.data_models import APIResponse, TokenUsage
from ..utils.error_manager import ErrorContext, ErrorType


logger = logging.getLogger(__name__)


class ResponseGenerationCoordinator:
    """Coordinate request preparation, Gemini execution, and result handling."""

    def __init__(
        self,
        *,
        gemini_client: Any,
        error_manager: Any,
        performance_logger: Any,
        extract_images: Callable[..., Awaitable[Tuple[List[Any], List[Dict[str, Any]]]]],
        extract_context_images: Callable[..., Awaitable[Tuple[List[Any], List[Dict[str, Any]]]]],
        extract_audio: Callable[..., Awaitable[List[Dict[str, Any]]]],
        extract_files: Callable[..., Awaitable[Tuple[List[Dict[str, Any]], List[str]]]],
        attach_image_order_metadata: Callable[..., List[Dict[str, Any]]],
        remove_duplicate_context: Callable[[List[Any], List[Any]], List[Any]],
        resolve_request_preferences: Callable[..., Tuple[str, Optional[str]]],
        get_personality_prompt: Callable[[int], Optional[str]],
        get_token_tracker: Callable[[], Any],
        send_status_message: Callable[[discord.Message, str], Awaitable[discord.Message]],
        deliver_response: Callable[..., Awaitable[None]],
    ) -> None:
        self.gemini_client = gemini_client
        self.error_manager = error_manager
        self.performance_logger = performance_logger
        self.extract_images = extract_images
        self.extract_context_images = extract_context_images
        self.extract_audio = extract_audio
        self.extract_files = extract_files
        self.attach_image_order_metadata = attach_image_order_metadata
        self.remove_duplicate_context = remove_duplicate_context
        self.resolve_request_preferences = resolve_request_preferences
        self.get_personality_prompt = get_personality_prompt
        self.get_token_tracker = get_token_tracker
        self.send_status_message = send_status_message
        self.deliver_response = deliver_response

    async def handle_response_error(
        self,
        message: discord.Message,
        api_response: APIResponse,
    ) -> None:
        """Map an API response error to the shared Discord error flow."""
        error_type_mapping = {
            "rate_limit": ErrorType.RATE_LIMIT,
            "timeout": ErrorType.TIMEOUT,
            "authentication_error": ErrorType.AUTHENTICATION_ERROR,
            "service_unavailable": ErrorType.SERVICE_UNAVAILABLE,
            "empty_response": ErrorType.EMPTY_RESPONSE,
            "invalid_request": ErrorType.INVALID_REQUEST,
            "configuration_error": ErrorType.CONFIGURATION_ERROR,
            "unknown_error": ErrorType.UNKNOWN_ERROR,
            "safety_filter": ErrorType.EMPTY_RESPONSE,
            "max_tokens": ErrorType.INVALID_REQUEST,
            "recitation": ErrorType.EMPTY_RESPONSE,
        }
        error_type = error_type_mapping.get(
            api_response.error_type,
            ErrorType.UNKNOWN_ERROR,
        )
        base_message = self.error_manager.get_user_message(error_type)
        if api_response.content:
            user_message = f"{base_message}\n\n**Details:** {api_response.content}"
        else:
            user_message = (
                f"{base_message}\n\n**Error Type:** `{api_response.error_type}`"
            )
        error_context = ErrorContext(
            error_type=error_type,
            user_message=user_message,
            technical_details=(
                api_response.content
                or f"API response error: {api_response.error_type}"
            ),
            retry_after=api_response.retry_after,
        )
        self.error_manager.log_error(
            error_context,
            f"API Response Error: {api_response.error_type}",
        )
        await self.error_manager.send_error_response(message, error_context)

    async def record_token_usage(
        self,
        message: discord.Message,
        token_usage: Optional[TokenUsage],
    ) -> None:
        """Persist request token counts without failing response delivery."""
        if not token_usage:
            return
        token_tracker = self.get_token_tracker()
        if not token_tracker:
            return
        username = (
            message.author.display_name
            or getattr(message.author, "global_name", None)
            or message.author.name
            or str(message.author)
        )[:80]
        guild_id = message.guild.id if message.guild else 0
        guild_name = message.guild.name if message.guild else "Direct Messages"
        try:
            await token_tracker.record_usage(
                user_id=message.author.id,
                username=username,
                guild_id=guild_id,
                guild_name=guild_name,
                input_tokens=token_usage.input_tokens,
                output_tokens=token_usage.output_tokens,
                total_tokens=token_usage.total_tokens,
            )
        except Exception as exc:
            logger.error(
                "Failed to record token usage for user %s: %s",
                message.author.id,
                exc,
            )

    async def _collect_request_inputs(
        self,
        message: discord.Message,
        user_prompt: str,
        context: List[Any],
        *,
        skip_context_media: bool,
    ) -> Optional[Tuple[str, List[Any], List[Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]]:
        """Collect Discord-owned media callbacks and build the final prompt."""
        if skip_context_media:
            images, image_context = [], []
        else:
            images, image_context = await self.extract_images(message)

        exclude_context_ids = {message.id}
        if (
            message.reference
            and message.reference.resolved
            and isinstance(message.reference.resolved, discord.Message)
        ):
            exclude_context_ids.add(message.reference.resolved.id)
        if skip_context_media:
            context_images, context_image_context = [], []
        else:
            context_images, context_image_context = await self.extract_context_images(
                message,
                context,
                exclude_context_ids,
            )
        if context_images:
            images.extend(context_images)
            image_context.extend(context_image_context)
            logger.info(
                "Added %s context image(s) from recent channel history "
                "(total images sent: %s)",
                len(context_images),
                len(images),
            )

        audio_files = await self.extract_audio(message)
        if audio_files:
            logger.info("Extracted %s audio file(s)", len(audio_files))
            original_context_len = len(context)
            if message.reference and message.reference.message_id:
                replied_id = message.reference.message_id
                replied = [item for item in context if item.message_id == replied_id]
                recent = context[-5:] if len(context) > 5 else context
                context = self.remove_duplicate_context(recent, replied)
                logger.info(
                    "Audio transcription: Context restricted to replied message + "
                    "recent (kept %s/%s messages)",
                    len(context),
                    original_context_len,
                )
            else:
                context = context[-5:] if len(context) > 5 else context
                logger.info(
                    "Audio transcription: Context trimmed to recent messages "
                    "(kept %s/%s messages)",
                    len(context),
                    original_context_len,
                )

        if image_context:
            image_context = self.attach_image_order_metadata(
                image_context,
                context,
                message,
            )

        logger.info("=" * 80)
        logger.info("FILE EXTRACTION STARTED")
        files, unsupported_files = await self.extract_files(message)
        logger.info(
            "File extraction complete: %s supported, %s unsupported",
            len(files),
            len(unsupported_files),
        )
        for file_info in files:
            logger.info(
                "Processed file %s (%s bytes, %s characters)",
                file_info["name"],
                file_info["size"],
                len(file_info["content"]),
            )
        for unsupported in unsupported_files:
            logger.warning("Unsupported/failed file: %s", unsupported)
        logger.info("=" * 80)

        if unsupported_files:
            unsupported_msg = (
                "ℹ️ Note: The following files could not be processed:\n"
                + "\n".join(f"- {item}" for item in unsupported_files)
            )
            try:
                await message.channel.send(unsupported_msg)
            except Exception as exc:
                logger.error("Failed to send unsupported files message: %s", exc)

        if (
            (not user_prompt or not user_prompt.strip())
            and not images
            and not files
            and not audio_files
        ):
            logger.warning(
                "Empty user prompt and no images/files/audio provided to response generation"
            )
            await message.reply(
                "Please provide a message, attach an image/audio, or upload a file "
                "for me to respond to! 📝"
            )
            return None

        enhanced_prompt = user_prompt
        if files:
            file_contents_text = "\n\n--- UPLOADED FILES ---\n"
            for file_info in files:
                file_contents_text += (
                    f"\n📄 **File: {file_info['name']}** "
                    f"(Size: {file_info['size']} bytes)\n"
                    f"```\n{file_info['content']}\n```\n"
                )
            if not enhanced_prompt or not enhanced_prompt.strip():
                enhanced_prompt = (
                    "I have uploaded the following file(s). Please analyze them:\n"
                    f"{file_contents_text}"
                )
            else:
                enhanced_prompt = (
                    f"{file_contents_text}\n\nUser's question/request: {enhanced_prompt}"
                )
        elif not enhanced_prompt or not enhanced_prompt.strip():
            if audio_files:
                enhanced_prompt = (
                    "Please provide a verbatim transcription of this audio. Preserve "
                    "all speech patterns including stutters, repetitions, and informal "
                    "language (e.g., 'gonna', 'wanna'). Include non-verbal sounds and "
                    "emotions in brackets, such as [laughter], [sigh], [unintelligible]. "
                    "Do not summarize or clean up the text; transcribe exactly what is heard."
                )
            elif images:
                enhanced_prompt = "What's in this image? Please describe it in detail."

        return (
            enhanced_prompt,
            context,
            images,
            image_context,
            audio_files,
            files,
        )

    @staticmethod
    async def _delete_status_message(status_message: Optional[discord.Message], reason: str) -> None:
        if not status_message:
            return
        try:
            await status_message.delete()
        except Exception as exc:
            logger.debug("Failed to delete status message %s: %s", reason, exc)

    async def generate_and_send_response(
        self,
        message: discord.Message,
        user_prompt: str,
        context: List[Any],
        *,
        model_override: Optional[str] = None,
        prompt_mode_override: Optional[str] = None,
        search_override: Optional[bool] = None,
        show_status_message: bool = True,
        skip_context_media: bool = False,
        apply_user_preferences: bool = True,
        complexity_level: str = "low",
    ) -> None:
        """Generate an AI response and deliver it through the canonical callback."""
        status_message = None
        try:
            async with message.channel.typing():
                effective_model_override = model_override
                try:
                    start_time = time.time()
                    collected = await self._collect_request_inputs(
                        message,
                        user_prompt,
                        context,
                        skip_context_media=skip_context_media,
                    )
                    if collected is None:
                        return
                    (
                        enhanced_prompt,
                        context,
                        images,
                        image_context,
                        audio_files,
                        files,
                    ) = collected

                    personality_prompt = self.get_personality_prompt(message.channel.id)
                    effective_model_override, user_language = (
                        self.resolve_request_preferences(
                            user_id=message.author.id,
                            request_model_override=effective_model_override,
                            apply_user_preferences=apply_user_preferences,
                            complexity_level=complexity_level,
                        )
                    )
                    model_name = (
                        effective_model_override
                        or self.gemini_client.get_current_model()
                    )
                    estimated_time = self.gemini_client.get_estimated_response_time(
                        model_name=model_name,
                        prompt_mode=prompt_mode_override,
                    )
                    if show_status_message:
                        try:
                            status_message = await self.send_status_message(
                                message,
                                f"⏳ Processing your request with {model_name}...\n"
                                f"*Estimated time: {estimated_time}*",
                            )
                        except Exception as exc:
                            logger.warning("Failed to send status message: %s", exc)

                    api_response = await self.gemini_client.generate_response(
                        enhanced_prompt,
                        context,
                        images=images if images else None,
                        image_context=image_context if image_context else None,
                        audio_files=audio_files if audio_files else None,
                        on_chunk=None,
                        model_override=effective_model_override,
                        prompt_mode_override=prompt_mode_override,
                        complexity_override=complexity_level,
                        search_override=search_override,
                        personality_prompt=personality_prompt,
                        language=user_language,
                    )
                    await self._delete_status_message(status_message, "after response")

                    duration = time.time() - start_time
                    if api_response.success:
                        self.performance_logger.log_message_processing(
                            duration=duration,
                            context_messages=len(context),
                            response_length=(
                                len(api_response.content) if api_response.content else 0
                            ),
                            user_id=message.author.id,
                            guild_id=message.guild.id if message.guild else None,
                        )
                        await self.record_token_usage(message, api_response.token_usage)
                        await self.deliver_response(
                            message,
                            api_response.content,
                            api_response.grounding_sources,
                        )
                    else:
                        logger.error(
                            "Failed to generate response: %s",
                            api_response.error_type,
                        )
                        await self.handle_response_error(message, api_response)
                except asyncio.TimeoutError:
                    await self._delete_status_message(status_message, "after timeout")
                    timeout_used = self.gemini_client.get_timeout_for_model(
                        effective_model_override
                        or self.gemini_client.get_current_model(),
                        prompt_mode_override,
                    )
                    logger.error(
                        "Response generation timed out for message %s after %ss",
                        message.id,
                        timeout_used,
                    )
                    await self.handle_response_error(
                        message,
                        APIResponse(
                            success=False,
                            error_type="timeout",
                            content=(
                                "Response generation timed out after "
                                f"{timeout_used} seconds. The model may be overloaded. "
                                "Please try again or use a simpler question."
                            ),
                        ),
                    )
                except ConnectionError as exc:
                    logger.error("Connection error during API call: %s", exc)
                    error_context = self.error_manager.create_error_context(
                        exc,
                        "I'm having trouble connecting to my AI service. "
                        "Please try again in a moment! 🌐",
                    )
                    self.error_manager.log_error(error_context, "API connection error")
                    await self.error_manager.send_error_response(message, error_context)
                except ValueError as exc:
                    logger.error("Invalid value provided to API: %s", exc)
                    error_context = self.error_manager.create_error_context(
                        exc,
                        "There was an issue with the request format. "
                        "Please try rephrasing your message! 📝",
                    )
                    self.error_manager.log_error(error_context, "API value error")
                    await self.error_manager.send_error_response(message, error_context)
                except Exception as exc:
                    error_context = self.error_manager.create_error_context(exc)
                    self.error_manager.log_error(
                        error_context,
                        "Unexpected error during response generation for message "
                        f"{message.id}",
                    )
                    await self.error_manager.send_error_response(message, error_context)
        except discord.Forbidden as exc:
            logger.error(
                "Missing permissions to show typing indicator in channel %s",
                message.channel.id,
            )
            error_context = self.error_manager.create_error_context(
                exc,
                "I don't have permission to respond in this channel. "
                "Please check my permissions! 🔒",
            )
            self.error_manager.log_error(
                error_context,
                "Permission error showing typing indicator",
            )
            await self.error_manager.send_error_response(message, error_context)
        except discord.HTTPException as exc:
            logger.error("Discord HTTP error showing typing indicator: %s", exc)
            error_context = self.error_manager.create_error_context(
                exc,
                "I'm having trouble communicating with Discord. Please try again! 🌐",
            )
            self.error_manager.log_error(error_context, "Discord HTTP error")
            await self.error_manager.send_error_response(message, error_context)
