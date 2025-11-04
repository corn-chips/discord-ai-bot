#!/usr/bin/env python3
"""
Health check script for the Discord Grok Bot.

This script performs comprehensive health checks including:
- Configuration validation
- Service connectivity
- Memory usage monitoring
- Log file accessibility
"""

import sys
import os
import logging
import asyncio
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from config import BotConfig
    from services.nano_banana_client import NanoBananaClient, ServiceStatus
    import google.generativeai as genai
except ImportError as e:
    print(f"Health check failed: Missing dependencies - {e}")
    sys.exit(1)


async def check_configuration():
    """Check if bot configuration is valid."""
    try:
        config = BotConfig()
        
        # Check required configuration
        if not config.discord_bot_token:
            return False, "Discord bot token not configured"
        
        if not config.gemini_api_key:
            return False, "Gemini API key not configured"
        
        # Check optional image generation configuration
        # nano_banana_api_key defaults to gemini_api_key, so just check if it exists
        if hasattr(config, 'nano_banana_api_key') and config.nano_banana_api_key:
            # Image generation uses Gemini SDK directly, no base_url needed
            pass
        
        return True, "Configuration valid"
        
    except Exception as e:
        return False, f"Configuration error: {e}"


async def check_gemini_service():
    """Check Gemini API connectivity."""
    try:
        config = BotConfig()
        genai.configure(api_key=config.gemini_api_key)
        
        # Try to list models to verify API key
        models = genai.list_models()
        model_list = list(models)
        
        if model_list:
            return True, f"Gemini API accessible ({len(model_list)} models available)"
        else:
            return False, "Gemini API accessible but no models found"
            
    except Exception as e:
        return False, f"Gemini API error: {e}"


async def check_nano_banana_service():
    """Check Gemini image generation service if configured."""
    try:
        config = BotConfig()
        
        if not hasattr(config, 'nano_banana_api_key') or not config.nano_banana_api_key:
            return True, "Image generation not configured (optional)"
        
        # Initialize client (uses Gemini SDK directly)
        client = NanoBananaClient(
            api_key=config.nano_banana_api_key,
            timeout=10
        )
        
        status = await client.check_service_status()
        
        if status == ServiceStatus.HEALTHY:
            return True, "Gemini image generation service healthy"
        elif status == ServiceStatus.DEGRADED:
            return True, "Gemini image generation service degraded but functional"
        else:
            return False, f"Gemini image generation service unavailable: {status.value}"
            
    except Exception as e:
        return False, f"Gemini image generation service error: {e}"


def check_file_system():
    """Check file system access and permissions."""
    try:
        # Check logs directory
        logs_dir = Path("logs")
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
        
        if not logs_dir.is_dir():
            return False, "Logs directory is not accessible"
        
        # Check temp directory
        temp_dir = Path("temp")
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