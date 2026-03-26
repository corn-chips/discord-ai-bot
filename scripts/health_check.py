#!/usr/bin/env python3
"""
Health check script for the Discord Grok Bot.

This script performs comprehensive health checks including:
- Configuration validation
- Service connectivity
- Memory usage monitoring
- Log file accessibility
"""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables if a .env file exists (falls back to system env otherwise)
dotenv_path = ROOT_DIR / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
else:
    load_dotenv()

try:
    from src.config import BotConfig
    from src.services.nano_banana_client import NanoBananaClient, ServiceStatus
    import google.generativeai as genai
except ImportError as e:
    print(f"Health check failed: Missing dependencies - {e}")
    sys.exit(1)


def _load_config() -> tuple[bool, BotConfig | None, str]:
    """Load bot configuration from config.yaml and .env."""
    try:
        config = BotConfig.from_yaml()
        return True, config, ""
    except Exception as exc:  # noqa: BLE001
        return False, None, f"Failed to load configuration: {exc}"


async def check_configuration():
    """Check if bot configuration is valid."""
    success, config, error = _load_config()
    if not success or not config:
        return False, error

    validation_errors = config.validate()
    if validation_errors:
        return False, "; ".join(validation_errors)

    return True, "Configuration valid"


async def check_gemini_service():
    """Check Gemini API connectivity."""
    success, config, error = _load_config()
    if not success or not config:
        return False, error

    if not config.gemini_api_key:
        return False, "GEMINI_API_KEY not configured"

    try:
        genai.configure(api_key=config.gemini_api_key)
        model_list = list(genai.list_models())
        if model_list:
            return True, f"Gemini API accessible ({len(model_list)} models available)"
        return False, "Gemini API accessible but no models found"
    except Exception as e:  # noqa: BLE001
        return False, f"Gemini API error: {e}"


async def check_nano_banana_service():
    """Check Gemini image generation service if configured."""
    success, config, error = _load_config()
    if not success or not config:
        return False, error

    api_key = getattr(config, "nano_banana_api_key", None)
    if not api_key:
        return True, "Image generation not configured (optional)"

    try:
        client = NanoBananaClient(api_key=api_key, timeout=10)
        status = await client.check_service_status()
        if status == ServiceStatus.HEALTHY:
            return True, "Gemini image generation service healthy"
        if status == ServiceStatus.DEGRADED:
            return True, "Gemini image generation service degraded but functional"
        return False, f"Gemini image generation service unavailable: {status.value}"
    except Exception as e:  # noqa: BLE001
        return False, f"Gemini image generation service error: {e}"


def check_file_system():
    """Check file system access and permissions."""
    try:
        # Check logs directory
        logs_dir = ROOT_DIR / "logs"
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
        
        if not logs_dir.is_dir():
            return False, "Logs directory is not accessible"
        
        # Check temp directory
        temp_dir = ROOT_DIR / "temp"
        if not temp_dir.exists():
            temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Test write permissions
        test_file = logs_dir / "health_check.tmp"
        try:
            test_file.write_text("health check")
            test_file.unlink()
        except Exception as e:
            return False, f"Cannot write to logs directory: {e}"
        
        return True, "File system accessible"
        
    except Exception as e:
        return False, f"File system error: {e}"


def check_memory_usage():
    """Check memory usage."""
    try:
        import psutil
        
        # Get current process memory usage
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        
        # Get system memory
        system_memory = psutil.virtual_memory()
        available_mb = system_memory.available / 1024 / 1024
        
        if memory_mb > 1024:  # More than 1GB
            return False, f"High memory usage: {memory_mb:.1f}MB"
        
        if available_mb < 256:  # Less than 256MB available
            return False, f"Low system memory: {available_mb:.1f}MB available"
        
        return True, f"Memory usage normal: {memory_mb:.1f}MB used, {available_mb:.1f}MB available"
        
    except ImportError:
        return True, "Memory check skipped (psutil not available)"
    except Exception as e:
        return False, f"Memory check error: {e}"


async def main():
    """Run all health checks."""
    print("Discord Grok Bot Health Check")
    print("=" * 40)
    
    checks = [
        ("Configuration", check_configuration()),
        ("File System", check_file_system()),
        ("Memory Usage", check_memory_usage()),
        ("Gemini Chat", check_gemini_service()),
        ("Gemini Images", check_nano_banana_service()),
    ]
    
    all_passed = True
    
    for check_name, check_coro in checks:
        if asyncio.iscoroutine(check_coro):
            passed, message = await check_coro
        else:
            passed, message = check_coro
        
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{check_name:20} {status:8} {message}")
        
        if not passed:
            all_passed = False
    
    print("=" * 40)
    
    if all_passed:
        print("✓ All health checks passed")
        sys.exit(0)
    else:
        print("✗ Some health checks failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())