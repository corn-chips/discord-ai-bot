# Analysis corrections

Last updated: 2026-07-29 | Repo state: branch `dev`, HEAD `6f1dc79` | Baseline suite: 140 tests, OK

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
suite at **140 tests, OK** (re-measured at `6f1dc79`; it was 125 at `4baa29c`, and the two test
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

Landed in the corrected order by `1ee538a` — clamp first, then delete the round-trip. The ticket
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
