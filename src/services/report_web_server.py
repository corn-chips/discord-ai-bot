"""Small aiohttp web UI for bot report tracking."""

from __future__ import annotations

from html import escape
import ipaddress
import logging
from typing import List, Optional, Tuple

from aiohttp import web

from .report_service import Report, ReportService, VALID_REPORT_STATUSES


logger = logging.getLogger(__name__)


# Methods that cannot change server state, and so do not need an Origin.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The one non-literal host name that always means "this machine".
LOOPBACK_HOST_NAME = "localhost"


def _is_loopback_literal(value: str) -> bool:
    """True when ``value`` parses as an IP address in a loopback range."""

    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _parses_as_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _format_url(host: str, port: int) -> str:
    """Render ``host``/``port`` as a URL, bracketing an IPv6 literal."""

    try:
        bracket = ipaddress.ip_address(host).version == 6
    except ValueError:
        bracket = False
    return f"http://[{host}]:{port}/" if bracket else f"http://{host}:{port}/"


def _hostname_of(host_header: str) -> str:
    """Strip the port and IPv6 brackets from a Host header value."""

    host_header = host_header.strip()
    if host_header.startswith("["):
        return host_header[1 : host_header.find("]")] if "]" in host_header else ""
    return host_header.split(":", 1)[0]


class ReportWebServer:
    """Serves a loopback-only report tracking page with status update forms.

    Two properties are load-bearing and are enforced here rather than in
    ``validate_config``:

    * **The socket may only bind a loopback address.** The page has no
      authentication of any kind and serves every guild's reports -- including
      raw Discord user ids -- so there is no configuration under which exposing
      it off-box is safe. The refusal lives at the socket because a
      ``validate_config`` rule is ``sys.exit(1)``
      (``config.py:251-278``): that turns one misconfigured auxiliary feature
      into a permanent boot loop for an otherwise healthy bot, which is the
      shape ``docs/ANALYSIS_CORRECTIONS.md`` item 16 records as worse than the
      defect it fixes. Refusing here costs the operator the web UI and nothing
      else.

      Remote access is still available and needs no code: bind loopback and
      forward a port, ``ssh -L 8080:127.0.0.1:8080 <host>``. The guard below is
      deliberately port-agnostic so that a tunnel on a different local port
      still works.

    * **A state-changing request must prove it came from this page.** See
      ``_guard_request``.
    """

    def __init__(self, report_service: ReportService, host: str, port: int):
        self.report_service = report_service
        # Coerced and stripped: YAML happily yields an int for `web_host: 2130706433`,
        # and an unstripped " 127.0.0.1 " reaches getaddrinfo as a host name.
        self.host = str(host).strip()
        self.port = int(port)
        self._runner: Optional[web.AppRunner] = None
        self._bound: List[Tuple[str, int]] = []

    @property
    def bound_addresses(self) -> List[Tuple[str, int]]:
        """The ``(host, port)`` pairs actually bound, empty when not running."""

        return list(self._bound)

    @property
    def urls(self) -> List[str]:
        """Every URL this server can be reached at, truthfully.

        When running these are read back from the sockets, so a name like
        ``localhost`` that binds both ``127.0.0.1`` and ``::1`` reports both.
        """

        if self._bound:
            return [_format_url(host, port) for host, port in self._bound]
        return [_format_url(self.host, self.port)]

    @property
    def url(self) -> str:
        """The primary URL.

        This used to rewrite a ``0.0.0.0`` or ``::`` bind to ``127.0.0.1`` for
        display, which told the operator -- at the one moment they were looking
        -- that a service listening on every interface was loopback-only
        (DAB-144). It reports the real address now.
        """

        return self.urls[0]

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        """Start the web UI, unless it would bind a non-loopback address."""

        if self._runner:
            return

        # Cheap pre-check, so the common misconfiguration (`0.0.0.0`) never
        # opens a socket at all. Only address literals are judged here; a host
        # *name* is left to the authoritative post-bind check below, which
        # costs no name resolution of our own.
        if not self.host or (_parses_as_ip(self.host) and not _is_loopback_literal(self.host)):
            self._refuse(self.host or "<empty>")
            return

        app = web.Application(middlewares=[self._guard_request])
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

        # Authoritative check: what the socket layer actually bound, not what we
        # predicted it would. Resolving the host ourselves and then handing the
        # same string to aiohttp is two independent lookups that can disagree,
        # and they need not even be lookups: `web_host: "0"` is rejected by
        # `ipaddress` as a literal, resolves to `0.0.0.0`, and binds every
        # interface. An empty address list is refused for the same reason -- if
        # the bind cannot be read back, it cannot be claimed to be loopback.
        bound = [(str(address[0]), int(address[1])) for address in runner.addresses]
        exposed = [host for host, _ in bound if not _is_loopback_literal(host)]
        if not bound or exposed:
            await runner.cleanup()
            self._refuse(", ".join(exposed) or "no readable address")
            return

        self._runner = runner
        self._bound = bound
        logger.info("Report web UI running at %s", ", ".join(self.urls))

    def _refuse(self, bound_description: str) -> None:
        """Log why the UI is not starting.

        Logged and returned rather than raised: this is a deliberate refusal,
        not a fault, and ``discord_bot.on_ready`` would render a raise as
        "Failed to start report web UI" plus a traceback, which reads as a bug
        in the server rather than as a problem with the address.
        """

        logger.error(
            "Report web UI NOT started: reports.web_host %r binds %s, which is not a "
            "loopback address. This page has no authentication and serves every guild's "
            "reports, so it is loopback-only by construction. Set reports.web_host to "
            "127.0.0.1 and reach it remotely with a port forward "
            "(ssh -L %d:127.0.0.1:%d <host>), or set reports.web_enabled to false.",
            self.host,
            bound_description,
            self.port,
            self.port,
        )

    async def stop(self) -> None:
        """Stop the web UI."""

        if not self._runner:
            return

        runner, self._runner, self._bound = self._runner, None, []
        # Cleared first, and in a finally, so a cleanup that raises cannot leave
        # the object claiming to be running. `DiscordBot.close` swallows the
        # exception, so without this a failed shutdown would report a live
        # server for the rest of the process.
        try:
            await runner.cleanup()
        finally:
            logger.info("Report web UI stopped")

    @web.middleware
    async def _guard_request(self, request: web.Request, handler) -> web.StreamResponse:
        """Reject requests that a browser on this page could not have made.

        Two checks, in order, because the second depends on the first:

        1. **The Host header must name this machine.** Without this, DNS
           rebinding defeats everything below it: an attacker points
           ``evil.example`` at ``127.0.0.1``, the operator's browser loads their
           page, and the form POST then carries ``Host: evil.example`` *and*
           ``Origin: http://evil.example`` -- self-consistent, so an
           Origin-equals-Host comparison passes it. Verified before this guard
           existed: that request shape returned 303 and changed a real row.

        2. **A state-changing request must carry a matching Origin.** The
           comparison is an exact string match against the Host the client
           used, not against the configured ``web_host``: an operator reaching
           the UI at ``localhost:8080``, or through a port forward on some other
           local port, must not have their own form rejected. A guard that
           breaks the UI gets switched off, which is worse than no guard.

           Exact match rather than a parsed comparison, because ``urlsplit``
           accepts six shapes the Fetch ABNF forbids -- a trailing slash, a
           path, ``HTTP://``, a leading space, an embedded tab -- and every one
           of them mutated a row against a netloc comparison.

           A missing Origin is refused. Per Fetch Standard section 3.2, a
           browser appends Origin to every non-GET/HEAD request
           unconditionally; a suppressing referrer policy sets it to the literal
           ``null`` rather than omitting it. So no browser form can be refused
           by this branch, and allowing it would let anything that is not a
           browser skip the check entirely.

        Note for whoever adds security headers here: do NOT send
        ``Referrer-Policy: no-referrer``. By the same section 3.2 it would make
        this page's own form send ``Origin: null``, and check 2 would then
        reject every save.
        """

        host_header = request.headers.get("Host", "")
        if not self._is_own_host(host_header):
            return self._refusal_response(
                421, "This server only answers to its own loopback address."
            )

        if request.method not in SAFE_METHODS:
            if request.headers.get("Origin") != f"http://{host_header}":
                return self._refusal_response(
                    403,
                    "Cross-origin request refused. Submit the form from the report "
                    f"page itself, or send Origin: http://{host_header}.",
                )

        try:
            response = await handler(request)
        except web.HTTPException as exc:
            self._apply_security_headers(exc)
            raise
        self._apply_security_headers(response)
        return response

    def _is_own_host(self, host_header: str) -> bool:
        """True when the Host header names this machine.

        The port is deliberately not checked, so that a port forward onto a
        different local port keeps working.
        """

        hostname = _hostname_of(host_header).lower().rstrip(".")
        if not hostname:
            return False
        return (
            hostname == LOOPBACK_HOST_NAME
            or _is_loopback_literal(hostname)
            or hostname == self.host.lower()
        )

    def _refusal_response(self, status: int, text: str) -> web.Response:
        """A refusal is still a response, so it carries the same headers."""

        response = web.Response(status=status, text=text)
        self._apply_security_headers(response)
        return response

    @staticmethod
    def _apply_security_headers(response: web.StreamResponse) -> None:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

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
  <td>#{escape(str(report.id))}</td>
  <td>{escape(report.report_type.title())}</td>
  <td class="status">{escape(self._format_status(report.status))}</td>
  <td class="description">{escape(report.description)}</td>
  <td>
    {escape(report.reporter_name)}
    <div class="meta">{escape(str(report.reporter_id))}</div>
  </td>
  <td>{escape(report.created_at)}</td>
  <td>{escape(report.updated_at)}</td>
  <td>
    <form method="post" action="/reports/{escape(str(report.id))}/status">
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
