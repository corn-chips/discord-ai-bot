# Analysis corrections

Last updated: 2026-07-31 | Repo state: branch `dev`, HEAD `922e899` | Baseline suite: 273 tests,
OK; mutation harness 56/56

Items 1-16 and 21 correct the 2026-07-29 analysis corpus. **Items 17-20 and 22 are different in
kind:** they correct claims made by the remediation programme's own landed commits, found by
reviewing the combined diff before the repository was pushed and, for item 22, by an independent
review of round 2 Phase 3. Where a commit message is wrong, history is not being rewritten for it
— the correction lives here, and where the same false claim also reached a source comment or a
document, that was fixed in place.

**A note on commit hashes.** This history was rewritten once, to scrub absolute paths. Hashes
quoted inside commit messages written before that rewrite may name objects that are no longer
reachable from `dev` — the same failure mode that got `docs/DEEP_BUG_HUNT_REPORT.md` deleted (it
cited a build id no object in this history matches; see `BUG_ANALYSIS_2026-07-29.md` §8.1).
Check with `git merge-base --is-ancestor <sha> HEAD` before trusting a hash you find in a message.

## What this is

The 2026-07-29 analysis corpus (`BUG_ANALYSIS_2026-07-29.md`, `IMPROVEMENT_ANALYSIS_2026-07-29.md`,
`ANALYSIS_BACKLOG.md` and the 37 files in `analysis-tickets/`) is strong, and it already carries its
own refutation section — §7 of the bug analysis knocked down eleven claims and cut back fifteen
more. This file is the next layer: **claims that survived that pass and were nonetheless refuted
when an implementer went to act on them.**

Read this before acting on any ticket. Where this file and a ticket disagree, **this file wins** —
every entry below was established by running something, and the command is given so you can
re-run it.

The corpus's own warning applies to the corpus: *"When reading any finding below S1, assume the
mechanism and check the trigger."* Four of the entries below are cases where the mechanism was
real and the trigger, the blast radius, or the prescribed fix was not.

---

## 1. `DAB-203` does not gate `DAB-194` — the "no test fails" claim is false

**Claimed**, in four places: `analysis-tickets/DAB-203.md`, `analysis-tickets/DAB-194.md`,
`IMPROVEMENT_ANALYSIS_2026-07-29.md` §13 item 18, and `BUG_ANALYSIS_2026-07-29.md` §7.1 — that a
reference-counting dead-code pass would delete the live `/image-queue` slash command and **no test
would fail**, because `test_command_registration._register()` passes
`image_processing_service=None` and therefore pins a degraded 22-command tree.

**Refuted.** The command is pinned twice, in the *image-enabled* tree, by a test that passes
`image_processing_service=object()`:

- `tests/test_command_registration.py:239-240` — the positional slice
  `[command.name for command in bot.tree.get_commands()][5:10]` is asserted equal to
  `["features", "edit-image", "image-queue", "clear-cache", "dev"]`.
- `tests/test_command_registration.py:256` — a second, independent assertion on
  `self._command(bot, "image-queue").description`.

Reproduced by deleting the command registration in a `/tmp` copy of the tree and running the suite:
`FAILED (failures=1)`, the diff being the `[5:10]` slice.

**Root cause of the error.** The claim was derived from `grep image_queue`, which finds only the
Python callback name. The test refers to the command by its *Discord* name, `image-queue`, with a
hyphen. Both spellings have to be searched.

**Consequence.** `DAB-194` was never blocked. It landed as `9894bcc` / `4baa29c` ahead of
`DAB-203`, and `/edit-image` and `/image-queue` both survive (`src/bot/command_modules/general.py`).
`DAB-203` remains worth doing for its other reasons — see item 5 — but it is not a prerequisite for
anything in the dead-code lane.

## 2. `DAB-128` is already live — fixing `DAB-114` does not create it

**Claimed**, in `ANALYSIS_BACKLOG.md` Tier 3 and in `analysis-tickets/DAB-114.md`: that
`fig_height` is unbounded but inert, and *"becomes a live DoS the moment `DAB-114` is fixed"*,
making the two a co-requisite pair.

**Refuted.** The allocation happens *before* the failure, so it is reachable on the unfixed tree:

- `src/services/content_renderer.py:446` — `fig, ax = plt.subplots(figsize=(10, fig_height))`
- `src/services/content_renderer.py:487-489` — the `axhline(..., transform=ax.transAxes)` call that
  is `DAB-114`'s actual bug, guarded by `if i < n - 1`

With `n == 1` the separator line is never reached, so a **single** equation reaches `savefig`
normally. One expression containing 300 `\\` row separators computes `figsize=(10, 135.85)` on the
tree as shipped, with `DAB-114` unfixed — an unbounded height, reached without fixing anything.

> **This entry overstated its own magnitude, and the figure is corrected here.** An earlier
> revision claimed **2,558 MB peak RSS** for that 300-separator input. Re-measured directly:
> **270 MB**, and that input does not even reach `savefig` — mathtext rejects the expression
> first. The unbounded *height* is real and was computed exactly as described; the memory number
> attached to it was not reproducible. Applying this file's own rule to itself: assume the
> mechanism, check the trigger.
>
> The honest magnitude comes from the many-expressions path, measured after `DAB-114` was fixed:
> 400 expressions compute a 280 in figure, `1500x42060` px, **5.9 s and 568 MB resident**, growing
> linearly with the expression count. That is the shape the cap has to stop.

**Consequence.** `DAB-128` is a real unbounded-allocation bug and needed its own fix, not a rider
on `DAB-114`. In the end both landed in one commit, for a reason worth recording: shipping
`DAB-114` alone knowingly unlocks the many-expressions path that makes the bomb *large*, and
shipping the cap alone gives it a vacuous test, because with `DAB-114` unfixed every
multi-expression render returns `None` anyway and the cap is never what refused it. The two are one
logical change. Landed with `MAX_LATEX_FIGURE_HEIGHT_INCHES = 40.0` in `src/constants.py`; over the
cap the renderer degrades to inline code blocks, which is the path every other failure there takes.

## 3. `DAB-141`'s prescribed fix does not work on discord.py 2.7.1

**Claimed**, in `analysis-tickets/DAB-141.md`: apply `@app_commands.default_permissions(...)` to the
`/rag delete` **subcommand**, and verify with a test asserting the decorator took effect.

**Refuted.** discord.py serialises permissions only at the top level of the command tree. A
subcommand's `default_permissions` sets the local attribute — so the ticket's own acceptance test
passes — but is **silently dropped from `to_dict()`**, and the payload Discord receives carries
`default_member_permissions: None`. The gate looks correct in the test and protects nothing in
production. This is the most dangerous kind of wrong fix: it closes the ticket while leaving the
S1 open.

**Consequence.** Gate at the **group** level. A useful side effect: a group-level gate does not
populate `.checks`, so the assertion at `tests/test_command_registration.py:176`
(`self.assertEqual(self._command(bot, path).checks, [])` for `rag status`, `rag backfill`,
`rag delete`) does **not** break. An `app_commands.check` *would* break it — verified both ways.

**What landed** (Phase 2): `default_permissions=Permissions(manage_guild=True)` and
`guild_only=True` on the group, serialising to `32` / `dm_permission: false`, **plus** a runtime
`_has_rag_admin` check inside the `/rag delete` callback. The runtime check is not belt-and-braces:
`default_member_permissions` is a *default* a guild admin can re-grant from the integrations UI,
and Discord does not evaluate it outside a guild at all, so an unrecoverable delete cannot rest on
it alone.

The regression tests assert the **serialised `to_dict()` payload**, never the Python attribute, and
that distinction is the whole lesson here. Applying the ticket's subcommand-level fix to a scratch
copy fails the new tests with *"the /rag group ships no permission gate to Discord"*, while an
attribute-reading assertion passes on exactly the same broken code. `M-DAB141` and `M-DAB141B` pin
both halves.

One correction to this entry's own earlier wording: it claimed `DAB-141` "needs no test churn at
all". It needs a little. `test_rag_delete_scopes_deletion_and_stops_background_work` built a fake
interaction with no `guild` and no `user` — precisely the caller the gate now rejects — so that
fixture gained a privileged member. No assertion changed; only the fixture, and only because the
contract did.

## 4. `DAB-065`'s suggested fix contradicts the same document

**Claimed**, in `BUG_ANALYSIS_2026-07-29.md` §3 (the executive summary of the nine S1s): fix the
tombstone defect by *"adding `deleted_at = NULL` to the `ON CONFLICT` list"*.

**Refuted by §8.2 of the same document**, which records that **removing** that clause *was* the
BUG-0004 fix — the one that stopped paginator edits from resurrecting deliberately deleted
messages. Re-adding it reintroduces BUG-0004.

**Consequence.** Take the path `analysis-tickets/DAB-065.md` offers instead: make `upsert_message`
**raise** on a failed write rather than returning a silent `False`, and have the edit handler
tombstone only on genuine *content* ineligibility. Optionally add an explicit `restore`
/`mark_undeleted` method. Do not touch the `ON CONFLICT` clause.

## 5. The deny-filter hazard is real, but not the one that was documented

The corpus warned that a dead-code pass needs a deny-filter because reference counting cannot see
discord.py's dispatch-by-name, and illustrated that with `/image-queue` — which item 1 shows is
actually protected.

**The underlying warning is still correct, and the real gap is wider.** Deleting three live gateway
handlers — `on_disconnect`, `on_resumed`, `on_raw_bulk_message_delete` — in a `/tmp` copy leaves the
suite at **140 tests, OK** (re-measured at `4fc5063`; it was 125 at `4baa29c`, and the two test
files added since change nothing here). No test references them. `DAB-203` as specified would not have caught
that either, because it pins the *command* tree, not the event-handler surface.

**Consequence.** The rule in `AGENTS.md` — verify reachability by execution, not by grep — stands and
is load-bearing. `9894bcc` was checked specifically against this: it removes no `on_*` handler and
no command decorator. Any future pass needs the same check, and `DAB-203`'s scope should be widened
to cover event handlers if it is ever written.

---

## 6. `DAB-197 -> DAB-198` is reversed, and the published direction was hazardous

`docs/analysis-tickets/DAB-197.md` stated that DAB-197 (delete the PDF PNG round-trip) *blocks*
DAB-198 (clamp page rasterisation), and instructed an implementer to land the deletion first.

**The edge points the other way, and following it would have made the bomb worse.** On the
decompression-bomb fixture MuPDF does not fail — it renders happily. The only thing that stops the
allocation today is Pillow raising `DecompressionBombError`, and it is raised by precisely the
`Image.open` call that DAB-197 removes. Deleting the round-trip before the clamp exists therefore
converts a slow, contained failure into a **successful 256 MP allocation**: strictly worse than the
S1 it was meant to help close.

Landed in the corrected order by `4fb091c` — clamp first, then delete the round-trip. The ticket
now carries the corrected edge inline.

The general lesson matches §1: the corpus's dependency edges were inferred from reading, not from
executing, and a dependency direction asserted without a runtime check can be exactly backwards.
Verify the edge before trusting the ordering, especially where the "blocked" ticket is a safety
guard.

## 7. `DAB-073 -> DAB-150` is a weak edge, and the obvious implementation makes it dangerous

**Claimed**, in `ANALYSIS_BACKLOG.md`'s dependency graph: `DAB-073` must land before `DAB-150`.

**Partly upheld, with a trap underneath.** The edge is real in one direction only: `DAB-150`'s own
acceptance criterion (30 pins at `max_messages=30`, at least 7 retrieved items surviving) cannot be
met without `DAB-073`'s retrieval floor — measured at 5. So `DAB-150` can be *landed* alone but not
*closed* alone.

The trap is in the other direction, and it is the same shape as item 6. `DAB-073` adds a `LIMIT` to
`PinService.get_pins`. The natural way to implement `DAB-150`'s caps is to count the rows
`get_pins` returns — at which point the caps are silently defeated by whatever limit `DAB-073`
chose. Measured with an 8-row limit and a 25-pin cap:

| Order | `add_pin` accepted | rows actually stored | caps |
|---|---|---|---|
| `DAB-150` alone | 10 | 10 | enforced |
| `DAB-073` then `DAB-150`, caps via `get_pins` | 60 | 60 | **bypassed 6x** |
| `DAB-073` then `DAB-150`, caps via SQL `COUNT` | 10 | 10 | enforced |

`add_pin` reports success every time in the bypassed case.

**Consequence.** Unlike item 6 this is not forced by the physics — it is one avoidable
implementation choice. `DAB-150` landed with its caps computed by
`SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0)` inside the existing transaction, which no
`get_pins` limit can affect, so the two tickets are order-independent in practice.
`M-DAB150B` reintroduces the row-counting form and is killed by
`test_the_caps_survive_a_limited_get_pins`.

Two further corrections for whoever lands `DAB-073`:

- It will break `test_reply_anchor_is_not_starved_by_pins` at the **length** assertion (`:217`),
  not the pin-count assertion (`:218`) the ticket predicts — **but only if the floor is put in the
  wrong place.** When `DAB-073` actually landed, that test did not break at all. Putting the floor
  in `_priority_slot_counts` (the packing arithmetic) breaks it; putting it in
  `available_retrieval_slots` (the pre-retrieval budget) does not, and the latter is the correct
  site: the defect is that retrieval is starved *before it runs*, while pin priority *within* an
  assembled pack was never the problem. One prediction, two placements, different outcomes.
- It will also break `test_rerank_boundary_uses_slots_remaining_after_pins`
  (`test_rag_optimization.py:166`), which the ticket does not mention at all: the floor lifts
  `available_slots` from 1 to 2 and short-circuits `reranker_reason` to `within_context_limit`.
  **Confirmed on landing** — this is the only test that broke, and it is a genuine contract
  change. It had encoded the starvation as intended: three pins against a budget of four left one
  retrieval slot, so two candidates competing for it forced a paid Gemini rerank to break the tie.
  Removing that is the point of the fix.
- Its `get_pins` `LIMIT` should be an **opt-in keyword argument used only by the retriever**.
  `/pins` (`personalization.py:191`) is the only delete UI, so an unconditional limit makes older
  pins both invisible and undeletable.

## 8. `DAB-019`'s prescribed requeue expression is wrong three ways, and its test-file premise is false

**Claimed**, in `analysis-tickets/DAB-019.md` line 110, as the fix to apply:

```python
self.pending_messages[cid] = pending_messages + self.pending_messages.get(cid, [])
```

**Refuted.** Requeuing the *popped* list rather than the messages still owed an answer is wrong on
three independent counts, each demonstrated by running the design in a scratch copy:

1. **It duplicates the attachment suffix.** The batch-answering body — `_answer_batch` after this
   ticket, `process_messages` before it — already requeues the post-attachment suffix itself
   *before* the failure window. Requeuing the popped list queues that suffix a second time, and
   because the `finally` clause respawns a worker that re-splits, the duplication compounds every
   pass. Measured on a three-message batch `[A, B(attachment), C]` with a failing rate limiter: the
   suffix `3` is requeued over and over, e.g. `[1, 2, 3, 3, 3, 3, 3, 3, 3, 3]`. The exact sequence
   depends on how many respawn cycles the harness lets run before it stops — an independent
   re-measurement produced `[1,2,3,1,2,3,3,1,2,3,3,3,3,3,3,3,3]` — so treat the compounding as the
   claim and the literal list as illustration.
2. **It re-debits the rate limiter.** The popped list contains every author's messages, so a retry
   re-runs `check_and_record` for users the limiter already cleared. One user, one message, a
   limiter allowing one request: `charges: [2, 2]`, and the retry is then *refused* — so a transient
   Gemini fault is reported to the user as "Text rate limit reached" and the message is dropped
   anyway. It also re-notifies users who were already told "limited".
3. **It re-bills Gemini.** The popped list is still requeued when the failure lands *after*
   `generate_response` returned — during `record_token_usage` (a SQLite write, whose ordinary
   failure mode is "database is locked") or `send_response`. Measured: **3 paid generations for one
   inbound message**, reproduced independently. This is DAB-001 in a second file.

**Consequence.** What landed is an explicit receipt. `_answer_batch` maintains an `owed` list — the
messages this call still has to answer — narrowing it whenever the batch narrows and clearing it
the moment a model call has been *made*. The worker requeues `owed`, never the popped list. A model
call that raises produced nothing and is retryable; one that returns has been billed and is not.
A companion `charged` set means one turn debits each participant once however many attempts it
takes. `M-DAB019B` reintroduces the ticket's expression verbatim and is killed by
`test_the_attachment_suffix_is_not_duplicated_by_a_retry`; `M-DAB019C` and `M-DAB019D` pin the
other two.

Two smaller corrections to the same ticket:

- It says the module "has no test file today" and asks for a new one. There has been a
  `LiveMessageCoordinatorTest` in `tests/test_discord_orchestration.py:33` all along — three tests,
  covering the attachment split, `close()` and the rate-limit filter. A separate file for the
  durability contract is still the right call, but the premise was wrong.
- Its acceptance criterion "the popped batch is re-queued **under the lock**, ahead of anything
  enqueued while it was in flight" is right about the ordering and wrong about the subject. Requeue
  the unanswered subset, not the batch.

The general lesson repeats items 1, 6 and 7: the corpus's *fixes* are inferred from reading, exactly
like its dependency edges, and a prescribed one-liner in a ticket deserves the same execution check
as a prescribed ordering.

## 9. `DAB-165`'s headline trigger is unreachable, and its real one is worse

**Claimed**, in `analysis-tickets/DAB-165.md`: the defect is that a `%` in a log extra *silently
drops the whole record from the log file*, filed S2 on that basis.

**The mechanism is exactly as described and the trigger is not reachable from any current call
site.** `LogRecord.getMessage` only evaluates `self.msg % self.args` when `record.args` is truthy,
so the crash needs a `%`-style call that passes args **and** an extra containing `%`. There are six
`extra=` call sites under `src/`, and every one uses a literal or f-string message with no args.
The `%` case is a latent trap, not a live bug; it is kept as a guard test.

**What is live, on the shipped path, is the doubling** — `DAB-164`, which the ticket treats as a
rider. The reports attribute it to a record passing through two handlers. **One is enough.**
`logging.handlers.RotatingFileHandler.emit` calls `shouldRollover(record)`, which formats the
record to measure it, and then formats it again to write it. Because the old formatter mutated
`record.msg` in place, every extra was written twice on any deployment with file logging on:

```
2026-07-29 21:54:32 | INFO | t | indexed [user_id=7, action=backfill] [user_id=7, action=backfill]
```

captured with a single `RotatingFileHandler` and `enable_console=False`. `M-DAB165` is killed by
`test_the_rotating_file_handler_does_not_double_the_extras` for that reason.

A third consequence neither ticket mentions: the ticket's suggested fix — format first, then append
the extras to the returned string — puts them **after the exception traceback**, because
`logging.Formatter.format` appends `exc_text` last. What landed overrides `formatMessage`, which
runs before the traceback is attached.

## 10. `DAB-166`'s "preferred" option 1 needs a companion change to be safe

**Claimed**, in `analysis-tickets/DAB-166.md`: option 1 — delete the two handler `setLevel` calls in
`setup_logging` — is "structurally better", with the only stated caveat being to check the
performance handler still gets the level it needs.

**Incomplete, and the gap is in the other direction.** `logging.getLogger("performance")` sets its
own level (`INFO`) and never sets `propagate = False`, and `Logger.callHandlers` walks ancestor
*handlers* without consulting ancestor *logger levels*. The root handlers' pin was therefore the
only thing keeping performance records out of `bot.log`. Measured at `log_level: WARNING`, before
and after deleting the pins:

```
[BEFORE] PERF-MARKER in main log: False   root handler levels: [30]
[AFTER]  PERF-MARKER in main log: True    root handler levels: [0]
```

Currently inert — `PerformanceLogger` is only ever constructed as `PerformanceLogger("discord_bot")`
and `PerformanceLogger("gemini_client")`, so the `performance` logger has a handler and no traffic
(that orphan is `DAB-172`). It would become live the moment `DAB-172` is fixed. Option 1 landed
**with** `perf_logger.propagate = False`, and `M-DAB166C` /
`test_performance_records_stay_out_of_the_main_log` keep it that way.

`/config debug` also walks `root_logger.handlers` and clears any that are pinned above the target.
That is redundant against `setup_logging` as it now stands and is deliberately kept: it is the one
place where a stale pin would silently make the command a lie again. It carries no mutant, because
removing it changes nothing observable — which is the point of belt and braces.

## 11. `on_ready` cannot be awaited in a test without `PropertyMock`, and two Phase 2 tests were passing without reaching it

**Not a corpus claim — a defect in this programme's own earlier work, recorded here for the same
reason the corpus records its own.**

`DAB-009`'s ticket says to "await `DiscordBot.on_ready()`" and assert. That does not work.
`discord.Client.user` and `.guilds` are read-only properties with no setter, and `on_ready`
dereferences `self.user.id` on its third line:

```
NAKED on_ready raised:
  File ".../src/bot/discord_bot.py", line 509, in on_ready
AttributeError: 'NoneType' object has no attribute 'id'
```

A test must patch `type(bot).user` and `type(bot).guilds` with `PropertyMock`. With that in place
the coroutine runs to completion, and `DAB-009` reproduces exactly: presence raising leaves
`setup_commands` awaited **0 times**.

**The consequence for Phase 2.** `RegistrarFailureTest.test_live_mode_survives_a_registrar_raising`
and `..._user_preferences_survive_a_registrar_raising` (`tests/test_startup_integrity.py`, landed
in `82b38a3`) wrapped `await self.bot.on_ready()` in `try/except Exception: pass`. They therefore
died at line 509 every run, several hundred lines before the `setup_commands` call they were named
for, and passed on assertions that only needed `DiscordBot.__init__` to have run —
which `StartupServiceAvailabilityTest` already proves. `M-DAB002` was killed by them anyway,
because deleting the eager construction makes those same assertions raise, so the mutant score did
not reveal it.

Both now run `on_ready` to completion on the shared `OnReadyHarness` and assert
`setup_commands.assert_awaited_once()` as well, so the registrar really does explode where the
test says it does.

**Two side effects the harness has to suppress.** A completed `on_ready` starts the report web
server, which binds `127.0.0.1:8080` and is never stopped — the first test to run held the socket
for the rest of the process and the next one failed to bind. It also starts the image processing
service. Both are set to `None` in the harness.

The lesson is discipline 3's, applied to a test rather than to code: a test that swallows the
exception it provokes cannot tell you where it stopped. `try/except Exception: pass` around the
subject of a test is a smell worth grepping for.

## 12. Three defects the Phase 3b review found in Phase 3b

Recorded here for the same reason as item 11: the programme's own work gets the same treatment as
the corpus.

**a. `asyncio.CancelledError` is a `BaseException`, so DAB-019's fix missed the shutdown path.**
The worker's `except Exception` cannot see a cancellation, and `close()` cancels in-flight workers
on every shutdown — so the receipt was never requeued, the attempt counter never advanced, and not
one log line was written. That is DAB-019's exact symptom, reintroduced by the commit that closed
it. The `finally` now checks the receipt: requeue if the channel is still live, log the discard at
WARNING if it is not. `M-DAB019F` pins it.

**b. Emptying the receipt after `generate_response` bought the no-duplicate half and dropped the
liveness half.** A failure in `record_token_usage` or `send_response` left `owed` empty, so
`_abandon_batch` never fired: Gemini was billed, no reply arrived, and the user was told nothing.
The post-payment section is now wrapped — it never retries, and it always notifies. `M-DAB019G`
pins it.

**c. The two tests that were supposed to catch (b) could not.** They asserted only
`generate.await_count == 1` and an empty queue, which the *original, unfixed* implementation —
pop, log, drop — satisfies exactly. They were pure no-duplicate tests with no liveness half. This
is discipline 3 in its purest form: a test written alongside a fix inherits the fix's framing, and
"did it avoid doing the bad thing" is not the same assertion as "did it do the right thing".

Two smaller ones from the same review, fixed in the same commit:

- **A leading status code is only a status code when a reason phrase follows it.** DAB-040's
  anchored matcher allowed a bare `^`, so `"500 tokens over budget"` — a message that merely
  *starts* with the digits — was still classified as a retryable transport failure. The matcher now
  requires either an introducer (`http`/`status`/`code`/`error`) or a capitalised reason phrase
  after the code, which is how both HTTP and the SDK word one. That needs the original casing, so
  it takes `str(error)` rather than the lower-cased copy the rest of the chain matches on.
- **A stringified 401/403 still fell through.** The structured pass only sees live `google.genai`
  error objects; anything that has been through a log-and-rethrow or an `Exception(str(e))` wrapper
  has no `.code` left. 404 had a string path and 401/403 did not, which was an asymmetry with no
  reason behind it. Both now do.

One consequence worth recording: with the string chain carrying 401/403/404, `M-DAB040B` stopped
discriminating — deleting the structured pass no longer changed any classification, because
`APIError.__str__` always leads with the code. It was retargeted at the case only the code can
settle: a 5xx whose details mention a timeout, where the substring chain reaches `"timeout"` first
and answers `TIMEOUT` instead of `SERVICE_UNAVAILABLE`. A mutant that stops discriminating is a
signal, not a nuisance.

## 13. `DAB-212` is refuted, not deferred: applied on top of `DAB-078` it makes the query it targets 111x slower

**Claimed**, in `analysis-tickets/DAB-212.md`: rewriting `get_pending_embeddings`'s
`ORDER BY m.created_at DESC` to `ORDER BY e.message_id DESC` removes a temp B-tree and is worth
**1844x**, with "no reason for it to wait behind anything".

**Refuted twice over.**

First, the measurement is against a query the bot never issues. `get_pending_embeddings` is reached
only through `HybridContextRetriever._drain_pending_documents`, whose signature is
`(*, channel_id: int)` — a **required** keyword — and both call sites pass a real channel id. Every
production poll is channel-scoped; the unscoped form is dead code. The ticket's 1844x, and my own
first re-measurement of it, were both taken on the unscoped variant.

Second, on the query that *is* issued, `DAB-078`'s index inverts the result. Measured at 100k rows
against the real service SQL:

| `DAB-078` index | `ORDER BY m.created_at DESC` | `ORDER BY e.message_id DESC` |
|---|---|---|
| absent | 38.757 ms, temp B-tree | 0.066 ms, no B-tree |
| **present** | **0.078 ms, no B-tree** | **8.650 ms, temp B-tree** |

With the index present the planner drives from `message_index` and the existing `ORDER BY` is
already free. `DAB-212`'s rewrite forces the join back the other way and **reintroduces the temp
B-tree it exists to remove**, at 111x the cost — violating its own acceptance criterion.

**Consequence.** `DAB-078` alone delivers the win on that query (38.4 → 0.070 ms, 549x) as a side
effect of an index added for `search_recent`. `DAB-212` was **not applied**, and `M-DAB212` applies
it so that nobody re-applies it from the ticket.

Two further corrections to the same ticket. Its behavioural caveat — "bot responses are indexed
with `created_at=datetime.now()`" — is wrong; the live caller passes Discord's own
`sent_message.created_at` and `datetime.now()` is only a fallback. And its premise that
`ORDER BY e.message_id DESC` is "semantically identical" does not hold in general:
`created_at` is a TEXT column sorted as a string, so mixed offsets already sort wrongly
(`2026-01-01T12:00:00-05:00` sorts before `2026-01-01T13:00:00+00:00` although it is later).
`tests/test_rag_query_plans.py` fixes a fixture where recency order and id order disagree, and the
rewrite would return that batch backwards.

**The lesson is discipline 2 applied to a benchmark.** I verified the `DAB-212 ↔ DAB-078` edge by
execution and still got it wrong, because I executed a paraphrase of the query instead of the query.
The same mistake then appeared in the test: `tests/test_rag_query_plans.py` originally explained SQL
copied into the test file, so `M-DAB212` **survived** — the assertions could not see a mutation of
the service's own `ORDER BY`. The test now captures the executed statement with
`Connection.set_trace_callback` and explains that. A plan assertion against a copy of the query is
a proxy, not an end state.

## 14. `DAB-095`'s WAL half is deferred, and its headline payoff needs a precondition the ticket omits

**Claimed**, in `analysis-tickets/DAB-095.md`: set `journal_mode=WAL` plus an explicit busy timeout;
payoff "a contended read **fails after 5.01 s -> succeeds in 3.3 ms**", and the change **blocks**
`DAB-065` and `DAB-096` because "the pragmas remove the contention".

**Three corrections, all measured.**

**a. WAL does nothing for `DAB-065`.** The tombstone is produced by a failed *write*. Driving the
real `upsert_message` under a six-second `BEGIN EXCLUSIVE`:

| journal | busy timeout | `upsert_message` | elapsed |
|---|---|---|---|
| delete | 5000 ms | `False` | 5009 ms |
| delete | 15000 ms | `True` | 6036 ms |
| wal | 5000 ms | `False` | 5007 ms |
| wal | 15000 ms | `True` | 6037 ms |

WAL is irrelevant in all four cells; the timeout is the entire effect, and it only moves the cliff.
The edge is soft, and `DAB-065`'s own risk note already said the semantics fix is required
regardless.

**b. The headline read figure reproduces only against an EXCLUSIVE lock.** Against `BEGIN
IMMEDIATE` a contended read succeeds in **0.1 ms in both journal modes**; against `BEGIN EXCLUSIVE`
it fails at **5005.9 ms** on a rollback journal and succeeds in **0.1 ms** on WAL. The ticket's
severity line and its own "honest counter-evidence" paragraph contradict each other, and both are
right — under different lock modes. Quote the lock mode with the number.

**c. WAL costs more than it buys *here*, and the ticket says so in one line before recommending it
anyway.** On this per-call-connection architecture, connect+query+close measures **0.230 ms today,
0.573 ms with the pragmas** — and a database already in WAL mode costs the same with the pragmas
removed, so the cost is WAL's *connect*, paid on every SQLite call the bot makes, including
`get_live_enabled` on every inbound message.

**What landed:** the busy timeout only, as `bot.sqlite_busy_timeout_ms`, defaulting to **5000 ms** —
the value Python's `sqlite3.connect` was already applying. That is deliberate and is the actual
defect the ticket describes: *"there is a busy timeout... the defect is that it is not a stated
contract and cannot be tuned."* Raising it to the ticket's 15000/30000 was rejected because it
trades a failed write for a proportionally longer **event-loop stall** — most SQLite calls still run
on the loop (`DAB-096`) — and with `DAB-065` fixed a failed write is now benign.

**Reopen condition for WAL: connection pooling or long-lived connections.** That is when the
per-call connect disappears and WAL's reader-concurrency win (finding b) arrives free.
`test_the_journal_mode_is_still_the_default` pins the deferral so re-adding it is a deliberate act.

One smaller correction, against my own first attempt rather than the ticket: an explicit
`PRAGMA busy_timeout` was written alongside `connect(timeout=)` on the grounds that only the pragma
can be read back. That is false — CPython's `sqlite3` turns `timeout=` into exactly that pragma
(`connect(timeout=1.234)` reads back `1234`). The line changed nothing observable and its mutant
survived, which is the correct verdict on a line that restates the one above it. Removed.

## 15. `DAB-065`'s prescribed repair does not repair anything, and the test it predicted would invert does not

Item 4 already recorded that the executive summary's fix — add `deleted_at = NULL` to the
`ON CONFLICT` list — reintroduces BUG-0004. Two further findings, both measured, and the second one
corrects an expectation this programme was handed.

**a. Clearing `deleted_at` recovers nothing on its own.** `mark_deleted` sets `hidden = 1` as well,
and `hidden` independently removes the row from FTS and from all three retrieval paths. Driving the
real service:

```
after mark_deleted        hidden=1 deleted_at=SET  fts=0 recent=[] lexical=[] by_id=[] pending=[]
deleted_at = NULL only    hidden=1 deleted_at=NULL fts=0 recent=[] lexical=[] by_id=[] pending=[]
+ hidden = 0              hidden=0 deleted_at=NULL fts=0 recent=[4242] lexical=[]  by_id=[4242]
+ a later re-index        hidden=0 deleted_at=NULL fts=1 recent=[4242] lexical=[4242] by_id=[4242]
```

So the summary's one-line fix is wrong twice over: it reintroduces BUG-0004 *and* it would not have
restored the message. A `restore`/`mark_undeleted` method would have needed to clear both flags,
re-insert the FTS row, and reset `embedding_status` from `skipped` back to `pending` — four things,
where the ticket names one — and to run *before* the upsert rather than after it.

**What landed avoids all of that by not creating the tombstone in the first place.**
`upsert_message` raises `MessageIndexWriteError` on a failed write; `False` keeps its existing
meaning of "this content is not indexable"; and `handle_message_edit` tombstones only on `False`.
No new column, no `restore`, no `ON CONFLICT` change, no repair path — because there is nothing
left to repair.

**b. `test_stale_upsert_does_not_resurrect_deleted_message` does NOT invert.** The Phase 4 brief
predicted it would, and instructed that the inversion be justified as a real contract change. It
does not invert, and the reason is worth stating: that test asserts that a *stale edit event* must
not resurrect a deliberately deleted message, and the fix never touches that path. A deliberate
`mark_deleted` still wins, and a later `upsert_message` still leaves `deleted_at` set. The
prediction was sound for the `ON CONFLICT` fix and does not apply to this one.
`test_ineligible_edit_marks_existing_index_row_deleted` is likewise untouched: genuine
ineligibility still tombstones, and there is a test asserting exactly that so the fix cannot drift
into "never tombstone".

**Acceptance criteria not taken, and why.** The ticket also asks for jittered retries inside
`upsert_message` before surfacing. Not done. `backfill_channel` already resumes from a durable
cursor — verified: raising on message 3 of 10 leaves the cursor at the last success with
`completed_at` NULL, so the next pass continues correctly and loses nothing — and `on_message`
absorbs and logs. With the tombstone gone, a failed write is benign, so a retry would be an
optimisation rather than a correctness fix. The `hidden`/`deleted_at` overloading in `mark_deleted`
is likewise still there; it is now inert for this defect but remains a latent trap.

## 16. `DAB-066` is a latent-trap guard, and its prescribed fix would have been worse than the bug

**Precondition verified unreachable, by execution rather than by reading.** The `message_index`
NOT NULL column set is byte-identical (md5 `e98a0ad7`) across **all nine** revisions that have ever
touched the file — the corpus checked seven and found the same. Six of those nine columns have no
default. Nothing in this history can produce the drift the defect needs, so the S1 rating is
insurance against the next schema change, not a description of a live fault.

**The prescribed fix converts silent data loss into a permanent boot loop.** The acceptance
criteria ask for `MigrationError` and "refuse to run", with the ledger left unwritten so it
"retries next boot". But `_migrate_legacy_database` re-raises, `MessageIndexService(...)` is
constructed **unguarded** at `discord_bot.py`, and `legacy_db_path=config.token_db_path` is always
set — so the raise aborts `DiscordBot.__init__`. Schema drift is deterministic, not transient, so
it would abort **every** boot, forever, with no operator escape. That is `DAB-067`, which the
ticket never mentions. Today's behaviour at least starts the bot.

**What landed** counts rows before and after each table copy, and on a shortfall logs at ERROR,
leaves the ledger row unwritten, and **returns**. The migration is retried on the next start once
the cause is fixed; the bot boots either way. `M-DAB066B` reintroduces the raise and is killed by a
test that asserts the service is still usable afterwards.

**Not taken:** the declared column map from the `I2-03` design. `message_retrieval_events` has an
AUTOINCREMENT primary key and eight `_ensure_column`-added columns, so a hand-declared map there is
more brittle than the intersection it replaces, and a NOT NULL-with-default column would newly
raise where it previously worked. The count assertion closes the "0 rows + ledger written"
criterion without that risk. `DAB-068` — the sibling pin migration with the opposite bug — was
**not** fixed here; it closed in round 2 Phase 3, along with a second defect in the same method
that no finding had named: the copy bypassed every ceiling `add_pin` enforces. See the DAB-068
bullet in [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md).

## 17. `b9a4514`'s central premise is false: slash commands hit no rate limiter at all

**Claimed**, in the commit message of `b9a4514` "Put a per-user ceiling on the two commands that
fan out" (`f8baf56` before the path scrub rewrote this history; identical message), and in three
source comments it added:

> `/deepresearch` and `/summarize` were governed only by the general text rate limit of 60 an
> hour, which is not a meaningful ceiling on either.

**Measured** at `922e899`. `TextRateLimiter.check_and_record` has exactly **one** call site
against `self.text_rate_limiter`: `discord_bot.py:882`, inside `on_message`, after the mention
gate. The same instance is injected into `LiveMessageCoordinator` at `discord_bot.py:172` and
debited by the live worker at `live_message_coordinator.py:420`. Both paths are message paths.
**No slash command touches it** — before `b9a4514` there was no `check_and_record` call anywhere
under `src/bot/command_modules/`, and the only one there now is the `expensive_command_limiter`
that this very commit introduced.

```
$ grep -rn "check_and_record" --include=*.py src/
src/bot/command_modules/research.py:148   # added by b9a4514 itself
src/bot/live_message_coordinator.py:420   # self.rate_limiter, injected from text_rate_limiter
src/bot/discord_bot.py:882                # on_message
src/services/rate_limiter.py:17           # the definition
```

`/edit-image` and `/image-queue` do consult a per-user quota (`general.py:168`, `:288`), but that
is `image_processing_service`'s own counter, not the text limiter, and it does not reach
`/deepresearch` or `/summarize`.

**So the premise understates the hole it closes.** Those two commands were governed by **60 an
hour**, per the commit; they were governed by **nothing**. The commit's own worst-case arithmetic
(5.13e9 tokens/h across both commands) was computed *from* the 60/h figure, so the real
pre-`b9a4514` worst case is not 5.13e9 tokens/h — it is bounded only by Discord's interaction
rate, and is therefore larger. The fix is correct and the direction is unchanged; only the
"before" number and the stated baseline are wrong. **The 2.42e7 tokens/h "after" figure stands**,
because it is computed from the new limiter's own 1/min and 4/h settings.

**What was done about it.** History is not being rewritten for a commit message. The three source
comments that carried the same false claim into the code were corrected in place, because a
comment is read as a statement about the current system:

- `config.yaml:164-169` — the `expensive_command_limit_*` block header.
- `src/bot/discord_bot.py:447-452` — above the `expensive_command_limiter` construction.
- `src/bot/command_modules/research.py:128-133` — `_expensive_command_allowed`'s docstring.

No behaviour changed; the limits, the call sites and `M-DAB213` are untouched. `docs`-side, the
`DAB-213` row in `ANALYSIS_BACKLOG.md` and the ticket header now carry the correction.

The review that found this counted further, smaller inaccuracies across the 34 commit messages —
mostly restated figures and hashes that the rewrite moved. Only this one changes what a reader
would believe about how the code works, and only this one reached the source, so only this one is
reproduced here.

## 18. `DAB-078`'s stated justification is false: three call paths lose the index

**Claimed**, in `15fc69a` and inline at `message_index_service.py:174-179`: the composite
`idx_message_index_scope_time (guild_id, channel_id, created_at DESC)` "serves no query the two
new ones do not", so it is dropped rather than kept alongside them.

**Measured.** The two replacements are **partial** indexes, predicated on
`WHERE hidden = 0 AND deleted_at IS NULL`. Three call paths deliberately touch hidden and
tombstoned rows and so cannot use them: `get_status` (`:1599`, `/rag status` must count hidden
messages), `delete_rag_data(channel_id=...)` (`:1082`, `/rag delete scope:channel` must remove
tombstones or orphan them), and the bare channel `COUNT(*)` both issue first. On a synthetic
100k-row index (20 channels, 5% hidden, 5% tombstoned), median of 7:

| Path | Composite | Partial only | |
|---|---|---|---|
| channel `COUNT(*)` | 4.97 ms | 11.69 ms | 2.35x slower; `COVERING INDEX` -> bare `SCAN` |
| `/rag status`, channel-scoped | 26.43 ms | 32.88 ms | 1.24x slower |
| `/rag delete scope:channel` | 113.9 ms | 139.4 ms | 1.22x slower |
| `search_recent`, channel-scoped | 21.36 ms | 0.032 ms | **674x faster** |

**The trade is still correct** and nothing should be reverted: three admin paths a few
milliseconds slower buys 674x on a path that runs per message. The commit's read plans and its
+15%-write / +0.1 MB cost all reproduce. What is wrong is the reason recorded next to the code,
and it is the kind of wrong that costs somebody later: the next person to touch these indexes
will read "serves no query the two new ones do not" and not think to check the tombstone paths.
Full detail as `PPR-01` in [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md).

## 19. `DAB-066`'s landed fix is not idempotent: one shortfall makes every later boot re-run it

**Claimed**, by `922e899`'s ERROR message and by the inline comment at
`message_index_service.py:786-796`: on a shortfall the ledger is left unwritten "so it will be
retried on the next start once the cause is fixed".

**Measured.** The retry cannot succeed for any table that already copied. `copied` is
`after_count - before` (`:755`) and is compared against `expected`, the legacy row count. On the
second boot the rows are already in `main`, `INSERT OR IGNORE` copies nothing, and the comparison
is `0 != N` — a shortfall, on a table holding 100% of its rows. The ledger is never written and
every boot re-runs the ATTACH, the copy, the eligibility reconcile and the full FTS rebuild,
because the `if shortfalls:` gate (`:785`) sits *after* them.

Reproduced by driving the service into the state a shortfall leaves behind, 20,000 rows in each
of `message_index` and `message_embeddings`, median of 7 boots: **134.1 ms** with the ledger
present against **519.8 ms** without, i.e. **+385.8 ms per boot (3.9x)**, indefinitely. After 7
boots the ledger row was still absent and the target still held exactly 20,000 rows. Each boot
logged `expected 20000 rows, copied 0 ... This usually means the legacy schema lacks a column this
version declares NOT NULL`, which by then is false and sends the operator after a schema that is
already correct.

This does not make the DAB-066 fix wrong — recording a short copy as a success was strictly
worse, and the prescribed alternative (raise) would have been worse still (item 16). It makes the
recovery story wrong. Compare `after_count` against `expected + before`, or count only the rows
genuinely absent from `main`. Full detail as `PPR-02` in
[`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md).

## 20. The security-residuals report's own recommendation is refuted on both halves

**Not a corpus claim.** Before Phase 2.3 the seven open security findings were re-verified at
`5552724`, each against a real HTTP response, a real SQLite row or an executed callback. That
verification is far better evidenced than the tickets it re-checks and its findings all hold. Its
two *recommendations* for the report web server do not, and both failed only when someone tried to
implement them. Recorded here for the same reason as items 11, 12 and 17: this programme's own
work gets the corpus's treatment. (The report itself is a working note that lives outside the
checkout, so nothing below rests on it -- every counter-claim was re-measured here and the command
is given.)

**a. "Refusing a non-loopback bind belongs in config validation, not in `ReportWebServer.start`",**
on the grounds that `validate_config` "fails the boot loudly, whereas a check inside `start()` is
swallowed by the `except Exception` at `discord_bot.py:546` and degrades to a log line". Both
halves of that are true. The conclusion does not follow, for a reason of proportion rather than
mechanism.

`validate_config`'s errors reach `load_and_validate_config`, which calls `sys.exit(1)`
(verified at `config.py:267`, via `BotConfig.validate`). So the rule would not fail one feature,
it would stop the bot -- every message, every command -- over the address of an auxiliary web
page, deterministically, on every start until someone edits the file. That is item 16's shape
without item 16's excuse: there the trapped operator had no escape at all, here they do, and the
disproportion is the objection on its own.

The distinction from the rules already there is worth stating, because `config_helpers.py:514`
does fail the boot on an empty `reports.web_host` and that is correct. An empty host is a value
that cannot work; `0.0.0.0` is a value that works perfectly and is merely unsafe. Refusing to
start over the first is a typo check. Refusing to start over the second is a policy disagreement,
and a policy disagreement should cost the operator the feature it is about.

The report's actual objection -- that a socket-level refusal degrades to a log line -- is answered
rather than dismissed: the refusal logs at ERROR with the remedy in the message, and `on_ready`'s
status line went from a two-state `Active`/`Disabled`, where a refusal was indistinguishable from
the operator having switched the feature off, to the three-state wording `_get_service_status`
already used further down the same file.

**b. "Origin/Referer check on POST -- reject a state-changing request whose `Origin` is not the
server's own", ~6 lines of middleware.** Implemented literally, that is bypassed three ways, each
measured against a real server with a real SQLite row read back afterwards:

| Request shape | Against the recommended check | Why |
|---|---|---|
| `Host: evil.example:P` + `Origin: http://evil.example:P` | **303, row mutated** | DNS rebinding. The two headers agree with each other, so comparing them to each other passes. Needs a loopback allowlist on `Host`, answering 421 |
| no `Origin`, no `Referer` | **303, row mutated** | "Check it when present" is the published form of this defence and it is optional for anything that is not a browser |
| `Origin: http://127.0.0.1:P/`, `.../path`, `HTTP://...`, embedded tab | **303, row mutated** | A netloc comparison accepts six shapes the Fetch ABNF forbids |

Two consequences worth carrying forward. The `Referer` fallback is **not** needed and was dropped:
per Fetch Standard §3.2 the `Origin` append is unconditional for a method that is not `GET`/`HEAD`,
and a suppressing referrer policy sets the value to the literal `null` rather than omitting the
header, so a browser form always carries one and the fallback only widens the surface. And the
comparison must be against the `Host` the client used rather than the configured `web_host`,
because an operator reaching the page at `localhost` or through a port forward on another local
port must not have their own form refused -- but that is only safe *with* the allowlist, which is
what stops the same leniency becoming the rebinding hole in row 1.

**c. One thing the report does not mention at all**, and the Origin check cannot see by
construction: there was no `X-Frame-Options` or CSP, so a framed copy of the page submits its own
form and the `Origin` is genuinely correct. Three response headers, added in the same middleware.
`Referrer-Policy: no-referrer` is the tempting fourth and is deliberately **absent**: by the same
§3.2 it would make this page's own form send `Origin: null` and the server would then 403 every
save. That trap is pinned by a test.

## 21. `DAB-052`'s valid-value set contains a name the SDK does not define, and half of it targets a function that does not exist

Two errors in the same finding, both settled by running the SDK and grepping the tree.

**a. `BLOCK_HIGH_AND_ABOVE` is not a Gemini threshold.**
`IMPROVEMENT_ANALYSIS_2026-07-29.md:2210` gives the permitted set as
`{BLOCK_NONE, BLOCK_LOW_AND_ABOVE, BLOCK_MEDIUM_AND_ABOVE, BLOCK_HIGH_AND_ABOVE, BLOCK_ONLY_HIGH, OFF}`
and prescribes validating against it. Five of those six are right. `BLOCK_HIGH_AND_ABOVE` is not a
member of `types.HarmBlockThreshold` and never has been -- it was invented by this repo's own
lookup table -- and the set omits `HARM_BLOCK_THRESHOLD_UNSPECIFIED`, which is. Executed against
google-genai 2.11.0:

```
[m.name for m in types.HarmBlockThreshold]
['HARM_BLOCK_THRESHOLD_UNSPECIFIED', 'BLOCK_LOW_AND_ABOVE', 'BLOCK_MEDIUM_AND_ABOVE',
 'BLOCK_ONLY_HIGH', 'BLOCK_NONE', 'OFF']
```

Validating against the published set would have rejected a legal config and accepted an illegal
one. What landed validates against the enum's real names and keeps `BLOCK_HIGH_AND_ABOVE` as an
explicit **alias** rather than as a valid name. The reason is not that it was the only threshold
that worked -- `BLOCK_LOW_AND_ABOVE` and `BLOCK_MEDIUM_AND_ABOVE` were in the old map too and were
honoured correctly. It is that `config.yaml`'s own comment read
`# Options: BLOCK_NONE, BLOCK_LOW_AND_ABOVE, BLOCK_MEDIUM_AND_ABOVE, BLOCK_HIGH_AND_ABOVE`,
advertising the invented name and listing neither `BLOCK_ONLY_HIGH` nor `OFF`. An operator who
followed the shipped documentation to ask for the strictest-but-one setting wrote the one name
that does not exist, and it worked. Removing it would turn their working config into a boot
error.
`tests/test_safety_thresholds.py` asserts the declared set equals the SDK enum exactly, so the
next SDK release is a test failure rather than a silent divergence.

**b. The "unknown category" half was accurate when written, and this programme deleted its
subject.** The entry states that "`get_safety_threshold()` even `.get(category, 'BLOCK_NONE')`
-defaults an unknown *category* to the most permissive value". Grepping the tree today finds no
such function, and an earlier revision of this item said so and called the corpus wrong. **That
correction was itself wrong**, and is retracted here rather than quietly edited, because it is the
exact failure this file exists to catch. At the corpus's own baseline the method was there:

```
$ git show c83f740:src/config.py | sed -n '236,244p'
    def get_safety_threshold(self, category: str) -> str:
        ...
        return mapping.get(category, 'BLOCK_NONE')
```

It was removed by `9894bcc`, this programme's dead-code pass -- and the corpus had already
identified it as dead (`IMPROVEMENT_ANALYSIS_2026-07-29.md:630`), which is how it came to be
deleted. So the category half is closed, by deletion rather than by repair, and no live code ever
looked a category up by name: `safety_mapping` is four literal entries iterated with `.values()`.
The permissive default that survived to Phase 2.4 was the one on the **threshold**.

The lesson is the one item 1 records, in the other direction: a claim checked against the *current*
tree says nothing about a corpus written against a different commit. `git show <baseline>:<file>`
before calling a finding imaginary.

The corpus's own severity assessment of this finding is sound and worth repeating, because it is
what keeps the fix honest: `config.yaml` ships `BLOCK_NONE` for all four categories and the system
prompts instruct the model to apply no content restrictions, so a typo changed nothing relative to
the shipped posture. **The victim is the operator who deliberately tightens safety, mistypes or
uses the SDK's own name, and is told nothing.** That is why the shipped default was left exactly
as it is: changing it is a product decision, and it is not one a lookup-table repair gets to make.

## 22. Seven figures in round 2 Phase 3's own commit messages do not reproduce

**Not a corpus claim — this programme's own work, corrected here for the same reason as items 11,
12, 17 and 20.** An independent review of `0fb7016`, `56bd52c` and `a55c601` re-ran every
quantitative claim in them. Eight reproduced exactly; seven did not. History is not being
rewritten for a commit message, so the corrections live here, and where the same figure reached a
source comment or a document it was fixed in place.

| Claim | As committed | Re-measured | Why it was wrong |
|---|---|---|---|
| `/pins` footer overflow | "5,996 → **6,017**" | **6,010** | 6,017 was measured against a `"Showing 25 of 25 pins"` footer that the fix does not ship. The shipped complete-listing footer was `"Showing all 25"`, 14 characters — and it has since been removed entirely, because at that exact shape *any* footer is the difference between 25 pins and 24 |
| the footer guard's coverage | "5,985 shapes, **53** go over" | **not reproducible** | The sweep was defined nowhere, and two attempts to reconstruct it gave 107 and 0. The figure is withdrawn rather than restated. What *is* reproducible is the guard itself: run `scripts/mutation_check.py M-PPR06D` and **18** of the 122 shapes it sweeps go over, up to 6,046 |
| that guard's band | "holds **nine**" | **18** | Nine is the count at 25 pins alone; the sweep covers 25 **and** 26 |
| clear-and-rebuild damage | "22 commands to **6**" | **5** | 6 is `register_admin_commands`; 5 is `register_feature_commands`, which is the registrar the test beside that comment actually patches |
| the reconnect sync payload | "**8,695** bytes" | **8,048** | 8,695 is `json.dumps` with default separators; discord.py serialises compact. (`json.dumps` defaults give 8,713 here, so even the loose reading does not match) |
| unbounded `fetchall` | "**+192 MB** against +11 MB" | **+194 MB** against +11 MB | Within noise across three runs (191, 192, 194) — the claim stands, the number is restated |
| the pre-fix pin fixture | "61 rows, **300,871** characters, a 5,014-character pin" | reproduces **only with the fixture stated** | 61 pins of `"legacy pin {i} " + "y"*5000` plus one delimiter pin. A re-creation using plain 5,000-character pins gives 300,059. The fixture is not in the tree, so the figure needs it quoted |

**The lesson is discipline 5 applied to a figure rather than to a payoff.** Every one of the seven
was measured on a *prototype* of the fix and then not re-measured after the fix changed shape —
which is exactly the failure this file's summary table attributes to the corpus. The footer figure
is the sharpest case: the fix that produced 6,017 was superseded twice before it landed, and the
number travelled unchanged through both.

## The pattern across the corpus's measured claims

Collected in one place because the individual corrections above make each look like a one-off, and
it is not one. **This corpus measured prototypes in isolation and inferred dependency edges by
reading. Both failure modes produce confident, specific, wrong numbers.**

| Claim | As published | Re-measured | What went wrong |
|---|---|---|---|
| `DAB-212` ORDER BY rewrite | 1844x (127.666 → 0.069 ms) | **Refuted, and inverted: 111x slower** on the real query once `DAB-078` lands | Benchmarked a query production never issues — the scope argument is a required keyword |
| `DAB-078` scope index | 1200x channel | **924x**, and the composite it adds beside is better dropped | Prototype used a hand-copied schema and a different corpus shape |
| `DAB-095` WAL | "5.01 s failure → 3.3 ms" and "removes the contention behind `DAB-065`" | Reproduces **only against `BEGIN EXCLUSIVE`**; **zero** effect on the `DAB-065` trigger in all four lock cells; costs **+0.34 ms per call** here | Measured a read against one lock mode, then generalised to writes; ignored the per-call-connection architecture, which the ticket itself names in one line |
| `DAB-096` per-call cost | 0.258 ms inline → 0.089 ms offloaded | **0.144 → 0.225 ms** — offloading is *slower* | The published figure was a *pooled* connection vs a per-call one; a plain `to_thread` is a different change |
| `DAB-077` reconcile | "1938 ms → 0.034 ms (57,000x)" | Ratio is against a no-op; ~25 ms at this deployment's size | Ratio against zero work is unbounded and says nothing |
| `DAB-128` LaTeX bomb | 2,558 MB peak RSS | **270 MB**, and that input never reaches `savefig` | Memory figure was not reproducible; the mechanism was real |
| `DAB-065` fix | "add `deleted_at = NULL` to `ON CONFLICT`" | Reintroduces BUG-0004 **and does not restore the message** — `hidden` is set too | Fix inferred from reading one statement, never executed |
| `DAB-019` fix | requeue the popped batch | Duplicates the attachment suffix, re-debits the limiter, **re-bills Gemini 3x** | Same: a plausible one-liner, never run |

Two rules follow, and they are the ones this file keeps re-deriving:

1. **Benchmark the call path, not the query.** Find the production caller and its arguments first.
   `DAB-212` and `DAB-095` both failed on this, and so did my own first verification of each.
2. **A prescribed fix deserves the same execution check as a prescribed ordering.** Four of the
   eight rows above are fixes, not measurements. Items 4, 6, 8 and 15 are all the same shape.

## Restated figures

| Claim | As published | Corrected | Why |
|---|---|---|---|
| `DAB-077` startup reconcile | "1938 ms -> 0.034 ms (57,000x)" | ~25 ms at 2k rows, ~120 ms at 10k, 1938 ms at 100k | The ratio is against a no-op, so it is unbounded and says nothing about the saving. The 1938 ms reproduces, but only at 100k indexed messages; this deployment is two orders of magnitude smaller. Quote the absolute saving at your corpus size. |
| `DAB-106` validation gaps | "44-47 of 100 config fields" | **40-43 of 96** | `9894bcc` deleted four dead `BotConfig` fields, three of which were on the unvalidated list. |
| `DAB-194` dead code | "-2,027 lines, 23,973 -> 21,946" | delivered **2,111 deletions** across 25 files, 23,973 -> 21,895 | Landed as `9894bcc`; the extra came from 96 unused import bindings rather than the predicted 87. |
| `docs/tech-debt-register.md` TD-004 | "`README.md` still lists `BOT_SYSTEM_REPORT.md`, `pipeline.html` and `message-sequence-flowchart.html` at the repo root" | Already repaired | `README.md:234` now states the report lives in `docs/` and that neither diagram exists. `find . -name '*.html'` returns nothing. The TD-004 retraction was itself stale. |

## A near-miss worth recording

`config.yaml`'s orphaned `system_prompts.thinking_mode_addon` block was described in one working
note as occupying lines **205-212**. It occupies **206-212**. Line 205 is the `system_prompts:`
parent key, and deleting it reparents three live prompts (`high_complexity`, `low_complexity`,
`medium_complexity`) under the preceding `personalities:` mapping. Both ranges parse as valid YAML,
so nothing would have raised — the bot would simply have lost every system prompt. Caught before it
landed; `9894bcc` deletes 206-212 only.

The general lesson, which applies to every YAML edit in this repo: a range that parses is not a
range that is correct. Check the resulting key structure, not just that `yaml.safe_load` succeeds.

## Method

Every entry above was established against the working tree, not by reading. Where a claim concerned
test behaviour, the repository was copied to `/tmp`, mutated **in the copy**, and the suite re-run
there; the real tree was never modified. Where a claim concerned resource use, the code path was
executed and measured. `git status --porcelain` was verified clean before and after.
