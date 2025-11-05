"""
Logging configuration for the Discord Grok Bot.

This module provides structured logging setup with appropriate levels,
formatters, and handlers for different environments.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that provides structured logging output.
    
    Formats log records with consistent structure including timestamp,
    level, module, and message with optional exception information.
    """
    
    def __init__(self, include_extra_fields: bool = True):
        """
        Initialize the structured formatter.
        
        Args:
            include_extra_fields: Whether to include extra context fields
        """
        self.include_extra_fields = include_extra_fields
        
        # Define format string
        format_string = (
            "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
        )
        
        super().__init__(
            fmt=format_string,
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record with structured information.
        
        Args:
            record: The log record to format
            
        Returns:
            Formatted log string
        """
        # Add extra context if available
        if self.include_extra_fields:
            extra_fields = []
            
            # Add user context if available
            if hasattr(record, 'user_id'):
                extra_fields.append(f"user_id={record.user_id}")
            if hasattr(record, 'guild_id'):
                extra_fields.append(f"guild_id={record.guild_id}")
            if hasattr(record, 'channel_id'):
                extra_fields.append(f"channel_id={record.channel_id}")
            if hasattr(record, 'message_id'):
                extra_fields.append(f"message_id={record.message_id}")
            
            # Add performance context if available
            if hasattr(record, 'duration'):
                extra_fields.append(f"duration={record.duration:.3f}s")
            if hasattr(record, 'api_calls'):
                extra_fields.append(f"api_calls={record.api_calls}")
            
            # Add image processing context if available
            if hasattr(record, 'image_size'):
                extra_fields.append(f"image_size={record.image_size}")
            if hasattr(record, 'edit_type'):
                extra_fields.append(f"edit_type={record.edit_type}")
            if hasattr(record, 'operation_type'):
                extra_fields.append(f"operation={record.operation_type}")
            
            # Add message formatting context if available
            if hasattr(record, 'original_length'):
                extra_fields.append(f"original_length={record.original_length}")
            if hasattr(record, 'split_count'):
                extra_fields.append(f"split_count={record.split_count}")
            if hasattr(record, 'markdown_blocks'):
                extra_fields.append(f"markdown_blocks={record.markdown_blocks}")
            
            # Add error context if available
            if hasattr(record, 'error_type'):
                extra_fields.append(f"error_type={record.error_type}")
            if hasattr(record, 'retry_count'):
                extra_fields.append(f"retry_count={record.retry_count}")
            if hasattr(record, 'action'):
                extra_fields.append(f"action={record.action}")
            if hasattr(record, 'success'):
                extra_fields.append(f"success={record.success}")
            
            if extra_fields:
                record.msg = f"{record.msg} [{', '.join(extra_fields)}]"
        
        return super().format(record)


class PerformanceLogger:
    """
    Logger for tracking performance metrics and timing information.
    """
    
    def __init__(self, logger_name: str = "performance"):
        """
        Initialize the performance logger.
        
        Args:
            logger_name: Name of the logger to use
        """
        self.logger = logging.getLogger(logger_name)
        self._stats = {
            'message_count': 0,
            'api_calls': 0,
            'api_failures': 0,
            'total_response_time': 0.0,
            'total_api_time': 0.0,
            'total_tokens': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'image_processing_count': 0,
            'total_image_processing_time': 0.0,
            'image_processing_failures': 0,
            'message_splits_count': 0,
            'total_message_split_time': 0.0,
            'message_split_failures': 0
        }
    
    def log_api_call(
        self, 
        api_name: str, 
        duration: float, 
        success: bool, 
        error_type: Optional[str] = None
    ) -> None:
        """
        Log an API call with performance metrics.
        
        Args:
            api_name: Name of the API called
            duration: Duration of the call in seconds
            success: Whether the call was successful
            error_type: Type of error if call failed
        """
        extra = {
            'api_name': api_name,
            'duration': duration,
            'success': success
        }
        
        if error_type:
            extra['error_type'] = error_type
        
        if success:
            self.logger.info(f"API call to {api_name} completed", extra=extra)
        else:
            self.logger.warning(f"API call to {api_name} failed", extra=extra)
            self._stats['api_failures'] += 1
        
        # Update statistics
        self._stats['api_calls'] += 1
        self._stats['total_api_time'] += duration
    
    def log_message_processing(
        self, 
        duration: float, 
        context_messages: int, 
        response_length: int,
        user_id: Optional[int] = None,
        guild_id: Optional[int] = None
    ) -> None:
        """
        Log message processing performance.
        
        Args:
            duration: Total processing duration in seconds
            context_messages: Number of context messages processed
            response_length: Length of generated response
            user_id: Optional user ID
            guild_id: Optional guild ID
        """
        extra = {
            'duration': duration,
            'context_messages': context_messages,
            'response_length': response_length
        }
        
        if user_id:
            extra['user_id'] = user_id
        if guild_id:
            extra['guild_id'] = guild_id
        
        self.logger.info("Message processing completed", extra=extra)
        
        # Update statistics
        self._stats['message_count'] += 1
        self._stats['total_response_time'] += duration
        
        # Estimate tokens (rough approximation: 1 token ≈ 4 characters)
        estimated_input = context_messages * 50  # Avg 50 tokens per context message
        estimated_output = response_length // 4
        self._stats['input_tokens'] += estimated_input
        self._stats['output_tokens'] += estimated_output
        self._stats['total_tokens'] += estimated_input + estimated_output
    
    def get_summary_stats(self) -> dict:
        """
        Get summary statistics.
        
        Returns:
            Dictionary containing performance statistics
        """
        stats = dict(self._stats)
        
        # Calculate averages
        if stats['message_count'] > 0:
            stats['avg_response_time'] = stats['total_response_time'] / stats['message_count']
            stats['success_rate'] = ((stats['message_count'] - stats['api_failures']) / stats['message_count']) * 100
        else:
            stats['avg_response_time'] = 0.0
            stats['success_rate'] = 100.0
        
        if stats['api_calls'] > 0:
            stats['avg_api_time'] = stats['total_api_time'] / stats['api_calls']
        else:
            stats['avg_api_time'] = 0.0
        
        # Calculate image processing averages
        if stats['image_processing_count'] > 0:
            stats['avg_image_processing_time'] = stats['total_image_processing_time'] / stats['image_processing_count']
            stats['image_processing_success_rate'] = ((stats['image_processing_count'] - stats['image_processing_failures']) / stats['image_processing_count']) * 100
        else:
            stats['avg_image_processing_time'] = 0.0
            stats['image_processing_success_rate'] = 100.0
        
        # Calculate message splitting averages
        if stats['message_splits_count'] > 0:
            stats['avg_message_split_time'] = stats['total_message_split_time'] / stats['message_splits_count']
            stats['message_split_success_rate'] = ((stats['message_splits_count'] - stats['message_split_failures']) / stats['message_splits_count']) * 100
        else:
            stats['avg_message_split_time'] = 0.0
            stats['message_split_success_rate'] = 100.0
        
        return stats
    
    def log_image_processing(
        self,
        duration: float,
        success: bool,
        image_size: Optional[int] = None,
        edit_type: Optional[str] = None,
        user_id: Optional[int] = None,
        error_type: Optional[str] = None
    ) -> None:
        """
        Log image processing performance and outcomes.
        
        Args:
            duration: Processing duration in seconds
            success: Whether processing was successful
            image_size: Optional size of processed image in bytes
            edit_type: Optional type of edit performed
            user_id: Optional user ID
            error_type: Optional error type if processing failed
        """
        extra = {
            'duration': duration,
            'success': success,
            'operation_type': 'image_processing'
        }
        
        if image_size:
            extra['image_size'] = image_size
        if edit_type:
            extra['edit_type'] = edit_type
        if user_id:
            extra['user_id'] = user_id
        if error_type:
            extra['error_type'] = error_type
        
        if success:
            self.logger.info("Image processing completed", extra=extra)
        else:
            self.logger.warning("Image processing failed", extra=extra)
            self._stats['image_processing_failures'] += 1
        
        # Update statistics
        self._stats['image_processing_count'] += 1
        self._stats['total_image_processing_time'] += duration
    
    def log_message_splitting(
        self,
        duration: float,
        success: bool,
        original_length: int,
        split_count: int,
        markdown_blocks: Optional[int] = None,
        user_id: Optional[int] = None,
        error_type: Optional[str] = None
    ) -> None:
        """
        Log message splitting performance and outcomes.
        
        Args:
            duration: Splitting duration in seconds
            success: Whether splitting was successful
            original_length: Length of original message
            split_count: Number of message parts created
            markdown_blocks: Optional number of markdown blocks processed
            user_id: Optional user ID
            error_type: Optional error type if splitting failed
        """
        extra = {
            'duration': duration,
            'success': success,
            'original_length': original_length,
            'split_count': split_count,
            'operation_type': 'message_splitting'
        }
        
        if markdown_blocks:
            extra['markdown_blocks'] = markdown_blocks
        if user_id:
            extra['user_id'] = user_id
        if error_type:
            extra['error_type'] = error_type
        
        if success:
            self.logger.info("Message splitting completed", extra=extra)
        else:
            self.logger.warning("Message splitting failed", extra=extra)
            self._stats['message_split_failures'] += 1
        
        # Update statistics
        self._stats['message_splits_count'] += 1
        self._stats['total_message_split_time'] += duration
    
    def log_user_action(
        self,
        user_id: int,
        action: str,
        duration: Optional[float] = None,
        success: bool = True,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        additional_context: Optional[dict] = None
    ) -> None:
        """
        Log user actions for debugging and monitoring.
        
        Args:
            user_id: ID of the user performing the action
            action: Type of action performed
            duration: Optional duration of the action
            success: Whether the action was successful
            guild_id: Optional guild ID where action occurred
            channel_id: Optional channel ID where action occurred
            additional_context: Optional additional context information
        """
        extra = {
            'user_id': user_id,
            'action': action,
            'success': success,
            'operation_type': 'user_action'
        }
        
        if duration:
            extra['duration'] = duration
        if guild_id:
            extra['guild_id'] = guild_id
        if channel_id:
            extra['channel_id'] = channel_id
        if additional_context:
            extra.update(additional_context)
        
        log_level = logging.INFO if success else logging.WARNING
        message = f"User action: {action}"
        
        self.logger.log(log_level, message, extra=extra)
    
    def clear_cache(self) -> None:
        """Clear all cached statistics."""
        self._stats = {
            'message_count': 0,
            'api_calls': 0,
            'api_failures': 0,
            'total_response_time': 0.0,
            'total_api_time': 0.0,
            'total_tokens': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'image_processing_count': 0,
            'total_image_processing_time': 0.0,
            'image_processing_failures': 0,
            'message_splits_count': 0,
            'total_message_split_time': 0.0,
            'message_split_failures': 0
        }
        self.logger.info("Performance statistics cache cleared")


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    enable_console: bool = True,
    enable_performance_logging: bool = True
) -> None:
    """
    Set up structured logging for the Discord Grok Bot.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file
        enable_console: Whether to enable console logging
        enable_performance_logging: Whether to enable performance logging
    """
    # Convert log level string to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Create formatters
    console_formatter = StructuredFormatter(include_extra_fields=False)
    file_formatter = StructuredFormatter(include_extra_fields=True)
    
    # Set up console handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    # Set up file handler if specified
    if log_file:
        # Ensure log directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create rotating file handler (10MB max, keep 5 files)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # Set up performance logging if enabled
    if enable_performance_logging:
        perf_logger = logging.getLogger("performance")
        perf_logger.setLevel(logging.INFO)
        
        # Performance logs go to separate file if file logging is enabled
        if log_file:
            perf_log_file = log_path.parent / f"performance_{log_path.name}"
            perf_handler = logging.handlers.RotatingFileHandler(
                perf_log_file,
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding='utf-8'
            )
            perf_handler.setLevel(logging.INFO)
            perf_handler.setFormatter(file_formatter)
            perf_logger.addHandler(perf_handler)
    
    # Configure third-party library logging levels
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    
    # Log the logging setup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured - Level: {log_level}, Console: {enable_console}, File: {log_file}")


def get_logger_with_context(name: str, **context) -> logging.LoggerAdapter:
    """
    Get a logger with additional context information.
    
    Args:
        name: Logger name
        **context: Additional context fields to include in log messages
        
    Returns:
        LoggerAdapter with context information
    """
    logger = logging.getLogger(name)
    return logging.LoggerAdapter(logger, context)


class ImageProcessingLogger:
    """
    Specialized logger for image processing operations.
    """
    
    def __init__(self, logger_name: str = "image_processing"):
        """
        Initialize the image processing logger.
        
        Args:
            logger_name: Name of the logger to use
        """
        self.logger = logging.getLogger(logger_name)
    
    def log_image_validation(
        self,
        success: bool,
        image_size: int,
        image_format: str,
        user_id: Optional[int] = None,
        error_details: Optional[str] = None
    ) -> None:
        """
        Log image validation results.
        
        Args:
            success: Whether validation was successful
            image_size: Size of the image in bytes
            image_format: Format of the image
            user_id: Optional user ID
            error_details: Optional error details if validation failed
        """
        extra = {
            'operation_type': 'image_validation',
            'success': success,
            'image_size': image_size,
            'image_format': image_format
        }
        
        if user_id:
            extra['user_id'] = user_id
        if error_details:
            extra['error_details'] = error_details
        
        if success:
            self.logger.info("Image validation successful", extra=extra)
        else:
            self.logger.warning("Image validation failed", extra=extra)
    
    def log_api_request(
        self,
        api_name: str,
        duration: float,
        success: bool,
        edit_type: str,
        image_size: int,
        user_id: Optional[int] = None,
        error_details: Optional[str] = None
    ) -> None:
        """
        Log API requests for image processing.
        
        Args:
            api_name: Name of the API (e.g., 'nano-banana')
            duration: Request duration in seconds
            success: Whether the request was successful
            edit_type: Type of edit requested
            image_size: Size of the image being processed
            user_id: Optional user ID
            error_details: Optional error details if request failed
        """
        extra = {
            'operation_type': 'api_request',
            'api_name': api_name,
            'duration': duration,
            'success': success,
            'edit_type': edit_type,
            'image_size': image_size
        }
        
        if user_id:
            extra['user_id'] = user_id
        if error_details:
            extra['error_details'] = error_details
        
        if success:
            self.logger.info(f"API request to {api_name} successful", extra=extra)
        else:
            self.logger.error(f"API request to {api_name} failed", extra=extra)
    
    def log_processing_queue(
        self,
        queue_size: int,
        processing_count: int,
        user_id: Optional[int] = None
    ) -> None:
        """
        Log image processing queue status.
        
        Args:
            queue_size: Current size of the processing queue
            processing_count: Number of images currently being processed
            user_id: Optional user ID for context
        """
        extra = {
            'operation_type': 'queue_status',
            'queue_size': queue_size,
            'processing_count': processing_count
        }
        
        if user_id:
            extra['user_id'] = user_id
        
        self.logger.info("Processing queue status", extra=extra)


class MessageFormattingLogger:
    """
    Specialized logger for message formatting operations.
    """
    
    def __init__(self, logger_name: str = "message_formatting"):
        """
        Initialize the message formatting logger.
        
        Args:
            logger_name: Name of the logger to use
        """
        self.logger = logging.getLogger(logger_name)
    
    def log_markdown_processing(
        self,
        success: bool,
        block_count: int,
        processing_time: float,
        user_id: Optional[int] = None,
        error_details: Optional[str] = None
    ) -> None:
        """
        Log markdown processing results.
        
        Args:
            success: Whether processing was successful
            block_count: Number of markdown blocks processed
            processing_time: Time taken to process in seconds
            user_id: Optional user ID
            error_details: Optional error details if processing failed
        """
        extra = {
            'operation_type': 'markdown_processing',
            'success': success,
            'block_count': block_count,
            'duration': processing_time
        }
        
        if user_id:
            extra['user_id'] = user_id
        if error_details:
            extra['error_details'] = error_details
        
        if success:
            self.logger.info("Markdown processing successful", extra=extra)
        else:
            self.logger.warning("Markdown processing failed", extra=extra)
    
    def log_message_split(
        self,
        success: bool,
        original_length: int,
        split_count: int,
        processing_time: float,
        preserved_formatting: bool = True,
        user_id: Optional[int] = None,
        error_details: Optional[str] = None
    ) -> None:
        """
        Log message splitting results.
        
        Args:
            success: Whether splitting was successful
            original_length: Length of original message
            split_count: Number of parts the message was split into
            processing_time: Time taken to split in seconds
            preserved_formatting: Whether formatting was preserved
            user_id: Optional user ID
            error_details: Optional error details if splitting failed
        """
        extra = {
            'operation_type': 'message_split',
            'success': success,
            'original_length': original_length,
            'split_count': split_count,
            'duration': processing_time,
            'preserved_formatting': preserved_formatting
        }
        
        if user_id:
            extra['user_id'] = user_id
        if error_details:
            extra['error_details'] = error_details
        
        if success:
            self.logger.info("Message splitting successful", extra=extra)
        else:
            self.logger.warning("Message splitting failed", extra=extra)


class TimingContext:
    """
    Context manager for timing operations and logging performance.
    """
    
    def __init__(
        self, 
        logger: logging.Logger, 
        operation_name: str, 
        log_level: int = logging.INFO,
        **extra_context
    ):
        """
        Initialize timing context.
        
        Args:
            logger: Logger to use for timing information
            operation_name: Name of the operation being timed
            log_level: Log level for timing messages
            **extra_context: Additional context to include in logs
        """
        self.logger = logger
        self.operation_name = operation_name
        self.log_level = log_level
        self.extra_context = extra_context
        self.start_time = None
        self.duration = None
    
    def __enter__(self):
        """Start timing the operation."""
        import time
        self.start_time = time.time()
        self.logger.log(
            self.log_level, 
            f"Starting {self.operation_name}",
            extra=self.extra_context
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """End timing and log the duration."""
        import time
        self.duration = time.time() - self.start_time
        
        extra = dict(self.extra_context)
        extra['duration'] = self.duration
        
        if exc_type is None:
            self.logger.log(
                self.log_level,
                f"Completed {self.operation_name}",
                extra=extra
            )
        else:
            extra['error_type'] = exc_type.__name__
            self.logger.error(
                f"Failed {self.operation_name}",
                extra=extra,
                exc_info=True
            )