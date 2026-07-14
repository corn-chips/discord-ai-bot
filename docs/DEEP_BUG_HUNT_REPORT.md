# Deep Bug Hunt Report

## Summary

- **Repository**: `discord-ai-bot`
- **Build**: `8bc80f9`
- **Status**: Open
- **Reported**: 2026-07-12
- **Reporter**: Codex repository audit
- **Platform inspected**: Windows / Python 3.12
- **Scope**: Discord message processing, Gemini routing and retries, hybrid RAG persistence, response splitting, and live-mode batching

The audit confirmed five reachable correctness defects. No production code was changed while investigating them.

## Validation Performed

- Parsed all 40 Python files successfully with `ast.parse`.
- Ran the repository inventory helper across all 50 tracked and untracked workspace files.
- Attempted `python -m unittest discover -s tests -p "test_*.py" -v`; test collection was blocked because the available Python environment does not have `google-genai` installed.
- Ran isolated standard-library probes for nested timeout behavior, long fenced-code splitting, and message-index visibility transitions.
- Preserved the pre-existing untracked `AGENTS.md` file and left the worktree otherwise unchanged.

---

## BUG-0001: Main response timeout cancels configured Gemini retries

### Classification

- **Severity**: S2-Major
- **Priority**: P1-Immediate
- **Status**: Open
- **Category**: Network / Async lifecycle
- **System**: Normal mention and DM response generation
- **Frequency**: Always when the first attempt consumes its full timeout
- **Regression**: Unknown

### Reproduction Steps

**Preconditions**:

- Use the default `response.timeout: 30` and `response.max_retries: 3` configuration.
- Cause the first Gemini attempt to exceed 30 seconds.
- Make the second attempt require more than 10 seconds but less than its valid 30-second per-attempt timeout.

1. Send a normal mention or DM that enters `DiscordBot._generate_and_send_response`.
2. Allow the first Gemini attempt to time out after 30 seconds.
3. Observe the client begin its second attempt.
4. Wait until the outer 40-second deadline expires.

**Expected Result**: The second attempt receives its complete 30-second timeout, and the remaining configured retries remain available.

**Actual Result**: The outer `asyncio.wait_for` cancels the entire retrying coroutine ten seconds into attempt two.

### Technical Context

- **Affected files**:
  - `src/bot/discord_bot.py:1982-2006`
  - `src/services/gemini_client.py:949-967`
  - `src/services/gemini_client.py:1173-1218`
- **Root cause**: `GeminiClient.generate_response` owns per-attempt timeouts and retries, while its caller adds a second timeout covering the whole retry sequence. The caller budgets only one attempt timeout plus ten seconds.
- **Concrete trace**: `on_message -> _process_message_with_context -> _generate_and_send_response -> outer wait_for(40s) -> generate_response -> attempt 1 wait_for(30s) -> attempt 2 -> outer cancellation at 40s`.
- **Probe result**: An isolated nested-timeout probe returned `outer-timeout`; the same retry coroutine returned `ok` without the outer wrapper.

### Recommended Fix

Give timeout ownership to `GeminiClient.generate_response`, or calculate an outer budget that includes every possible attempt and backoff delay. Add a regression test where attempt one times out and attempt two succeeds after more than the current ten-second remainder.

### Related Paths Checked

Live mode, summarization, and deep-research commands call `generate_response` directly and therefore retain the internal retry behavior. The defect is specific to the main context-processing path.

---

## BUG-0002: Routed messages ignore global and per-user model selections

### Classification

- **Severity**: S3-Minor
- **Priority**: P2-Next Sprint
- **Status**: Open
- **Category**: Configuration / Routing
- **System**: `/config model`, `/preferences model`, and normal text responses
- **Frequency**: Always when the enhanced router is active
- **Regression**: Unknown

### Reproduction Steps

**Preconditions**: Run the normally configured bot with the enhanced command handler active.

1. Select a model using `/preferences model`, or change the global model using `/config model`.
2. Send a normal text mention or DM.
3. Observe the processing status or request log showing the model selected from `model_complexity`.

**Expected Result**: The selected model affects subsequent requests according to a documented precedence policy.

**Actual Result**: The request uses the router's complexity model; both the stored user preference and current global model are bypassed.

### Technical Context

- **Affected files**:
  - `src/bot/commands.py:170-203`
  - `src/bot/discord_bot.py:803-846`
  - `src/bot/discord_bot.py:1953-2004`
  - `src/services/user_preferences_service.py:74-78`
- **Root cause**: After routing, `model_override` is always populated by `get_model_for_complexity`. User preferences are applied only when `effective_model_override is None`, and the global current model is also used only as a fallback.
- **Violated contract**: README documentation says user model preferences persist and apply automatically, while `/config model` reports that the AI model was switched.

### Recommended Fix

Resolve the final model once using an explicit precedence order, such as request-specific override, user preference, runtime/global selection, then complexity default. Add tests covering every precedence combination.

### Related Paths Checked

Language preferences still apply because they are not guarded by `effective_model_override is None`. Deep research and live mode use explicit models intentionally.

---

## BUG-0003: Long fenced-code responses can exceed Discord's message limit

### Classification

- **Severity**: S3-Minor
- **Priority**: P2-Next Sprint
- **Status**: Open
- **Category**: Response delivery
- **System**: Markdown-aware message splitting
- **Frequency**: Always for sufficiently long fenced code containing an unbroken line
- **Regression**: Unknown

### Reproduction Steps

**Preconditions**: Generate a response longer than 2,000 characters containing a fenced code block with an individual line longer than approximately 1,900 characters.

1. Pass a fenced code response containing a 5,000-character unbroken line to `MessageSplitter.split_message`.
2. Allow code-block preservation and continuation indicators to be added.
3. Observe integrity validation fail and `_send_split_response` invoke `_send_simple_split_response`.
4. Observe the fallback retain the 5,000-character line as a single outbound part.

**Expected Result**: Every final outbound message is at most Discord's 2,000-character limit and contains all source content.

**Actual Result**: Discord rejects the oversized fallback part, leaving a partial response followed by error handling.

### Technical Context

- **Affected files**:
  - `src/services/message_splitter.py:114-120`
  - `src/services/message_splitter.py:288-384`
  - `src/services/message_splitter.py:386-422`
  - `src/bot/discord_bot.py:2312-2347`
  - `src/bot/discord_bot.py:2349-2415`
- **Root cause**:
  1. Continuation markers and reopening/closing code fences are added after the initial size calculation.
  2. Integrity validation detects the modified length and triggers the simple fallback.
  3. The fallback assigns an overlong sentence directly to `current_part` without a character-boundary hard split.
- **Probe result**: A 5,000-character fenced-code input produced final intelligent parts of `[1981, 2030, 1153]` characters and `integrity=False`.

### Recommended Fix

Enforce the Discord limit after all formatting artifacts are added. Add a final hard-split path for individual sentences, lines, and tokens that still exceed the safe limit. Test fenced code with long lines and no whitespace.

---

## BUG-0004: Paginator edits overwrite complete bot responses in the RAG index

### Classification

- **Severity**: S3-Minor
- **Priority**: P2-Next Sprint
- **Status**: Open
- **Category**: Persistence / RAG
- **System**: Bot-response indexing and paginated embeds
- **Frequency**: Often for long responses when the cached message receives a component or embed update
- **Regression**: Unknown

### Reproduction Steps

**Preconditions**:

- Enable `rag.enabled` and `rag.index_bot_responses`.
- Generate a response longer than 2,000 characters so it is sent as a paginator embed.

1. Inspect the response row in `message_index`; it initially contains the complete generated response.
2. Press a paginator navigation button, or wait for the view's 120-second timeout edit.
3. Inspect the same row again after `on_message_edit` runs.

**Expected Result**: UI-only edits preserve the canonical full response used for retrieval.

**Actual Result**: The complete response is replaced by the current Discord representation, typically generic message/embed metadata, and a new embedding is queued for that degraded content.

### Technical Context

- **Affected files**:
  - `src/bot/discord_bot.py:90-126`
  - `src/bot/discord_bot.py:403-422`
  - `src/bot/discord_bot.py:2135-2159`
  - `src/services/message_index_service.py:325-446`
- **Root cause**: `_index_sent_bot_response` initially stores canonical generated text under the paginator message ID. The generic edit handler later rebuilds content from the live embed-only Discord message and upserts it over the canonical row.
- **Related persistence defect**: The same upsert path writes `hidden = excluded.hidden`, so an ordinary edit using the default `hidden=False` can also clear an existing hidden state.
- **Probe result**: An indexed row disappeared from retrieval after `mark_hidden`, then an ordinary upsert made it visible again with the edited content.

### Recommended Fix

Ignore component/embed-only edits for canonical indexed bot responses, or store canonical response text separately from the mutable Discord presentation. Preserve existing hidden state unless the caller explicitly requests a visibility change.

---

## BUG-0005: Live-mode attachment batches discard other queued messages

### Classification

- **Severity**: S3-Minor
- **Priority**: P2-Next Sprint
- **Status**: Open
- **Category**: Async queue / Message handling
- **System**: Mention-free live mode
- **Frequency**: Sometimes, when messages accumulate during an in-flight response and any queued message has an attachment
- **Regression**: Unknown

### Reproduction Steps

**Preconditions**: Enable live mode in a channel and keep one AI response in progress.

1. While that response is running, send text message A.
2. Send attachment message B.
3. Send text message C before the worker processes the pending queue.
4. Allow the live worker to pop `[A, B, C]` as one batch.

**Expected Result**: A, B, and C are represented in a combined response or retained for later processing.

**Actual Result**: Only attachment message B is processed. A and C are removed from the queue without a response, requeue, or live-context entry.

### Technical Context

- **Affected files**:
  - `src/bot/discord_bot.py:537-597`
  - `src/bot/discord_bot.py:628-680`
- **Root cause**: `_process_live_messages` checks whether any message has attachments, selects one attachment-bearing message, invokes the full pipeline for only that message, and returns before handling the remaining prompt entries.
- **Concrete trace**: `_enqueue_live_message -> _run_live_channel_worker pops complete pending batch -> _process_live_messages has_attachments=True -> select one attachment_message -> _process_message_with_context -> return True`.

### Recommended Fix

Partition attachment work without discarding adjacent messages, or construct one combined request containing all queued prompts and the selected attachment data. Add an async test that queues multiple messages while a prior live response is blocked.

## Suggested Triage Order

1. **BUG-0001** — fixes a core reliability path and restores configured retry behavior.
2. **BUG-0002** — restores advertised model-selection behavior.
3. **BUG-0003** — prevents response loss for long code output.
4. **BUG-0004** — prevents durable RAG content degradation.
5. **BUG-0005** — prevents silent message loss in live mode.

## Verification Requirements

Each fix should receive a focused regression test before closure. After implementation, rerun the full `unittest` suite in an environment with `requirements.txt` installed, then manually verify Discord-specific paginator and live-mode event behavior.
