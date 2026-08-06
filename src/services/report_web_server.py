"""Small aiohttp web UI for bot report tracking."""

from __future__ import annotations

from html import escape
import asyncio
import ipaddress
import logging
import socket
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


def _resolve_bind_targets(host: str, port: int) -> List[str]:
    """Every distinct address ``host`` would bind, in the order it would bind them.

    One resolution, whose *result* is what gets bound -- see
    ``ReportWebServer._start_locked``. Duplicates are dropped because
    ``getaddrinfo`` can report the same address more than once and binding it
    twice is ``EADDRINUSE``.

    Raises ``OSError`` (``socket.gaierror``) for a name that does not resolve;
    the caller turns that into a refusal rather than letting it escape.
    """

    targets: List[str] = []
    for info in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    ):
        address = str(info[4][0])
        if address not in targets:
            targets.append(address)
    return targets


def _format_url(host: str, port: int) -> str:
    """Render ``host``/``port`` as a URL, bracketing an IPv6 literal."""

    try:
        bracket = ipaddress.ip_address(host).version == 6
    except ValueError:
        bracket = False
    return f"http://[{host}]:{port}/" if bracket else f"http://{host}:{port}/"


def _hostname_of(host_header: str) -> str:
    """The host name in a Host header: no port, no IPv6 brackets, no root dot.

    The trailing dot is dropped here rather than at the call site. It is a
    syntactic part of the header value in the same way the port and the
    brackets are -- ``localhost.`` is ``localhost`` with the DNS root written
    out, and a browser sends whatever the operator typed -- so a second caller
    must not be able to get a different answer from the first. Case is not
    dropped here: that is a property of the comparison in ``_is_own_host``, not
    of the name.
    """

    host_header = host_header.strip()
    if host_header.startswith("["):
        hostname = host_header[1 : host_header.find("]")] if "]" in host_header else ""
    else:
        hostname = host_header.split(":", 1)[0]
    return hostname.rstrip(".")


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

      **The refusal is decided before anything binds, for a host *name* as
      well as for a literal.** A literal is judged as it stands. A name is
      resolved once, here, and the addresses that come back are both what gets
      judged and what gets handed to aiohttp.

      That last clause is the whole trick. An earlier revision resolved
      nothing and left a name to the post-bind check, which reads back what the
      socket actually got -- correct, but only after the socket exists, so a
      name resolving off-box did open a listening socket for the microseconds
      between ``site.start()`` and ``runner.cleanup()``. The stated reason for
      accepting that window was that resolving the name here and then handing
      aiohttp *the same string* is two independent lookups that can disagree,
      which is the failure the post-bind check exists to catch. Handing it the
      resolved *addresses* is one lookup, and there is nothing left to
      disagree with: aiohttp binds the literals this class already judged.

      The post-bind read-back stays, as a backstop rather than as the
      authority. Nothing this class can be configured with reaches it any
      more, which is why its test injects the fault.

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
        # `start()` awaits twice between checking `self._runner` and setting it,
        # so two concurrent calls both passed the guard and both bound. Measured:
        # two listening sockets, one of them untracked, and `stop()` closed only
        # the one that won the assignment -- the other stayed open for the life
        # of the process. `on_ready` re-fires on every gateway reconnect, which
        # is exactly where a second call comes from.
        self._start_lock = asyncio.Lock()

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

        async with self._start_lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        if self._runner:
            return

        # Cheap pre-check, so the common misconfiguration (`0.0.0.0`) never
        # opens a socket at all and costs no name resolution either. Only
        # address literals are judged here; a host *name* is judged by the
        # resolution below.
        if not self.host or (_parses_as_ip(self.host) and not _is_loopback_literal(self.host)):
            self._refuse("is not a loopback literal", bound=False)
            return

        # A name cannot be judged without resolving it, so resolve it once and
        # bind the result. `bind_targets` is what reaches aiohttp below, which
        # is what makes the refusal atomic for a name: every address that gets
        # bound has already been judged here, and no second resolution happens
        # that could produce a different one.
        bind_targets = [self.host]
        if not _parses_as_ip(self.host):
            try:
                bind_targets = _resolve_bind_targets(self.host, self.port)
            except OSError as exc:
                # A name that does not resolve is the same class of operator
                # error as an address that will not bind, and gets the same
                # treatment: logged with the remedy, and the bot boots.
                logger.error(
                    "Report web UI NOT started: could not resolve reports.web_host "
                    "%r (%s). Set reports.web_host to 127.0.0.1, or set "
                    "reports.web_enabled to false.",
                    self.host, exc,
                )
                return
            exposed = [host for host in bind_targets if not _is_loopback_literal(host)]
            if exposed:
                self._refuse(f"resolves to {', '.join(exposed)}", bound=False)
                return

        app = web.Application(middlewares=[self._guard_request])
        app.router.add_get("/", self._index)
        app.router.add_post("/reports/{report_id}/status", self._update_status)

        runner = web.AppRunner(app)
        await runner.setup()

        try:
            # One site per address, rather than one site handed the whole list:
            # `TCPSite.name` builds a URL from its host and raises TypeError on
            # a list, and a name that resolves to both ::1 and 127.0.0.1 is the
            # ordinary case here rather than an exotic one.
            for target in bind_targets:
                await web.TCPSite(runner, target, self.port).start()
        except OSError as exc:
            # A refusal, not a fault. `::ffff:127.0.0.1` is the case that
            # matters: `ipaddress` calls it loopback so it clears the pre-check,
            # and the kernel then rejects the bind outright --
            # `OSError: [Errno 22] invalid argument`. Left to propagate, that
            # reaches `on_ready`'s handler as "Failed to start report web UI"
            # with a traceback, which reads as a bug in this server rather than
            # as an address the operator can fix. Any unbindable address --
            # a port already in use, a privileged port -- lands here too, and
            # the same reasoning applies: the bot must still boot.
            await runner.cleanup()
            logger.error(
                "Report web UI NOT started: could not bind reports.web_host %r "
                "on port %d (%s). Set reports.web_host to 127.0.0.1, or set "
                "reports.web_enabled to false.",
                self.host, self.port, exc,
            )
            return
        except Exception:
            await runner.cleanup()
            raise

        # Backstop: what the socket layer actually bound, not what was asked
        # for. Since the resolution above decides what gets bound, no
        # configuration reaches this branch any more -- it is here because the
        # cost of a redundant check is one list comprehension and the cost of
        # trusting an unverified bind is publishing every guild's reports.
        # An empty address list is refused for the same reason: if the bind
        # cannot be read back, it cannot be claimed to be loopback.
        bound = [(str(address[0]), int(address[1])) for address in runner.addresses]
        exposed = [host for host, _ in bound if not _is_loopback_literal(host)]
        if not bound or exposed:
            await runner.cleanup()
            self._refuse(
                f"bound {', '.join(exposed)}" if exposed else "bound no readable address",
                bound=True,
            )
            return

        self._runner = runner
        self._bound = bound
        # Adopt the port the socket actually got. With `web_port: 0` the kernel
        # picks one, and `self.port` stayed 0 -- so `_refuse`'s `ssh -L` remedy
        # named port 0, and any later reader of `.port` was told the request
        # rather than the result. `.urls` already reads `_bound` and was right.
        self.port = bound[0][1]
        logger.info("Report web UI running at %s", ", ".join(self.urls))

    def _refuse(self, reason: str, *, bound: bool) -> None:
        """Log why the UI is not starting.

        Logged and returned rather than raised: this is a deliberate refusal,
        not a fault, and ``discord_bot.on_ready`` would render a raise as
        "Failed to start report web UI" plus a traceback, which reads as a bug
        in the server rather than as a problem with the address.

        ``bound`` distinguishes the two callers, because the message used to
        claim the address "binds" something on the pre-check path -- where no
        socket was ever opened -- which sends an operator looking for a listener
        that does not exist.
        """

        logger.error(
            "Report web UI NOT started: reports.web_host %r %s, so it is not a "
            "loopback-only listener%s. This page has no authentication and serves "
            "every guild's reports, so it is loopback-only by construction. Set "
            "reports.web_host to 127.0.0.1 and reach it remotely with a port forward "
            "(ssh -L %d:127.0.0.1:%d <host>), or set reports.web_enabled to false.",
            self.host,
            reason,
            "; the socket has been closed again" if bound else "",
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

        hostname = _hostname_of(host_header).lower()
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
