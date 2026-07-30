import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fitz

from src.bot.discord_bot import DiscordBot
from src.constants import MAX_PDF_PAGE_PIXELS


class PdfProcessingTest(unittest.IsolatedAsyncioTestCase):
    def _make_bot(self, *, max_pdf_pages=5, pdf_render_scale=1):
        bot = object.__new__(DiscordBot)
        bot.config = SimpleNamespace(
            max_pdf_pages=max_pdf_pages,
            pdf_render_scale=pdf_render_scale,
        )
        bot._pdf_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="test-pdf",
        )
        self.addCleanup(
            bot._pdf_executor.shutdown,
            wait=True,
            cancel_futures=True,
        )
        return bot

    async def _wait_for_event(self, event, timeout=1):
        await self._wait_for_condition(
            event.is_set,
            timeout=timeout,
            failure_message="Timed out waiting for PDF worker event",
        )

    async def _wait_for_condition(self, predicate, *, timeout=1, failure_message):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                self.fail(failure_message)
            await asyncio.sleep(0.001)

    @staticmethod
    def _make_pdf(page_count=1):
        with fitz.open() as document:
            for page_number in range(page_count):
                page = document.new_page(width=20, height=10)
                color = (1, 0, 0) if page_number == 0 else (0, 0, 1)
                page.draw_rect(page.rect, color=color, fill=color)
            return document.tobytes()

    async def test_rasterization_and_pillow_conversion_run_in_worker_thread(self):
        bot = self._make_bot()
        event_loop_thread_id = threading.get_ident()
        conversion_thread_ids = []
        original_convert = bot._convert_image_to_rgb

        def record_conversion_thread(image):
            conversion_thread_ids.append(threading.get_ident())
            return original_convert(image)

        bot._convert_image_to_rgb = record_conversion_thread

        images = await bot._convert_pdf_to_images(
            self._make_pdf(),
            "threaded.pdf",
        )
        self.addCleanup(lambda: [image.close() for image in images])

        self.assertEqual(len(images), 1)
        self.assertEqual(len(conversion_thread_ids), 1)
        self.assertNotEqual(conversion_thread_ids[0], event_loop_thread_id)

    async def test_page_limit_and_images_remain_usable_after_worker_cleanup(self):
        bot = self._make_bot(max_pdf_pages=1, pdf_render_scale=2)

        images = await bot._convert_pdf_to_images(
            self._make_pdf(page_count=2),
            "limited.pdf",
        )
        self.addCleanup(lambda: [image.close() for image in images])

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mode, "RGB")
        self.assertEqual(images[0].size, (40, 20))
        self.assertEqual(images[0].getpixel((20, 10)), (255, 0, 0))

    async def test_invalid_pdf_preserves_empty_result_error_behavior(self):
        bot = self._make_bot()

        with self.assertLogs("src.bot.discord_bot", level="ERROR") as logs:
            images = await bot._convert_pdf_to_images(
                b"not a pdf",
                "invalid.pdf",
            )

        self.assertEqual(images, [])
        self.assertTrue(
            any("Failed to convert PDF invalid.pdf" in message for message in logs.output)
        )

    async def test_concurrent_conversions_never_overlap(self):
        bot = self._make_bot()
        state_lock = threading.Lock()
        first_started = threading.Event()
        release_first = threading.Event()
        state = {"active": 0, "max_active": 0, "calls": 0}

        def controlled_conversion(_pdf_bytes, filename):
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                call_number = state["calls"]
            try:
                if call_number == 1:
                    first_started.set()
                    release_first.wait(2)
                return [filename]
            finally:
                with state_lock:
                    state["active"] -= 1

        bot._convert_pdf_to_images_sync = controlled_conversion
        first_task = asyncio.create_task(
            bot._convert_pdf_to_images(b"first", "first.pdf")
        )
        second_task = None
        try:
            await self._wait_for_event(first_started)
            second_task = asyncio.create_task(
                bot._convert_pdf_to_images(b"second", "second.pdf")
            )
            await asyncio.sleep(0.05)
            with state_lock:
                calls_before_release = state["calls"]
                max_active_before_release = state["max_active"]
        finally:
            release_first.set()

        self.assertIsNotNone(second_task)
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=1,
        )

        self.assertEqual(calls_before_release, 1)
        self.assertEqual(max_active_before_release, 1)
        self.assertEqual(results, [["first.pdf"], ["second.pdf"]])
        self.assertEqual(state["max_active"], 1)

    async def test_cancel_then_restart_waits_for_cancelled_worker(self):
        bot = self._make_bot()
        state_lock = threading.Lock()
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        state = {"active": 0, "max_active": 0}

        def controlled_conversion(_pdf_bytes, filename):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                if filename == "first.pdf":
                    first_started.set()
                    release_first.wait(2)
                else:
                    second_started.set()
                return [filename]
            finally:
                with state_lock:
                    state["active"] -= 1

        bot._convert_pdf_to_images_sync = controlled_conversion
        first_task = asyncio.create_task(
            bot._convert_pdf_to_images(b"first", "first.pdf")
        )
        second_task = None
        try:
            await self._wait_for_event(first_started)
            first_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first_task

            second_task = asyncio.create_task(
                bot._convert_pdf_to_images(b"second", "second.pdf")
            )
            await asyncio.sleep(0.05)
            second_started_before_release = second_started.is_set()
        finally:
            release_first.set()

        self.assertIsNotNone(second_task)
        result = await asyncio.wait_for(second_task, timeout=1)

        self.assertFalse(second_started_before_release)
        self.assertEqual(result, ["second.pdf"])
        self.assertEqual(state["max_active"], 1)

    async def test_close_drains_and_shuts_down_pdf_executor(self):
        bot = self._make_bot()
        executor = bot._pdf_executor
        worker_started = threading.Event()
        release_worker = threading.Event()

        def controlled_conversion(_pdf_bytes, filename):
            worker_started.set()
            release_worker.wait(2)
            return [filename]

        bot._convert_pdf_to_images_sync = controlled_conversion
        bot.report_web_server = None
        bot.image_processing_service = None
        bot.user_experience_service = None
        bot._live_channel_tasks = {}

        conversion_task = asyncio.create_task(
            bot._convert_pdf_to_images(b"pdf", "closing.pdf")
        )
        await self._wait_for_event(worker_started)

        with patch(
            "src.bot.discord_bot.discord.Client.close",
            new_callable=AsyncMock,
        ) as parent_close:
            close_task = asyncio.create_task(bot.close())
            await self._wait_for_condition(
                lambda: bot._pdf_executor is None,
                failure_message="Timed out waiting for PDF executor shutdown",
            )

            self.assertFalse(close_task.done())
            release_worker.set()
            result = await asyncio.wait_for(conversion_task, timeout=1)
            await asyncio.wait_for(close_task, timeout=1)

        self.assertEqual(result, ["closing.pdf"])
        parent_close.assert_awaited_once_with()
        with self.assertRaisesRegex(RuntimeError, "cannot schedule new futures"):
            executor.submit(lambda: None)
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            await bot._convert_pdf_to_images(b"pdf", "after-close.pdf")


class PdfRenderScaleClampTest(unittest.TestCase):
    """DAB-198: page geometry comes from the upload, so render size is its choice.

    A 520-byte PDF declaring an 8000x8000 pt page rasterises at the shipped
    scale of 2.0 to 16000x16000 = 256 MP. Measured before the clamp: MuPDF
    allocated 792 MB and succeeded, the PNG encode peaked at 1,525 MB, and only
    Pillow's decompression-bomb check then refused it -- after roughly 5 s of
    CPU, returning zero images for the trouble.

    That ordering is why the clamp had to land WITH the DAB-197 speedup rather
    than after it: deleting the PNG round-trip also deletes the Pillow check,
    which was the only thing stopping the allocation.
    """

    @staticmethod
    def _bot(scale=2.0):
        bot = object.__new__(DiscordBot)
        bot.config = SimpleNamespace(max_pdf_pages=20, pdf_render_scale=scale)
        return bot

    @staticmethod
    def _pdf_with_page(width, height):
        with fitz.open() as document:
            document.new_page(width=width, height=height)
            return document.tobytes()

    def test_an_ordinary_page_renders_at_the_configured_scale(self):
        bot = self._bot(scale=2.0)
        with fitz.open(stream=self._pdf_with_page(595, 842), filetype="pdf") as document:
            self.assertEqual(bot._pdf_render_scale_for_page(document[0]), 2.0)

    def test_an_enormous_page_is_scaled_down_rather_than_rendered_whole(self):
        bot = self._bot(scale=2.0)
        with fitz.open(stream=self._pdf_with_page(8000, 8000), filetype="pdf") as document:
            page = document[0]
            scale = bot._pdf_render_scale_for_page(page)

            self.assertLess(scale, 2.0, "an oversized page was not clamped")
            pixels = (page.rect.width * scale) * (page.rect.height * scale)
            self.assertLessEqual(pixels, MAX_PDF_PAGE_PIXELS)

    def test_the_resource_bomb_now_yields_a_usable_image(self):
        # Not merely "does not explode": it must actually return something.
        # Before the clamp this path burned seconds and produced nothing at all.
        bot = self._bot(scale=2.0)
        images = bot._convert_pdf_to_images_sync(
            self._pdf_with_page(8000, 8000), "bomb.pdf"
        )

        self.assertEqual(len(images), 1)
        width, height = images[0].size
        self.assertLessEqual(width * height, MAX_PDF_PAGE_PIXELS)
        self.assertEqual(images[0].mode, "RGB")

    def test_the_pixel_ceiling_stays_below_pillows_own_bomb_threshold(self):
        # If this ceiling ever rises past Pillow's default, Pillow becomes the
        # thing that refuses again -- after the allocation has been paid for.
        from PIL import Image

        self.assertIsNotNone(Image.MAX_IMAGE_PIXELS)
        self.assertLess(MAX_PDF_PAGE_PIXELS, Image.MAX_IMAGE_PIXELS)


class PdfPixmapConversionTest(unittest.TestCase):
    """DAB-197: the PNG encode/decode round-trip was pure waste."""

    @staticmethod
    def _bot():
        bot = object.__new__(DiscordBot)
        bot.config = SimpleNamespace(max_pdf_pages=20, pdf_render_scale=2.0)
        return bot

    @staticmethod
    def _text_pdf(pages=3):
        with fitz.open() as document:
            for index in range(pages):
                page = document.new_page(width=595, height=842)
                page.insert_text((72, 100), f"Page {index}", fontsize=14)
            return document.tobytes()

    def test_direct_conversion_is_pixel_identical_to_the_png_round_trip(self):
        import io as _io

        from PIL import Image

        bot = self._bot()
        pdf = self._text_pdf(pages=3)

        direct = bot._convert_pdf_to_images_sync(pdf, "doc.pdf")

        round_trip = []
        with fitz.open(stream=pdf, filetype="pdf") as document:
            for index in range(len(document)):
                pixmap = document[index].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                image = Image.open(_io.BytesIO(pixmap.tobytes("png")))
                image.load()
                round_trip.append(bot._convert_image_to_rgb(image))

        self.assertEqual(len(direct), len(round_trip))
        for index, (new, old) in enumerate(zip(direct, round_trip)):
            with self.subTest(page=index):
                self.assertEqual(new.size, old.size)
                self.assertEqual(new.tobytes(), old.tobytes())

    def test_the_png_round_trip_is_not_reintroduced(self):
        # The equivalence test above passes either way, so this is the assertion
        # that actually fails if someone puts the encode/decode back.
        bot = self._bot()
        calls = []
        original = fitz.Pixmap.tobytes

        def counting_tobytes(pixmap, *args, **kwargs):
            calls.append(args)
            return original(pixmap, *args, **kwargs)

        with patch.object(fitz.Pixmap, "tobytes", counting_tobytes):
            bot._convert_pdf_to_images_sync(self._text_pdf(pages=2), "doc.pdf")

        self.assertEqual(calls, [], "pixmap was PNG-encoded during conversion again")


if __name__ == "__main__":
    unittest.main()
