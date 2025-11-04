"""
Slash commands for the Discord Grok Bot.

This module contains all slash commands for bot configuration,
statistics, and utility functions.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands

from ..config import BotConfig
from ..services.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class BotCommands(app_commands.CommandTree):
    """Command tree for Discord bot slash commands."""
    
    def __init__(self, client, config: BotConfig, gemini_client: GeminiClient, performance_logger):
        """
        Initialize bot commands.
        
        Args:
            client: Discord client instance
            config: Bot configuration
            gemini_client: Gemini API client
            performance_logger: Performance logging instance
        """
        super().__init__(client)
        self.config = config
        self.gemini_client = gemini_client
        self.performance_logger = performance_logger
        self.client = client
        

async def setup_commands(bot, config: BotConfig, gemini_client: GeminiClient, performance_logger):
    """
    Set up all slash commands for the bot.
    
    Args:
        bot: Discord bot instance
        config: Bot configuration
        gemini_client: Gemini API client
        performance_logger: Performance logging instance
    """
    
    @bot.tree.command(name="ping", description="Check if the bot is responsive")
    async def ping(interaction: discord.Interaction):
        """Check bot responsiveness and latency."""
        latency = round(bot.latency * 1000)
        
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Bot is online and responsive!",
            color=discord.Color.green()
        )
        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
        embed.add_field(name="Status", value="✅ Operational", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
    @bot.tree.command(name="model", description="Switch between Gemini Flash AI models")
    @app_commands.describe(
        model_name="The Gemini Flash model to use"
    )
    @app_commands.choices(model_name=[
        app_commands.Choice(name="Gemini 2.5 Flash (Latest, Recommended)", value="gemini-2.5-flash"),
        app_commands.Choice(name="Gemini 2.5 Flash-Lite (Ultra Fast)", value="gemini-2.5-flash-lite"),
        app_commands.Choice(name="Gemini 2.0 Flash (Stable)", value="gemini-2.0-flash-exp"),
        app_commands.Choice(name="Gemini 2.0 Flash-Lite (Lightweight)", value="gemini-2.0-flash-lite"),
    ])
    async def model(interaction: discord.Interaction, model_name: app_commands.Choice[str]):
        """Switch the Gemini AI model."""
        try:
            old_model = gemini_client.get_current_model()
            success = gemini_client.set_model(model_name.value)
            
            if success:
                embed = discord.Embed(
                    title="🤖 Model Changed",
                    description=f"Successfully switched AI model!",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Previous Model", value=old_model, inline=True)
                embed.add_field(name="New Model", value=model_name.value, inline=True)
                embed.add_field(
                    name="Model Info", 
                    value=_get_model_description(model_name.value), 
                    inline=False
                )
                
                logger.info(f"User {interaction.user} changed model from {old_model} to {model_name.value}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    "❌ Failed to change model. Please try again later.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error changing model: {e}")
            await interaction.response.send_message(
                f"❌ Error: {str(e)}",
                ephemeral=True
            )
    
    
    @bot.tree.command(name="prompt-mode", description="Switch between thinking (detailed) and short (concise) response modes")
    @app_commands.describe(
        mode="The response mode to use"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Short - Concise & Direct Responses", value="short"),
        app_commands.Choice(name="Thinking - Detailed Analysis (Single-Use)", value="thinking"),
    ])
    async def prompt_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
        """Switch between prompt modes for different response styles."""
        try:
            old_mode = gemini_client.get_prompt_mode()
            success = gemini_client.set_prompt_mode(mode.value)
            
            if success:
                embed = discord.Embed(
                    title="💭 Prompt Mode Changed",
                    description=f"Successfully switched response mode!",
                    color=discord.Color.purple()
                )
                embed.add_field(name="Previous Mode", value=old_mode.capitalize(), inline=True)
                embed.add_field(name="New Mode", value=mode.value.capitalize(), inline=True)
                
                mode_descriptions = {
                    "short": "✨ **Short Mode:** Quick, concise responses focused on directly answering your question without unnecessary details.",
                    "thinking": "🧠 **Thinking Mode:** Comprehensive, in-depth analysis with detailed explanations, context, and thorough reasoning.\n\n⚡ *Note: Thinking mode automatically reverts to Short mode after one use.*"
                }
                
                embed.add_field(
                    name="Mode Description", 
                    value=mode_descriptions.get(mode.value, "Standard response mode"), 
                    inline=False
                )
                
                logger.info(f"User {interaction.user} changed prompt mode from {old_mode} to {mode.value}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    "❌ Failed to change prompt mode. Please try again later.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error changing prompt mode: {e}")
            await interaction.response.send_message(
                f"❌ Error: {str(e)}",
                ephemeral=True
            )
    
    
    @bot.tree.command(name="stats", description="View bot statistics and usage data")
    async def stats(interaction: discord.Interaction):
        """Display bot statistics including token usage and performance metrics."""
        try:
            # Get performance metrics
            metrics = performance_logger.get_summary_stats()
            
            # Calculate uptime
            uptime = datetime.now() - bot.start_time
            uptime_str = _format_timedelta(uptime)
            
            # Create embed
            embed = discord.Embed(
                title="📊 Bot Statistics",
                description="Usage statistics and performance metrics",
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            
            # Bot info
            embed.add_field(
                name="🤖 Bot Info",
                value=f"**Uptime:** {uptime_str}\n"
                      f"**Guilds:** {len(bot.guilds)}\n"
                      f"**Latency:** {round(bot.latency * 1000)}ms",
                inline=False
            )
            
            # Message statistics
            if metrics.get('message_count', 0) > 0:
                embed.add_field(
                    name="💬 Message Statistics",
                    value=f"**Total Messages:** {metrics.get('message_count', 0):,}\n"
                          f"**Avg Response Time:** {metrics.get('avg_response_time', 0):.2f}s\n"
                          f"**Success Rate:** {metrics.get('success_rate', 0):.1f}%",
                    inline=True
                )
            
            # API statistics
            if metrics.get('api_calls', 0) > 0:
                embed.add_field(
                    name="🔌 API Statistics",
                    value=f"**API Calls:** {metrics.get('api_calls', 0):,}\n"
                          f"**Avg API Time:** {metrics.get('avg_api_time', 0):.2f}s\n"
                          f"**Failures:** {metrics.get('api_failures', 0):,}",
                    inline=True
                )
            
            # Token usage (estimated)
            if metrics.get('total_tokens', 0) > 0:
                embed.add_field(
                    name="🎟️ Token Usage (Estimated)",
                    value=f"**Total Tokens:** {metrics.get('total_tokens', 0):,}\n"
                          f"**Input Tokens:** {metrics.get('input_tokens', 0):,}\n"
                          f"**Output Tokens:** {metrics.get('output_tokens', 0):,}",
                    inline=False
                )
            
            # Current model
            current_model = gemini_client.get_current_model()
            current_prompt_mode = gemini_client.get_prompt_mode()
            embed.add_field(
                name="⚙️ Configuration",
                value=f"**Model:** {current_model}\n"
                      f"**Prompt Mode:** {current_prompt_mode.capitalize()}\n"
                      f"**Max Context:** {config.max_context_messages} messages\n"
                      f"**Timeout:** {config.response_timeout}s",
                inline=False
            )
            
            # API Usage and Rate Limits
            api_info = gemini_client.get_api_usage_info()
            if api_info.get('api_configured'):
                rate_limits = api_info.get('rate_limits', {}).get('free_tier', {})
                embed.add_field(
                    name="📈 API Rate Limits (Free Tier)",
                    value=f"**Requests/Minute:** {rate_limits.get('requests_per_minute', 'N/A'):,}\n"
                          f"**Requests/Day:** {rate_limits.get('requests_per_day', 'N/A'):,}\n"
                          f"**Tokens/Minute:** {rate_limits.get('tokens_per_minute', 'N/A'):,}",
                    inline=True
                )
                
                model_caps = api_info.get('model_capabilities', {})
                embed.add_field(
                    name="🔧 Model Capabilities",
                    value=f"**Max Input:** {model_caps.get('max_input_tokens', 0):,} tokens\n"
                          f"**Max Output:** {model_caps.get('max_output_tokens', 0):,} tokens\n"
                          f"**Context Window:** 1M tokens",
                    inline=True
                )
            
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error generating stats: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Failed to generate statistics. Please try again later.",
                ephemeral=True
            )
    
    
    @bot.tree.command(name="help", description="Show help information and available commands")
    async def help_command(interaction: discord.Interaction):
        """Display help information about the bot."""
        embed = discord.Embed(
            title="🤖 Discord Grok Bot - Help",
            description="AI-powered conversational bot using Google's Gemini API",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="💬 How to Use",
            value="Simply mention the bot (@grok) in any message to get an AI response!\n"
                  "The bot will analyze recent conversation context to provide relevant answers.",
            inline=False
        )
        
        embed.add_field(
            name="📝 Slash Commands",
            value="`/ping` - Check bot status and latency\n"
                  "`/model` - Switch between AI models\n"
                  "`/prompt-mode` - Switch between thinking and short modes\n"
                  "`/stats` - View usage statistics\n"
                  "`/config` - View current configuration\n"
                  "`/help` - Show this help message",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Features",
            value="✅ Context-aware responses\n"
                  "✅ Reply chain analysis\n"
                  "✅ Multiple AI models\n"
                  "✅ Graceful error handling",
            inline=False
        )
        
        embed.add_field(
            name="🔗 Links",
            value="[Documentation](https://github.com/dankmrpanda/discord-ai-bot) • "
                  "[Report Issues](https://github.com/dankmrpanda/discord-ai-bot/issues)",
            inline=False
        )
        
        embed.set_footer(text="Made with ❤️ using discord.py and Google Gemini")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
    @bot.tree.command(name="config", description="View current bot configuration")
    async def config_command(interaction: discord.Interaction):
        """Display current bot configuration."""
        embed = discord.Embed(
            title="⚙️ Bot Configuration",
            description="Current settings and parameters",
            color=discord.Color.gold()
        )
        
        # Model settings
        current_model = gemini_client.get_current_model()
        current_prompt_mode = gemini_client.get_prompt_mode()
        embed.add_field(
            name="🤖 AI Model",
            value=f"**Current Model:** {current_model}\n"
                  f"**Description:** {_get_model_description(current_model)}",
            inline=False
        )
        
        # Prompt mode settings
        mode_emoji = "🧠" if current_prompt_mode == "thinking" else "✨"
        mode_desc = "Detailed analysis" if current_prompt_mode == "thinking" else "Concise responses"
        embed.add_field(
            name="💭 Response Mode",
            value=f"{mode_emoji} **{current_prompt_mode.capitalize()} Mode**\n{mode_desc}",
            inline=False
        )
        
        # Context settings
        embed.add_field(
            name="📝 Context Settings",
            value=f"**Max Messages:** {config.max_context_messages}\n"
                  f"**Reply Range:** {config.reply_context_range} messages\n"
                  f"**Time Limit:** 24 hours",
            inline=True
        )
        
        # Performance settings
        embed.add_field(
            name="⚡ Performance",
            value=f"**Timeout:** {config.response_timeout}s\n"
                  f"**Max Retries:** {config.max_retries}\n"
                  f"**Logging:** {config.log_level}",
            inline=True
        )
        
        # Permissions
        if interaction.guild:
            bot_member = interaction.guild.get_member(bot.user.id)
            if bot_member:
                perms = interaction.channel.permissions_for(bot_member)
                embed.add_field(
                    name="🔒 Permissions",
                    value=f"**Read Messages:** {'✅' if perms.read_messages else '❌'}\n"
                          f"**Send Messages:** {'✅' if perms.send_messages else '❌'}\n"
                          f"**Message History:** {'✅' if perms.read_message_history else '❌'}",
                    inline=False
                )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
    @bot.tree.command(name="clear-cache", description="Clear bot's message cache (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def clear_cache(interaction: discord.Interaction):
        """Clear the bot's internal caches (admin only)."""
        try:
            # Clear performance logger cache
            performance_logger.clear_cache()
            
            embed = discord.Embed(
                title="🗑️ Cache Cleared",
                description="Successfully cleared bot caches!",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Cleared",
                value="✅ Performance metrics cache\n"
                      "✅ Internal statistics",
                inline=False
            )
            
            logger.info(f"Admin {interaction.user} cleared bot cache")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            await interaction.response.send_message(
                "❌ Failed to clear cache. Please check logs.",
                ephemeral=True
            )


def _get_model_description(model_name: str) -> str:
    """Get description for a specific model."""
    descriptions = {
        "gemini-2.5-flash": "🌟 Latest generation model. Best overall performance with advanced features and optimal speed.",
        "gemini-2.5-flash-lite": "⚡ Ultra-fast lightweight variant. Optimized for maximum speed with minimal latency.",
        "gemini-2.0-flash-exp": "🚀 Stable 2.0 generation. Reliable performance with excellent capabilities.",
        "gemini-2.0-flash-lite": "💨 Lightweight 2.0 variant. Great for simple tasks requiring quick responses.",
    }
    return descriptions.get(model_name, "Standard Gemini Flash model")


def _format_timedelta(td: timedelta) -> str:
    """Format a timedelta object into a human-readable string."""
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)
