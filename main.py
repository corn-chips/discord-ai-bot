#!/usr/bin/env python3
"""
Discord Grok Bot - Main Entry Point

A Discord bot that provides AI-powered responses using Google's Gemini API.
"""

import asyncio
import logging
import signal
import sys
from dotenv import load_dotenv

from src.config import load_and_validate_config
from src.bot.discord_bot import DiscordBot

# Load environment variables
load_dotenv()

# Global bot instance for signal handling
bot_instance = None

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger = logging.getLogger(__name__)
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    
    if bot_instance:
        # Create a new event loop for cleanup if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the cleanup
                asyncio.create_task(shutdown_bot())
            else:
                # Run cleanup in new loop
                asyncio.run(shutdown_bot())
        except Exception as e:
            logger.error(f"Error during signal cleanup: {e}")
    
    sys.exit(0)

async def shutdown_bot():
    """Perform graceful bot shutdown."""
    logger = logging.getLogger(__name__)
    
    if bot_instance:
        try:
            logger.info("Closing Discord connection...")
            await bot_instance.close()
            logger.info("✅ Bot shutdown complete")
        except Exception as e:
            logger.error(f"Error during bot shutdown: {e}")

async def main():
    """Main application entry point."""
    global bot_instance
    
    # Load and validate configuration first
    config = load_and_validate_config()
    logger = logging.getLogger(__name__)
    
    logger.info("🚀 Starting Discord Grok Bot...")
    logger.info(f"Configuration: Max context messages: {config.max_context_messages}, "
                f"Reply context range: {config.reply_context_range}, "
                f"Response timeout: {config.response_timeout}s")
    
    try:
        # Initialize the Discord bot
        bot_instance = DiscordBot(config)
        logger.info("✅ Bot instance created successfully")
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start the bot
        logger.info("🔌 Connecting to Discord...")
        await bot_instance.start(config.discord_token)
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        if bot_instance:
            try:
                await bot_instance.close()
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup: {cleanup_error}")
        raise
    finally:
        # Ensure cleanup happens
        if bot_instance:
            try:
                await shutdown_bot()
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