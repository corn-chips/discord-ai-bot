"""
Tests for enhanced logging functionality.

Tests image processing logging, message formatting logging, and performance tracking
according to requirements 6.1, 6.2, and 6.5.
"""

import logging
import unittest
from unittest.mock import Mock, patch, MagicMock
from src.utils.logging_config import (
    PerformanceLogger, 
    ImageProcessingLogger, 
    MessageFormattingLogger,
    StructuredFormatter,
    TimingContext
)


class TestEnhancedLogging(unittest.TestCase):
    """Test cases for enhanced logging functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.performance_logger = PerformanceLogger("test_performance")
        self.image_logger = ImageProcessingLogger("test_image")
        self.message_logger = MessageFormattingLogger("test_message")

    def test_performance_logger_image_processing(self):
        """Test performance logging for image processing operations."""
        with patch.object(self.performance_logger, 'logger') as mock_logger:
            # Test successful image processing
            self.performance_logger.log_image_processing(
                duration=2.5,
                success=True,
                image_size=1024000,
                edit_type="object_removal",
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Image processing completed", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['duration'], 2.5)
            self.assertTrue(extra['success'])
            self.assertEqual(extra['image_size'], 1024000)
            self.assertEqual(extra['edit_type'], "object_removal")
            self.assertEqual(extra['user_id'], 123456789)
            self.assertEqual(extra['operation_type'], 'image_processing')
            
            # Check statistics update
            self.assertEqual(self.performance_logger._stats['image_processing_count'], 1)
            self.assertEqual(self.performance_logger._stats['total_image_processing_time'], 2.5)
            self.assertEqual(self.performance_logger._stats['image_processing_failures'], 0)

    def test_performance_logger_image_processing_failure(self):
        """Test performance logging for failed image processing operations."""
        with patch.object(self.performance_logger, 'logger') as mock_logger:
            # Test failed image processing
            self.performance_logger.log_image_processing(
                duration=1.0,
                success=False,
                error_type="image_size_error",
                user_id=123456789
            )
            
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            
            # Check message content
            self.assertIn("Image processing failed", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertFalse(extra['success'])
            self.assertEqual(extra['error_type'], "image_size_error")
            
            # Check statistics update
            self.assertEqual(self.performance_logger._stats['image_processing_failures'], 1)

    def test_performance_logger_message_splitting(self):
        """Test performance logging for message splitting operations."""
        with patch.object(self.performance_logger, 'logger') as mock_logger:
            # Test successful message splitting
            self.performance_logger.log_message_splitting(
                duration=0.1,
                success=True,
                original_length=5000,
                split_count=3,
                markdown_blocks=5,
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Message splitting completed", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['duration'], 0.1)
            self.assertTrue(extra['success'])
            self.assertEqual(extra['original_length'], 5000)
            self.assertEqual(extra['split_count'], 3)
            self.assertEqual(extra['markdown_blocks'], 5)
            self.assertEqual(extra['operation_type'], 'message_splitting')
            
            # Check statistics update
            self.assertEqual(self.performance_logger._stats['message_splits_count'], 1)
            self.assertEqual(self.performance_logger._stats['total_message_split_time'], 0.1)

    def test_performance_logger_user_action(self):
        """Test performance logging for user actions."""
        with patch.object(self.performance_logger, 'logger') as mock_logger:
            # Test successful user action
            self.performance_logger.log_user_action(
                user_id=123456789,
                action="image_edit",
                duration=3.0,
                success=True,
                guild_id=987654321,
                channel_id=555666777,
                additional_context={'edit_type': 'background_removal'}
            )
            
            mock_logger.log.assert_called_once()
            call_args = mock_logger.log.call_args
            
            # Check log level (should be INFO for success)
            self.assertEqual(call_args[0][0], logging.INFO)
            
            # Check message content
            self.assertIn("User action: image_edit", call_args[0][1])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['user_id'], 123456789)
            self.assertEqual(extra['action'], "image_edit")
            self.assertEqual(extra['duration'], 3.0)
            self.assertTrue(extra['success'])
            self.assertEqual(extra['guild_id'], 987654321)
            self.assertEqual(extra['channel_id'], 555666777)
            self.assertEqual(extra['edit_type'], 'background_removal')

    def test_image_processing_logger_validation(self):
        """Test image processing logger validation logging."""
        with patch.object(self.image_logger, 'logger') as mock_logger:
            # Test successful validation
            self.image_logger.log_image_validation(
                success=True,
                image_size=2048000,
                image_format="PNG",
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Image validation successful", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['operation_type'], 'image_validation')
            self.assertTrue(extra['success'])
            self.assertEqual(extra['image_size'], 2048000)
            self.assertEqual(extra['image_format'], "PNG")
            self.assertEqual(extra['user_id'], 123456789)

    def test_image_processing_logger_validation_failure(self):
        """Test image processing logger validation failure logging."""
        with patch.object(self.image_logger, 'logger') as mock_logger:
            # Test failed validation
            self.image_logger.log_image_validation(
                success=False,
                image_size=15000000,  # Too large
                image_format="BMP",
                user_id=123456789,
                error_details="Image size exceeds 10MB limit"
            )
            
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            
            # Check message content
            self.assertIn("Image validation failed", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertFalse(extra['success'])
            self.assertEqual(extra['error_details'], "Image size exceeds 10MB limit")

    def test_image_processing_logger_api_request(self):
        """Test image processing logger API request logging."""
        with patch.object(self.image_logger, 'logger') as mock_logger:
            # Test successful API request
            self.image_logger.log_api_request(
                api_name="nano-banana",
                duration=4.2,
                success=True,
                edit_type="object_removal",
                image_size=1024000,
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("API request to nano-banana successful", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['operation_type'], 'api_request')
            self.assertEqual(extra['api_name'], "nano-banana")
            self.assertEqual(extra['duration'], 4.2)
            self.assertTrue(extra['success'])
            self.assertEqual(extra['edit_type'], "object_removal")

    def test_image_processing_logger_queue_status(self):
        """Test image processing logger queue status logging."""
        with patch.object(self.image_logger, 'logger') as mock_logger:
            # Test queue status logging
            self.image_logger.log_processing_queue(
                queue_size=5,
                processing_count=2,
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Processing queue status", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['operation_type'], 'queue_status')
            self.assertEqual(extra['queue_size'], 5)
            self.assertEqual(extra['processing_count'], 2)

    def test_message_formatting_logger_markdown_processing(self):
        """Test message formatting logger markdown processing logging."""
        with patch.object(self.message_logger, 'logger') as mock_logger:
            # Test successful markdown processing
            self.message_logger.log_markdown_processing(
                success=True,
                block_count=8,
                processing_time=0.05,
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Markdown processing successful", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['operation_type'], 'markdown_processing')
            self.assertTrue(extra['success'])
            self.assertEqual(extra['block_count'], 8)
            self.assertEqual(extra['duration'], 0.05)

    def test_message_formatting_logger_message_split(self):
        """Test message formatting logger message split logging."""
        with patch.object(self.message_logger, 'logger') as mock_logger:
            # Test successful message split
            self.message_logger.log_message_split(
                success=True,
                original_length=4500,
                split_count=3,
                processing_time=0.02,
                preserved_formatting=True,
                user_id=123456789
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            
            # Check message content
            self.assertIn("Message splitting successful", call_args[0][0])
            
            # Check extra context
            extra = call_args[1]['extra']
            self.assertEqual(extra['operation_type'], 'message_split')
            self.assertTrue(extra['success'])
            self.assertEqual(extra['original_length'], 4500)
            self.assertEqual(extra['split_count'], 3)
            self.assertEqual(extra['duration'], 0.02)
            self.assertTrue(extra['preserved_formatting'])

    def test_structured_formatter_enhanced_fields(self):
        """Test structured formatter with enhanced context fields."""
        formatter = StructuredFormatter(include_extra_fields=True)
        
        # Create a log record with enhanced fields
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None
        )
        
        # Add enhanced context fields
        record.user_id = 123456789
        record.image_size = 1024000
        record.edit_type = "object_removal"
        record.operation_type = "image_processing"
        record.duration = 2.5
        record.success = True
        
        formatted = formatter.format(record)
        
        # Check that enhanced fields are included
        self.assertIn("user_id=123456789", formatted)
        self.assertIn("image_size=1024000", formatted)
        self.assertIn("edit_type=object_removal", formatted)
        self.assertIn("operation=image_processing", formatted)
        self.assertIn("duration=2.500s", formatted)
        self.assertIn("success=True", formatted)

    def test_performance_logger_summary_stats_enhanced(self):
        """Test performance logger summary statistics with enhanced metrics."""
        # Add some test data
        self.performance_logger._stats.update({
            'image_processing_count': 10,
            'total_image_processing_time': 25.0,
            'image_processing_failures': 2,
            'message_splits_count': 5,
            'total_message_split_time': 0.5,
            'message_split_failures': 1
        })
        
        stats = self.performance_logger.get_summary_stats()
        
        # Check enhanced statistics
        self.assertEqual(stats['image_processing_count'], 10)
        self.assertEqual(stats['avg_image_processing_time'], 2.5)
        self.assertEqual(stats['image_processing_success_rate'], 80.0)
        
        self.assertEqual(stats['message_splits_count'], 5)
        self.assertEqual(stats['avg_message_split_time'], 0.1)
        self.assertEqual(stats['message_split_success_rate'], 80.0)

    def test_timing_context_enhanced(self):
        """Test timing context with enhanced error handling."""
        mock_logger = Mock()
        
        # Test successful operation
        with TimingContext(mock_logger, "test_operation", user_id=123, operation_type="test"):
            pass  # Simulate work
        
        # Check that start and completion were logged
        self.assertEqual(mock_logger.log.call_count, 2)
        
        # Check start log
        start_call = mock_logger.log.call_args_list[0]
        self.assertIn("Starting test_operation", start_call[0][1])
        
        # Check completion log
        completion_call = mock_logger.log.call_args_list[1]
        self.assertIn("Completed test_operation", completion_call[0][1])
        self.assertIn('duration', completion_call[1]['extra'])

    def test_timing_context_with_exception(self):
        """Test timing context with exception handling."""
        mock_logger = Mock()
        
        # Test operation with exception
        try:
            with TimingContext(mock_logger, "failing_operation", user_id=123):
                raise ValueError("Test error")
        except ValueError:
            pass  # Expected
        
        # Check that error was logged
        error_call = mock_logger.error.call_args
        self.assertIn("Failed failing_operation", error_call[0][0])
        self.assertIn('error_type', error_call[1]['extra'])
        self.assertEqual(error_call[1]['extra']['error_type'], 'ValueError')

    def test_clear_cache_enhanced(self):
        """Test clearing enhanced performance cache."""
        # Add some test data
        self.performance_logger._stats.update({
            'image_processing_count': 5,
            'total_image_processing_time': 10.0,
            'message_splits_count': 3
        })
        
        # Clear cache
        self.performance_logger.clear_cache()
        
        # Check that all enhanced stats are reset
        self.assertEqual(self.performance_logger._stats['image_processing_count'], 0)
        self.assertEqual(self.performance_logger._stats['total_image_processing_time'], 0.0)
        self.assertEqual(self.performance_logger._stats['image_processing_failures'], 0)
        self.assertEqual(self.performance_logger._stats['message_splits_count'], 0)
        self.assertEqual(self.performance_logger._stats['total_message_split_time'], 0.0)
        self.assertEqual(self.performance_logger._stats['message_split_failures'], 0)


if __name__ == '__main__':
    unittest.main()