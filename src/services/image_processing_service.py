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
from dataclasses import dataclass
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
        
        # Initialize Gemini 2.5 Flash Image client (nano-banana)
        self.client = NanoBananaClient(
            api_key=config.nano_banana_api_key,
            timeout=config.image_processing_timeout,
            max_retries=3
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
        self._max_requests_per_user_per_hour = 10
        
        # Service state
        self._is_running = False
        self._worker_tasks: List[asyncio.Task] = []
        
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
            self._worker_tasks.append(task)
        
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
        
        # Wait for tasks to complete
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        
        self._worker_tasks.clear()
        
        # Cancel any remaining jobs
        for job in self._active_jobs.values():
            job.status = ProcessingStatus.CANCELLED
        
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
        
        # Store job and add to queue
        self._jobs[job_id] = job
        if progress_callback:
            self._progress_callbacks[job_id] = progress_callback
        
        await self._job_queue.put(job)
        
        # Update statistics
        self._stats['total_requests'] += 1
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
        return self._jobs.get(job_id)
    
    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a processing job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was cancelled, False if not found or already completed
        """
        job = self._jobs.get(job_id)
        if not job:
            return False
        
        if job.status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED]:
            return False
        
        job.status = ProcessingStatus.CANCELLED
        job.completed_at = datetime.now()
        
        # Remove from active jobs if present
        if job_id in self._active_jobs:
            del self._active_jobs[job_id]
        
        # Clean up progress callback
        if job_id in self._progress_callbacks:
            del self._progress_callbacks[job_id]
        
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
                
                # Process with Gemini 2.5 Flash Image (nano-banana)
                edit_response = await self.client.edit_image(
                    job.request.image_data,  # Send raw image data to Gemini
                    job.request.instruction,
                    job.request.edit_type
                )
                
                await self._update_progress(job_id, 0.9, "Finalizing...")
                
                # Create result
                job.result = ImageEditResult(
                    success=edit_response.success,
                    edited_image=edit_response.image_data,
                    processing_time=edit_response.processing_time,
                    error_message=edit_response.error_message,
                    metadata=edit_response.metadata
                )
                
                # Update job status
                job.status = ProcessingStatus.COMPLETED
                job.completed_at = datetime.now()
                
                await self._update_progress(job_id, 1.0, "Complete!")
                
                # Update statistics
                if job.result.success:
                    self._stats['successful_requests'] += 1
                else:
                    self._stats['failed_requests'] += 1
                
                # Update average processing time
                total_time = (job.completed_at - job.started_at).total_seconds()
                current_avg = self._stats['average_processing_time']
                total_completed = self._stats['successful_requests'] + self._stats['failed_requests']
                self._stats['average_processing_time'] = (current_avg * (total_completed - 1) + total_time) / total_completed
                
                logger.info(f"Completed job {job_id} in {total_time:.1f}s (success: {job.result.success})")
                
            except Exception as e:
                # Handle job failure
                job.status = ProcessingStatus.FAILED
                job.completed_at = datetime.now()
                job.error_message = str(e)
                
                job.result = ImageEditResult(
                    success=False,
                    error_message=str(e),
                    processing_time=(job.completed_at - job.started_at).total_seconds() if job.started_at else 0.0
                )
                
                self._stats['failed_requests'] += 1
                
                logger.error(f"Job {job_id} failed: {e}")
                
            finally:
                # Clean up
                if job_id in self._active_jobs:
                    del self._active_jobs[job_id]
                
                if job_id in self._progress_callbacks:
                    del self._progress_callbacks[job_id]
                
                # Move to completed jobs (keep last 100)
                self._completed_jobs.append(job)
                if len(self._completed_jobs) > 100:
                    old_job = self._completed_jobs.pop(0)
                    if old_job.job_id in self._jobs:
                        del self._jobs[old_job.job_id]
    
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
        job = self._jobs.get(job_id)
        if job:
            job.progress = progress
        
        # Call progress callback if registered
        callback = self._progress_callbacks.get(job_id)
        if callback:
            try:
                callback(job_id, progress)
            except Exception as e:
                logger.warning(f"Progress callback error for job {job_id}: {e}")
    
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
    
    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """
        Clean up old completed jobs to free memory.
        
        Args:
            max_age_hours: Maximum age of jobs to keep in hours
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        
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
        
        requests_used = len(user_requests)
        requests_remaining = max(0, self._max_requests_per_user_per_hour - requests_used)
        
        reset_time = None
        if user_requests:
            oldest_request = min(user_requests)
            reset_time = oldest_request + timedelta(hours=1)
        
        return {
            'requests_used': requests_used,
            'requests_remaining': requests_remaining,
            'reset_time': reset_time.isoformat() if reset_time else None
        }