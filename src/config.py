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
    
    # Router configuration
    router_model_name: str = "gemini-2.0-flash-lite"
    
    # Optional configuration with defaults
    max_context_messages: int = 100
    reply_context_range: int = 10
    response_timeout: int = 30
    max_retries: int = 3
    log_level: str = "INFO"
    log_file: Optional[str] = None
    enable_performance_logging: bool = True
    token_db_path: str = "data/token_usage.db"
    
    # Image processing configuration (uses Gemini 2.5 Flash Image model)
    # Note: nano_banana_api_key should be the same as gemini_api_key
    nano_banana_api_key: str = ""
    max_image_size_mb: int = 10
    image_processing_timeout: int = 60
    max_concurrent_image_edits: int = 3
    
    # Message formatting settings
    message_split_length: int = 2000
    preserve_code_blocks: bool = True
    add_continuation_indicators: bool = True
    
    # UX enhancement settings
    show_typing_indicators: bool = True
    use_rich_embeds: bool = True
    enable_reaction_feedback: bool = True
    command_suggestion_threshold: float = 0.7
    
    # Developer mode (detailed error output)
    dev_mode_enabled: bool = False
    
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
            enable_performance_logging=os.getenv('ENABLE_PERFORMANCE_LOGGING', 'true').lower() == 'true',
            token_db_path=os.getenv('TOKEN_DB_PATH', 'data/token_usage.db'),
            
            # Image processing configuration (uses Gemini 2.5 Flash Image)
            # If not set, falls back to GEMINI_API_KEY
            nano_banana_api_key=os.getenv('NANO_BANANA_API_KEY', os.getenv('GEMINI_API_KEY', '')),
            max_image_size_mb=int(os.getenv('MAX_IMAGE_SIZE_MB', '10')),
            image_processing_timeout=int(os.getenv('IMAGE_PROCESSING_TIMEOUT', '60')),
            max_concurrent_image_edits=int(os.getenv('MAX_CONCURRENT_IMAGE_EDITS', '3')),
            
            # Message formatting settings
            message_split_length=int(os.getenv('MESSAGE_SPLIT_LENGTH', '2000')),
            preserve_code_blocks=os.getenv('PRESERVE_CODE_BLOCKS', 'true').lower() == 'true',
            add_continuation_indicators=os.getenv('ADD_CONTINUATION_INDICATORS', 'true').lower() == 'true',
            
            # UX enhancement settings
            show_typing_indicators=os.getenv('SHOW_TYPING_INDICATORS', 'true').lower() == 'true',
            use_rich_embeds=os.getenv('USE_RICH_EMBEDS', 'true').lower() == 'true',
            enable_reaction_feedback=os.getenv('ENABLE_REACTION_FEEDBACK', 'true').lower() == 'true',
            command_suggestion_threshold=float(os.getenv('COMMAND_SUGGESTION_THRESHOLD', '0.7')),
            
            # Developer mode settings
            dev_mode_enabled=os.getenv('DEV_MODE_ENABLED', 'false').lower() == 'true'
        )
    
    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        # Required configuration validation
        if not self.discord_token:
            errors.append("DISCORD_BOT_TOKEN is required")
        
        if not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required")
        
        # Core bot settings validation
        if self.max_context_messages <= 0:
            errors.append("MAX_CONTEXT_MESSAGES must be positive")

        if not self.token_db_path or not self.token_db_path.strip():
            errors.append("TOKEN_DB_PATH must be a valid filesystem path")
        
        if self.reply_context_range <= 0:
            errors.append("REPLY_CONTEXT_RANGE must be positive")
        
        if self.response_timeout <= 0:
            errors.append("RESPONSE_TIMEOUT must be positive")
        
        if self.max_retries < 0:
            errors.append("MAX_RETRIES must be non-negative")
        
        # Image processing validation
        if self.max_image_size_mb <= 0:
            errors.append("MAX_IMAGE_SIZE_MB must be positive")
        elif self.max_image_size_mb > 25:  # Discord's file size limit
            errors.append("MAX_IMAGE_SIZE_MB cannot exceed 25MB (Discord limit)")
        
        if self.image_processing_timeout <= 0:
            errors.append("IMAGE_PROCESSING_TIMEOUT must be positive")
        elif self.image_processing_timeout > 300:  # 5 minutes max
            errors.append("IMAGE_PROCESSING_TIMEOUT should not exceed 300 seconds")
        
        if self.max_concurrent_image_edits <= 0:
            errors.append("MAX_CONCURRENT_IMAGE_EDITS must be positive")
        elif self.max_concurrent_image_edits > 10:
            errors.append("MAX_CONCURRENT_IMAGE_EDITS should not exceed 10 for performance")
        
        # Gemini image generation validation (nano-banana is Gemini 2.5 Flash Image)
        # API key validation handled by main Gemini validation
        
        # Message formatting validation
        if self.message_split_length <= 0:
            errors.append("MESSAGE_SPLIT_LENGTH must be positive")
        
        if self.message_split_length > 2000:
            errors.append("MESSAGE_SPLIT_LENGTH cannot exceed Discord's 2000 character limit")
        
        if self.message_split_length < 100:
            errors.append("MESSAGE_SPLIT_LENGTH should be at least 100 characters for effective splitting")
        
        # UX settings validation
        if not (0.0 <= self.command_suggestion_threshold <= 1.0):
            errors.append("COMMAND_SUGGESTION_THRESHOLD must be between 0.0 and 1.0")
        
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
        
        # Nano-banana API key validation
        if self.nano_banana_api_key:
            if len(self.nano_banana_api_key) < 20:
                errors.append("NANO_BANANA_API_KEY appears to be invalid (too short)")
        
        return len(errors) == 0, errors
    
    async def validate_service_connectivity(self) -> tuple[bool, dict[str, str]]:
        """
        Validate connectivity to external services.
        Returns (all_services_ok, service_status_dict).
        """
        service_status = {}
        all_ok = True
        
        # Gemini image generation (nano-banana) uses the Gemini SDK directly
        # No separate API endpoint to test - availability is determined by API key
        if self.nano_banana_api_key:
            service_status['gemini_image'] = "✅ Configured (uses Gemini SDK)"
        else:
            service_status['gemini_image'] = "⚪ Not configured"
        
        return all_ok, service_status
    
    def get_feature_availability(self) -> dict[str, bool]:
        """
        Get availability status of optional features based on configuration.
        Returns dictionary of feature names to availability status.
        """
        return {
            'image_generation': bool(self.nano_banana_api_key),
            'enhanced_ux': self.show_typing_indicators or self.use_rich_embeds or self.enable_reaction_feedback,
            'message_splitting': self.preserve_code_blocks or self.add_continuation_indicators,
            'command_suggestions': self.command_suggestion_threshold > 0.0,
            'performance_logging': self.enable_performance_logging
        }


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
        print("\nImage generation/editing configuration:")
        print("   • NANO_BANANA_API_KEY - API key for Gemini image generation (defaults to GEMINI_API_KEY)")
        print("   • MAX_IMAGE_SIZE_MB (default: 10)")
        print("   • IMAGE_PROCESSING_TIMEOUT (default: 60)")
        print("   • MAX_CONCURRENT_IMAGE_EDITS (default: 3)")
        print("\nMessage formatting settings:")
        print("   • MESSAGE_SPLIT_LENGTH (default: 2000)")
        print("   • PRESERVE_CODE_BLOCKS (default: true)")
        print("   • ADD_CONTINUATION_INDICATORS (default: true)")
        print("\nUX enhancement settings:")
        print("   • SHOW_TYPING_INDICATORS (default: true)")
        print("   • USE_RICH_EMBEDS (default: true)")
        print("   • ENABLE_REACTION_FEEDBACK (default: true)")
        print("   • COMMAND_SUGGESTION_THRESHOLD (default: 0.7)")
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
        if config.nano_banana_api_key:
            print("   • Nano-Banana API Key: Verify with your service provider")
        sys.exit(1)
    
    # Set up logging with the configuration
    setup_logging(
        log_level=config.log_level,
        log_file=config.log_file,
        enable_console=True,
        enable_performance_logging=config.enable_performance_logging
    )
    
    # Display feature availability
    features = config.get_feature_availability()
    print("✅ Configuration loaded successfully")
    print("🔧 Feature availability:")
    for feature, available in features.items():
        status = "✅ Enabled" if available else "❌ Disabled"
        print(f"   • {feature.replace('_', ' ').title()}: {status}")
    
    return config


async def validate_startup_connectivity(config: BotConfig) -> bool:
    """
    Validate connectivity to external services during startup.
    Returns True if all critical services are available, False otherwise.
    """
    print("🔍 Checking service connectivity...")
    
    try:
        all_ok, service_status = await config.validate_service_connectivity()
        
        print("📡 Service connectivity status:")
        for service, status in service_status.items():
            print(f"   • {service.replace('_', ' ').title()}: {status}")
        
        if not all_ok:
            print("⚠️  Some services are unavailable. The bot will start with reduced functionality.")
            print("   Image editing features may not work if nano-banana service is unavailable.")
        
        return True  # Always return True to allow startup with degraded functionality
        
    except Exception as e:
        print(f"❌ Error checking service connectivity: {e}")
        print("⚠️  Continuing startup without connectivity validation...")
        return True