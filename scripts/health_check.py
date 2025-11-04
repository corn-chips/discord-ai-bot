#!/usr/bin/env python3
"""
Health check script for Discord Grok Bot.

This script can be used to monitor the bot's health status.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.config import load_and_validate_config


class HealthChecker:
    """Health checker for the Discord Grok Bot."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
    def check_configuration(self) -> bool:
        """Check if configuration is valid."""
        try:
            config = load_and_validate_config()
            return True
        except Exception as e:
            self.logger.error(f"Configuration check failed: {e}")
            return False
    
    def check_log_file(self) -> bool:
        """Check if log file is being written to recently."""
        try:
            log_file = Path("bot.log")
            if not log_file.exists():
                self.logger.warning("Log file does not exist")
                return False
            
            # Check if log file was modified in the last 5 minutes
            last_modified = log_file.stat().st_mtime
            current_time = time.time()
            
            if current_time - last_modified > 300:  # 5 minutes
                self.logger.warning("Log file not updated recently")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Log file check failed: {e}")
            return False
    
    def check_dependencies(self) -> bool:
        """Check if required dependencies are available."""
        try:
            import discord
            import google.generativeai as genai
            import aiohttp
            return True
        except ImportError as e:
            self.logger.error(f"Dependency check failed: {e}")
            return False
    
    def run_health_check(self) -> bool:
        """Run all health checks."""
        checks = [
            ("Configuration", self.check_configuration),
            ("Dependencies", self.check_dependencies),
            ("Log File", self.check_log_file),
        ]
        
        all_passed = True
        
        for check_name, check_func in checks:
            try:
                result = check_func()
                status = "✅ PASS" if result else "❌ FAIL"
                print(f"{check_name}: {status}")
                
                if not result:
                    all_passed = False
                    
            except Exception as e:
                print(f"{check_name}: ❌ ERROR - {e}")
                all_passed = False
        
        return all_passed


def main():
    """Main health check entry point."""
    logging.basicConfig(level=logging.WARNING)  # Reduce noise
    
    checker = HealthChecker()
    
    print("🏥 Discord Grok Bot Health Check")
    print("=" * 40)
    
    success = checker.run_health_check()
    
    print("=" * 40)
    if success:
        print("✅ All health checks passed")
        sys.exit(0)
    else:
        print("❌ Some health checks failed")
        sys.exit(1)


if __name__ == "__main__":
    main()