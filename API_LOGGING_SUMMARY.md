# API Logging Implementation Summary

## Overview
Comprehensive logging has been added to track all API token input/output values and related API call information in the `bot.log` file.

## What's Being Logged

### 1. **API Configuration (at startup)**
When the bot initializes, the following information is logged:
- API Key validation status
- API Key length (character count)
- API Key first 8 characters (for verification)
- API Key last 4 characters (for verification)
- Model name being used
- Generation configuration parameters:
  - Temperature
  - top_p
  - top_k
  - max_output_tokens
- Safety settings configuration
- Configuration success/failure status

**Example log output:**
```
2025-11-04 07:30:30 | INFO | src.services.gemini_client | ================================================================================
2025-11-04 07:30:30 | INFO | src.services.gemini_client | CONFIGURING GEMINI API
2025-11-04 07:30:30 | INFO | src.services.gemini_client | API Key length: 39 characters
2025-11-04 07:30:30 | INFO | src.services.gemini_client | API Key (first 8 chars): AIzaSyAr...
2025-11-04 07:30:30 | INFO | src.services.gemini_client | API Key (last 4 chars): ...u0PY
2025-11-04 07:30:30 | INFO | src.services.gemini_client | Model name: gemini-flash-latest
2025-11-04 07:30:30 | INFO | src.services.gemini_client | Generation config: temperature=0.7, top_p=0.8, top_k=40, max_output_tokens=65536
2025-11-04 07:30:30 | INFO | src.services.gemini_client | Safety settings: All filters set to BLOCK_NONE
2025-11-04 07:30:30 | INFO | src.services.gemini_client | Gemini API configured successfully
2025-11-04 07:30:30 | INFO | src.services.gemini_client | ================================================================================
```

### 2. **Model Switching**
When switching between different Gemini models:
- Old model name
- New model name
- Switch success/failure status
- Any errors encountered during switching

### 3. **API Call Initiation**
For every API request, the following is logged:
- API call initiation marker
- Current model being used
- API Key verification (last 4 characters)
- Whether Google Search is enabled for the request
- Input prompt length (character count)
- First 200 characters of the input prompt
- Whether images are included and how many
- Context message count and details for each:
  - Author
  - Message length
  - Timestamp

### 4. **Prompt Formatting**
- User message length
- Number of context messages being included
- Total formatted prompt length

### 5. **API Response Details**
For every API response received:
- Response duration (in seconds)
- Response object type
- Number of candidates returned
- Finish reason code and human-readable name
- Safety ratings (if available)
- Output token count (estimated word count)
- Output length (character count)
- First 200 characters of the output
- Grounding sources (when web search is used):
  - Number of sources
  - Title and URI for each source

### 6. **Error Handling**
Detailed logging for all error scenarios:
- **Timeouts:**
  - Timeout duration
  - Attempt number
  
- **Exceptions:**
  - Exception type name
  - Exception message
  - Full stack trace
  - Error type classification
  - Whether retry should occur
  - Retry delay time

- **Safety Filters:**
  - Which safety categories were triggered
  - Safety rating probabilities

- **Max Tokens:**
  - Partial response length when truncated
  - Token limit information

- **Empty/Invalid Responses:**
  - Response validation failures
  - Missing candidate information

### 7. **Performance Metrics**
- API call duration for each request
- Success/failure status
- Error types for failed calls

## Log Format

All API-related logs use clear separator lines (`===` or `---`) to make them easy to find and read:

```
================================================================================
API CALL INITIATED
Model: gemini-flash-latest
API Key (last 4 chars): ...u0PY
...
--------------------------------------------------------------------------------
API RESPONSE RECEIVED (Duration: 2.345s)
...
================================================================================
```

## Benefits

1. **Debugging:** Easily trace API issues by seeing exact inputs and outputs
2. **Monitoring:** Track API usage patterns and performance
3. **Security:** Verify API key being used (via partial display)
4. **Performance Analysis:** Measure response times and identify bottlenecks
5. **Error Diagnosis:** Comprehensive error information for troubleshooting
6. **Token Usage Tracking:** Monitor input/output token estimates
7. **Search Feature Tracking:** See when and how web search is being used

## Files Modified

- `src/services/gemini_client.py` - Added comprehensive logging throughout all API-related methods

## Log Location

All logs are written to: `logs/bot.log`

Both the Docker container and the host system have access to this log file through volume mounting.
