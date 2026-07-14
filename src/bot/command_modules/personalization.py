"""Personalization, live-mode, and local-memory command registrars."""

import asyncio
import logging
import os
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional

import discord
import jinja2
from discord import app_commands

from ...services.channel_settings_service import ChannelSettingsService
from ...services.message_visibility_service import MessageVisibilityService
from ...services.pin_service import PinService
from ...services.user_preferences_service import UserPreferencesService
from ...models.data_models import MessageContext
from .common import (
    _format_report_status,
    _format_timedelta,
    _get_model_description,
    _truncate_text,
)
from .context import CommandContext


logger = logging.getLogger("src.bot.commands")


def register_personalization_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

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

    pin_service = getattr(bot, "_pin_service", None) or PinService(
        db_path=config.rag_database_path,
        legacy_db_path=config.token_db_path,
    )
    bot._pin_service = pin_service
    if getattr(bot, "hybrid_context_retriever", None):
        bot.hybrid_context_retriever.set_pin_service(pin_service)
    message_visibility_service = getattr(bot, "_message_visibility_service", None) or MessageVisibilityService(db_path=config.token_db_path)
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
                    if getattr(bot, "message_index_service", None):
                        bot.message_index_service.mark_hidden(candidate.id, True)
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
                    if getattr(bot, "message_index_service", None):
                        bot.message_index_service.mark_hidden(target.id, False)
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
