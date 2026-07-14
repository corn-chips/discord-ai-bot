"""Public formatting helpers shared by command registrars."""

from datetime import timedelta

from ...config import BotConfig


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
