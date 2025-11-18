"""
Slash commands for the Discord Grok Bot.

This module contains all slash commands for bot configuration,
statistics, and utility functions.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands

from ..config import BotConfig
from ..services.gemini_client import GeminiClient
from ..services.token_tracker import TokenTracker


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
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message(
                    "❌ Failed to change prompt mode. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error changing prompt mode: {e}")
            await interaction.response.send_message(
                f"❌ Error: {str(e)}"
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
            entries = await token_tracker.get_top_users(interaction.guild.id, limit=10)
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


    @bot.tree.command(name="config", description="View current bot configuration and feature status")
    async def config_command(interaction: discord.Interaction):
        """Display current bot configuration and feature availability."""
        embed = discord.Embed(
            title="⚙️ Enhanced Bot Configuration",
            description="Current settings, parameters, and feature status",
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
            embed.add_field(
                name="🖼️ Image Processing",
                value=f"**Max Image Size:** {config.max_image_size_mb}MB\n"
                      f"**Processing Timeout:** {config.image_processing_timeout}s\n"
                      f"**Max Concurrent:** {config.max_concurrent_image_edits}",
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
                except:
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
            current_prompt_mode = gemini_client.get_prompt_mode()
            embed.add_field(
                name="⚙️ Current Configuration",
                value=f"**Text Model:** `{current_model}`\n"
                      f"**Prompt Mode:** {current_prompt_mode.capitalize()}\n"
                      f"**Image Model:** `gemini-2.5-flash-image`",
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
            md_lines.append(f"- Prompt Mode: {gemini_client.get_prompt_mode().capitalize()}")
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
