"""Guard for DAB-029 container 5, item 5a — one of six, and only one.

`ImageProcessingService._completed_jobs` is a 100-entry ring swept hourly, so it
is not retained "forever" -- one source in the corpus says so and the shipped
code disagrees. The defect is what each entry *weighs*: an archived job keeps
the entire uploaded image in `request.image_data`, up to `max_image_size_mb`
each, for as long as it sits in the ring. Nothing reads it once the job is
terminal.

**This closes item 5a only.** DAB-029 names six never-evicting containers and
five of them are untouched: the per-channel live-mode state (1-2), the vector
cache (3), `_pregeneration_status` (4), this archive's byte cap (5b), and the
per-user rate-limit map (6). Do not read a green suite here as DAB-029 closed.

`result.edited_image` is deliberately still retained. The ticket puts it in the
same clause, but the command callback polls `get_job_status` and reads it to
send the image, so the service has no way to know it has been delivered -- which
`test_the_delivered_image_survives_archiving` pins, because dropping it is the
obvious next step and it would break `/edit-image` outright.
"""

import asyncio
import dataclasses
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.models.data_models import EditType, ImageEditRequest, ImageEditResult
from src.services.image_processing_service import (
    ImageProcessingService,
    ProcessingJob,
    ProcessingStatus,
)

PAYLOAD = b"\x89PNG" + b"x" * (1024 * 1024)


def make_job(job_id="j1"):
    request = ImageEditRequest(
        user_id="7",
        image_data=PAYLOAD,
        instruction="make it blue",
        edit_type=EditType.STYLE_TRANSFER,
        timestamp=datetime.now(),
        channel_id="4242",
    )
    return ProcessingJob(
        job_id=job_id,
        request=request,
        status=ProcessingStatus.COMPLETED,
        created_at=datetime.now(),
    )


class ImageJobArchiveTest(unittest.IsolatedAsyncioTestCase):
    """Drives the real `_process_job`, never a copy of its bookkeeping.

    The first draft of this file re-implemented the archive step in a helper and
    asserted against that. Both mutants -- archiving the payload intact, and
    dropping the delivered image as well -- survived it, because the helper was
    not the code. `ANALYSIS_CORRECTIONS.md` item 13, for the third time in this
    programme.
    """

    def _service(self, edited=b"the edited png"):
        return SimpleNamespace(
            _processing_semaphore=asyncio.Semaphore(1),
            _lock=asyncio.Lock(),
            _active_jobs={},
            _completed_jobs=[],
            _progress_callbacks={},
            _jobs={},
            _stats={
                "successful_requests": 0,
                "failed_requests": 0,
                "average_processing_time": 0.0,
            },
            _update_progress=AsyncMock(),
            client=SimpleNamespace(
                edit_image=AsyncMock(
                    return_value=SimpleNamespace(
                        success=True,
                        image_data=edited,
                        error_message=None,
                        processing_time=0.1,
                        metadata=None,
                        token_usage=None,
                    )
                )
            ),
            config=SimpleNamespace(max_image_size_mb=10),
        )

    async def _run(self, service, job):
        service._jobs[job.job_id] = job
        with patch(
            "src.services.image_processing_service.validate_image",
            return_value=SimpleNamespace(is_valid=True),
        ):
            await ImageProcessingService._process_job(service, job, "worker")

    async def test_an_archived_job_does_not_retain_the_uploaded_image(self):
        service = self._service()

        for index in range(5):
            await self._run(service, make_job(f"j{index}"))

        self.assertEqual(len(service._completed_jobs), 5)
        retained = sum(
            len(job.request.image_data) for job in service._completed_jobs
        )
        self.assertEqual(
            retained, 0,
            f"{retained} bytes of uploaded image survived in the archive",
        )

    async def test_the_delivered_image_survives_archiving(self):
        # The half that must NOT be dropped: `/edit-image` polls
        # `get_job_status` after completion and reads this to send the file.
        service = self._service()

        await self._run(service, make_job("j1"))

        found = await ImageProcessingService.get_job_status(service, "j1")
        self.assertIsNotNone(found)
        self.assertEqual(found.result.edited_image, b"the edited png")

    async def test_the_archived_job_is_still_reachable_by_id(self):
        service = self._service()
        job = make_job("wanted")

        await self._run(service, job)

        self.assertIs(
            await ImageProcessingService.get_job_status(service, "wanted"), job
        )

    async def test_stripping_the_payload_does_not_go_through_the_constructor(self):
        # The ticket prescribes
        # `job.request = dataclasses.replace(job.request, image_data=b"")`.
        # That re-runs `__post_init__`, which forbids empty image data, so it
        # raises inside `_process_job`'s own `finally` and takes the worker with
        # it -- measured, it broke an existing lifecycle test on the first
        # attempt. Pinned so the "cleaner" spelling is not reinstated.
        with self.assertRaises(ValueError):
            dataclasses.replace(make_job().request, image_data=b"")


if __name__ == "__main__":
    unittest.main()
