"""
Slash commands for the Discord Grok Bot.

This module is the stable composition entry point for slash-command registrars.
"""

import logging
from typing import Optional

from ..config import BotConfig
from ..services.gemini_client import GeminiClient
from ..services.token_tracker import TokenTracker
# Intentional re-exports: these helpers are unused inside this module but are
# imported from here by tests, which pin them as part of this module's surface.
from .command_modules.common import (
    _format_report_status,
    _format_timedelta,
    _get_model_description,
    _truncate_text,
)
from .command_modules.configuration import (
    create_config_group,
    register_admin_commands,
)
from .command_modules.context import CommandContext
from .command_modules.general import (
    register_feature_commands,
    register_ping_command,
)
from .command_modules.personalization import register_personalization_commands
from .command_modules.reports_usage import (
    register_report_commands,
    register_statistics_commands,
    register_usage_commands,
)
from .command_modules.research import (
    create_rag_group,
    register_deepresearch_command,
    register_summarize_command,
)


logger = logging.getLogger(__name__)


async def setup_commands(
    bot,
    config: BotConfig,
    gemini_client: GeminiClient,
    performance_logger,
    token_tracker: Optional[TokenTracker],
):
    """Set up all slash commands while preserving their historical order."""
    context = CommandContext(
        bot=bot,
        config=config,
        gemini_client=gemini_client,
        performance_logger=performance_logger,
        token_tracker=token_tracker,
    )

    register_ping_command(context)
    register_report_commands(context)

    # These groups historically collected callbacks early but were inserted
    # after the standalone research command. Keep that ordering stable.
    config_group = create_config_group(context)

    register_statistics_commands(context)
    register_feature_commands(context)
    register_admin_commands(context)
    register_usage_commands(context)
    register_deepresearch_command(context)

    rag_group = create_rag_group(context)
    bot.tree.add_command(config_group)
    bot.tree.add_command(rag_group)

    register_summarize_command(context)
    register_personalization_commands(context)
