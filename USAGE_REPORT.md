# `/usage-report` Command

## Overview
The `/usage-report` command generates a comprehensive usage report since the bot last started. It posts a summary embed and attaches two files:

- A Markdown report with human-readable sections
- A CSV file for spreadsheets and further analysis

This helps you track performance, API usage, token estimates, and image processing activity at a glance.

## Command

```
/usage-report
```

## What’s Included

### Summary Embed
- Uptime, guild count, current latency
- API calls, failures, average API time
- Message count and average response time
- Token estimates (if available)
- Image processing summary (if applicable)

### Markdown Report
Sections:
- Overview
- API Usage (incl. free-tier limits)
- Token Usage (estimated)
- Message Processing
- Image Processing (if available)
- Image Queue Snapshot (if available)
- Notes

### CSV Report
Key fields:
- uptime_seconds, guilds, latency_ms
- message_count, avg_response_time
- api_calls, api_failures, avg_api_time
- total_tokens, input_tokens, output_tokens
- image_processing_count, avg_image_processing_time, image_processing_failures
- message_splits_count, avg_message_split_time, message_split_failures
- free_tier_requests_per_minute, free_tier_requests_per_day, free_tier_tokens_per_minute

## Notes
- Token counts are estimates (~4 characters ≈ 1 token)
- Image editing and generation require a paid API key
- Report covers metrics since last startup; use `/clear-cache` to reset counters

## Related
- `/api-usage` — real-time quotas, model capabilities, and current metrics (public)
- `/stats` — quick, ephemeral summary of general metrics
