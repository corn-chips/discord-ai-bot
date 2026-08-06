"""
Pin service for the Discord bot.

Manages per-channel pinned messages stored in SQLite. Pinned messages
are injected into the AI prompt so the bot always remembers them.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)

#: Ceilings on pinned memory per channel (DAB-150).
#:
#: Pins are injected verbatim into every prompt for their channel, forever, and
#: /pin previously accepted any length from any member. A single 100 KB pin was
#: accepted untruncated and rode in on every subsequent request, so one user
#: could permanently raise the cost of every conversation in a channel.
#:
#: 40,000 characters is roughly 10k tokens -- generous for genuine standing
#: instructions, and small next to the context budget.
MAX_PINS_PER_CHANNEL = 25
MAX_PIN_CHARS_PER_CHANNEL = 40_000
MAX_PIN_CHARS = 4_000

#: Columns the legacy copy cannot synthesise. `guild_id` and `message_id` are
#: nullable in `pinned_messages` and are filled with NULL when a legacy schema
#: lacks them; everything here is NOT NULL, or is the ordering key.
_REQUIRED_LEGACY_PIN_COLUMNS = frozenset(
    {"id", "channel_id", "content", "author_name", "pinned_by", "pinned_at"}
)

#: Delimiters the prompt builder uses to fence the context block. A pin
#: containing one of these can forge the end of the block and inject text that
#: reads as system-level instruction.
_PROMPT_DELIMITERS = (
    "--- End Context ---",
    "--- Context ---",
    "--- End Pinned",
    "--- Pinned",
)


def _sanitise_pin_content(content: str) -> str:
    """Trim a pin to its per-pin cap and defuse prompt-structure delimiters."""
    cleaned = (content or "").strip()

    for delimiter in _PROMPT_DELIMITERS:
        # Zero-width space after the leading dashes: visually identical in
        # Discord, no longer matches the delimiter the prompt builder emits.
        cleaned = cleaned.replace(delimiter, delimiter.replace("---", "-\u200b--", 1))

    if len(cleaned) > MAX_PIN_CHARS:
        cleaned = cleaned[:MAX_PIN_CHARS].rstrip() + " [truncated]"

    return cleaned


class PinService:
    """Manages per-channel pinned messages persisted in SQLite."""

    def __init__(
        self,
        db_path: str = "data/message_rag.db",
        legacy_db_path: Optional[str] = None,
    ):
        self.db_path = db_path
        self._ensure_table()
        if legacy_db_path:
            self._migrate_legacy_pins(legacy_db_path)

    def _ensure_table(self):
        """Create the pinned_messages table if it doesn't exist."""
        try:
            with sqlite_transaction(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pinned_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id INTEGER NOT NULL,
                        guild_id INTEGER,
                        content TEXT NOT NULL,
                        author_name TEXT NOT NULL,
                        pinned_by TEXT NOT NULL,
                        pinned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        message_id INTEGER
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pinned_channel
                    ON pinned_messages (channel_id)
                """)
            logger.info("Pinned messages table ready")
        except Exception as e:
            logger.error(f"Failed to create pinned_messages table: {e}")

    def _migrate_legacy_pins(self, legacy_db_path: str) -> None:
        """Copy legacy pins once into the dedicated RAG database.

        Two properties this has to get right, both of them learned the hard way
        from the sibling migration in `MessageIndexService` (DAB-083, DAB-066).

        **The ledger records work, not attempts.** The row used to be written
        unconditionally, so a fresh install -- where `TokenTracker` has created
        `token_usage.db` and it has never held a pin -- burned the one shot
        having copied nothing, and logged it as a success. Any later genuine
        migration was then gated out forever. An absent legacy table now returns
        without writing the ledger, so the migration stays armed. Measured cost
        of staying armed: +0.12 ms per boot, and no FTS rebuild on this path,
        so nothing like PPR-02's +385.8 ms.

        **Every pin admitted here goes through the ceilings `/pin` enforces.**
        This is the only code path that writes `pinned_messages` without going
        through `add_pin`, and it used to be a bulk `INSERT ... SELECT`.
        Measured on 61 legacy pins in one channel: 61 rows stored against a
        25-pin cap, 300,871 characters against a 40,000 cap, a single 5,014-char
        pin against a 4,000 cap, and a `--- End Context ---` delimiter copied in
        undefused. The invariant restored here is precisely *the table is left
        in a state `add_pin` itself could have produced* -- which is also what
        makes the >25-field `/pins` embed unreachable going forward.

        Rows are admitted oldest first, so it is the *newer* pins a channel
        loses to a ceiling -- matching `add_pin`, which refuses the new pin once
        the channel is full. Nothing deletes the legacy rows, so a dropped
        memory is still readable in the legacy database, and the WARNING says
        so.

        One legacy row that cannot become a valid pin is skipped, not fatal.
        The bulk `INSERT OR IGNORE` swallowed those silently; a plain `INSERT`
        would abort the transaction, and because the ledger is only written on
        success that would retry -- and fail -- on every boot forever. Reachable
        through the view support above, since a view carries no NOT NULL.
        """
        source = Path(legacy_db_path).expanduser()
        target = Path(self.db_path).expanduser()
        if not source.exists() or source.resolve() == target.resolve():
            return
        try:
            with sqlite_transaction(self.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_migrations (
                        name TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL
                    )
                    """
                )
                migration_name = "legacy_shared_pins_v1"
                if conn.execute(
                    "SELECT 1 FROM rag_migrations WHERE name = ?",
                    (migration_name,),
                ).fetchone():
                    return
                conn.execute("ATTACH DATABASE ? AS legacy", (str(source),))
                # A view is as migratable as a table, and excluding one would
                # leave the migration permanently pending now that "nothing to
                # do" no longer writes the ledger.
                if not conn.execute(
                    "SELECT 1 FROM legacy.sqlite_master "
                    "WHERE type IN ('table', 'view') AND name = 'pinned_messages'"
                ).fetchone():
                    logger.debug(
                        "No legacy pinned_messages in %s; leaving the pin "
                        "migration pending rather than recording it as done.",
                        source,
                    )
                    return

                available = {
                    row[1]
                    for row in conn.execute("PRAGMA legacy.table_info(pinned_messages)")
                }
                missing = sorted(_REQUIRED_LEGACY_PIN_COLUMNS - available)
                if missing:
                    logger.error(
                        "Legacy pinned_messages in %s is missing %s; no pins were "
                        "copied and the migration is left pending, so it will run "
                        "again once the legacy schema is repaired.",
                        source, ", ".join(missing),
                    )
                    return

                copied, capped, unusable = self._copy_legacy_pins(conn, available)
                conn.execute(
                    "INSERT INTO rag_migrations(name, completed_at) VALUES (?, ?)",
                    (migration_name, datetime.utcnow().isoformat()),
                )
            logger.info(
                "Copied %d legacy pinned memor%s from %s",
                copied, "y" if copied == 1 else "ies", source,
            )
            # Two reasons a row can be left behind, and they need different
            # words: one is the ceiling working as designed, the other is a
            # legacy row that was never a valid pin.
            for channel_id, count in sorted(capped.items()):
                logger.warning(
                    "Dropped %d legacy pin(s) for channel %s: the channel was "
                    "already at the %d-pin / %d-character ceiling. They remain "
                    "readable in %s.",
                    count, channel_id, MAX_PINS_PER_CHANNEL,
                    MAX_PIN_CHARS_PER_CHANNEL, source,
                )
            if unusable:
                logger.warning(
                    "Skipped %d legacy pin row(s) that could not become a pin: "
                    "no channel, no author, or no content after sanitising. "
                    "They remain readable in %s.",
                    unusable, source,
                )
        except Exception as exc:
            logger.error("Failed to copy legacy pinned memories from %s: %s", source, exc)

    def _copy_legacy_pins(self, conn, available: set) -> Tuple[int, dict, int]:
        """Admit legacy rows one at a time, under `add_pin`'s ceilings.

        Returns `(copied, {channel_id: dropped_by_a_ceiling}, unusable_rows)`.
        """
        guild_expr = "guild_id" if "guild_id" in available else "NULL"
        message_expr = "message_id" if "message_id" in available else "NULL"

        # substr bounds the read. Everything past MAX_PIN_CHARS is truncated by
        # _sanitise_pin_content anyway, and the pins this migration exists to
        # move are precisely the ones created before any length cap existed:
        # on 2,000 x 100 KB legacy pins, an unbounded fetchall costs +194 MB
        # inside DiscordBot.__init__ against 7.8 MB with the substr (re-measured at
        # 083ebea; this comment and ANALYSIS_CORRECTIONS item 22 both said +11 MB).
        #
        # ORDER BY id, not pinned_at. `pinned_at` is TEXT and carries two
        # formats -- add_pin's isoformat "T" separator and the column default's
        # CURRENT_TIMESTAMP space -- so a same-day default row sorts before
        # every ISO row (0x20 < 0x54). `id` is INTEGER PRIMARY KEY AUTOINCREMENT
        # and therefore is insertion order, with no format variants.
        rows = conn.execute(
            f"""
            SELECT channel_id, {guild_expr}, substr(content, 1, ?), author_name,
                   pinned_by, pinned_at, {message_expr}
            FROM legacy.pinned_messages
            ORDER BY channel_id, id
            """,
            (MAX_PIN_CHARS + 1,),
        ).fetchall()

        state = {}
        copied = 0
        capped = {}
        unusable = 0

        for channel_id, guild_id, content, author_name, pinned_by, pinned_at, message_id in rows:
            # Everything NOT NULL in `pinned_messages` that cannot be
            # synthesised. A legacy view has no NOT NULL of its own, so one bad
            # row would otherwise abort the transaction -- and with the ledger
            # written only on success, retry and abort on every boot forever.
            if channel_id is None or not author_name or not pinned_by:
                unusable += 1
                continue

            cleaned = _sanitise_pin_content(content)
            if not cleaned:
                unusable += 1
                continue

            if channel_id not in state:
                state[channel_id] = list(
                    conn.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0)
                        FROM main.pinned_messages WHERE channel_id = ?
                        """,
                        (channel_id,),
                    ).fetchone()
                )
            count, chars = state[channel_id]

            if (
                count >= MAX_PINS_PER_CHANNEL
                or chars + len(cleaned) > MAX_PIN_CHARS_PER_CHANNEL
            ):
                capped[channel_id] = capped.get(channel_id, 0) + 1
                continue

            # The legacy `id` is deliberately NOT preserved. Carrying it over
            # with INSERT OR IGNORE means a target row that already holds that
            # id silently swallows the legacy pin and still counts as copied --
            # and now that the ledger stays unwritten until there is something
            # to copy, "the target already has rows" is a reachable state. A
            # plain INSERT also means a genuine constraint failure aborts the
            # transaction and is retried next boot, rather than being ignored.
            conn.execute(
                """
                INSERT INTO main.pinned_messages
                    (channel_id, guild_id, content, author_name, pinned_by,
                     pinned_at, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id, guild_id, cleaned, author_name, pinned_by,
                    pinned_at or datetime.utcnow().isoformat(), message_id,
                ),
            )
            state[channel_id] = [count + 1, chars + len(cleaned)]
            copied += 1

        return copied, capped, unusable

    def add_pin(
        self,
        channel_id: int,
        content: str,
        author_name: str,
        pinned_by: str,
        guild_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Pin a message for a channel.

        Returns:
            The pin ID if successful, None otherwise.
        """
        content = _sanitise_pin_content(content)
        if not content:
            logger.info("Refusing to pin empty content for channel %s", channel_id)
            return None

        try:
            with sqlite_transaction(self.db_path) as conn:
                # Enforce the caps with a SQL aggregate, not by counting rows
                # from get_pins().
                #
                # This is deliberate. get_pins() is the obvious thing to reach
                # for, and DAB-073 gives it an optional LIMIT; a cap that
                # counted its rows would then silently admit far more pins than
                # it advertises -- measured at 6x, with add_pin reporting
                # success every time. COUNT(*) cannot be defeated that way, so
                # the two changes stay independent whichever order they land in.
                existing_count, existing_chars = conn.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0)
                    FROM pinned_messages
                    WHERE channel_id = ?
                    """,
                    (channel_id,),
                ).fetchone()

                if existing_count >= MAX_PINS_PER_CHANNEL:
                    logger.info(
                        "Refusing to pin for channel %s: already at the %d-pin limit",
                        channel_id, MAX_PINS_PER_CHANNEL,
                    )
                    return None

                if existing_chars + len(content) > MAX_PIN_CHARS_PER_CHANNEL:
                    logger.info(
                        "Refusing to pin for channel %s: would take pinned text to %d "
                        "characters, past the %d limit",
                        channel_id, existing_chars + len(content),
                        MAX_PIN_CHARS_PER_CHANNEL,
                    )
                    return None

                cursor = conn.execute(
                    """
                    INSERT INTO pinned_messages
                        (channel_id, guild_id, content, author_name, pinned_by, pinned_at, message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        guild_id,
                        content,
                        author_name,
                        pinned_by,
                        datetime.utcnow().isoformat(),
                        message_id,
                    ),
                )
                pin_id = cursor.lastrowid
            logger.info(f"Pinned message #{pin_id} in channel {channel_id}")
            return pin_id
        except Exception as e:
            logger.error(f"Failed to pin message in channel {channel_id}: {e}")
            return None

    def get_pins(self, channel_id: int) -> List[Tuple[int, str, str, str, str]]:
        """
        Get all pinned messages for a channel.

        Returns:
            List of (id, content, author_name, pinned_by, pinned_at) tuples.
        """
        try:
            with sqlite_connection(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT id, content, author_name, pinned_by, pinned_at
                    FROM pinned_messages
                    WHERE channel_id = ?
                    ORDER BY pinned_at ASC
                    """,
                    (channel_id,),
                )
                rows = cursor.fetchall()
            return rows
        except Exception as e:
            logger.error(f"Failed to get pins for channel {channel_id}: {e}")
            return []

    def delete_pin(self, pin_id: int, channel_id: int) -> bool:
        """
        Delete a pinned message by ID (scoped to channel for safety).

        Returns:
            True if a row was deleted, False otherwise.
        """
        try:
            with sqlite_transaction(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM pinned_messages WHERE id = ? AND channel_id = ?",
                    (pin_id, channel_id),
                )
                deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted pin #{pin_id} from channel {channel_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete pin #{pin_id}: {e}")
            return False

    def get_pins_for_prompt(self, channel_id: int) -> Optional[str]:
        """
        Format pinned messages for injection into the AI prompt.

        Returns:
            Formatted string of pinned messages, or None if no pins exist.
        """
        pins = self.get_pins(channel_id)
        if not pins:
            return None

        lines = ["=== PINNED MEMORIES FOR THIS CHANNEL ==="]
        for pin_id, content, author_name, _pinned_by, _pinned_at in pins:
            lines.append(f"[Pin #{pin_id}] {author_name}: {content}")
        lines.append("=== END PINNED MEMORIES ===")
        lines.append(
            "IMPORTANT: The above pinned messages are persistent memories for this channel. "
            "Always keep them in mind when responding."
        )
        return "\n".join(lines)
