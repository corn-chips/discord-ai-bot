"""Report, statistics, and usage slash-command registrars."""

import logging
from io import BytesIO
from datetime import datetime

import discord
from discord import app_commands

from .common import (
    _format_report_status,
    _format_timedelta,
    _truncate_text,
)
from .context import CommandContext


logger = logging.getLogger("src.bot.commands")


def register_report_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    @bot.tree.command(name="report", description="Submit a bot issue or feature request")
    @app_commands.describe(
        category="Choose whether this is a bug/issue or a feature request",
        details="Describe what is broken or what should be added",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Issue", value="issue"),
        app_commands.Choice(name="Feature", value="feature"),
    ])
    async def report(
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        details: str,
    ):
        """Create a tracked bot report."""
        report_service = getattr(bot, "report_service", None)
        if not report_service:
            await interaction.response.send_message(
                "Report tracking is not available right now.",
                ephemeral=True,
            )
            return

        cleaned_details = details.strip()
        if len(cleaned_details) < 5:
            await interaction.response.send_message(
                "Please include a little more detail so the report is actionable.",
                ephemeral=True,
            )
            return

        try:
            created = report_service.create_report(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                reporter_id=interaction.user.id,
                reporter_name=interaction.user.display_name or str(interaction.user),
                report_type=category.value,
                description=cleaned_details,
            )
        except Exception as exc:
            logger.error("Failed to create report: %s", exc, exc_info=True)
            await interaction.response.send_message(
                "Failed to save that report. Please try again later.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Report #{created.id} submitted",
            description=_truncate_text(created.description, 1000),
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Type", value=created.report_type.title(), inline=True)
        embed.add_field(name="Status", value=_format_report_status(created.status), inline=True)
        embed.add_field(
            name="Check Status",
            value=f"`/report-status report_id:{created.id}`",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="report-status", description="Check the status of a submitted report")
    @app_commands.describe(report_id="The report number returned by /report")
    async def report_status(interaction: discord.Interaction, report_id: int):
        """Show the current status for a bot report."""
        report_service = getattr(bot, "report_service", None)
        if not report_service:
            await interaction.response.send_message(
                "Report tracking is not available right now.",
                ephemeral=True,
            )
            return

        existing = report_service.get_report(report_id)
        if not existing:
            await interaction.response.send_message(
                f"Report #{report_id} was not found.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Report #{existing.id}",
            description=_truncate_text(existing.description, 1000),
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Type", value=existing.report_type.title(), inline=True)
        embed.add_field(name="Status", value=_format_report_status(existing.status), inline=True)
        embed.add_field(name="Submitted By", value=existing.reporter_name, inline=True)
        embed.add_field(name="Created", value=existing.created_at, inline=True)
        embed.add_field(name="Updated", value=existing.updated_at, inline=True)
        if existing.admin_notes:
            embed.add_field(
                name="Notes",
                value=_truncate_text(existing.admin_notes, 1000),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Create config group


def register_statistics_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

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




def register_usage_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

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
                from ...services.nano_banana_client import nano_banana_client
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
