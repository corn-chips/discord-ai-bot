import io
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from PIL import Image

from src.bot.discord_bot import DiscordBot
from src.bot.media_extraction import MediaExtractionCoordinator
from src.constants import SUPPORTED_TEXT_EXTENSIONS
from src.models.data_models import MessageContext


class MediaExtractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.created_images = []
        self.addCleanup(self._close_images)

    def _close_images(self):
        for image in self.created_images:
            image.close()

    def make_image(self, color="red"):
        image = Image.new("RGB", (2, 2), color)
        self.created_images.append(image)
        return image

    def image_bytes(self, color="red"):
        image = self.make_image(color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def make_attachment(filename, content_type, data=b"data", *, size=None, error=None):
        read = AsyncMock(side_effect=error) if error else AsyncMock(return_value=data)
        return SimpleNamespace(
            filename=filename,
            content_type=content_type,
            size=len(data) if size is None else size,
            read=read,
        )

    @staticmethod
    def make_message(message_id, attachments, *, created_at=None, reference=None):
        return SimpleNamespace(
            id=message_id,
            attachments=attachments,
            created_at=created_at or datetime.now(timezone.utc),
            author=SimpleNamespace(display_name=f"User {message_id}"),
            reference=reference,
            channel=SimpleNamespace(id=50),
        )

    def make_coordinator(self, **overrides):
        defaults = {
            "pdf_converter": AsyncMock(return_value=[]),
            "image_to_rgb": lambda image: image,
            "get_max_context_images": lambda: 6,
            "get_max_context_messages": lambda: 20,
            "get_max_text_file_size": lambda: 1024,
            "supported_text_extensions": SUPPORTED_TEXT_EXTENSIONS,
        }
        defaults.update(overrides)
        return MediaExtractionCoordinator(**defaults)

    async def test_image_and_pdf_failures_are_isolated_and_metadata_stays_parallel(self):
        page_one = self.make_image("blue")
        page_two = self.make_image("green")
        pdf_converter = AsyncMock(return_value=[page_one, page_two])
        response = SimpleNamespace(status=500, reason="Server Error")
        read_error = discord.HTTPException(response, "read failed")
        attachments = [
            self.make_attachment("bad.pdf", "application/pdf", error=read_error),
            self.make_attachment("pages.pdf", "application/pdf", data=b"pdf"),
            self.make_attachment("bad.png", "image/png", error=read_error),
            self.make_attachment("good.png", "image/png", data=self.image_bytes("yellow")),
        ]
        message = self.make_message(10, attachments)
        coordinator = self.make_coordinator(pdf_converter=pdf_converter)

        images, metadata = await coordinator.extract_images_from_message(message)

        self.assertEqual(len(images), 3)
        self.assertEqual(len(metadata), len(images))
        self.assertEqual([entry["attachment_index"] for entry in metadata], [2, 2, 4])
        self.assertEqual(
            [entry.get("pdf_page_number") for entry in metadata],
            [1, 2, None],
        )
        self.assertTrue(all(entry["source_type"] == "current_message" for entry in metadata))
        pdf_converter.assert_awaited_once_with(b"pdf", "pages.pdf")

    async def test_context_images_respect_exclusions_limit_and_chronological_order(self):
        base = datetime.now(timezone.utc)
        older_attachment = self.make_attachment(
            "older.png",
            "image/png",
            data=self.image_bytes("blue"),
        )
        newer_attachment = self.make_attachment(
            "newer.png",
            "image/png",
            data=self.image_bytes("green"),
        )
        excluded_attachment = self.make_attachment(
            "excluded.png",
            "image/png",
            data=self.image_bytes("yellow"),
        )
        older = self.make_message(1, [older_attachment], created_at=base)
        newer = self.make_message(2, [newer_attachment], created_at=base + timedelta(seconds=1))
        excluded = self.make_message(3, [excluded_attachment], created_at=base + timedelta(seconds=2))
        history_limit = []

        async def history(*, limit):
            history_limit.append(limit)
            for item in (excluded, newer, older):
                yield item

        current = self.make_message(99, [])
        current.channel = SimpleNamespace(id=50, history=history)
        context = [
            MessageContext("older", "A", base, 1, 50),
            MessageContext("newer", "B", base + timedelta(seconds=1), 2, 50),
            MessageContext("excluded", "C", base + timedelta(seconds=2), 3, 50),
        ]
        coordinator = self.make_coordinator(get_max_context_images=lambda: 2)

        images, metadata = await coordinator.extract_context_images(
            current,
            context,
            exclude_message_ids={3},
        )

        self.assertEqual(len(images), 2)
        self.assertEqual([entry["source_message_id"] for entry in metadata], [1, 2])
        self.assertEqual(history_limit, [40])
        excluded_attachment.read.assert_not_awaited()

    def test_order_metadata_labels_context_current_reply_and_unknown_without_mutation(self):
        base = datetime.now(timezone.utc)
        current = self.make_message(
            10,
            [],
            reference=SimpleNamespace(message_id=20),
        )
        context = [
            MessageContext("later", "B", base + timedelta(seconds=1), 2, 50),
            MessageContext("earlier", "A", base, 1, 50),
        ]
        original = [
            {"source_message_id": 1},
            {"source_message_id": 10},
            {"source_message_id": 20},
            {"source_message_id": 999},
        ]

        enriched = MediaExtractionCoordinator.attach_image_order_metadata(
            original,
            context,
            current,
        )

        self.assertEqual(
            [entry["source_message_order"] for entry in enriched],
            ["CTX_MSG_001", "CURRENT_USER_MESSAGE", "REPLIED_TO_MESSAGE", "NON_CONTEXT_MESSAGE"],
        )
        self.assertEqual([entry["image_index"] for entry in enriched], [1, 2, 3, 4])
        self.assertNotIn("image_index", original[0])

    async def test_audio_keeps_supported_order_and_isolates_read_failures(self):
        response = SimpleNamespace(status=500, reason="Server Error")
        read_error = discord.HTTPException(response, "read failed")
        voice = self.make_attachment("voice.bin", None, data=b"voice")
        voice.is_voice_message = Mock(return_value=True)
        extension_audio = self.make_attachment("clip.M4A", None, data=b"clip")
        failed = self.make_attachment("failed.wav", "audio/wav", error=read_error)
        ignored = self.make_attachment("image.png", "image/png", data=b"ignored")
        replied_audio = self.make_attachment("reply.mpga", None, data=b"reply")
        replied_message = Mock(spec=discord.Message)
        replied_message.attachments = [replied_audio]
        message = self.make_message(
            10,
            [voice, extension_audio, failed, ignored],
            reference=SimpleNamespace(resolved=replied_message),
        )
        coordinator = self.make_coordinator()

        audio_files = await coordinator.extract_audio_from_message(message)

        self.assertEqual(
            [(item["filename"], item["mime_type"], item["data"]) for item in audio_files],
            [
                ("voice.bin", "audio/ogg", b"voice"),
                ("clip.M4A", "audio/mp4", b"clip"),
                ("reply.mpga", "audio/mpeg", b"reply"),
            ],
        )
        failed.read.assert_awaited_once()
        ignored.read.assert_not_awaited()

    async def test_text_files_preserve_decoding_size_type_and_reply_order(self):
        response = SimpleNamespace(status=500, reason="Server Error")
        read_error = discord.HTTPException(response, "download failed")
        utf8_file = self.make_attachment("notes.txt", "application/octet-stream", data=b"hello")
        latin1_file = self.make_attachment("legacy.txt", None, data=b"caf\xe9")
        typed_file = self.make_attachment("manifest", "application/json", data=b'{"ok": true}')
        oversized = self.make_attachment("large.txt", "text/plain", data=b"x", size=2048)
        image = self.make_attachment("skip.png", "image/png", data=b"image")
        pdf = self.make_attachment("skip.pdf", "application/pdf", data=b"pdf")
        unsupported = self.make_attachment("archive.bin", "application/octet-stream", data=b"bin")
        failed = self.make_attachment("failed.md", "text/markdown", error=read_error)
        reply_file = self.make_attachment("reply.csv", "text/csv", data=b"a,b")
        replied_message = Mock(spec=discord.Message)
        replied_message.attachments = [reply_file]
        message = self.make_message(
            10,
            [utf8_file, latin1_file, typed_file, oversized, image, pdf, unsupported, failed],
            reference=SimpleNamespace(resolved=replied_message),
        )
        coordinator = self.make_coordinator(get_max_text_file_size=lambda: 1024)

        files, unsupported_files = await coordinator.extract_files_from_message(message)

        self.assertEqual(
            [(item["name"], item["content"]) for item in files],
            [
                ("notes.txt", "hello"),
                ("legacy.txt", "café"),
                ("manifest", '{"ok": true}'),
                ("reply.csv", "a,b"),
            ],
        )
        self.assertEqual(
            unsupported_files,
            [
                "large.txt (too large: 0.0MB)",
                "archive.bin (unsupported type)",
                "failed.md (error: 500 Server Error (error code: 0): download failed)",
            ],
        )
        oversized.read.assert_not_awaited()
        image.read.assert_not_awaited()
        pdf.read.assert_not_awaited()
        unsupported.read.assert_not_awaited()

    async def test_discord_bot_media_private_wrapper_delegates(self):
        expected = ([{"name": "notes.txt"}], [])
        media = SimpleNamespace(
            extract_files_from_message=AsyncMock(return_value=expected),
        )
        bot = SimpleNamespace(_media_extraction=media)
        message = self.make_message(10, [])

        result = await DiscordBot._extract_files_from_message(bot, message)

        self.assertEqual(result, expected)
        media.extract_files_from_message.assert_awaited_once_with(message)


if __name__ == "__main__":
    unittest.main()
