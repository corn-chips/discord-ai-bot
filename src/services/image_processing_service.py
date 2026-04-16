"""
Image Processing Service for the Discord Grok Bot.

This module provides the main image processing service that orchestrates
image editing operations, manages queues, implements rate limiting,
and provides progress tracking for image editing requests.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import uuid

from ..config import BotConfig
from ..models.data_models import ImageEditRequest, ImageEditResult, EditType, ValidationResult
from ..utils.image_utils import validate_image, estimate_processing_time
from .nano_banana_client import NanoBananaClient, ServiceStatus

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    """Enumeration of processing status states."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ProcessingJob:
    """Represents an image processing job in the queue."""
    job_id: str
    request: ImageEditRequest
    status: ProcessingStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: float = 0.0
    estimated_time: float = 0.0
    result: Optional[ImageEditResult] = None
    error_message: Optional[str] = None
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)


class ImageProcessingService:
    """
    Main service for handling image processing operations.
    
    Manages image editing requests, implements rate limiting and queue management,
    provides progress tracking, and coordinates with the nano-banana API client.
    """
    
    def __init__(self, config: BotConfig):
        """
        Initialize the image processing service.
        
        Args:
            config: Bot configuration containing image processing settings
        """
        self.config = config
        
        # Initialize configured Gemini image client (nano-banana)
        self.client = NanoBananaClient(
            api_key=config.nano_banana_api_key,
            model_name=config.nano_banana_model,
            timeout=config.nano_banana_timeout,
            max_retries=config.nano_banana_max_retries,
            retry_delay=config.nano_banana_retry_delay,
            max_requests_per_minute=config.max_image_requests_per_minute,
        )
        
        # Job management
        self._jobs: Dict[str, ProcessingJob] = {}
        self._job_queue: asyncio.Queue = asyncio.Queue()
        self._active_jobs: Dict[str, ProcessingJob] = {}
        self._completed_jobs: List[ProcessingJob] = []
        
        # Rate limiting and concurrency
        self._max_concurrent_jobs = config.max_concurrent_image_edits
        self._processing_semaphore = asyncio.Semaphore(self._max_concurrent_jobs)
        self._user_rate_limits: Dict[str, List[datetime]] = {}
        self._max_requests_per_user_per_hour = config.max_requests_per_user_per_hour
        
        # Service state
        self._is_running = False
        self._worker_tasks: List[asyncio.Task] = []
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # Progress callbacks
        self._progress_callbacks: Dict[str, Callable[[str, float], None]] = {}
        
        # Statistics
        self._stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'average_processing_time': 0.0,
            'queue_size': 0,
            'active_jobs': 0
        }
    
    async def start(self):
        """Start the image processing service and worker tasks."""
        if self._is_running:
            logger.warning("Image processing service is already running")
            return
        
        logger.info("Starting image processing service")
        self._is_running = True
        
        # Start worker tasks
        for i in range(self._max_concurrent_jobs):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            task.add_done_callback(self._task_error_handler)
            self._worker_tasks.append(task)

        # Start periodic cleanup task
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        self._cleanup_task.add_done_callback(self._task_error_handler)
        
        # Check service health
        await self._check_service_health()
        
        logger.info(f"Image processing service started with {self._max_concurrent_jobs} workers")
    
    async def stop(self):
        """Stop the image processing service and clean up resources."""
        if not self._is_running:
            return
        
        logger.info("Stopping image processing service")
        self._is_running = False
        
        # Cancel all worker tasks
        for task in self._worker_tasks:
            task.cancel()

        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        # Wait for tasks to complete
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

        if self._cleanup_task:
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None
        
        self._worker_tasks.clear()
        
        # Cancel any remaining jobs
        async with self._lock:
            for job in self._active_jobs.values():
                job.status = ProcessingStatus.CANCELLED
                job.completed_at = datetime.now()
                job.completion_event.set()
        
        logger.info("Image processing service stopped")
    
    async def process_image_edit(self, request: ImageEditRequest, 
                               progress_callback: Optional[Callable[[str, float], None]] = None) -> str:
        """
        Submit an image editing request for processing.
        
        Args:
            request: Image edit request
            progress_callback: Optional callback for progress updates
            
        Returns:
            Job ID for tracking the request
            
        Raises:
            ValueError: If request validation fails
            RuntimeError: If service is not running or rate limited
        """
        if not self._is_running:
            raise RuntimeError("Image processing service is not running")
        
        # Validate request
        await self._validate_request(request)
        
        # Check rate limiting
        await self._check_user_rate_limit(request.user_id)
        
        # Create job
        job_id = str(uuid.uuid4())
        job = ProcessingJob(
            job_id=job_id,
            request=request,
            status=ProcessingStatus.QUEUED,
            created_at=datetime.now(),
            estimated_time=estimate_processing_time(request.image_data, request.edit_type.value)
        )
        
        # Store job and callback under lock before enqueueing
        async with self._lock:
            self._jobs[job_id] = job
            if progress_callback:
                self._progress_callbacks[job_id] = progress_callback
            self._stats['total_requests'] += 1

        await self._job_queue.put(job)

        async with self._lock:
            self._stats['queue_size'] = self._job_queue.qsize()
        
        logger.info(f"Queued image edit job {job_id} for user {request.user_id}")
        return job_id
    
    async def get_job_status(self, job_id: str) -> Optional[ProcessingJob]:
        """
        Get the status of a processing job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            ProcessingJob if found, None otherwise
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return job

            # Fallback for recently-completed jobs removed from _jobs
            for completed_job in reversed(self._completed_jobs):
                if completed_job.job_id == job_id:
                    return completed_job

        return None
    
    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a processing job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was cancelled, False if not found or already completed
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED]:
                return False

            job.status = ProcessingStatus.CANCELLED
            job.completed_at = datetime.now()
            job.completion_event.set()

            # Remove from active jobs if present
            if job_id in self._active_jobs:
                del self._active_jobs[job_id]

            # Clean up progress callback
            if job_id in self._progress_callbacks:
                del self._progress_callbacks[job_id]

            self._completed_jobs.append(job)
            if len(self._completed_jobs) > 100:
                self._completed_jobs.pop(0)

            # Remove terminal job from active lookup map
            if job_id in self._jobs:
                del self._jobs[job_id]

        logger.info(f"Cancelled job {job_id}")
        return True
    
    def get_queue_info(self) -> Dict[str, Any]:
        """
        Get information about the current processing queue.
        
        Returns:
            Dictionary with queue statistics
        """
        return {
            'queue_size': self._job_queue.qsize(),
            'active_jobs': len(self._active_jobs),
            'total_jobs': len(self._jobs),
            'completed_jobs': len(self._completed_jobs),
            'max_concurrent': self._max_concurrent_jobs,
            'service_available': self.client.is_service_available()
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get service statistics.
        
        Returns:
            Dictionary with processing statistics
        """
        self._stats['queue_size'] = self._job_queue.qsize()
        self._stats['active_jobs'] = len(self._active_jobs)
        return self._stats.copy()
    
    async def _worker_loop(self, worker_name: str):
        """
        Main worker loop for processing jobs.
        
        Args:
            worker_name: Name identifier for the worker
        """
        logger.info(f"Started image processing worker: {worker_name}")
        
        while self._is_running:
            try:
                # Wait for a job with timeout
                job = await asyncio.wait_for(self._job_queue.get(), timeout=1.0)
                
                if job.status == ProcessingStatus.CANCELLED:
                    continue
                
                # Process the job
                await self._process_job(job, worker_name)
                
            except asyncio.TimeoutError:
                # No job available, continue loop
                continue
            except Exception as e:
                logger.error(f"Error in worker {worker_name}: {e}")
                await asyncio.sleep(1.0)
        
        logger.info(f"Stopped image processing worker: {worker_name}")
    
    async def _process_job(self, job: ProcessingJob, worker_name: str):
        """
        Process a single image editing job.
        
        Args:
            job: Job to process
            worker_name: Name of the processing worker
        """
        async with self._processing_semaphore:
            job_id = job.job_id
            
            try:
                # Update job status
                async with self._lock:
                    job.status = ProcessingStatus.PROCESSING
                    job.started_at = datetime.now()
                    self._active_jobs[job_id] = job
                
                logger.info(f"Worker {worker_name} processing job {job_id}")
                
                # Update progress
                await self._update_progress(job_id, 0.1, "Validating image...")
                
                # Validate image
                validation = validate_image(job.request.image_data, self.config.max_image_size_mb)
                if not validation.is_valid:
                    raise ValueError(f"Image validation failed: {validation.error_message}")
                
                await self._update_progress(job_id, 0.3, "Preparing image for AI processing...")
                
                # Note: Gemini API handles image normalization and optimization internally
                # No need for manual preprocessing
                
                await self._update_progress(job_id, 0.4, "Sending to AI service...")
                
                # Process with configured Gemini image model (nano-banana)
                edit_response = await self.client.edit_image(
                    job.request.image_data,  # Send raw image data to Gemini
                    job.request.instruction,
                    job.request.edit_type
                )
                
                await self._update_progress(job_id, 0.9, "Finalizing...")
                
                # Create result
                metadata = dict(edit_response.metadata or {})
                if edit_response.token_usage:
                    metadata['token_usage'] = edit_response.token_usage

                job.result = ImageEditResult(
                    success=edit_response.success,
                    edited_image=edit_response.image_data,
                    processing_time=edit_response.processing_time,
                    error_message=edit_response.error_message,
                    metadata=metadata or None,
                    token_usage=edit_response.token_usage,
                )
                
                # Update job status
                now = datetime.now()
                job.status = ProcessingStatus.COMPLETED
                job.completed_at = now
                
                await self._update_progress(job_id, 1.0, "Complete!")
                
                # Update statistics
                total_time = (job.completed_at - job.started_at).total_seconds() if job.started_at else 0.0
                async with self._lock:
                    if job.result.success:
                        self._stats['successful_requests'] += 1
                    else:
                        self._stats['failed_requests'] += 1

                    current_avg = self._stats['average_processing_time']
                    total_completed = self._stats['successful_requests'] + self._stats['failed_requests']
                    self._stats['average_processing_time'] = (
                        (current_avg * (total_completed - 1) + total_time) / total_completed
                    ) if total_completed > 0 else 0.0
                
                logger.info(f"Completed job {job_id} in {total_time:.1f}s (success: {job.result.success})")
                
            except Exception as e:
                # Handle job failure
                now = datetime.now()
                job.status = ProcessingStatus.FAILED
                job.completed_at = now
                job.error_message = str(e)

                job.result = ImageEditResult(
                    success=False,
                    error_message=str(e),
                    processing_time=(job.completed_at - job.started_at).total_seconds() if job.started_at else 0.0,
                    metadata=None,
                    token_usage=None,
                )

                async with self._lock:
                    self._stats['failed_requests'] += 1
                
                logger.error(f"Job {job_id} failed: {e}")
                
            finally:
                # Signal completion and remove terminal jobs from active maps
                job.completion_event.set()

                async with self._lock:
                    if job_id in self._active_jobs:
                        del self._active_jobs[job_id]

                    if job_id in self._progress_callbacks:
                        del self._progress_callbacks[job_id]

                    self._completed_jobs.append(job)
                    if len(self._completed_jobs) > 100:
                        self._completed_jobs.pop(0)

                    if job.status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED]:
                        self._jobs.pop(job_id, None)
    
    async def _validate_request(self, request: ImageEditRequest):
        """
        Validate an image edit request.
        
        Args:
            request: Request to validate
            
        Raises:
            ValueError: If validation fails
        """
        # Validate image data
        validation = validate_image(request.image_data, self.config.max_image_size_mb)
        if not validation.is_valid:
            raise ValueError(f"Image validation failed: {validation.error_message}")
        
        # Validate instruction
        if not request.instruction.strip():
            raise ValueError("Edit instruction cannot be empty")
        
        if len(request.instruction) > 500:
            raise ValueError("Edit instruction is too long (max 500 characters)")
        
        # Check service availability
        if not self.client.is_service_available():
            service_status = await self.client.check_service_status()
            if service_status == ServiceStatus.UNAVAILABLE:
                raise RuntimeError("Image editing service is currently unavailable")
    
    async def _check_user_rate_limit(self, user_id: str):
        """
        Check and enforce user rate limiting.
        
        Args:
            user_id: User identifier
            
        Raises:
            RuntimeError: If user has exceeded rate limit
        """
        now = datetime.now()

        async with self._lock:
            # Initialize user rate limit tracking
            if user_id not in self._user_rate_limits:
                self._user_rate_limits[user_id] = []

            # Remove requests older than 1 hour
            user_requests = self._user_rate_limits[user_id]
            user_requests[:] = [
                req_time for req_time in user_requests
                if now - req_time < timedelta(hours=1)
            ]

            # Check rate limit
            if len(user_requests) >= self._max_requests_per_user_per_hour:
                oldest_request = min(user_requests)
                reset_time = oldest_request + timedelta(hours=1)
                raise RuntimeError(f"Rate limit exceeded. Try again after {reset_time.strftime('%H:%M:%S')}")

            # Record this request
            user_requests.append(now)
    
    async def _update_progress(self, job_id: str, progress: float, message: str = ""):
        """
        Update job progress and notify callback if registered.
        
        Args:
            job_id: Job identifier
            progress: Progress value (0.0 to 1.0)
            message: Progress message
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.progress = progress
            callback = self._progress_callbacks.get(job_id)

        if callback:
            try:
                callback(job_id, progress)
            except Exception as e:
                logger.warning(f"Progress callback error for job {job_id}: {e}")

    def _task_error_handler(self, task: asyncio.Task) -> None:
        """Log unhandled task exceptions from background tasks."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception as callback_exc:
            logger.error(f"Failed to inspect background task exception: {callback_exc}")
            return
        if exc:
            logger.error(
                f"Unhandled background task exception in image processing service: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def _periodic_cleanup(self):
        """Periodically clean up old completed jobs."""
        while self._is_running:
            try:
                await asyncio.sleep(3600)
                if not self._is_running:
                    break
                await self.cleanup_old_jobs(max_age_hours=1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic cleanup task failed: {e}", exc_info=True)
    
    async def _check_service_health(self):
        """Check the health of the nano-banana service."""
        try:
            status = await self.client.check_service_status()
            if status == ServiceStatus.UNAVAILABLE:
                logger.warning("Nano-banana service is unavailable")
            elif status == ServiceStatus.DEGRADED:
                logger.warning("Nano-banana service is degraded")
            else:
                logger.info("Nano-banana service is healthy")
        except Exception as e:
            logger.error(f"Failed to check service health: {e}")

    async def get_service_health(self) -> bool:
        """Return whether the underlying image service is healthy enough to serve requests."""
        try:
            status = await self.client.check_service_status()
            return status != ServiceStatus.UNAVAILABLE
        except Exception as e:
            logger.error(f"Failed to read service health: {e}")
            return False
    
    async def cleanup_old_jobs(self, max_age_hours: int = 24):
        """
        Clean up old completed jobs to free memory.
        
        Args:
            max_age_hours: Maximum age of jobs to keep in hours
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        
        async with self._lock:
            # Remove old jobs from main storage
            jobs_to_remove = []
            for job_id, job in self._jobs.items():
                if (job.completed_at and job.completed_at < cutoff_time and
                    job.status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED]):
                    jobs_to_remove.append(job_id)

            for job_id in jobs_to_remove:
                del self._jobs[job_id]

            # Remove old jobs from completed list
            self._completed_jobs[:] = [
                job for job in self._completed_jobs
                if not (job.completed_at and job.completed_at < cutoff_time)
            ]
        
        if jobs_to_remove:
            logger.info(f"Cleaned up {len(jobs_to_remove)} old jobs")
    
    async def get_user_rate_limit_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get rate limit information for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            Dictionary with rate limit information
        """
        now = datetime.now()

        async with self._lock:
            if user_id not in self._user_rate_limits:
                return {
                    'requests_used': 0,
                    'requests_remaining': self._max_requests_per_user_per_hour,
                    'reset_time': None
                }

            # Clean up old requests
            user_requests = self._user_rate_limits[user_id]
            user_requests[:] = [
                req_time for req_time in user_requests
                if now - req_time < timedelta(hours=1)
            ]

            if not user_requests:
                del self._user_rate_limits[user_id]
                return {
                    'requests_used': 0,
                    'requests_remaining': self._max_requests_per_user_per_hour,
                    'reset_time': None
                }

            requests_used = len(user_requests)
            requests_remaining = max(0, self._max_requests_per_user_per_hour - requests_used)

            oldest_request = min(user_requests)
            reset_time = oldest_request + timedelta(hours=1)

            return {
                'requests_used': requests_used,
                'requests_remaining': requests_remaining,
                'reset_time': reset_time.isoformat() if reset_time else None
            }
