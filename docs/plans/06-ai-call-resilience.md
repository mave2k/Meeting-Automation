# Feature: Resilient AI call (timeout, retries, failure notification)

Status: ready

Planned against: commit `57c1a65`
Depends on: none. Interacts with `08-project-context` (either order works — see below).

**If `08-project-context` is implemented (before or after this plan):** `process_meeting()` makes a second AI call (`identify_project()`) before the note call. It goes through `call_ai()`, so the timeout + retries from this plan apply to it automatically — no extra code. In the timeout test (acceptance criteria), the classifier call fails first; the result (retry lines, `Failed […]`, file not marked, notification) is the same. Worst-case time until the final error doubles when both calls time out. The new "Identifying project via …" log line doesn't need the `timeout …, max … attempts` suffix.

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 06-ai-call-resilience`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff 57c1a65 -- meeting_processor/src`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing.

## Goal
User story: *As user, I want a failed or hanging AI call to end with a clear error and a notification, so that I notice right away when a meeting note was not created and can simply re-run it.*

Trigger (2026-10-28): two exports hung for many minutes in `client.models.generate_content` (one TCP connection stuck in `SYN_SENT`, one established without a response) — the Gemini client has no timeout, so the processes never finished and nothing was logged after "AFC is enabled". The notes only went missing silently.

Afterwards: an AI call never waits forever (timeout per attempt), transient failures (timeout, connection error, 408, 429, 5xx) are retried automatically, permanent ones (401 invalid key, 404 unknown model) fail immediately. Any final failure is logged as today, shows a macOS notification, and leaves the file unmarked so it can be re-run.

## Out of scope
- Automatic re-processing later (queue / re-scan of failed files). After a final failure the user re-runs the file manually.
- Fallback to another AI provider.
- Streaming responses.
- Obsidian call — already has `timeout=15`; no retries added there.
- Exit code of the Automator wrapper (`Finished (exit 0)` is logged even when processing failed) — noticed during planning, separate topic.
- Mode B `WATCH_FOLDER` path — see `07-watch-folder-config`.
- Making timeout / attempts configurable in `config.toml` — explicitly decided: constants in code.
- New dependencies (`requirements.txt` unchanged).

## Approach
**Use each SDK's built-in timeout and retry support instead of an own retry loop.** The SDKs know their own exception classes and retryable status codes; an own loop would have to re-implement that per provider. Verified against the installed versions (google-genai 2.2.0, anthropic 0.97.0, openai 2.33.0) — re-check with Context7 when implementing:

| Provider | Today | After |
|---|---|---|
| Gemini | no timeout, no retries (`retry_options=None` → single attempt) | `genai.Client(api_key=…, http_options=types.HttpOptions(timeout=AI_TIMEOUT_SECONDS * 1000, retry_options=types.HttpRetryOptions(attempts=AI_MAX_ATTEMPTS)))`. `timeout` is in **milliseconds**. The SDK retries on 408/429/500/502/503/504, `httpx.TimeoutException` and `httpx.ConnectError` with exponential backoff + jitter (1 s initial, 60 s max) and logs each retry at INFO via tenacity `before_sleep_log`. |
| Claude | SDK default: 600 s timeout, 2 retries | `anthropic.Anthropic(api_key=…, timeout=AI_TIMEOUT_SECONDS, max_retries=AI_MAX_ATTEMPTS - 1)` |
| OpenAI | SDK default: 600 s timeout, 2 retries | `OpenAI(api_key=…, timeout=AI_TIMEOUT_SECONDS, max_retries=AI_MAX_ATTEMPTS - 1)` |

Note the difference: Gemini's `attempts` counts the first call, Anthropic/OpenAI `max_retries` does not — hence `- 1`.

**`processor.py`:**
- New constants in the CONFIGURATION section, with a short comment:
  - `AI_TIMEOUT_SECONDS = 180` — per attempt. Observed calls take 15–40 s (up to ~100k-char transcripts); 180 s leaves headroom without a false timeout doubling the cost.
  - `AI_MAX_ATTEMPTS = 3` — including the first call. Worst case until the final error ≈ 3 × 180 s + backoff ≈ 9–10 min.
- `call_ai()`: pass the options above to each client. Behaviour on success unchanged; on final failure the SDK's exception propagates as today.
- `process_meeting()`: extend the "Sending to …" log line with `timeout 180s, max 3 attempts`.
- New `notify_failure(title: str, message: str) -> None`:
  - `subprocess.run(["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run", title, message], timeout=5, check=False, capture_output=True)` — texts go in via `argv`, never interpolated into AppleScript source (no quoting/injection issues with filenames or error texts).
  - Best effort: any exception (incl. `FileNotFoundError`, `TimeoutExpired`) or non-zero return code → one `log.warning`, never raises.
  - Message: file name + first line of the error, truncated (~200 chars).
- Call sites in `main()`:
  - Mode A `except Exception` → `notify_failure("Meeting Processor: failed", f"{filepath.name}: {e}")`
  - Mode B `except Exception` → same with `transcript_file.name`
  - `except ConfigError` → `notify_failure("Meeting Processor: config error", str(e))` before `sys.exit(1)`

LLM output contract: unchanged (no template, frontmatter or regex changes).

## Steps
- [ ] 1. Gemini: add `AI_TIMEOUT_SECONDS` / `AI_MAX_ATTEMPTS`, pass `http_options` (timeout + retry options) to `genai.Client` in `call_ai()`, extend the "Sending to …" log line.
- [ ] 2. Claude and OpenAI: pass `timeout=AI_TIMEOUT_SECONDS` and `max_retries=AI_MAX_ATTEMPTS - 1` to their clients in `call_ai()`.
- [ ] 3. Add `notify_failure()` and call it in the three places in `main()` (Mode A, Mode B, `ConfigError`).
- [ ] 4. Docs: `README.md` (repo root) — "Troubleshooting": hanging/failed AI call, what retries look like in the log, the failure notification (and where to allow notifications if none appear), how to re-run a file (not in `processed.txt` → trigger again or run `meeting-processor "<path>"`). `CLAUDE.md` — Architecture step 4 (`call_ai`): timeout + retries via SDK options, constants; mention `notify_failure` in the `main()` description. Set Status to `done` and update the plan index.

## Acceptance criteria
- Given a normal export, the note is created exactly as before; the log shows `timeout 180s, max 3 attempts` on the "Sending to …" line.
- Given an unreachable or unresponsive API (simulated with `AI_TIMEOUT_SECONDS = 1`), the log shows 2 retry lines, then `Failed [<file>]: …`; the process exits on its own; the file is **not** added to `meeting_processor_processed.txt`; a macOS notification "Meeting Processor: failed" appears.
- Given an invalid model name in `config.toml` (404), the call fails immediately without retries; notification appears.
- Given an invalid `config.toml` (e.g. `[template] active` removed), the log shows `Config error: …`, a "Meeting Processor: config error" notification appears, exit code 1.
- Given `osascript` fails (e.g. notification permission missing), processing behaviour is unchanged; at most one warning line in the log.
- Claude/OpenAI (if a key is available): the same timeout test produces retries and a final error; without a key, step 2 is verified by code review only.

## How to test
Work in `meeting_processor/` with the venv active. Use a synthetic sample to keep API cost low, e.g. `meeting-processor "samples/Roadmap Review_2026-04-02.vtt"`; remove its line from `logs/meeting_processor_processed.txt` before each re-run.

- **Step 1:** (a) normal run → note in Obsidian, new log text on the "Sending to …" line. (b) Temporarily set `AI_TIMEOUT_SECONDS = 1` → retry lines from `google_genai` in the log, final `Failed […]`, process terminates, file not in `processed.txt`. Revert the constant. (c) Temporarily set a non-existent model in `config.toml` → immediate 404 error, no retry lines. Revert.
- **Step 2:** only if a Claude/OpenAI key is configured: switch `[ai] provider`, repeat (a) and (b). Otherwise review the diff.
- **Step 3:** repeat 1(b) → notification appears. Remove `[template] active` from `config.toml` → config-error notification, exit 1; restore. Then trigger once **from Automator** (copy the sample into the watched folder with the low timeout set) to check that the notification also appears in the Folder Action context.
- **Step 4:** read the README section and the CLAUDE.md change.

## Open questions
- Does `osascript display notification` show up when run from the Automator Folder Action, or does macOS need a notification permission first (and for which app — Automator / Script Editor / `osascript`)? Checked in the step-3 test; the answer goes into the README troubleshooting text in step 4.
