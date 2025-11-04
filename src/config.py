"""
Configuration management for Discord Grok Bot.
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

from .utils.logging_config import setup_logging


@dataclass
class BotConfig:
    """Configuration class for the Discord Grok Bot."""
    
    # Required configuration
    discord_token: str
    gemini_api_key: str
    
    # Optional configuration with defaults
    max_context_messages: int = 100
    reply_context_range: int = 10
    response_timeout: int = 30
    max_retries: int = 3
    log_level: str = "INFO"
    log_file: Optional[str] = None
    enable_performance_logging: bool = True
    
    @classmethod
    def from_environment(cls) -> 'BotConfig':
        """Create BotConfig instance from environment variables."""
        return cls(
            discord_token=os.getenv('DISCORD_BOT_TOKEN', ''),
            gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
            max_context_messages=int(os.getenv('MAX_CONTEXT_MESSAGES', '100')),
            reply_context_range=int(os.getenv('REPLY_CONTEXT_RANGE', '10')),
            response_timeout=int(os.getenv('RESPONSE_TIMEOUT', '30')),
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            log_file=os.getenv('LOG_FILE'),
            enable_performance_logging=os.getenv('ENABLE_PERFORMANCE_LOGGING', 'true').lower() == 'true'
        )
    
    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.discord_token:
            errors.append("DISCORD_BOT_TOKEN is required")
        
        if not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required")
        
        if self.max_context_messages <= 0:
            errors.append("MAX_CONTEXT_MESSAGES must be positive")
        
        if self.reply_context_range <= 0:
            errors.append("REPLY_CONTEXT_RANGE must be positive")
        
        if self.response_timeout <= 0:
            errors.append("RESPONSE_TIMEOUT must be positive")
        
        if self.max_retries < 0:
            errors.append("MAX_RETRIES must be non-negative")
        
        return errors
    
    def validate_tokens(self) -> tuple[bool, list[str]]:
        """
        Validate Discord and Gemini API tokens.
        Returns (is_valid, error_messages).
        """
        errors = []
        
        # Basic token format validation
        if self.discord_token:
            if len(self.discord_token) < 50:  # Discord tokens are typically 59+ chars
                errors.append("DISCORD_BOT_TOKEN appears to be invalid (too short)")
        
        if self.gemini_api_key:
            if len(self.gemini_api_key) < 30:  # Gemini API keys are typically longer
                errors.append("GEMINI_API_KEY appears to be invalid (too short)")
        
        return len(errors) == 0, errors


class ConfigurationError(Exception):
    """Exception raised for configuration-related errors."""
    pass


def load_and_validate_config() -> BotConfig:
    """
    Load configuration from environment and validate it.
    Exits the application with clear error messages if configuration is invalid.
    """
    try:
        config = BotConfig.from_environment()
    except ValueError as e:
        print(f"❌ Configuration Error: Invalid environment variable format - {e}")
        print("Please check your environment variables and ensure numeric values are valid.")
        sys.exit(1)
    
    # Validate required fields
    validation_errors = config.validate()
    if validation_errors:
        print("❌ Configuration Error: Missing or invalid required configuration:")
        for error in validation_errors:
            print(f"   • {error}")
        print("\nPlease set the required environment variables:")
        print("   • DISCORD_BOT_TOKEN - Your Discord bot token")
        print("   • GEMINI_API_KEY - Your Google Gemini API key")
        print("\nOptional environment variables:")
        print("   • MAX_CONTEXT_MESSAGES (default: 100)")
        print("   • REPLY_CONTEXT_RANGE (default: 10)")
        print("   • RESPONSE_TIMEOUT (default: 30)")
        print("   • MAX_RETRIES (default: 3)")
        print("   • LOG_LEVEL (default: INFO)")
        sys.exit(1)
    
    # Validate token formats
    token_valid, token_errors = config.validate_tokens()
    if not token_valid:
        print("❌ Configuration Error: Invalid API tokens:")
        for error in token_errors:
            print(f"   • {error}")
        print("\nPlease verify your API tokens are correct:")
        print("   • Discord Bot Token: Get from https://discord.com/developers/applications")
        print("   • Gemini API Key: Get from https://makersuite.google.com/app/apikey")
        sys.exit(1)
    
    # Set up logging with the configuration
    setup_logging(
        log_level=config.log_level,
        log_file=config.log_file,
        enable_console=True,
        enable_performance_logging=config.enable_performance_logging
    )
    
    print("✅ Configuration loaded successfully")
    return config