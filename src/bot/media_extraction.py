"""Attachment and context-media extraction for Discord messages."""

from __future__ import annotations

import io
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import discord
from PIL import Image

from ..models.data_models import MessageContext


logger = logging.getLogger(__name__)


class MediaExtractionCoordinator:
    """Extract ordered media with Discord-specific failure isolation."""

    def __init__(
        self,
        *,
        pdf_converter: Callable[[bytes, str], Awaitable[List[Image.Image]]],
        image_to_rgb: Callable[[Image.Image], Image.Image],
        get_max_context_images: Callable[[], int],
        get_max_context_messages: Callable[[], int],
        get_max_text_file_size: Callable[[], int],
        supported_text_extensions: set[str],
    ) -> None:
        self.pdf_converter = pdf_converter
        self.image_to_rgb = image_to_rgb
        self.get_max_context_images = get_max_context_images
        self.get_max_context_messages = get_max_context_messages
        self.get_max_text_file_size = get_max_text_file_size
        self.supported_text_extensions = supported_text_extensions

    @staticmethod
    def create_image_context_entry(
        source_type: str,
        source_message: discord.Message,
        attachment_name: str,
        attachment_index: int,
        pdf_page_number: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create metadata linking one image part to its source message."""
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
    def attach_image_order_metadata(
        image_context: List[Dict[str, Any]],
        context: List[MessageContext],
        current_message: discord.Message,
    ) -> List[Dict[str, Any]]:
        """Annotate image metadata with explicit image and message order labels."""
        sorted_context = sorted(context, key=lambda message: (message.timestamp, message.message_id))
        context_order_map = {
            message.message_id: f"CTX_MSG_{index:03d}"
            for index, message in enumerate(sorted_context, start=1)
        }
        replied_message_id = (
            current_message.reference.message_id
            if current_message.reference
            else None
        )

        enriched_entries = []
        for index, entry in enumerate(image_context, start=1):
            enriched = dict(entry)
            enriched["image_index"] = index
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

    async def _extract_image_attachments(
        self,
        *,
        source_message: discord.Message,
        source_type: str,
        replied: bool,
    ) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        images: List[Image.Image] = []
        image_context: List[Dict[str, Any]] = []
        for attachment_index, attachment in enumerate(source_message.attachments, start=1):
            is_pdf = (
                attachment.content_type == "application/pdf"
                or attachment.filename.lower().endswith(".pdf")
            )
            if is_pdf:
                try:
                    location = " in replied message" if replied else ""
                    logger.info("PDF detected%s: %s", location, attachment.filename)
                    pdf_bytes = await attachment.read()
                    pdf_images = await self.pdf_converter(pdf_bytes, attachment.filename)
                    for page_number, page_image in enumerate(pdf_images, start=1):
                        images.append(page_image)
                        image_context.append(
                            self.create_image_context_entry(
                                source_type=source_type,
                                source_message=source_message,
                                attachment_name=attachment.filename,
                                attachment_index=attachment_index,
                                pdf_page_number=page_number,
                            )
                        )
                    if pdf_images:
                        logger.info(
                            "Added %s page(s) from%s PDF: %s",
                            len(pdf_images),
                            " replied" if replied else "",
                            attachment.filename,
                        )
                    elif not replied:
                        logger.warning(
                            "No pages could be extracted from PDF: %s",
                            attachment.filename,
                        )
                except Exception as exc:
                    if replied:
                        logger.error(
                            "Failed to process PDF from replied message %s: %s",
                            attachment.filename,
                            exc,
                        )
                    else:
                        logger.error(
                            "Failed to process PDF %s: %s",
                            attachment.filename,
                            exc,
                            exc_info=True,
                        )
                continue

            if not (
                attachment.content_type
                and attachment.content_type.startswith("image/")
            ):
                continue
            try:
                image_bytes = await attachment.read()
                image = Image.open(io.BytesIO(image_bytes))
                image = self.image_to_rgb(image)
                images.append(image)
                image_context.append(
                    self.create_image_context_entry(
                        source_type=source_type,
                        source_message=source_message,
                        attachment_name=attachment.filename,
                        attachment_index=attachment_index,
                    )
                )
                logger.info(
                    "Loaded image from %s: %s (%sx%s)",
                    "replied message" if replied else "attachment",
                    attachment.filename,
                    image.size[0],
                    image.size[1],
                )
            except Exception as exc:
                logger.error(
                    "Failed to load image from %s%s: %s",
                    "replied message attachment " if replied else "attachment ",
                    attachment.filename,
                    exc,
                )

        return images, image_context

    async def extract_images_from_message(
        self,
        message: discord.Message,
    ) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """Extract current and replied-message images, including PDF pages."""
        images, image_context = await self._extract_image_attachments(
            source_message=message,
            source_type="current_message",
            replied=False,
        )

        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                replied_images, replied_context = await self._extract_image_attachments(
                    source_message=replied_message,
                    source_type="replied_message",
                    replied=True,
                )
                images.extend(replied_images)
                image_context.extend(replied_context)

        return images, image_context

    async def extract_context_images(
        self,
        message: discord.Message,
        context: List[MessageContext],
        exclude_message_ids: Optional[set[int]] = None,
    ) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
        """Extract bounded, chronologically ordered images from context history."""
        max_context_images = max(0, self.get_max_context_images())
        if max_context_images == 0 or not context:
            return [], []

        excluded_ids = exclude_message_ids or set()
        context_message_ids = {
            entry.message_id
            for entry in context
            if entry.message_id not in excluded_ids
        }
        if not context_message_ids:
            return [], []

        images: List[Image.Image] = []
        image_context: List[Dict[str, Any]] = []
        history_limit = max(
            len(context_message_ids) * 2,
            self.get_max_context_messages() * 2,
        )

        try:
            async for context_message in message.channel.history(limit=history_limit):
                if len(images) >= max_context_images:
                    break
                if context_message.id not in context_message_ids:
                    continue

                for attachment_index, attachment in enumerate(
                    context_message.attachments,
                    start=1,
                ):
                    if len(images) >= max_context_images:
                        break
                    is_image = (
                        attachment.content_type
                        and attachment.content_type.startswith("image/")
                    ) or attachment.filename.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                    )
                    if not is_image:
                        continue
                    try:
                        image_bytes = await attachment.read()
                        image = Image.open(io.BytesIO(image_bytes))
                        image = self.image_to_rgb(image)
                        images.append(image)
                        image_context.append(
                            self.create_image_context_entry(
                                source_type="context_message",
                                source_message=context_message,
                                attachment_name=attachment.filename,
                                attachment_index=attachment_index,
                            )
                        )
                        logger.info(
                            "Loaded context image: %s from message %s (%sx%s)",
                            attachment.filename,
                            context_message.id,
                            image.size[0],
                            image.size[1],
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to load context image %s from message %s: %s",
                            attachment.filename,
                            context_message.id,
                            exc,
                        )
        except discord.Forbidden:
            logger.warning(
                "No permission to read channel history for context images in channel %s",
                message.channel.id,
            )
        except discord.HTTPException as exc:
            logger.error("Discord API error retrieving context images: %s", exc)

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

    async def extract_audio_from_message(
        self,
        message: discord.Message,
    ) -> List[Dict[str, Any]]:
        """Extract supported audio from the current and replied messages."""
        audio_files: List[Dict[str, Any]] = []

        async def process_audio_attachment(attachment, source: str = "message") -> None:
            is_audio = False
            mime_type = attachment.content_type
            if hasattr(attachment, "is_voice_message") and attachment.is_voice_message():
                is_audio = True
                mime_type = "audio/ogg"
                logger.info("Voice message detected in %s: %s", source, attachment.filename)
            elif mime_type and any(kind in mime_type for kind in ("audio/", "video/mp4")):
                is_audio = True
            elif attachment.filename.lower().endswith(
                (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".mpga")
            ):
                is_audio = True
                extension_mime_types = {
                    ".mp3": "audio/mp3",
                    ".wav": "audio/wav",
                    ".aac": "audio/aac",
                    ".m4a": "audio/mp4",
                    ".ogg": "audio/ogg",
                    ".mpga": "audio/mpeg",
                }
                for extension, resolved_mime_type in extension_mime_types.items():
                    if attachment.filename.lower().endswith(extension):
                        mime_type = resolved_mime_type
                        break

            if is_audio:
                try:
                    logger.info("Audio detected in %s: %s", source, attachment.filename)
                    audio_bytes = await attachment.read()
                    audio_files.append(
                        {
                            "data": audio_bytes,
                            "mime_type": mime_type or "audio/mp3",
                            "filename": attachment.filename,
                        }
                    )
                    logger.info(
                        "Loaded audio from %s: %s (%s bytes)",
                        source,
                        attachment.filename,
                        len(audio_bytes),
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to process audio %s: %s",
                        attachment.filename,
                        exc,
                        exc_info=True,
                    )

        for attachment in message.attachments:
            await process_audio_attachment(attachment, "message")

        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                for attachment in replied_message.attachments:
                    await process_audio_attachment(attachment, "reply")

        return audio_files

    async def extract_files_from_message(
        self,
        message: discord.Message,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Extract supported text files from current and replied messages."""
        files: List[Dict[str, Any]] = []
        unsupported_files: List[str] = []
        logger.info("Extracting files from message %s", message.id)
        logger.info("Total attachments in message: %s", len(message.attachments))
        max_file_size = self.get_max_text_file_size()

        async def process_attachment(attachment: discord.Attachment) -> None:
            logger.info("Processing attachment: %s", attachment.filename)
            logger.info("  - Content type: %s", attachment.content_type)
            logger.info(
                "  - Size: %s bytes (%.2f KB)",
                attachment.size,
                attachment.size / 1024,
            )

            if attachment.content_type and attachment.content_type.startswith("image/"):
                logger.info(
                    "  -> Skipping %s (image file - handled separately)",
                    attachment.filename,
                )
                return

            if (
                attachment.content_type == "application/pdf"
                or attachment.filename.lower().endswith(".pdf")
            ):
                logger.info(
                    "  -> Skipping %s (PDF file - converted to images and handled separately)",
                    attachment.filename,
                )
                return

            if attachment.size > max_file_size:
                logger.warning(
                    "  -> REJECTED: %s is too large (%s bytes = %.1fMB)",
                    attachment.filename,
                    attachment.size,
                    attachment.size / (1024 * 1024),
                )
                logger.warning(
                    "  -> Maximum allowed size: %.1fMB",
                    max_file_size / (1024 * 1024),
                )
                unsupported_files.append(
                    f"{attachment.filename} "
                    f"(too large: {attachment.size / (1024 * 1024):.1f}MB)"
                )
                return

            file_extension = None
            if "." in attachment.filename:
                file_extension = "." + attachment.filename.rsplit(".", 1)[1].lower()
                logger.info("  - File extension: %s", file_extension)
            else:
                logger.info("  - No file extension detected")

            content_type = attachment.content_type
            is_supported = file_extension in self.supported_text_extensions or content_type and (
                content_type.startswith("text/")
                or "json" in content_type
                or "xml" in content_type
                or "yaml" in content_type
            )

            if not is_supported:
                logger.warning(
                    "  -> REJECTED: %s is an UNSUPPORTED file type",
                    attachment.filename,
                )
                logger.warning(
                    "  -> Extension '%s' not in supported list",
                    file_extension,
                )
                logger.warning(
                    "  -> Content type '%s' not recognized as text-based",
                    content_type,
                )
                unsupported_files.append(f"{attachment.filename} (unsupported type)")
                return

            logger.info("  - %s is a SUPPORTED file type", attachment.filename)
            try:
                logger.info("  -> Downloading file content...")
                file_bytes = await attachment.read()
                logger.info("  -> Downloaded %s bytes", len(file_bytes))

                content = None
                tried_encodings = []
                for encoding in ("utf-8", "latin-1", "cp1252", "ascii"):
                    try:
                        content = file_bytes.decode(encoding)
                        logger.info(
                            "  - Successfully decoded %s with %s encoding",
                            attachment.filename,
                            encoding,
                        )
                        break
                    except UnicodeDecodeError:
                        tried_encodings.append(encoding)
                        logger.debug("  - Failed to decode with %s", encoding)

                if content is None:
                    logger.error(
                        "  -> FAILED: Could not decode file %s with any encoding",
                        attachment.filename,
                    )
                    logger.error("  -> Tried encodings: %s", ", ".join(tried_encodings))
                    unsupported_files.append(f"{attachment.filename} (encoding error)")
                    return

                files.append(
                    {
                        "name": attachment.filename,
                        "content": content,
                        "size": attachment.size,
                    }
                )
                logger.info("  - SUCCESS: Loaded %s", attachment.filename)
                logger.info(
                    "     - File size: %s bytes (%.2f KB)",
                    attachment.size,
                    attachment.size / 1024,
                )
                logger.info("     - Content length: %s characters", len(content))
                logger.info(
                    "     - Content preview: %s..." if len(content) > 150 else "     - Full content: %s",
                    content[:150] if len(content) > 150 else content,
                )
            except Exception as exc:
                logger.error(
                    "  -> FAILED: Error loading %s: %s",
                    attachment.filename,
                    exc,
                )
                logger.error("  -> Exception type: %s", type(exc).__name__)
                unsupported_files.append(f"{attachment.filename} (error: {str(exc)})")

        logger.info("Processing attachments from main message...")
        for index, attachment in enumerate(message.attachments, start=1):
            logger.info(
                "Attachment %s/%s: %s",
                index,
                len(message.attachments),
                attachment.filename,
            )
            await process_attachment(attachment)

        if message.reference and message.reference.resolved:
            replied_message = message.reference.resolved
            if isinstance(replied_message, discord.Message):
                logger.info(
                    "Message is a reply, processing %s attachments from replied message...",
                    len(replied_message.attachments),
                )
                for index, attachment in enumerate(replied_message.attachments, start=1):
                    logger.info(
                        "Replied attachment %s/%s: %s",
                        index,
                        len(replied_message.attachments),
                        attachment.filename,
                    )
                    await process_attachment(attachment)

        logger.info("=" * 40)
        logger.info("FILE EXTRACTION SUMMARY:")
        logger.info("  - Successfully processed: %s file(s)", len(files))
        for file_info in files:
            logger.info("     - %s", file_info["name"])
        logger.info("  - Failed/Unsupported: %s file(s)", len(unsupported_files))
        for unsupported_file in unsupported_files:
            logger.info("     - %s", unsupported_file)
        logger.info("=" * 40)
        return files, unsupported_files
