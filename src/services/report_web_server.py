"""Small aiohttp web UI for bot report tracking."""

from __future__ import annotations

from html import escape
import logging
from typing import Optional

from aiohttp import web

from .report_service import Report, ReportService, VALID_REPORT_STATUSES


logger = logging.getLogger(__name__)


class ReportWebServer:
    """Serves a local report tracking page with status update forms."""

    def __init__(self, report_service: ReportService, host: str, port: int):
        self.report_service = report_service
        self.host = host
        self.port = int(port)
        self._runner: Optional[web.AppRunner] = None

    @property
    def url(self) -> str:
        display_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{display_host}:{self.port}/"

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        """Start the web UI if it is not already running."""

        if self._runner:
            return

        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_post("/reports/{report_id}/status", self._update_status)

        runner = web.AppRunner(app)
        await runner.setup()

        try:
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise

        self._runner = runner
        logger.info("Report web UI running at %s", self.url)

    async def stop(self) -> None:
        """Stop the web UI."""

        if not self._runner:
            return

        await self._runner.cleanup()
        self._runner = None
        logger.info("Report web UI stopped")

    async def _index(self, request: web.Request) -> web.Response:
        status_filter = request.query.get("status") or None
        if status_filter not in VALID_REPORT_STATUSES:
            status_filter = None

        reports = self.report_service.list_reports(status=status_filter, limit=200)
        return web.Response(
            text=self._render_index(reports, status_filter),
            content_type="text/html",
            charset="utf-8",
        )

    async def _update_status(self, request: web.Request) -> web.Response:
        try:
            report_id = int(request.match_info["report_id"])
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text="Invalid report id") from exc

        data = await request.post()
        status = str(data.get("status", ""))
        admin_notes = str(data.get("admin_notes", ""))

        try:
            updated = self.report_service.update_status(
                report_id,
                status,
                admin_notes=admin_notes,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

        if not updated:
            raise web.HTTPNotFound(text="Report not found")

        raise web.HTTPSeeOther(location="/")

    def _render_index(self, reports: list[Report], status_filter: Optional[str]) -> str:
        rows = "\n".join(self._render_report_row(report) for report in reports)
        if not rows:
            rows = '<tr><td colspan="8" class="empty">No reports found.</td></tr>'

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bot Reports</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f5f5f5; color: #222; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ margin: 0 0 16px; font-size: 26px; }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
    nav a {{ color: #174ea6; text-decoration: none; padding: 6px 10px; border: 1px solid #bbb; border-radius: 4px; background: white; }}
    nav a.active {{ background: #174ea6; border-color: #174ea6; color: white; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border: 1px solid #ddd; padding: 10px; vertical-align: top; text-align: left; }}
    th {{ background: #eee; font-size: 13px; text-transform: uppercase; }}
    .description {{ white-space: pre-wrap; min-width: 280px; }}
    .status {{ font-weight: 700; }}
    .empty {{ text-align: center; padding: 28px; color: #666; }}
    textarea, select {{ width: 100%; box-sizing: border-box; }}
    textarea {{ min-height: 58px; resize: vertical; margin-top: 8px; }}
    button {{ margin-top: 8px; padding: 7px 10px; border: 0; border-radius: 4px; background: #174ea6; color: white; cursor: pointer; }}
    .meta {{ color: #555; font-size: 13px; line-height: 1.4; }}
  </style>
</head>
<body>
  <main>
    <h1>Bot Reports</h1>
    {self._render_filters(status_filter)}
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Type</th>
          <th>Status</th>
          <th>Description</th>
          <th>Reporter</th>
          <th>Created</th>
          <th>Updated</th>
          <th>Update</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </main>
</body>
</html>"""

    def _render_filters(self, status_filter: Optional[str]) -> str:
        all_class = "active" if not status_filter else ""
        links = [f'<a class="{all_class}" href="/">All</a>']
        for status in VALID_REPORT_STATUSES:
            active_class = "active" if status == status_filter else ""
            label = self._format_status(status)
            links.append(f'<a class="{active_class}" href="/?status={status}">{label}</a>')
        return f"<nav>{''.join(links)}</nav>"

    def _render_report_row(self, report: Report) -> str:
        notes = report.admin_notes or ""
        return f"""
<tr>
  <td>#{report.id}</td>
  <td>{escape(report.report_type.title())}</td>
  <td class="status">{escape(self._format_status(report.status))}</td>
  <td class="description">{escape(report.description)}</td>
  <td>
    {escape(report.reporter_name)}
    <div class="meta">{report.reporter_id}</div>
  </td>
  <td>{escape(report.created_at)}</td>
  <td>{escape(report.updated_at)}</td>
  <td>
    <form method="post" action="/reports/{report.id}/status">
      <select name="status">
        {self._render_status_options(report.status)}
      </select>
      <textarea name="admin_notes" placeholder="Internal notes">{escape(notes)}</textarea>
      <button type="submit">Save</button>
    </form>
  </td>
</tr>"""

    def _render_status_options(self, current_status: str) -> str:
        options = []
        for status in VALID_REPORT_STATUSES:
            selected = " selected" if status == current_status else ""
            options.append(
                f'<option value="{status}"{selected}>{escape(self._format_status(status))}</option>'
            )
        return "\n".join(options)

    @staticmethod
    def _format_status(status: str) -> str:
        return status.replace("_", " ").title()
