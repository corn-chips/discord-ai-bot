"""Configuration and administrator slash-command registrars."""

import logging

import discord
from discord import app_commands

from .common import (
    _get_model_description,
)
from .context import CommandContext


logger = logging.getLogger("src.bot.commands")


async def _require_config_admin(interaction: discord.Interaction) -> bool:
    """
    Refuse a `/config` mutation unless the caller may run the bot.

    Everything `/config` writes is process-global. `set_force_search`,
    `set_model` and `set_thinking_level` take no guild argument, there is one
    GeminiClient for the process, and `bot.image_generation_enabled` and the
    root logger's level are likewise shared. So one member of one guild changes
    behaviour for every guild the bot serves -- the same blast radius as
    `/rag delete scope:all`, differing in reversibility rather than reach.
    `/config deepsearch` in particular forces a web search on every query for
    everyone until the process restarts, which is an unowned spend lever.

    Guarded at runtime rather than in the payload, deliberately. discord.py
    drops a *subcommand's* default_permissions from to_dict() (see DAB-141), so
    the only payload-level option is to gate the whole group -- which would take
    `/config info` away from ordinary members for no security benefit, since it
    only reports the current model, thinking level and a few limits. The payload
    permission was never the enforcement anyway: it is a default a guild admin
    can re-grant.

    Returns True when the caller may proceed, and has already told them why not
    when it returns False.
    """
    permissions = getattr(interaction.user, "guild_permissions", None)
    if interaction.guild is not None and permissions is not None:
        if permissions.manage_guild or permissions.administrator:
            return True

    await interaction.response.send_message(
        "You need the Manage Server permission to change bot configuration. "
        "These settings are global: they affect every server this bot runs in.",
        ephemeral=True,
    )
    return False


def create_config_group(context: CommandContext) -> app_commands.Group:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

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
        if not await _require_config_admin(interaction):
            return
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
        if not await _require_config_admin(interaction):
            return
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
        if not await _require_config_admin(interaction):
            return
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
        if not await _require_config_admin(interaction):
            return
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
        if not await _require_config_admin(interaction):
            return
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



    return config_group


def register_admin_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

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
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def dev_mode(interaction: discord.Interaction):
        """Toggle developer mode for detailed error messages (admin only)."""
        # The docstring claimed "admin only" while the decorator sat commented
        # out, so any member could flip a process-global flag that turns stack
        # traces -- absolute paths, OS username -- into channel messages for
        # every guild the bot serves.
        #
        # guild_only matters as much as the permission. This is a top-level
        # command so default_permissions does serialise, but Discord does not
        # evaluate it in a DM; without guild_only anyone sharing a guild with
        # the bot could DM /dev and flip the flag regardless.
        #
        # The runtime check matters more than either, because
        # default_member_permissions is a default a guild admin can re-grant.
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.guild is None or permissions is None or not permissions.administrator:
            await interaction.response.send_message(
                "You need the Administrator permission to toggle developer mode.",
                ephemeral=True,
            )
            return

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
