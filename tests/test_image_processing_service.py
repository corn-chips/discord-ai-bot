"""
Unit tests for ImageProcessingService.

Tests the main image processing service including queue management, rate limiting,
and progress tracking according to requirements 1.3, 1.4, and 6.3.
"""

import unittest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timedelta
from src.config import BotConfig
from src.services.image_processing_service import (
    ImageProcessingService, ProcessingStatus, ProcessingJob
)
from src.models.data_models import ImageEditRequest, EditType, ImageEditResult


def run_async_test(coro):
    """Helper function to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestImageProcessingService(unittest.TestCase):
    """Test cases for ImageProcessingService."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = BotConfig(
            discord_token="test_token_12345678901234567890123456789012345678901234567890",
            gemini_api_key="test_gemini_key_1234567890123456789012345678901234567890",
            nano_banana_api_key="test_nano_key",
            max_image_size_mb=10,
            image_processing_timeout=60,
            max_concurrent_image_edits=2
        )
        
        # Create test image data
        import io
        from PIL import Image
        test_image = Image.new('RGB', (100, 100), color='red')
        output = io.BytesIO()
        test_image.save(output, format='PNG')
        self.test_image_data = output.getvalue()
        
        # Create test request
        self.test_request = ImageEditRequest(
            user_id="test_user_123",
            image_data=self.test_image_data,
            instruction="remove the person",
            edit_type=EditType.OBJECT_REMOVAL,
            timestamp=datetime.now(),
            channel_id="test_channel_456"
        )

    def test_service_initialization(self):
        """Test service initialization with configuration."""
        service = ImageProcessingService(self.config)
        
        self.assertEqual(service.config, self.config)
        self.assertIsNotNone(service.client)
        self.assertEqual(service._max_concurrent_jobs, 2)
        self.assertFalse(service._is_running)
        self.assertEqual(len(service._jobs), 0)

    def test_start_and_stop_service(self):
        """Test starting and stopping the service."""
        service = ImageProcessingService(self.config)
        
        # Mock the health check
        with patch.object(service, '_check_service_health', new_callable=AsyncMock):
            # Start service
            run_async_test(service.start())
            
            self.assertTrue(service._is_running)
            self.assertEqual(len(service._worker_tasks), 2)  # max_concurrent_jobs
            
            # Stop service
            run_async_test(service.stop())
            
            self.assertFalse(service._is_running)
            self.assertEqual(len(service._worker_tasks), 0)

    def test_process_image_edit_request(self):
        """Test submitting an image edit request."""
        service = ImageProcessingService(self.config)
        
        # Mock validation and rate limiting
        with patch.object(service, '_validate_request', new_callable=AsyncMock), \
             patch.object(service, '_check_user_rate_limit', new_callable=AsyncMock):
            
            job_id = run_async_test(service.process_image_edit(self.test_request))
        
        self.assertIsInstance(job_id, str)
        self.assertIn(job_id, service._jobs)
        
        job = service._jobs[job_id]
        self.assertEqual(job.request, self.test_request)
        self.assertEqual(job.status, ProcessingStatus.QUEUED)
        self.assertGreater(job.estimated_time, 0)

    def test_process_image_edit_with_progress_callback(self):
        """Test submitting request with progress callback."""
        service = ImageProcessingService(self.config)
        progress_updates = []
        
        def progress_callback(job_id, progress):
            progress_updates.append((job_id, progress))
        
        with patch.object(service, '_validate_request', new_callable=AsyncMock), \
             patch.object(service, '_check_user_rate_limit', new_callable=AsyncMock):
            
            job_id = run_async_test(
                service.process_image_edit(self.test_request, progress_callback)
            )
        
        self.assertIn(job_id, service._progress_callbacks)
        self.assertEqual(service._progress_callbacks[job_id], progress_callback)

    def test_service_not_running_error(self):
        """Test error when service is not running."""
        service = ImageProcessingService(self.config)
        
        with self.assertRaises(RuntimeError) as context:
            run_async_test(service.process_image_edit(self.test_request))
        
        self.assertIn("not running", str(context.exception))

    def test_get_job_status(self):
        """Test getting job status."""
        service = ImageProcessingService(self.config)
        
        with patch.object(service, '_validate_request', new_callable=AsyncMock), \
             patch.object(service, '_check_user_rate_limit', new_callable=AsyncMock):
            
            job_id = run_async_test(service.process_image_edit(self.test_request))
        
        job_status = run_async_test(service.get_job_status(job_id))
        
        self.assertIsNotNone(job_status)
        self.assertEqual(job_status.job_id, job_id)
        self.assertEqual(job_status.status, ProcessingStatus.QUEUED)

    def test_get_nonexistent_job_status(self):
        """Test getting status of nonexistent job."""
        service = ImageProcessingService(self.config)
        
        job_status = run_async_test(service.get_job_status("nonexistent_job"))
        
        self.assertIsNone(job_status)

    def test_cancel_job(self):
        """Test cancelling a job."""
        service = ImageProcessingService(self.config)
        
        with patch.object(service, '_validate_request', new_callable=AsyncMock), \
             patch.object(service, '_check_user_rate_limit', new_callable=AsyncMock):
            
            job_id = run_async_test(service.process_image_edit(self.test_request))
        
        # Cancel the job
        cancelled = run_async_test(service.cancel_job(job_id))
        
        self.assertTrue(cancelled)
        
        job = service._jobs[job_id]
        self.assertEqual(job.status, ProcessingStatus.CANCELLED)
        self.assertIsNotNone(job.completed_at)

    def test_cancel_nonexistent_job(self):
        """Test cancelling a nonexistent job."""
        service = ImageProcessingService(self.config)
        
        cancelled = run_async_test(service.cancel_job("nonexistent_job"))
        
        self.assertFalse(cancelled)

    def test_cancel_completed_job(self):
        """Test cancelling an already completed job."""
        service = ImageProcessingService(self.config)
        
        with patch.object(service, '_validate_request', new_callable=AsyncMock), \
             patch.object(service, '_check_user_rate_limit', new_callable=AsyncMock):
            
            job_id = run_async_test(service.process_image_edit(self.test_request))
        
        # Mark job as completed
        job = service._jobs[job_id]
        job.status = ProcessingStatus.COMPLETED
        
        # Try to cancel
        cancelled = run_async_test(service.cancel_job(job_id))
        
        self.assertFalse(cancelled)

    def test_get_queue_info(self):
        """Test getting queue information."""
        service = ImageProcessingService(self.config)
        
        queue_info = service.get_queue_info()
        
        self.assertIsInstance(queue_info, dict)
        self.assertIn('queue_size', queue_info)
        self.assertIn('active_jobs', queue_info)
        self.assertIn('total_jobs', queue_info)
        self.assertIn('completed_jobs', queue_info)
        self.assertIn('max_concurrent', queue_info)
        self.assertIn('service_available', queue_info)

    def test_get_statistics(self):
        """Test getting service statistics."""
        service = ImageProcessingService(self.config)
        
        stats = service.get_statistics()
        
        self.assertIsInstance(stats, dict)
        self.assertIn('total_requests', stats)
        self.assertIn('successful_requests', stats)
        self.assertIn('failed_requests', stats)
        self.assertIn('average_processing_time', stats)
        self.assertIn('queue_size', stats)
        self.assertIn('active_jobs', stats)

    def test_validate_request_success(self):
        """Test successful request validation."""
        service = ImageProcessingService(self.config)
        
        # Mock image validation and service availability
        with patch('src.utils.image_utils.validate_image') as mock_validate, \
             patch.object(service.client, 'is_service_available', return_value=True):
            
            from src.models.data_models import ValidationResult
            mock_validate.return_value = ValidationResult(is_valid=True)
            
            # Should not raise any exception
            run_async_test(service._validate_request(self.test_request))

    def test_validate_request_invalid_image(self):
        """Test request validation with invalid image."""
        service = ImageProcessingService(self.config)
        
        with patch('src.utils.image_utils.validate_image') as mock_validate:
            from src.models.data_models import ValidationResult
            mock_validate.return_value = ValidationResult(
                is_valid=False,
                error_message="Image too large"
            )
            
            with self.assertRaises(ValueError) as context:
                run_async_test(service._validate_request(self.test_request))
            
            self.assertIn("Image validation failed", str(context.exception))

    def test_validate_request_empty_instruction(self):
        """Test request validation with empty instruction."""
        service = ImageProcessingService(self.config)
        
        # Create request with empty instruction
        invalid_request = ImageEditRequest(
            user_id="test_user",
            image_data=self.test_image_data,
            instruction="",
            edit_type=EditType.OBJECT_REMOVAL,
            timestamp=datetime.now(),
            channel_id="test_channel"
        )
        
        with self.assertRaises(ValueError) as context:
            run_async_test(service._validate_request(invalid_request))
        
        self.assertIn("instruction cannot be empty", str(context.exception))

    def test_validate_request_long_instruction(self):
        """Test request validation with overly long instruction."""
        service = ImageProcessingService(self.config)
        
        # Create request with very long instruction
        long_instruction = "x" * 501  # Over 500 character limit
        invalid_request = ImageEditRequest(
            user_id="test_user",
            image_data=self.test_image_data,
            instruction=long_instruction,
            edit_type=EditType.OBJECT_REMOVAL,
            timestamp=datetime.now(),
            channel_id="test_channel"
        )
        
        with self.assertRaises(ValueError) as context:
            run_async_test(service._validate_request(invalid_request))
        
        self.assertIn("instruction is too long", str(context.exception))

    def test_validate_request_service_unavailable(self):
        """Test request validation when service is unavailable."""
        service = ImageProcessingService(self.config)
        
        with patch('src.utils.image_utils.validate_image') as mock_validate, \
             patch.object(service.client, 'is_service_available', return_value=False), \
             patch.object(service.client, 'check_service_status', new_callable=AsyncMock) as mock_check:
            
            from src.models.data_models import ValidationResult
            from src.services.nano_banana_client import ServiceStatus
            
            mock_validate.return_value = ValidationResult(is_valid=True)
            mock_check.return_value = ServiceStatus.UNAVAILABLE
            
            with self.assertRaises(RuntimeError) as context:
                run_async_test(service._validate_request(self.test_request))
            
            self.assertIn("service is currently unavailable", str(context.exception))

    def test_user_rate_limiting(self):
        """Test user rate limiting functionality."""
        service = ImageProcessingService(self.config)
        user_id = "test_user"
        
        # Fill up the rate limit
        for _ in range(service._max_requests_per_user_per_hour):
            run_async_test(service._check_user_rate_limit(user_id))
        
        # Next request should be rate limited
        with self.assertRaises(RuntimeError) as context:
            run_async_test(service._check_user_rate_limit(user_id))
        
        self.assertIn("Rate limit exceeded", str(context.exception))

    def test_get_user_rate_limit_info(self):
        """Test getting user rate limit information."""
        service = ImageProcessingService(self.config)
        user_id = "test_user"
        
        # Initially no requests
        info = run_async_test(service.get_user_rate_limit_info(user_id))
        
        self.assertEqual(info['requests_used'], 0)
        self.assertEqual(info['requests_remaining'], service._max_requests_per_user_per_hour)
        self.assertIsNone(info['reset_time'])
        
        # Make a request
        run_async_test(service._check_user_rate_limit(user_id))
        
        # Check updated info
        info = run_async_test(service.get_user_rate_limit_info(user_id))
        
        self.assertEqual(info['requests_used'], 1)
        self.assertEqual(info['requests_remaining'], service._max_requests_per_user_per_hour - 1)
        self.assertIsNotNone(info['reset_time'])

    def test_cleanup_old_jobs(self):
        """Test cleanup of old completed jobs."""
        service = ImageProcessingService(self.config)
        
        # Create old completed job
        old_job = ProcessingJob(
            job_id="old_job",
            request=self.test_request,
            status=ProcessingStatus.COMPLETED,
            created_at=datetime.now() - timedelta(hours=25),  # Older than 24 hours
            completed_at=datetime.now() - timedelta(hours=25)
        )
        
        # Create recent job
        recent_job = ProcessingJob(
            job_id="recent_job",
            request=self.test_request,
            status=ProcessingStatus.COMPLETED,
            created_at=datetime.now() - timedelta(hours=1),
            completed_at=datetime.now() - timedelta(hours=1)
        )
        
        service._jobs["old_job"] = old_job
        service._jobs["recent_job"] = recent_job
        service._completed_jobs = [old_job, recent_job]
        
        # Cleanup with 24 hour limit
        service.cleanup_old_jobs(max_age_hours=24)
        
        # Old job should be removed, recent job should remain
        self.assertNotIn("old_job", service._jobs)
        self.assertIn("recent_job", service._jobs)
        self.assertEqual(len(service._completed_jobs), 1)
        self.assertEqual(service._completed_jobs[0].job_id, "recent_job")

    def test_progress_update(self):
        """Test progress update functionality."""
        service = ImageProcessingService(self.config)
        progress_updates = []
        
        def progress_callback(job_id, progress):
            progress_updates.append((job_id, progress))
        
        # Create a job with progress callback
        job_id = "test_job"
        job = ProcessingJob(
            job_id=job_id,
            request=self.test_request,
            status=ProcessingStatus.PROCESSING,
            created_at=datetime.now()
        )
        
        service._jobs[job_id] = job
        service._progress_callbacks[job_id] = progress_callback
        
        # Update progress
        run_async_test(service._update_progress(job_id, 0.5, "Processing..."))
        
        # Check that progress was updated
        self.assertEqual(job.progress, 0.5)
        self.assertEqual(len(progress_updates), 1)
        self.assertEqual(progress_updates[0], (job_id, 0.5))

    def test_progress_update_callback_error(self):
        """Test progress update with callback error."""
        service = ImageProcessingService(self.config)
        
        def failing_callback(job_id, progress):
            raise Exception("Callback error")
        
        job_id = "test_job"
        job = ProcessingJob(
            job_id=job_id,
            request=self.test_request,
            status=ProcessingStatus.PROCESSING,
            created_at=datetime.now()
        )
        
        service._jobs[job_id] = job
        service._progress_callbacks[job_id] = failing_callback
        
        # Should not raise exception even if callback fails
        run_async_test(service._update_progress(job_id, 0.5))
        
        # Progress should still be updated
        self.assertEqual(job.progress, 0.5)


if __name__ == '__main__':
    unittest.main()