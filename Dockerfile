# Discord Grok Bot with Gemini 2.5 Flash & Image Generation
# Uses Python 3.11 slim for optimal balance of size and compatibility
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for:
# - Image processing (Pillow, PIL) - Required for Gemini image generation/editing
# - PDF processing (PyMuPDF, pdf2image)
# - Compilation tools for native extensions
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    pkg-config \
    libffi-dev \
    libjpeg-dev \
    libpng-dev \
    libwebp-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies with optimizations
# Note: Includes both google-generativeai (legacy) and google-genai (new SDK)
# The new google-genai SDK is required for Google Search grounding functionality
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Make health check script executable
RUN chmod +x scripts/health_check.py

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash botuser && \
    chown -R botuser:botuser /app

# Create necessary directories with proper permissions
RUN mkdir -p /app/logs /app/temp /app/cache && \
    chown -R botuser:botuser /app/logs /app/temp /app/cache

USER botuser

# Enhanced health check that validates core services
HEALTHCHECK --interval=30s --timeout=15s --start-period=10s --retries=3 \
    CMD python scripts/health_check.py || exit 1

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

# Note: GEMINI_API_KEY enables:
#   - Chat/text generation
#   - Image generation/editing
#   - Google Search grounding (real-time web search integration)
# Set NANO_BANANA_API_KEY separately only if you want different quotas
# See .env.example for configuration options

# Expose port for health checks and monitoring
EXPOSE 8080

# Run the bot
CMD ["python", "-u", "main.py"]