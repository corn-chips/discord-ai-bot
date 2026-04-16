"""
Slash commands for the Discord Grok Bot.

This module contains all slash commands for bot configuration,
statistics, and utility functions.
"""

import asyncio
import logging
import os
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional

import discord
import jinja2
from discord import app_commands

from ..config import BotConfig
from ..services.gemini_client import GeminiClient
from ..services.token_tracker import TokenTracker
from ..services.channel_settings_service import ChannelSettingsService
from ..services.user_preferences_service import UserPreferencesService
from ..services.pin_service import PinService
from ..services.message_visibility_service import MessageVisibilityService
from ..models.data_models import MessageContext


logger = logging.getLogger(__name__)


async def setup_commands(
    bot,
    config: BotConfig,
    gemini_client: GeminiClient,
    performance_logger,
    token_tracker: Optional[TokenTracker],
):
    """
    Set up all slash commands for the bot.
    
    Args:
        bot: Discord bot instance
        config: Bot configuration
        gemini_client: Gemini API client
        performance_logger: Performance logging instance
        token_tracker: Token tracking service (optional)
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
        
        await interaction.response.send_message(embed=embed)
    
    # Create config group
    config_group = app_commands.Group(name="config", description="Configure bot settings")

    @config_group.command(name="model", description="Switch between Gemini Flash AI models")
    @app_commands.describe(
        model_name="The Gemini Flash model to use"
    )
    @app_commands.choices(model_name=[
        app_commands.Choice(name=model["name"], value=model["value"])
        for model in config.available_models
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
                    value=_get_model_description(model_name.value, config), 
                    inline=False
                )
                
                logger.info(f"User {interaction.user} changed model from {old_model} to {model_name.value}")
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message(
                    "❌ Failed to change model. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error changing model: {e}")
            await interaction.response.send_message(
                f"❌ Error: {str(e)}"
            )

    @config_group.command(name="thinking", description="Set API thinking level for responses")
    @app_commands.describe(level="Thinking level to use (default follows model_complexity in config.yaml)")
    @app_commands.choices(level=[
        app_commands.Choice(name="Default (Use config.yaml)", value="default"),
        app_commands.Choice(name="Minimal", value="minimal"),
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high"),
    ])
    async def thinking(interaction: discord.Interaction, level: app_commands.Choice[str]):
        """Set runtime thinking level using Gemini API native thinking config."""
        success = gemini_client.set_thinking_level(level.value)

        if success:
            display_level = level.value.capitalize()
            description = (
                "Runtime thinking level override updated. "
                "Use `Default` to return to complexity-configured levels from config.yaml."
            )
            embed = discord.Embed(
                title=f"🧠 Thinking Level: {display_level}",
                description=description,
                color=discord.Color.purple() if level.value in {"medium", "high"} else discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("Failed to set thinking level.", ephemeral=True)

    @config_group.command(name="deepsearch", description="Toggle DeepSearch (Force Google Search)")
    @app_commands.describe(enabled="Enable or disable DeepSearch")
    async def deepsearch(interaction: discord.Interaction, enabled: bool):
        """Toggle DeepSearch to force Google Search on all queries."""
        gemini_client.set_force_search(enabled)
        
        status = "✅ Enabled" if enabled else "❌ Disabled"
        description = "Bot will now search the web for EVERY query." if enabled else "Bot will only search when explicitly asked."
        
        embed = discord.Embed(
            title=f"🌐 DeepSearch {status}",
            description=description,
            color=discord.Color.blue() if enabled else discord.Color.light_grey()
        )
        await interaction.response.send_message(embed=embed)

    @config_group.command(name="image-generation", description="Enable or disable AI image generation")
    @app_commands.describe(enabled="Enable or disable image generation requests")
    async def image_generation(interaction: discord.Interaction, enabled: bool):
        """Toggle runtime image generation availability."""
        if not getattr(bot, "image_processing_service", None):
            await interaction.response.send_message(
                "Image processing service is not configured, so image generation cannot be toggled.",
                ephemeral=True,
            )
            return

        bot.image_generation_enabled = enabled
        status = "✅ Enabled" if enabled else "❌ Disabled"
        description = (
            "Users can now request image generation prompts."
            if enabled
            else "Image generation requests are now disabled."
        )
        embed = discord.Embed(
            title=f"🖼️ Image Generation {status}",
            description=description,
            color=discord.Color.green() if enabled else discord.Color.light_grey(),
        )
        await interaction.response.send_message(embed=embed)

    @config_group.command(name="debug", description="Toggle Debug Logging")
    @app_commands.describe(enabled="Enable or disable verbose debug logging")
    async def debug(interaction: discord.Interaction, enabled: bool):
        """Toggle debug logging level."""
        root_logger = logging.getLogger()
        
        if enabled:
            root_logger.setLevel(logging.DEBUG)
            status = "✅ Enabled"
            color = discord.Color.orange()
        else:
            root_logger.setLevel(logging.INFO)
            status = "❌ Disabled"
            color = discord.Color.light_grey()
            
        embed = discord.Embed(
            title=f"🐞 Debug Mode {status}",
            description=f"Logging level set to {'DEBUG' if enabled else 'INFO'}.",
            color=color
        )
        await interaction.response.send_message(embed=embed)
    
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
            current_thinking_level = gemini_client.get_thinking_level()
            embed.add_field(
                name="⚙️ Configuration",
                value=f"**Model:** {current_model}\n"
                      f"**Thinking Level:** {current_thinking_level.capitalize()}\n"
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
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error generating stats: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Failed to generate statistics. Please try again later."
            )
    
    
    @bot.tree.command(name="token-leaderboard", description="See the top token users in this server")
    async def token_leaderboard(interaction: discord.Interaction):
        """Display top 10 token consumers for the current guild."""

        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not token_tracker:
            await interaction.response.send_message(
                "Token tracking is not configured for this bot.",
                ephemeral=True,
            )
            return

        try:
            entries = await token_tracker.get_top_users(interaction.guild.id, limit=config.leaderboard_limit)
        except Exception as exc:
            logger.error("Failed to load token leaderboard: %s", exc, exc_info=True)
            await interaction.response.send_message(
                "❌ Unable to load the token leaderboard right now.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎟️ Token Leaderboard — {interaction.guild.name}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )

        if not entries:
            embed.description = "No token usage has been recorded yet. Be the first to talk to the bot!"
        else:
            lines = []
            for idx, entry in enumerate(entries, start=1):
                mention = f"<@{entry.user_id}>"
                display = entry.username or mention
                line = (
                    f"**{idx}.** {display} ({mention}) — {entry.total_tokens:,} tokens"
                    f" • {entry.request_count:,} requests"
                )
                lines.append(line)
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)


    @config_group.command(name="info", description="View current bot configuration and feature status")
    async def config_info(interaction: discord.Interaction):
        """Display current bot configuration and feature availability."""
        embed = discord.Embed(
            title="⚙️ Enhanced Bot Configuration",
            description="Current settings, parameters, and feature status",
            color=discord.Color.gold()
        )
        
        # Model settings
        current_model = gemini_client.get_current_model()
        current_thinking_level = gemini_client.get_thinking_level()
        embed.add_field(
            name="🤖 AI Model",
            value=f"**Current Model:** {current_model}\n"
                  f"**Description:** {_get_model_description(current_model, config)}",
            inline=False
        )
        
        # Thinking level settings
        mode_emoji = "🧠" if current_thinking_level in {"medium", "high"} else "✨"
        mode_desc = ("Deeper reasoning via Gemini API thinking config" if current_thinking_level in {"medium", "high"} else "Faster responses with lower thinking depth")
        embed.add_field(
            name="💭 Thinking Level",
            value=f"{mode_emoji} **{current_thinking_level.capitalize()}**\n{mode_desc}",
            inline=False
        )
        
        # Context settings
        embed.add_field(
            name="📝 Context Settings",
            value=f"**Max Messages:** {config.max_context_messages}\n"
                  f"**Reply Range:** {config.reply_context_range} messages\n"
                  f"**Message Split Length:** {config.message_split_length} chars",
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
        
        # Image processing settings (if available)
        if hasattr(bot, 'image_processing_service') and bot.image_processing_service:
            generation_enabled = getattr(bot, "image_generation_enabled", True)
            embed.add_field(
                name="🖼️ Image Processing",
                value=f"**Max Image Size:** {config.max_image_size_mb}MB\n"
                      f"**Processing Timeout:** {config.image_processing_timeout}s\n"
                      f"**Max Concurrent:** {config.max_concurrent_image_edits}\n"
                      f"**Image Generation:** {'Enabled' if generation_enabled else 'Disabled'}",
                inline=True
            )
        
        # UX Enhancement settings
        ux_features = []
        if config.show_typing_indicators:
            ux_features.append("✅ Typing indicators")
        if config.use_rich_embeds:
            ux_features.append("✅ Rich embeds")
        if config.enable_reaction_feedback:
            ux_features.append("✅ Reaction feedback")
        if config.preserve_code_blocks:
            ux_features.append("✅ Code block preservation")
        if config.add_continuation_indicators:
            ux_features.append("✅ Continuation indicators")
        
        if ux_features:
            embed.add_field(
                name="✨ UX Enhancements",
                value="\n".join(ux_features),
                inline=True
            )
        
        # Feature availability
        features = config.get_feature_availability()
        if hasattr(bot, "image_processing_service") and bot.image_processing_service:
            features["image_generation"] = bool(getattr(bot, "image_generation_enabled", True))
        feature_status = []
        for feature, available in features.items():
            status = "✅" if available else "❌"
            feature_name = feature.replace('_', ' ').title()
            feature_status.append(f"{status} {feature_name}")
        
        embed.add_field(
            name="🔧 Feature Status",
            value="\n".join(feature_status),
            inline=False
        )
        
        # Service health (if available)
        if hasattr(bot, 'get_service_health_status'):
            try:
                service_health = await bot.get_service_health_status()
                health_status = []
                for service, status in service_health.items():
                    service_name = service.replace('_', ' ').title()
                    health_status.append(f"{status} {service_name}")
                
                embed.add_field(
                    name="🏥 Service Health",
                    value="\n".join(health_status[:6]),  # Limit to 6 services
                    inline=True
                )
            except Exception as e:
                logger.error(f"Error getting service health: {e}")
        
        # Permissions
        if interaction.guild:
            bot_member = interaction.guild.get_member(bot.user.id)
            if bot_member:
                perms = interaction.channel.permissions_for(bot_member)
                embed.add_field(
                    name="🔒 Bot Permissions",
                    value=f"**Read Messages:** {'✅' if perms.read_messages else '❌'}\n"
                          f"**Send Messages:** {'✅' if perms.send_messages else '❌'}\n"
                          f"**Message History:** {'✅' if perms.read_message_history else '❌'}\n"
                          f"**Add Reactions:** {'✅' if perms.add_reactions else '❌'}\n"
                          f"**Attach Files:** {'✅' if perms.attach_files else '❌'}",
                    inline=True
                )
        
        # Command suggestion settings
        embed.add_field(
            name="🎯 Advanced Settings",
            value=f"**Command Suggestion Threshold:** {config.command_suggestion_threshold:.1%}\n"
                  f"**Performance Logging:** {'✅' if config.enable_performance_logging else '❌'}",
            inline=True
        )
        
        embed.set_footer(text=f"Configuration loaded at startup • Use /help for usage information")
        
        await interaction.response.send_message(embed=embed)
    
    
    @bot.tree.command(name="features", description="Discover all available bot features and capabilities")
    async def features_command(interaction: discord.Interaction):
        """Display feature discovery information."""
        
        # Feature discovery
        embed = discord.Embed(
            title="✨ Bot Features & Capabilities",
            description="Discover everything this bot can do for you!",
            color=discord.Color.purple()
        )
        
        # Core AI features
        embed.add_field(
            name="🤖 AI Conversations",
            value="• **Context-aware responses** - Remembers conversation history\n"
                  "• **Multi-turn conversations** - Natural dialogue flow\n"
                  "• **Reply chain analysis** - Understands conversation threads\n"
                  "• **File analysis** - Upload PDFs and text files\n"
                  "• **Multiple AI models** - Choose the best for your needs",
            inline=False
        )
        
        # Image editing (if available)
        if hasattr(bot, 'image_processing_service') and bot.image_processing_service:
            embed.add_field(
                name="🖼️ Image Editing",
                value="• **Object removal** - Remove unwanted elements\n"
                      "• **Background replacement** - Change image backgrounds\n"
                      "• **Artistic style transfer** - Apply artistic styles\n"
                      "• **Color adjustments** - Brightness, contrast, saturation\n"
                      "• **Natural language instructions** - No complex commands needed",
                inline=False
            )
        
        # Enhanced UX features
        embed.add_field(
            name="✨ Enhanced Experience",
            value="• **Smart message splitting** - Long responses split intelligently\n"
                  "• **Rich embeds** - Beautiful, structured responses\n"
                  "• **Typing indicators** - Shows when bot is working\n"
                  "• **Reaction feedback** - Visual confirmation of actions\n"
                  "• **Progress updates** - Real-time processing status",
            inline=False
        )
        
        # Getting started
        embed.add_field(
            name="🚀 Getting Started",
            value="1. **Mention the bot** in any message: `@bot your question`\n"
                  "2. **Upload images** with editing instructions\n"
                  "3. **Use slash commands** for quick actions\n"
                  "4. **Reply to bot messages** for context-aware conversations\n"
                  "5. **Try different AI models** with `/model`",
            inline=False
        )
        
        embed.set_footer(text="Use /help for detailed instructions • Try mentioning the bot to get started!")
        
        await interaction.response.send_message(embed=embed)
    
    
    # Image editing commands (only if image processing is available)
    if hasattr(bot, 'image_processing_service') and bot.image_processing_service:
        
        @bot.tree.command(name="edit-image", description="Edit an uploaded image using AI")
        @app_commands.describe(
            image="The image to edit",
            instruction="What you want to do to the image",
            edit_type="Type of edit to perform (optional - will be auto-detected)"
        )
        @app_commands.choices(edit_type=[
            app_commands.Choice(name="Auto-detect (Recommended)", value="general_edit"),
            app_commands.Choice(name="Remove Object", value="object_removal"),
            app_commands.Choice(name="Change Background", value="background_replacement"),
            app_commands.Choice(name="Apply Style", value="style_transfer"),
            app_commands.Choice(name="Adjust Colors", value="color_adjustment"),
        ])
        async def edit_image(interaction: discord.Interaction, image: discord.Attachment, 
                           instruction: str, edit_type: Optional[app_commands.Choice[str]] = None):
            """Edit an image using AI image processing."""
            try:
                # Validate image attachment
                if not image.content_type or not image.content_type.startswith('image/'):
                    await interaction.response.send_message(
                        "❌ Please upload a valid image file (PNG, JPEG, GIF)."
                    )
                    return
                
                # Check image size
                max_size_bytes = bot.config.max_image_size_mb * 1024 * 1024
                if image.size > max_size_bytes:
                    await interaction.response.send_message(
                        f"❌ Image is too large. Maximum size is {bot.config.max_image_size_mb}MB."
                    )
                    return
                
                # Defer response since this will take time
                await interaction.response.defer()
                
                # Download image
                image_data = await image.read()
                
                # Import required types
                from ..models.data_models import ImageEditRequest, EditType
                
                # Determine edit type
                if edit_type:
                    selected_edit_type = EditType(edit_type.value)
                else:
                    selected_edit_type = EditType.GENERAL_EDIT
                
                # Create edit request
                edit_request = ImageEditRequest(
                    user_id=str(interaction.user.id),
                    image_data=image_data,
                    instruction=instruction,
                    edit_type=selected_edit_type,
                    timestamp=datetime.now(),
                    channel_id=str(interaction.channel.id)
                )
                
                # Check rate limits
                rate_limit_info = await bot.image_processing_service.get_user_rate_limit_info(str(interaction.user.id))
                if rate_limit_info['requests_remaining'] <= 0:
                    reset_time = rate_limit_info.get('reset_time')
                    if reset_time:
                        await interaction.followup.send(f"⏳ You've reached your image editing limit. Try again after {reset_time}.")
                    else:
                        await interaction.followup.send("⏳ You've reached your image editing limit. Please try again later.")
                    return
                
                # Submit job
                job_id = await bot.image_processing_service.process_image_edit(edit_request)
                
                # Send initial response
                embed = discord.Embed(
                    title="🎨 Image Editing Started",
                    description=f"Processing your image edit request...",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Instruction", value=instruction, inline=False)
                embed.add_field(name="Edit Type", value=selected_edit_type.value.replace('_', ' ').title(), inline=True)
                embed.add_field(name="Status", value="⏳ Processing...", inline=True)
                
                await interaction.followup.send(embed=embed)
                
                # Wait for completion
                max_wait_time = 120  # 2 minutes
                check_interval = 3
                elapsed_time = 0
                
                while elapsed_time < max_wait_time:
                    job_status = await bot.image_processing_service.get_job_status(job_id)
                    
                    if not job_status:
                        await interaction.followup.send("❌ Image editing job was lost. Please try again.")
                        return
                    
                    if job_status.status.value == "completed":
                        if job_status.result and job_status.result.success:
                            # Send the edited image
                            import io
                            edited_image_file = discord.File(
                                io.BytesIO(job_status.result.edited_image),
                                filename=f"edited_{interaction.user.id}_{int(datetime.now().timestamp())}.png"
                            )
                            
                            success_embed = discord.Embed(
                                title="✅ Image Editing Complete",
                                description=f"Your image has been successfully edited!",
                                color=discord.Color.green()
                            )
                            success_embed.add_field(
                                name="Processing Time", 
                                value=f"{job_status.result.processing_time:.1f} seconds", 
                                inline=True
                            )
                            
                            await interaction.followup.send(embed=success_embed, file=edited_image_file)
                            return
                        else:
                            error_msg = job_status.result.error_message if job_status.result else "Unknown error"
                            await interaction.followup.send(f"❌ Image editing failed: {error_msg}")
                            return
                    
                    elif job_status.status.value == "failed":
                        error_msg = job_status.error_message or "Unknown error"
                        await interaction.followup.send(f"❌ Image editing failed: {error_msg}")
                        return
                    
                    await asyncio.sleep(check_interval)
                    elapsed_time += check_interval
                
                # Timeout
                await interaction.followup.send("⏰ Image editing is taking longer than expected. Please try again later.")
                
            except Exception as e:
                logger.error(f"Error in edit-image command: {e}", exc_info=True)
                try:
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}")
                except discord.HTTPException:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}")
        
        
        @bot.tree.command(name="image-queue", description="Check the image processing queue status")
        async def image_queue(interaction: discord.Interaction):
            """Check the status of the image processing queue."""
            try:
                queue_info = bot.image_processing_service.get_queue_info()
                stats = bot.image_processing_service.get_statistics()
                
                embed = discord.Embed(
                    title="🎨 Image Processing Queue",
                    description="Current queue status and statistics",
                    color=discord.Color.blue()
                )
                
                embed.add_field(
                    name="📊 Queue Status",
                    value=f"**Queued Jobs:** {queue_info['queue_size']}\n"
                          f"**Active Jobs:** {queue_info['active_jobs']}\n"
                          f"**Max Concurrent:** {queue_info['max_concurrent']}",
                    inline=True
                )
                
                embed.add_field(
                    name="📈 Statistics",
                    value=f"**Total Requests:** {stats['total_requests']}\n"
                          f"**Successful:** {stats['successful_requests']}\n"
                          f"**Failed:** {stats['failed_requests']}",
                    inline=True
                )
                
                if stats['average_processing_time'] > 0:
                    embed.add_field(
                        name="⏱️ Performance",
                        value=f"**Avg Processing Time:** {stats['average_processing_time']:.1f}s\n"
                              f"**Service Available:** {'✅' if queue_info['service_available'] else '❌'}",
                        inline=True
                    )
                
                # User rate limit info
                rate_limit_info = await bot.image_processing_service.get_user_rate_limit_info(str(interaction.user.id))
                embed.add_field(
                    name="👤 Your Rate Limit",
                    value=f"**Requests Used:** {rate_limit_info['requests_used']}\n"
                          f"**Remaining:** {rate_limit_info['requests_remaining']}",
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed)
                
            except Exception as e:
                logger.error(f"Error in image-queue command: {e}")
                await interaction.response.send_message(
                    "❌ Failed to get queue information. Please try again later."
                )
    
    
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
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            await interaction.response.send_message(
                "❌ Failed to clear cache. Please check logs."
            )
    
    
    @bot.tree.command(name="dev", description="Toggle developer mode for detailed error output")
    #@app_commands.default_permissions(administrator=True)
    async def dev_mode(interaction: discord.Interaction):
        """Toggle developer mode for detailed error messages (admin only)."""
        try:
            # Toggle dev mode
            config.dev_mode_enabled = not config.dev_mode_enabled
            
            status = "enabled" if config.dev_mode_enabled else "disabled"
            emoji = "🔧" if config.dev_mode_enabled else "🔒"
            color = discord.Color.orange() if config.dev_mode_enabled else discord.Color.blue()
            
            embed = discord.Embed(
                title=f"{emoji} Developer Mode {status.capitalize()}",
                description=f"Developer mode has been **{status}**.",
                color=color
            )
            
            if config.dev_mode_enabled:
                embed.add_field(
                    name="⚠️ What This Means",
                    value="• Full error messages will be displayed\n"
                          "• API errors will show complete stack traces\n"
                          "• Detailed debugging information included\n"
                          "• Useful for troubleshooting issues",
                    inline=False
                )
            else:
                embed.add_field(
                    name="ℹ️ What This Means",
                    value="• User-friendly error messages only\n"
                          "• Simplified notifications\n"
                          "• Stack traces hidden from users\n"
                          "• Better user experience",
                    inline=False
                )
            
            logger.info(f"Admin {interaction.user} {status} developer mode")
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error toggling dev mode: {e}")
            await interaction.response.send_message(
                "❌ Failed to toggle developer mode. Please check logs."
            )
    
    
    @bot.tree.command(name="api-usage", description="Display API usage statistics and rate limit information")
    async def api_usage(interaction: discord.Interaction):
        """Display comprehensive API usage statistics, rate limits, and logging metrics."""
        try:
            # Get API information from Gemini client
            api_info = gemini_client.get_api_usage_info()
            
            # Get performance metrics
            metrics = performance_logger.get_summary_stats()
            
            # Get nano-banana service health (if available)
            nano_health = None
            try:
                from ..services.nano_banana_client import nano_banana_client
                if nano_banana_client:
                    nano_health = nano_banana_client.get_service_health_info()
            except (ImportError, AttributeError):
                pass
            
            # Create main embed
            embed = discord.Embed(
                title="📊 API Usage & Statistics",
                description="Real-time API usage rates, quotas, and performance metrics",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            # Current Model Configuration
            current_model = gemini_client.get_current_model()
            current_thinking_level = gemini_client.get_thinking_level()
            configured_image_model = config.nano_banana_model
            if getattr(bot, "image_processing_service", None) and getattr(bot.image_processing_service, "client", None):
                configured_image_model = bot.image_processing_service.client.model_name
            embed.add_field(
                name="⚙️ Current Configuration",
                value=f"**Text Model:** `{current_model}`\n"
                      f"**Thinking Level:** {current_thinking_level.capitalize()}\n"
                      f"**Image Model:** `{configured_image_model}`",
                inline=False
            )
            
            # API Rate Limits - Free Tier
            if api_info.get('api_configured'):
                rate_limits = api_info.get('rate_limits', {}).get('free_tier', {})
                embed.add_field(
                    name="🔓 Free Tier Rate Limits",
                    value=f"📬 **Requests/Minute:** {rate_limits.get('requests_per_minute', 'N/A')}\n"
                          f"📅 **Requests/Day:** {rate_limits.get('requests_per_day', 'N/A'):,}\n"
                          f"🎟️ **Tokens/Minute:** {rate_limits.get('tokens_per_minute', 'N/A'):,}",
                    inline=True
                )
                
                # Model Capabilities
                model_caps = api_info.get('model_capabilities', {})
                embed.add_field(
                    name="🔧 Model Capabilities",
                    value=f"📥 **Max Input:** {model_caps.get('max_input_tokens', 0):,} tokens\n"
                          f"📤 **Max Output:** {model_caps.get('max_output_tokens', 0):,} tokens\n"
                          f"🪟 **Context Window:** 1M tokens",
                    inline=True
                )
            
            # Current Usage Statistics
            if metrics.get('api_calls', 0) > 0:
                embed.add_field(
                    name="📈 API Usage Statistics",
                    value=f"**Total API Calls:** {metrics.get('api_calls', 0):,}\n"
                          f"**Avg API Time:** {metrics.get('avg_api_time', 0):.2f}s\n"
                          f"**API Failures:** {metrics.get('api_failures', 0):,}\n"
                          f"**Success Rate:** {((metrics.get('api_calls', 0) - metrics.get('api_failures', 0)) / metrics.get('api_calls', 1) * 100):.1f}%",
                    inline=False
                )
            
            # Token Usage (Estimated)
            if metrics.get('total_tokens', 0) > 0:
                embed.add_field(
                    name="🎟️ Token Usage (Estimated)",
                    value=f"**Total Tokens:** {metrics.get('total_tokens', 0):,}\n"
                          f"**Input Tokens:** {metrics.get('input_tokens', 0):,}\n"
                          f"**Output Tokens:** {metrics.get('output_tokens', 0):,}",
                    inline=True
                )
            
            # Message Processing Stats
            if metrics.get('message_count', 0) > 0:
                embed.add_field(
                    name="💬 Message Processing",
                    value=f"**Total Messages:** {metrics.get('message_count', 0):,}\n"
                          f"**Avg Response Time:** {metrics.get('avg_response_time', 0):.2f}s\n"
                          f"**Success Rate:** {metrics.get('success_rate', 0):.1f}%",
                    inline=True
                )
            
            # Image Processing Stats (if available)
            if metrics.get('image_processing_count', 0) > 0:
                embed.add_field(
                    name="🖼️ Image Processing",
                    value=f"**Total Processed:** {metrics.get('image_processing_count', 0):,}\n"
                          f"**Avg Time:** {metrics.get('avg_image_processing_time', 0):.2f}s\n"
                          f"**Failures:** {metrics.get('image_processing_failures', 0):,}\n"
                          f"**Success Rate:** {metrics.get('image_processing_success_rate', 0):.1f}%",
                    inline=True
                )
            
            # Nano-Banana Service Health (if available)
            if nano_health:
                status_emoji = "✅" if nano_health.get('status') == 'healthy' else "⚠️" if nano_health.get('status') == 'degraded' else "❌"
                embed.add_field(
                    name="🍌 Image Generation Service",
                    value=f"**Status:** {status_emoji} {nano_health.get('status', 'Unknown').capitalize()}\n"
                          f"**Requests/Min:** {nano_health.get('requests_in_last_minute', 0)}\n"
                          f"**Rate Limit Remaining:** {nano_health.get('rate_limit_remaining', 0)}\n"
                          f"**Consecutive Failures:** {nano_health.get('consecutive_failures', 0)}",
                    inline=True
                )
            
            # Message Splitting Stats (if available)
            if metrics.get('message_splits_count', 0) > 0:
                embed.add_field(
                    name="✂️ Message Splitting",
                    value=f"**Total Splits:** {metrics.get('message_splits_count', 0):,}\n"
                          f"**Avg Time:** {metrics.get('avg_message_split_time', 0):.2f}s\n"
                          f"**Failures:** {metrics.get('message_split_failures', 0):,}\n"
                          f"**Success Rate:** {metrics.get('message_split_success_rate', 0):.1f}%",
                    inline=True
                )
            
            # Important Notes
            embed.add_field(
                name="ℹ️ Important Notes",
                value="• Free tier limits reset every minute/day\n"
                      "• Token counts are estimates (~4 chars = 1 token)\n"
                      "• Image generation requires paid API key\n"
                      "• Rate limits apply per API key, not per bot",
                inline=False
            )
            
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            
            await interaction.response.send_message(embed=embed)
            logger.info(f"User {interaction.user} viewed API usage statistics")
            
        except Exception as e:
            logger.error(f"Error generating API usage stats: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Failed to generate API usage statistics. Please try again later."
            )


    @bot.tree.command(name="usage-report", description="Generate a downloadable usage report (CSV + Markdown)")
    async def usage_report(interaction: discord.Interaction):
        """Generate a usage report since bot startup and attach CSV + Markdown files."""
        try:
            from io import StringIO, BytesIO

            # Gather data sources
            metrics = performance_logger.get_summary_stats()
            api_info = gemini_client.get_api_usage_info()
            uptime = datetime.now() - bot.start_time
            uptime_str = _format_timedelta(uptime)

            queue_info = None
            img_stats = None
            if hasattr(bot, 'image_processing_service') and bot.image_processing_service:
                try:
                    queue_info = bot.image_processing_service.get_queue_info()
                    img_stats = bot.image_processing_service.get_statistics()
                except Exception as _:
                    queue_info = None
                    img_stats = None

            # Build Markdown report
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            md_lines = []
            md_lines.append(f"# Usage Report")
            md_lines.append("")
            md_lines.append(f"Generated: {now_str}")
            md_lines.append(f"Uptime: {uptime_str}")
            md_lines.append("")
            md_lines.append("## Overview")
            md_lines.append(f"- Guilds: {len(bot.guilds)}")
            md_lines.append(f"- Latency: {round(bot.latency * 1000)}ms")
            md_lines.append(f"- Text Model: `{gemini_client.get_current_model()}`")
            md_lines.append(f"- Thinking Level: {gemini_client.get_thinking_level().capitalize()}")
            md_lines.append("")
            md_lines.append("## API Usage")
            md_lines.append(f"- API Calls: {metrics.get('api_calls', 0)}")
            md_lines.append(f"- API Failures: {metrics.get('api_failures', 0)}")
            md_lines.append(f"- Avg API Time: {metrics.get('avg_api_time', 0):.2f}s")
            if api_info.get('rate_limits'):
                rl = api_info['rate_limits'].get('free_tier', {})
                md_lines.append(f"- Free Tier: {rl.get('requests_per_minute', 'N/A')} req/min, {rl.get('requests_per_day', 'N/A')} req/day, {rl.get('tokens_per_minute', 'N/A')} tokens/min")
            md_lines.append("")
            md_lines.append("## Token Usage (Estimated)")
            md_lines.append(f"- Total Tokens: {metrics.get('total_tokens', 0)}")
            md_lines.append(f"- Input Tokens: {metrics.get('input_tokens', 0)}")
            md_lines.append(f"- Output Tokens: {metrics.get('output_tokens', 0)}")
            md_lines.append("")
            md_lines.append("## Message Processing")
            md_lines.append(f"- Messages: {metrics.get('message_count', 0)}")
            md_lines.append(f"- Avg Response Time: {metrics.get('avg_response_time', 0):.2f}s")
            md_lines.append(f"- Success Rate: {metrics.get('success_rate', 0):.1f}%")
            md_lines.append("")
            if metrics.get('image_processing_count', 0) > 0:
                md_lines.append("## Image Processing")
                md_lines.append(f"- Processed: {metrics.get('image_processing_count', 0)}")
                md_lines.append(f"- Avg Time: {metrics.get('avg_image_processing_time', 0):.2f}s")
                md_lines.append(f"- Failures: {metrics.get('image_processing_failures', 0)}")
                md_lines.append(f"- Success Rate: {metrics.get('image_processing_success_rate', 0):.1f}%")
                md_lines.append("")
            if queue_info and img_stats:
                md_lines.append("## Image Queue Snapshot")
                md_lines.append(f"- Queued Jobs: {queue_info.get('queue_size', 0)}")
                md_lines.append(f"- Active Jobs: {queue_info.get('active_jobs', 0)}")
                md_lines.append(f"- Max Concurrent: {queue_info.get('max_concurrent', 0)}")
                md_lines.append(f"- Total Requests: {img_stats.get('total_requests', 0)} (ok: {img_stats.get('successful_requests', 0)}, failed: {img_stats.get('failed_requests', 0)})")
                avg_pt = img_stats.get('average_processing_time', 0)
                if avg_pt:
                    md_lines.append(f"- Avg Processing Time: {avg_pt:.2f}s")
                md_lines.append("")
            md_lines.append("---")
            md_lines.append("Notes: Token counts are estimates (~4 chars ≈ 1 token). Image edits require paid API access.")

            md_content = "\n".join(md_lines)

            # Build CSV report
            csv = StringIO()
            csv.write("metric,value\n")
            csv.write(f"uptime_seconds,{int(uptime.total_seconds())}\n")
            csv.write(f"guilds,{len(bot.guilds)}\n")
            csv.write(f"latency_ms,{round(bot.latency * 1000)}\n")
            for key in [
                'message_count','avg_response_time','api_calls','api_failures','avg_api_time',
                'total_tokens','input_tokens','output_tokens',
                'image_processing_count','avg_image_processing_time','image_processing_failures',
                'message_splits_count','avg_message_split_time','message_split_failures'
            ]:
                val = metrics.get(key, 0)
                csv.write(f"{key},{val}\n")
            # Include free tier limits for convenience
            rl = api_info.get('rate_limits', {}).get('free_tier', {})
            csv.write(f"free_tier_requests_per_minute,{rl.get('requests_per_minute','')}\n")
            csv.write(f"free_tier_requests_per_day,{rl.get('requests_per_day','')}\n")
            csv.write(f"free_tier_tokens_per_minute,{rl.get('tokens_per_minute','')}\n")
            csv_content = csv.getvalue().encode('utf-8')

            # Prepare files
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            md_file = discord.File(BytesIO(md_content.encode('utf-8')), filename=f"usage_report_{timestamp}.md")
            csv_file = discord.File(BytesIO(csv_content), filename=f"usage_report_{timestamp}.csv")

            # Build summary embed
            embed = discord.Embed(
                title="📄 Usage Report Generated",
                description="Report since last startup (attached as CSV and Markdown)",
                color=discord.Color.teal(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="Overview",
                value=(
                    f"Uptime: {uptime_str}\n"
                    f"Guilds: {len(bot.guilds)}\n"
                    f"Latency: {round(bot.latency * 1000)}ms"
                ),
                inline=False
            )
            embed.add_field(
                name="API",
                value=(
                    f"Calls: {metrics.get('api_calls', 0)} | Failures: {metrics.get('api_failures', 0)}\n"
                    f"Avg Time: {metrics.get('avg_api_time', 0):.2f}s"
                ),
                inline=True
            )
            embed.add_field(
                name="Messages",
                value=(
                    f"Count: {metrics.get('message_count', 0)}\n"
                    f"Avg Response: {metrics.get('avg_response_time', 0):.2f}s"
                ),
                inline=True
            )
            if metrics.get('total_tokens', 0) > 0:
                embed.add_field(
                    name="Tokens (est)",
                    value=(
                        f"Total: {metrics.get('total_tokens', 0):,}\n"
                        f"In/Out: {metrics.get('input_tokens', 0):,}/{metrics.get('output_tokens', 0):,}"
                    ),
                    inline=True
                )
            if metrics.get('image_processing_count', 0) > 0:
                embed.add_field(
                    name="Images",
                    value=(
                        f"Processed: {metrics.get('image_processing_count', 0)}\n"
                        f"Avg Time: {metrics.get('avg_image_processing_time', 0):.2f}s"
                    ),
                    inline=True
                )

            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            await interaction.response.send_message(embed=embed, files=[md_file, csv_file])
            logger.info(f"User {interaction.user} generated usage report")

        except Exception as e:
            logger.error(f"Error generating usage report: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Failed to generate the usage report. Please try again later."
            )


    @bot.tree.command(name="deepresearch", description="Perform a deep research task and generate a report")
    @app_commands.describe(topic="The topic to research")
    async def deepresearch(interaction: discord.Interaction, topic: str):
        """Perform deep research on a topic and generate a comprehensive report."""
        await interaction.response.defer(thinking=True)
        
        try:
            research_complexity = "medium"
            synthesis_complexity = "high"
            research_cfg = config.model_complexity.get(research_complexity, {})
            synthesis_cfg = config.model_complexity.get(synthesis_complexity, {})
            research_model = str(research_cfg.get("model", gemini_client.get_current_model()))
            research_thinking = str(research_cfg.get("thinking_level", "default"))
            synthesis_model = str(synthesis_cfg.get("model", gemini_client.get_current_model()))
            synthesis_thinking = str(synthesis_cfg.get("thinking_level", "default"))

            # Step 1: Research Phase (Flash + Search)
            await interaction.followup.send(f"🔍 **Starting Deep Research on:** *{topic}*\nStep 1/2: Gathering information...")
            
            research_prompt = f"Research the following topic in depth: {topic}. Provide comprehensive details, facts, statistics, and different perspectives. Focus on gathering raw information."
            
            research_response = await gemini_client.generate_response(
                prompt=research_prompt,
                model_override=research_model,
                complexity_override=research_complexity,
                thinking_level_override=research_thinking,
                search_override=True
            )
            
            if not research_response.success:
                await interaction.followup.send(f"❌ Research failed: {research_response.content}")
                return

            research_data = research_response.content
            
            # Step 2: Thinking Phase (Pro + Template)
            await interaction.followup.send(f"🧠 Step 2/2: Analyzing and synthesizing report...")
            
            # Load template
            template_path = os.path.join("grok-prompts", "default_deepsearch_final_summarizer_prompt.j2")
            if not os.path.exists(template_path):
                # Fallback if path is different or running from different cwd
                template_path = os.path.join(os.getcwd(), "grok-prompts", "default_deepsearch_final_summarizer_prompt.j2")
            
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()
                
                template = jinja2.Template(template_content)
                final_prompt = template.render(
                    question=topic,
                    answer=research_data,
                    current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    language="English",
                    prefill=False,
                    is_grok_file_update_request=False,
                    real_time_data_provider_called=False,
                    contains_url=True,
                    supported_inline_rich_content_tools=False
                )
            except Exception as e:
                logger.error(f"Template error: {e}")
                # Fallback prompt if template fails
                final_prompt = f"User Query: {topic}\n\nResearch Data:\n{research_data}\n\nPlease write a comprehensive deep research report based on the above data."

            # Generate final report using Pro model
            report_response = await gemini_client.generate_response(
                prompt=final_prompt,
                model_override=synthesis_model,
                complexity_override=synthesis_complexity,
                thinking_level_override=synthesis_thinking,
                search_override=False # We already searched
            )
            
            if not report_response.success:
                await interaction.followup.send(f"❌ Report generation failed: {report_response.content}")
                return
                
            report_content = report_response.content
            
            # Step 3: Upload File
            file_buffer = BytesIO(report_content.encode('utf-8'))
            # Sanitize filename
            safe_topic = "".join([c for c in topic if c.isalnum() or c in (' ', '-', '_')]).strip()
            filename = f"DeepResearch_{safe_topic[:30].replace(' ', '_')}.md"
            discord_file = discord.File(file_buffer, filename=filename)
            
            await interaction.followup.send(f"✅ **Deep Research Complete!**\nHere is your report on: *{topic}*", file=discord_file)
            
        except Exception as e:
            logger.error(f"Deep research error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred during deep research: {str(e)}")

    bot.tree.add_command(config_group)
    
    
    @bot.tree.command(name="summarize", description="Summarize the current conversation")
    async def summarize(interaction: discord.Interaction):
        """Summarize the conversation in the current channel."""
        await interaction.response.defer(thinking=True)
        
        messages = []
        last_msg_time = None
        
        try:
            # Fetch messages backwards
            # Limit to 500 to avoid excessive processing, but should cover most "current" conversations
            async for message in interaction.channel.history(limit=config.channel_history_limit):
                current_msg_time = message.created_at
                
                if last_msg_time:
                    time_diff = last_msg_time - current_msg_time
                    # If gap is greater than 24 hours, stop fetching
                    if time_diff.total_seconds() > 86400:
                        break
                
                messages.append(message)
                last_msg_time = current_msg_time
                
            if not messages:
                await interaction.followup.send("No recent conversation found to summarize.")
                return
                
            # Reverse to chronological order (oldest to newest)
            messages.reverse()
            
            # Build context list
            context_list = []
            for msg in messages:
                content = msg.content
                
                # Handle attachments
                if msg.attachments:
                    attachment_names = [att.filename for att in msg.attachments]
                    if content:
                        content += f" [Attachments: {', '.join(attachment_names)}]"
                    else:
                        content = f"[Attachments: {', '.join(attachment_names)}]"
                
                # Create MessageContext
                msg_context = MessageContext(
                    content=content,
                    author=msg.author.display_name,
                    timestamp=msg.created_at,
                    message_id=msg.id,
                    is_reply=(msg.reference is not None),
                    replied_to_id=msg.reference.message_id if msg.reference else None
                )
                context_list.append(msg_context)
                
            prompt = "Please summarize the conversation. Focus on the main topics discussed, key decisions made, and any action items."
            
            # Generate summary using context
            response = await gemini_client.generate_response(prompt, context=context_list)
            
            if response.success:
                summary = response.content
                # Check length limits
                safe_len = config.safe_split_length
                if len(summary) > safe_len:
                    # Split into chunks
                    chunks = [summary[i:i+safe_len] for i in range(0, len(summary), safe_len)]

                    async def _send_summary_page(**kwargs):
                        return await interaction.followup.send(wait=True, **kwargs)

                    try:
                        await bot._send_paginated_embed(
                            pages=chunks,
                            sender_user_id=interaction.user.id,
                            send_page_callable=_send_summary_page,
                            title="Conversation Summary",
                        )
                    except Exception as paginate_error:
                        logger.warning(f"Falling back to flat summary chunks: {paginate_error}")
                        await interaction.followup.send(f"\u2705 **Conversation Summary** (Part 1/{len(chunks)})")
                        await interaction.followup.send(chunks[0])
                        for i, chunk in enumerate(chunks[1:], 1):
                            await interaction.channel.send(f"**(Part {i+1}/{len(chunks)})**\n{chunk}")
                else:
                    await interaction.followup.send(f"✅ **Conversation Summary**\n\n{summary}")
            else:
                await interaction.followup.send(f"❌ Failed to generate summary: {response.content}")
                
        except Exception as e:
            logger.error(f"Summarize error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while summarizing: {str(e)}")

    # ── Personality / Tone Command ────────────────────────────────────

    channel_settings_service = ChannelSettingsService(
        db_path=config.token_db_path,
        personalities=config.personalities,
    )
    # Store on bot so discord_bot.py can access it
    bot._channel_settings_service = channel_settings_service

    async def personality_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Dynamically provide personality choices from config."""
        return [
            app_commands.Choice(name=name.replace("-", " ").replace("_", " ").title(), value=name)
            for name in config.personalities.keys()
            if current.lower() in name.lower()
        ][:25]  # Discord max 25 choices

    @bot.tree.command(name="personality", description="Set the bot's personality/tone for this channel")
    @app_commands.describe(style="The personality style to use")
    @app_commands.autocomplete(style=personality_autocomplete)
    async def personality(interaction: discord.Interaction, style: str):
        """Set the bot's personality for the current channel."""
        if style not in config.personalities:
            await interaction.response.send_message(
                f"Unknown personality `{style}`. Valid options: {', '.join(config.personalities.keys())}",
                ephemeral=True
            )
            return

        success = channel_settings_service.set_personality(interaction.channel_id, style)
        if success:
            desc = config.personalities[style]
            display_name = style.replace("-", " ").replace("_", " ").title()
            embed = discord.Embed(
                title=f"Personality set to **{display_name}**",
                description=desc,
                color=discord.Color.purple()
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"Failed to set personality. Valid options: {', '.join(config.personalities.keys())}",
                ephemeral=True
            )

    @bot.tree.command(name="personality-info", description="Show the current personality setting for this channel")
    async def personality_info(interaction: discord.Interaction):
        """Show the current personality for this channel."""
        current = channel_settings_service.get_personality(interaction.channel_id)
        desc = config.personalities.get(current, "Unknown")
        embed = discord.Embed(
            title=f"Current Personality: **{current.capitalize()}**",
            description=desc,
            color=discord.Color.purple()
        )
        all_styles = "\n".join(f"- **{name}**: {d[:80]}..." if len(d) > 80 else f"- **{name}**: {d}"
                               for name, d in config.personalities.items())
        embed.add_field(name="Available Styles", value=all_styles, inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="live", description="Toggle mention-free live mode for this channel")
    @app_commands.describe(enabled="Optional explicit setting (on/off). Leave empty to toggle.")
    async def live(interaction: discord.Interaction, enabled: Optional[bool] = None):
        """Enable/disable channel-isolated mention-free live mode."""
        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.response.send_message(
                "This command must be used inside a channel.",
                ephemeral=True,
            )
            return

        current_state = channel_settings_service.get_live_enabled(channel_id)
        new_state = (not current_state) if enabled is None else enabled

        success = channel_settings_service.set_live_enabled(channel_id, new_state)
        if not success:
            await interaction.response.send_message(
                "Failed to update live mode for this channel.",
                ephemeral=True,
            )
            return

        state_label = "ON" if new_state else "OFF"
        mode_desc = (
            "Mention-free responses are enabled in this channel."
            if new_state
            else "Mention-free responses are disabled. Bot now requires mention/reply behavior."
        )
        embed = discord.Embed(
            title=f"Live Mode: {state_label}",
            description=mode_desc,
            color=discord.Color.green() if new_state else discord.Color.light_grey(),
        )
        embed.set_footer(text="Live mode is isolated to this channel only.")
        await interaction.response.send_message(embed=embed)

    # ── Pin / Memory Commands ─────────────────────────────────────────

    pin_service = PinService(db_path=config.token_db_path)
    bot._pin_service = pin_service
    message_visibility_service = MessageVisibilityService(db_path=config.token_db_path)
    bot._message_visibility_service = message_visibility_service

    class PinDeleteButton(discord.ui.Button):
        def __init__(self, pin_id: int, display_num: int, channel_id: int):
            super().__init__(
                label=f"Delete #{display_num}",
                style=discord.ButtonStyle.danger,
            )
            self.pin_id = pin_id
            self.display_num = display_num
            self.channel_id = channel_id

        async def callback(self, interaction: discord.Interaction):
            deleted = pin_service.delete_pin(self.pin_id, self.channel_id)
            if deleted:
                await interaction.response.send_message(
                    f"Deleted pin #{self.display_num}."
                )
            else:
                await interaction.response.send_message(
                    f"Pin #{self.display_num} not found or already deleted."
                )

    class PinDeleteView(discord.ui.View):
        def __init__(self, channel_pins, channel_id):
            super().__init__(timeout=120)
            for i, (pin_id, _content, _author, _pinned_by, _pinned_at) in enumerate(channel_pins[:20], start=1):
                self.add_item(PinDeleteButton(pin_id, i, channel_id))

    @bot.tree.command(name="pin", description="Pin a memory for the bot to always remember in this channel")
    @app_commands.describe(memory="The text you want the bot to always remember in this channel")
    async def pin(interaction: discord.Interaction, memory: str):
        """Pin a piece of text to the bot's memory for this channel."""
        guild_id = interaction.guild_id if interaction.guild else None
        pin_id = pin_service.add_pin(
            channel_id=interaction.channel_id,
            content=memory,
            author_name=interaction.user.display_name,
            pinned_by=interaction.user.display_name,
            guild_id=guild_id,
        )

        if pin_id:
            preview = memory[:100] + "..." if len(memory) > 100 else memory
            embed = discord.Embed(
                title="Pinned to Bot Memory",
                description=preview,
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"Pin #{pin_id} | Pinned by {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Failed to pin that memory."
            )

    @bot.tree.command(name="pins", description="List all pinned bot memories for this channel")
    async def pins(interaction: discord.Interaction):
        """List pinned messages with delete buttons."""
        channel_pins = pin_service.get_pins(interaction.channel_id)

        if not channel_pins:
            await interaction.response.send_message(
                "No pinned memories in this channel yet.\n"
                "Use `/pin` to add one.",
            )
            return

        embed = discord.Embed(
            title="Pinned Bot Memories",
            description=f"{len(channel_pins)} pinned message(s) in this channel",
            color=discord.Color.gold(),
        )

        for i, (pin_id, content, author_name, pinned_by, pinned_at) in enumerate(channel_pins, start=1):
            preview = content[:200] + "..." if len(content) > 200 else content
            embed.add_field(
                name=f"#{i} — {author_name}",
                value=f"{preview}\n*Pinned by {pinned_by}*",
                inline=False,
            )

        view = PinDeleteView(channel_pins, interaction.channel_id)
        await interaction.response.send_message(embed=embed, view=view)

    @bot.tree.command(name="hide", description="Replace recent Grok messages in this channel with '.'")
    async def hide(interaction: discord.Interaction):
        """Hide recent bot messages in this channel by replacing content with a dot."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.channel:
            await interaction.followup.send("This command must be run in a channel.", ephemeral=True)
            return

        hide_limit = 10
        scan_limit = min(max(hide_limit * 25, 100), max(config.channel_history_limit, hide_limit))

        hidden_count = 0
        skipped_already_hidden = 0
        failed_count = 0

        try:
            async for candidate in interaction.channel.history(limit=scan_limit):
                if hidden_count >= hide_limit:
                    break
                if not bot.user or candidate.author.id != bot.user.id:
                    continue
                if not candidate.content:
                    continue
                if candidate.content == ".":
                    skipped_already_hidden += 1
                    continue

                stored = message_visibility_service.save_hidden_message(
                    message_id=candidate.id,
                    channel_id=candidate.channel.id,
                    original_content=candidate.content,
                    hidden_by=interaction.user.id,
                    guild_id=interaction.guild_id,
                )
                if not stored:
                    failed_count += 1
                    continue

                try:
                    await candidate.edit(content=".")
                    hidden_count += 1
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning(f"Failed to hide message {candidate.id}: {exc}")
                    message_visibility_service.remove_hidden_message(candidate.id)
                    failed_count += 1

            if hidden_count == 0:
                await interaction.followup.send(
                    "No recent visible Grok messages were found to hide in this channel.",
                    ephemeral=True,
                )
                return

            summary = (
                f"Hidden {hidden_count} message(s) in this channel. "
                f"Run `/unhide` to restore up to {hide_limit} recent hidden messages."
            )
            if skipped_already_hidden > 0 or failed_count > 0:
                summary += (
                    f"\nSkipped already hidden: {skipped_already_hidden}. "
                    f"Failed: {failed_count}."
                )
            await interaction.followup.send(summary, ephemeral=True)
        except Exception as exc:
            logger.error(f"/hide failed in channel {interaction.channel_id}: {exc}", exc_info=True)
            await interaction.followup.send(
                "Failed to hide messages due to an unexpected error. Check logs for details.",
                ephemeral=True,
            )

    @bot.tree.command(name="unhide", description="Restore recent Grok messages hidden in this channel")
    async def unhide(interaction: discord.Interaction):
        """Restore recent hidden bot messages in this channel."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.channel:
            await interaction.followup.send("This command must be run in a channel.", ephemeral=True)
            return

        hide_limit = 10
        hidden_rows = message_visibility_service.get_recent_hidden_messages(
            channel_id=interaction.channel.id,
            limit=hide_limit,
        )

        if not hidden_rows:
            await interaction.followup.send(
                "No hidden Grok messages were found for this channel.",
                ephemeral=True,
            )
            return

        restored_count = 0
        missing_count = 0
        failed_count = 0

        try:
            # Restore oldest first for readability in-channel.
            for message_id, original_content, _hidden_at in reversed(hidden_rows):
                try:
                    target = await interaction.channel.fetch_message(message_id)
                except discord.NotFound:
                    message_visibility_service.remove_hidden_message(message_id)
                    missing_count += 1
                    continue
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning(f"Failed to fetch hidden message {message_id}: {exc}")
                    failed_count += 1
                    continue

                try:
                    await target.edit(content=original_content)
                    message_visibility_service.remove_hidden_message(message_id)
                    restored_count += 1
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning(f"Failed to restore hidden message {message_id}: {exc}")
                    failed_count += 1

            if restored_count == 0:
                await interaction.followup.send(
                    "No hidden messages could be restored.",
                    ephemeral=True,
                )
                return

            summary = f"Restored {restored_count} message(s) in this channel."
            if missing_count > 0 or failed_count > 0:
                summary += f"\nMissing/deleted: {missing_count}. Failed: {failed_count}."
            await interaction.followup.send(summary, ephemeral=True)
        except Exception as exc:
            logger.error(f"/unhide failed in channel {interaction.channel_id}: {exc}", exc_info=True)
            await interaction.followup.send(
                "Failed to restore messages due to an unexpected error. Check logs for details.",
                ephemeral=True,
            )

    # ── User Preferences Commands ─────────────────────────────────────

    user_prefs_service = UserPreferencesService(
        db_path=config.token_db_path,
        valid_models=config.valid_models,
        valid_languages=config.valid_languages,
    )
    # Store on bot so discord_bot.py can access it
    bot._user_prefs_service = user_prefs_service

    prefs_group = app_commands.Group(name="preferences", description="Manage your personal bot preferences")

    model_choices = [
        app_commands.Choice(name=model_name, value=model_name)
        for model_name in config.valid_models
    ]

    @prefs_group.command(name="model", description="Set your preferred AI model")
    @app_commands.describe(model="The model to use for your requests")
    @app_commands.choices(model=model_choices)
    async def prefs_model(interaction: discord.Interaction, model: app_commands.Choice[str]):
        success = user_prefs_service.set_model(interaction.user.id, model.value)
        if success:
            await interaction.response.send_message(
                f"Your preferred model is now **{model.name}**. It will be used for all your future requests.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Failed to set model preference.", ephemeral=True)

    lang_choices = [
        app_commands.Choice(name=lang.capitalize(), value=lang)
        for lang in config.valid_languages[:25]  # Discord max 25 choices
    ]

    @prefs_group.command(name="language", description="Set your preferred response language")
    @app_commands.describe(language="The language for bot responses")
    @app_commands.choices(language=lang_choices)
    async def prefs_language(interaction: discord.Interaction, language: app_commands.Choice[str]):
        success = user_prefs_service.set_language(interaction.user.id, language.value)
        if success:
            if language.value == "auto":
                await interaction.response.send_message(
                    "Language preference set to **Auto** (bot will respond in the same language you use).",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"Your preferred language is now **{language.name}**.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message("Failed to set language preference.", ephemeral=True)

    @prefs_group.command(name="show", description="Show your current preferences")
    async def prefs_show(interaction: discord.Interaction):
        prefs = user_prefs_service.get_preferences(interaction.user.id)
        embed = discord.Embed(
            title="Your Preferences",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Preferred Model",
            value=prefs.preferred_model or "Not set (uses channel/server default)",
            inline=False
        )
        embed.add_field(
            name="Preferred Language",
            value=(prefs.preferred_language or "auto").capitalize(),
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @prefs_group.command(name="clear", description="Reset all your preferences to defaults")
    async def prefs_clear(interaction: discord.Interaction):
        user_prefs_service.clear_preferences(interaction.user.id)
        await interaction.response.send_message(
            "All your preferences have been reset to defaults.",
            ephemeral=True
        )

    bot.tree.add_command(prefs_group)


def _get_model_description(model_name: str, config: BotConfig) -> str:
    """Get description for a specific model."""
    configured_description = config.model_descriptions.get(model_name)
    if configured_description:
        return configured_description

    display_name = config.model_display_names.get(model_name, model_name)
    return f"Configured model: {display_name}"


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

