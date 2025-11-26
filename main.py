#!/usr/bin/env python3
"""
Discord Grok Bot - Main Entry Point

A Discord bot that provides AI-powered responses using Google's Gemini API.
"""

import asyncio
import logging
import sys
from dotenv import load_dotenv

from src.config import load_and_validate_config, validate_startup_connectivity
from src.bot.discord_bot import DiscordBot

# Load environment variables
load_dotenv()

async def main():
    """Main application entry point."""
    
    # Load and validate configuration first
    config = load_and_validate_config()
    logger = logging.getLogger(__name__)
    
    logger.info("🚀 Starting Discord Grok Bot...")
    logger.info(f"Configuration: Max context messages: {config.max_context_messages}, "
                f"Reply context range: {config.reply_context_range}, "
                f"Response timeout: {config.response_timeout}s")
    
    bot_instance = None
    try:
        # Validate service connectivity
        connectivity_ok = await validate_startup_connectivity(config)
        if not connectivity_ok:
            logger.warning("Some services are unavailable, but continuing startup...")
        
        # Initialize the Discord bot
        bot_instance = DiscordBot(config)
        logger.info("✅ Bot instance created successfully")
        
        # Start the bot
        logger.info("🔌 Connecting to Discord...")
        await bot_instance.start(config.discord_token)
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        raise
    finally:
        # Ensure cleanup happens
        if bot_instance:
            try:
                logger.info("Closing Discord connection...")
                await bot_instance.close()
                logger.info("✅ Bot shutdown complete")
            except Exception as cleanup_error:
                logger.error(f"Error during final cleanup: {cleanup_error}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Bot shutdown requested by user")
    except Exception as e:
        logging.error(f"💥 Fatal error: {e}")
        sys.exit(1)