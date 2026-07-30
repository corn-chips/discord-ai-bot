"""Guards for DAB-041: transient faults must be classified as retryable.

`categorize_error` matches on `str(error)`. The most common transient faults in
a Discord/Gemini bot carry no message at all -- `asyncio.TimeoutError`, the
builtin `TimeoutError`, `ConnectionError`, `ConnectionResetError` and httpx's
transport errors all stringify to "" -- so they matched no keyword and fell
through to UNKNOWN_ERROR, which is not retryable. The bot gave up on precisely
the failures a retry would have fixed.

A real Gemini quota refusal was caught by the same class of gap for a different
reason: its prose is "exceeded your current quota", which does not contain the
substring "quota exceeded" that the keyword list was looking for.

The ordering test at the bottom is the important one. Dispatching on exception
type *before* the substring chain would be tidier and wrong, because
`PIL.UnidentifiedImageError` is an `OSError`: a corrupt upload would be
reclassified as a network fault and retried forever.
"""

import asyncio
import socket
import unittest

from PIL import UnidentifiedImageError

from src.utils.error_manager import ErrorManager, ErrorType

# Mirrors the set in ErrorManager.create_error_context. Duplicated rather than
# imported because it is a local in that method; if the two ever diverge, the
# assertion below is the thing that should notice.
RETRYABLE = {
    ErrorType.RATE_LIMIT,
    ErrorType.TIMEOUT,
    ErrorType.SERVICE_UNAVAILABLE,
    ErrorType.DISCORD_HTTP_ERROR,
    ErrorType.DISCORD_CONNECTION_ERROR,
    ErrorType.NANO_BANANA_API_ERROR,
    ErrorType.IMAGE_UPLOAD_ERROR,
    ErrorType.IMAGE_DOWNLOAD_ERROR,
    ErrorType.MESSAGE_SPLIT_ERROR,
}


def make_manager():
    return ErrorManager.__new__(ErrorManager)


class SilentTransientErrorTest(unittest.TestCase):
    """Exceptions that stringify to "" must still be understood."""

    def test_message_less_timeouts_are_classified_as_timeouts(self):
        manager = make_manager()
        for error in (asyncio.TimeoutError(), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(str(error), "", "precondition: carries no message")
                self.assertEqual(manager.categorize_error(error), ErrorType.TIMEOUT)

    def test_message_less_connection_faults_are_classified_as_connection_errors(self):
        manager = make_manager()
        for error in (ConnectionError(), ConnectionResetError(), socket.gaierror()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(
                    manager.categorize_error(error),
                    ErrorType.DISCORD_CONNECTION_ERROR,
                )

    def test_httpx_transport_errors_are_classified_as_connection_errors(self):
        try:
            import httpx
        except ImportError:  # pragma: no cover
            self.skipTest("httpx is not installed")

        manager = make_manager()
        for error in (
            httpx.ConnectError(""),
            httpx.ReadTimeout(""),
            httpx.RemoteProtocolError(""),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIn(
                    manager.categorize_error(error),
                    {ErrorType.DISCORD_CONNECTION_ERROR, ErrorType.TIMEOUT},
                )

    def test_every_transient_fault_is_retryable(self):
        manager = make_manager()
        transient = [
            asyncio.TimeoutError(),
            TimeoutError(),
            ConnectionError(),
            ConnectionResetError(),
        ]
        for error in transient:
            with self.subTest(error=type(error).__name__):
                self.assertIn(
                    manager.categorize_error(error),
                    RETRYABLE,
                    f"{type(error).__name__} would not be retried",
                )


class GeminiQuotaRefusalTest(unittest.TestCase):
    def test_a_real_429_is_a_rate_limit_not_an_unknown_error(self):
        manager = make_manager()
        # Verbatim shape of a google-genai ClientError for a quota refusal. Note
        # it says "exceeded your current quota", never "quota exceeded".
        error = Exception(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
            "'You exceeded your current quota, please check your plan and "
            "billing details.', 'status': 'RESOURCE_EXHAUSTED'}}"
        )
        self.assertNotIn("quota exceeded", str(error).lower())
        self.assertEqual(manager.categorize_error(error), ErrorType.RATE_LIMIT)
        self.assertIn(ErrorType.RATE_LIMIT, RETRYABLE)


class ClassificationOrderingTest(unittest.TestCase):
    """The type dispatch must run last, not first."""

    def test_a_corrupt_image_is_not_mistaken_for_a_network_fault(self):
        # UnidentifiedImageError subclasses OSError, and so does ConnectionError.
        # Hoisting an isinstance(error, OSError) check above the substring chain
        # would classify a corrupt upload as a transient transport failure and
        # retry it forever.
        self.assertTrue(issubclass(UnidentifiedImageError, OSError))

        manager = make_manager()
        error = UnidentifiedImageError("cannot identify image file")
        category = manager.categorize_error(error)

        self.assertNotEqual(category, ErrorType.DISCORD_CONNECTION_ERROR)
        self.assertNotIn(category, RETRYABLE, "a corrupt image must not be retried")

    def test_message_bearing_errors_keep_their_substring_classification(self):
        manager = make_manager()
        cases = {
            "Request timed out after 120s": ErrorType.TIMEOUT,
            "503 Service Unavailable": ErrorType.SERVICE_UNAVAILABLE,
            "Invalid API key supplied": ErrorType.AUTHENTICATION_ERROR,
            "image size exceeds maximum size": ErrorType.IMAGE_SIZE_ERROR,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(manager.categorize_error(Exception(message)), expected)


if __name__ == "__main__":
    unittest.main()
