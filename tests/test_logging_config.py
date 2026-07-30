"""DAB-164, DAB-165, DAB-166: the log file and the debug lever were both lying.

`StructuredFormatter.format` spliced its extra fields *into* `record.msg` and
then called `super().format()`, which calls `record.getMessage()`, which
evaluates `self.msg % self.args`. Three consequences, in descending order of
how often they actually bite:

- the mutation is permanent, so every extra was printed twice on the shipped
  file-logging path (DAB-164): `RotatingFileHandler.emit` formats the record
  once inside `shouldRollover` to measure it and once to write it. It also
  happened when the formatter was built with `include_extra_fields=False`;
- a `%` in any extra value became a rogue conversion specifier, so
  `getMessage()` raised, `Handler.handle` aborted, and the record was lost to a
  stderr traceback (DAB-165). This needs a `%`-style call site that also passes
  args, which no current call site does -- see
  `docs/ANALYSIS_CORRECTIONS.md` item 9 -- so it is a latent trap rather than a
  live bug, and the test below is a guard;
- `setup_logging` pinned each handler's level at startup while `/config debug`
  raised only the *logger's*, so no DEBUG record could ever reach a handler and
  the command reported success anyway (DAB-166).

Every assertion here reads the log file back, or reads the level a record
actually cleared. None of them asserts that a particular line was emitted to a
capture buffer, because the defect was precisely that the emitted line never
arrived.
"""

import logging
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot.command_modules.configuration import create_config_group
from src.bot.command_modules.context import CommandContext
from src.utils.logging_config import StructuredFormatter, setup_logging


class LoggingHarness(unittest.TestCase):
    """Give each test a private log file and put logging back afterwards."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.log_file = os.path.join(self._dir.name, "bot.log")

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level

        def restore():
            for handler in list(root.handlers):
                handler.close()
            root.handlers.clear()
            root.handlers.extend(saved_handlers)
            root.setLevel(saved_level)
            perf = logging.getLogger("performance")
            for handler in list(perf.handlers):
                handler.close()
                perf.removeHandler(handler)
            perf.propagate = True

        self.addCleanup(restore)

    def configure(self, level="INFO", performance=False):
        setup_logging(
            log_level=level,
            log_file=self.log_file,
            enable_console=False,
            enable_performance_logging=performance,
        )

    def read_log(self):
        for handler in logging.getLogger().handlers:
            handler.flush()
        with open(self.log_file, encoding="utf-8") as handle:
            return handle.read()


class StructuredFormatterTest(LoggingHarness):
    def test_the_formatter_does_not_mutate_the_record(self):
        formatter = StructuredFormatter(include_extra_fields=True)
        record = logging.LogRecord(
            "t", logging.INFO, "path", 1, "plain message", None, None
        )
        record.user_id = 7

        first = formatter.format(record)

        self.assertEqual(record.msg, "plain message")
        self.assertEqual(first, formatter.format(record), "format is not idempotent")
        self.assertEqual(first.count("user_id=7"), 1)

    def test_the_rotating_file_handler_does_not_double_the_extras(self):
        # One handler is enough. RotatingFileHandler.emit calls
        # shouldRollover(record), which formats the record to measure it, and
        # then formats it again to write it -- so the in-place mutation ran
        # twice per record on the shipped file-logging path.
        self.configure()
        logging.getLogger("t").info("indexed", extra={"user_id": 7, "action": "backfill"})
        logging.shutdown()

        body = self.read_log()
        self.assertEqual(body.count("user_id=7"), 1)
        self.assertEqual(body.count("action=backfill"), 1)

    def test_include_extra_fields_false_emits_no_extras(self):
        formatter = StructuredFormatter(include_extra_fields=False)
        record = logging.LogRecord("t", logging.INFO, "path", 1, "plain", None, None)
        record.user_id = 7

        self.assertNotIn("user_id", formatter.format(record))
        self.assertEqual(record.msg, "plain")

    def test_extras_stay_on_the_message_line_ahead_of_a_traceback(self):
        # Appending to the result of format() rather than to the message line
        # would strand the extras after the exception text.
        formatter = StructuredFormatter(include_extra_fields=True)
        try:
            raise ValueError("kaboom")
        except ValueError:
            record = logging.LogRecord(
                "t", logging.ERROR, "path", 1, "failed", None, sys.exc_info()
            )
        record.action = "retry"

        lines = formatter.format(record).splitlines()

        self.assertIn("action=retry", lines[0])
        self.assertTrue(
            any(line.startswith("ValueError") for line in lines[1:]),
            "the traceback should follow the annotated message line",
        )

    def test_a_percent_in_an_extra_does_not_drop_the_record(self):
        self.configure()
        logging.getLogger("t").info(
            "indexed %s messages", 5, extra={"action": "backfill 50% done"}
        )
        logging.shutdown()

        self.assertIn("indexed 5 messages", self.read_log())


class LoggingLevelTest(LoggingHarness):
    def test_raising_the_root_level_at_runtime_reaches_the_file(self):
        self.configure(level="INFO")

        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("x").debug("visible now")
        logging.shutdown()

        self.assertIn("visible now", self.read_log())

    def test_the_configured_level_still_suppresses_quieter_records(self):
        # Dropping the handler pins must not turn the level into a no-op in the
        # other direction.
        self.configure(level="WARNING")

        logging.getLogger("x").info("should not appear")
        logging.getLogger("x").warning("should appear")
        logging.shutdown()

        body = self.read_log()
        self.assertNotIn("should not appear", body)
        self.assertIn("should appear", body)

    def test_performance_records_stay_out_of_the_main_log(self):
        # The performance logger sets its own INFO level. With the root
        # handlers unpinned it would otherwise push timing lines into bot.log
        # at any configured level.
        self.configure(level="WARNING", performance=True)

        logging.getLogger("performance").info("PERF-MARKER")
        logging.shutdown()

        self.assertNotIn("PERF-MARKER", self.read_log())


class DebugCommandTest(unittest.IsolatedAsyncioTestCase, LoggingHarness):
    """Drive the real `/config debug` callback, not a reimplementation of it."""

    def _debug_command(self, startup_level="INFO"):
        config = SimpleNamespace(
            log_level=startup_level,
            available_models=[{"name": "A", "value": "model-a"}],
        )
        group = create_config_group(
            CommandContext(
                bot=SimpleNamespace(),
                config=config,
                gemini_client=SimpleNamespace(),
                performance_logger=SimpleNamespace(),
                token_tracker=None,
            )
        )
        for command in group.walk_commands():
            if command.name == "debug":
                return command, config
        raise AssertionError("/config debug is not registered")

    @staticmethod
    def _admin_interaction():
        return SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(
                guild_permissions=SimpleNamespace(
                    manage_guild=True, administrator=True
                )
            ),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_enabling_debug_actually_reaches_the_log_file(self):
        self.configure(level="INFO")
        command, config = self._debug_command("INFO")

        await command.callback(self._admin_interaction(), True)

        logging.getLogger("x").debug("now visible")
        logging.shutdown()

        self.assertIn("now visible", self.read_log())
        # /config info reads this field; it used to keep reporting INFO.
        self.assertEqual(config.log_level, "DEBUG")

    async def test_disabling_debug_returns_to_the_configured_level(self):
        self.configure(level="WARNING")
        command, config = self._debug_command("WARNING")

        await command.callback(self._admin_interaction(), True)
        await command.callback(self._admin_interaction(), False)

        logging.getLogger("x").debug("must not appear")
        logging.getLogger("x").warning("must appear")
        logging.shutdown()

        body = self.read_log()
        self.assertNotIn("must not appear", body)
        self.assertIn("must appear", body)
        # Not a hard-coded INFO: the operator asked for WARNING.
        self.assertEqual(config.log_level, "WARNING")


if __name__ == "__main__":
    unittest.main()
