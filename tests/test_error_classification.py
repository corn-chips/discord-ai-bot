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
from types import SimpleNamespace

import discord
from PIL import UnidentifiedImageError
from google.genai import errors as genai_errors

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


def genai_error(code, status, message):
    """Build a real `google.genai` APIError, worded the way the SDK words them."""
    body = {"error": {"code": code, "message": message, "status": status}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


class StructuredApiErrorTest(unittest.TestCase):
    """DAB-040: classify a Gemini failure from its code, not from its prose.

    `google.genai.errors.APIError.__str__` is `f"{code} {status}. {details}"`.
    Matching that string was guesswork in both directions -- a real 403 and a
    real 404 fell through to UNKNOWN_ERROR and were never retried, while
    "Response was 1500 tokens over budget" was classified SERVICE_UNAVAILABLE
    and retried four times.
    """

    CASES = [
        (429, "RESOURCE_EXHAUSTED",
         "You exceeded your current quota, please check your plan and billing "
         "details. For more information on this error, head to: "
         "https://ai.google.dev/gemini-api/docs/rate-limits.",
         ErrorType.RATE_LIMIT),
        (429, "RESOURCE_EXHAUSTED", "Quota exceeded for metric", ErrorType.RATE_LIMIT),
        (503, "UNAVAILABLE", "The model is overloaded", ErrorType.SERVICE_UNAVAILABLE),
        (500, "INTERNAL", "internal error", ErrorType.SERVICE_UNAVAILABLE),
        (401, "UNAUTHENTICATED", "missing credentials", ErrorType.AUTHENTICATION_ERROR),
        (403, "PERMISSION_DENIED", "API key not valid", ErrorType.AUTHENTICATION_ERROR),
        (404, "NOT_FOUND", "models/foo is not found", ErrorType.INVALID_REQUEST),
        (400, "INVALID_ARGUMENT", "Unsupported mimeType", ErrorType.INVALID_REQUEST),
        # The one that used to be retried four times: a permanent 400 whose
        # message happens to contain "1500".
        (400, "INVALID_ARGUMENT", "The prompt is 1500 tokens too long",
         ErrorType.INVALID_REQUEST),
    ]

    def test_the_code_and_status_decide_the_category(self):
        manager = make_manager()
        for code, status, message, expected in self.CASES:
            with self.subTest(code=code, status=status):
                self.assertEqual(
                    manager.categorize_error(genai_error(code, status, message)),
                    expected,
                )

    def test_a_real_429_is_retryable_and_a_real_400_is_not(self):
        manager = make_manager()
        quota = genai_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota")
        permanent = genai_error(400, "INVALID_ARGUMENT", "The prompt is 1500 tokens too long")

        self.assertIn(manager.categorize_error(quota), RETRYABLE)
        self.assertNotIn(manager.categorize_error(permanent), RETRYABLE)

    def test_the_structured_pass_does_not_outrank_the_image_block(self):
        # Hoisting the code dispatch to the top of categorize_error is tidier
        # and would move a Gemini upload rejection out of the image bucket that
        # the image handling depends on.
        manager = make_manager()
        error = genai_error(400, "INVALID_ARGUMENT", "invalid image supplied")

        self.assertEqual(
            manager.categorize_error(error), ErrorType.IMAGE_VALIDATION_ERROR
        )

    def test_a_discord_http_exception_is_not_read_as_an_http_status(self):
        # discord.HTTPException.code is a *Discord* error number -- 50013 for
        # "Missing Permissions", not 403 -- so a duck-typed
        # getattr(error, "code") pass would bucket the Discord surface by
        # coincidence. The dispatch is restricted to google.genai's hierarchy.
        manager = make_manager()
        response = SimpleNamespace(status=403, reason="Forbidden")
        error = discord.HTTPException(
            response, {"code": 50013, "message": "Missing Permissions"}
        )

        self.assertEqual(manager.categorize_error(error), ErrorType.DISCORD_HTTP_ERROR)


class NumericSubstringTest(unittest.TestCase):
    """DAB-040: a status code counts only where it is being used as one."""

    NOT_STATUS_CODES = [
        "Message exceeds 4000 characters",
        "Response was 1500 tokens over budget",
        "Backfill stalled after 500 messages",
        "Embedding dimension mismatch: expected 1500, got 768",
        "sqlite3.OperationalError: database is locked after 5002 ms",
        "Discarded 400 stale index rows",
        "user 500123456789012345 not found",
        "Retrieved 2400 candidate messages",
    ]

    REAL_STATUS_CODES = {
        "503 Service Unavailable": ErrorType.SERVICE_UNAVAILABLE,
        "HTTP 500 Internal Server Error": ErrorType.SERVICE_UNAVAILABLE,
        "HTTP 400 Bad Request": ErrorType.INVALID_REQUEST,
        "status: 502 upstream closed": ErrorType.SERVICE_UNAVAILABLE,
    }

    def test_incidental_digits_are_not_status_codes(self):
        manager = make_manager()
        for message in self.NOT_STATUS_CODES:
            with self.subTest(message=message):
                category = manager.categorize_error(Exception(message))
                self.assertEqual(category, ErrorType.UNKNOWN_ERROR)
                self.assertNotIn(
                    category,
                    RETRYABLE,
                    "a permanent local fault must not be retried four times",
                )

    def test_real_status_codes_still_classify(self):
        # Word boundaries alone would not achieve this: `\b500\b` matches
        # "after 500 messages" too. The code also has to start the message or
        # be introduced by http/status/code/error.
        manager = make_manager()
        for message, expected in self.REAL_STATUS_CODES.items():
            with self.subTest(message=message):
                self.assertEqual(manager.categorize_error(Exception(message)), expected)


class ErrorDetailDisclosureTest(unittest.TestCase):
    """DAB-153: raw exception text reached the channel by default.

    `str(exc)` routinely carries absolute filesystem paths -- including the OS
    username -- SQL fragments and internal table names, and that string was
    appended to the message posted back to Discord. The operator loses nothing
    by turning it off: `dev_mode_enabled` already yields a strict superset, the
    same text plus a full stack trace.
    """

    @staticmethod
    def _manager(dev_mode=False):
        from types import SimpleNamespace

        return ErrorManager(config=SimpleNamespace(dev_mode_enabled=dev_mode))

    def test_raw_exception_text_is_withheld_by_default(self):
        manager = self._manager()
        leaky = FileNotFoundError(
            "/home/someuser/discord-ai-bot/data/token_usage.db"
        )

        message = manager.create_error_context(leaky).user_message

        self.assertNotIn("/home/someuser", message)
        self.assertNotIn("token_usage.db", message)
        self.assertNotIn("Details:", message)

    def test_internal_database_wording_is_withheld_by_default(self):
        manager = self._manager()
        message = manager.create_error_context(
            Exception("no such table: message_index")
        ).user_message

        self.assertNotIn("message_index", message)

    def test_an_explicit_opt_in_still_works_for_callers_that_want_it(self):
        manager = self._manager()
        message = manager.create_error_context(
            Exception("boom"), include_error_details=True
        ).user_message

        self.assertIn("boom", message)

    def test_dev_mode_still_gives_the_operator_everything(self):
        manager = self._manager(dev_mode=True)
        try:
            raise ValueError("diagnostic detail")
        except ValueError as exc:
            message = manager.create_error_context(exc).user_message

        self.assertIn("diagnostic detail", message)


if __name__ == "__main__":
    unittest.main()
