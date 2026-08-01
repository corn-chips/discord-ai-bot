"""Public formatting and authorization helpers shared by command registrars."""

from datetime import timedelta

import discord

from ...config import BotConfig


async def require_guild_permission(
    interaction: discord.Interaction,
    permission: str,
    action: str,
) -> bool:
    """Refuse a guild-scoped command unless the caller holds ``permission``.

    Returns ``True`` when the caller may proceed. When it returns ``False`` it
    has already replied, ephemerally, saying what is missing.

    ``permission`` is an attribute name on :class:`discord.Permissions`, e.g.
    ``"administrator"`` or ``"manage_channels"``. ``action`` completes the
    sentence "You need the X permission to ...".

    **This is one of three layers, and each covers a hole the others do not.**
    A command that wants gating needs all three:

    * ``@app_commands.default_permissions(...)`` sets the default Discord shows
      in the UI. It is only a default -- a guild admin can re-grant it -- and
      discord.py silently drops it from a *subcommand's* ``to_dict()`` payload,
      which is DAB-141: a gate that passed its own acceptance test while
      protecting nothing. Never treat it as the enforcement.
    * ``@app_commands.guild_only()`` matters because Discord does not evaluate
      ``default_member_permissions`` in a DM at all. Without it, anyone sharing
      a guild with the bot can DM the command and walk straight past the payload
      gate. That is exactly how ``/clear-cache`` stayed reachable while carrying
      permission 8.
    * This runtime check is the actual enforcement, because it is the only layer
      the caller can neither be re-granted nor route around.

    When testing a gate, assert the serialised payload and an executed refusal.
    A local attribute proves nothing. See ``tests/test_command_authorization.py``.
    """
    permissions = getattr(interaction.user, "guild_permissions", None)
    if (
        interaction.guild is None
        or permissions is None
        or not getattr(permissions, permission, False)
    ):
        label = permission.replace("_", " ").title()
        await interaction.response.send_message(
            f"You need the {label} permission to {action}.",
            ephemeral=True,
        )
        return False
    return True


def _get_model_description(model_name: str, config: BotConfig) -> str:
    """Get description for a specific model."""
    configured_description = config.model_descriptions.get(model_name)
    if configured_description:
        return configured_description

    display_name = config.model_display_names.get(model_name, model_name)
    return f"Configured model: {display_name}"


def _format_report_status(status: str) -> str:
    """Format report status labels for Discord."""
    return status.replace("_", " ").title()


def _truncate_text(text: str, limit: int) -> str:
    """Return text capped at Discord embed-safe lengths."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


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
