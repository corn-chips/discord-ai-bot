"""DAB-143, DAB-144 and PPR-11: the report web UI's enforcement boundary.

The page has no authentication of any kind and renders every guild's reports --
descriptions, reporter display names and raw Discord user ids -- so the whole
of its access control is "only this machine can reach it, and only this page can
change anything". These tests hold both halves at the boundary that enforces
them: a real aiohttp server on a real socket, real rows in a real SQLite
database, and real HTTP requests.

Nothing here asserts a Python attribute or a log line. A refusal is proven by
the socket layer -- either no socket was opened at all, or the one that was is
no longer answering; a rejected write is proven by reading the row back out of
SQLite and finding it unchanged.

Nothing here opens a listener off this machine either, which was not true
before the bind became atomic: proving that `web_host: "0"` is refused used to
mean binding 0.0.0.0, on every interface, on every run of the suite, for as
long as the refusal took to decide. See
`test_a_host_name_that_resolves_off_box_never_opens_a_socket`.

The awkward half is deliberate. A CSRF guard that rejects the operator's own
form gets switched off, and a switched-off guard is worse than none -- so for
every refusal below there is a matching test that the legitimate request shape
still works, including the two ways an operator reaches a loopback service
without editing the config: by name (`localhost`) and through a port forward
onto some other local port.
"""

import asyncio
import socket
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import aiohttp
from aiohttp import web

from src.services import report_web_server
from src.services.report_service import ReportService
from src.services.report_web_server import ReportWebServer, _parses_as_ip


def _socket_recorder():
    """A ``TCPSite`` that records every socket it opens, and the record.

    Returns the class to patch in and the list it appends ``(host, port)`` to.

    This replaces the ``_free_port()`` helper these tests used to ask for a
    port and then assert nothing was listening on it. That was a TOCTOU
    window -- the probe socket is closed before the server starts, so another
    process can take the port in between and turn a refusal into a false
    failure -- and it answered a weaker question. ``runner.addresses`` reports
    only the sockets the server *kept*, so a socket that was opened and then
    closed again, which is the whole question a refusal raises, does not appear
    there at all. This observes the socket layer directly: every port the
    server opened, including the ones it did not keep.
    """

    opened = []

    class RecordingSite(web.TCPSite):
        async def start(self):
            await super().start()
            for sock in self._server.sockets:
                name = sock.getsockname()
                opened.append((str(name[0]), int(name[1])))

    return RecordingSite, opened


def _runner_reporting(addresses):
    """An ``AppRunner`` that reports ``addresses`` whatever it really bound.

    The post-bind read-back is a backstop that no value of ``reports.web_host``
    can reach any more -- the resolution decides what gets bound, so what comes
    back is what went in. Injecting the fault is the only way left to exercise
    it. What is faked is the environment's report of the bind; the socket, the
    decision and the teardown under test are all real.
    """

    class ReportingRunner(web.AppRunner):
        @property
        def addresses(self):
            return list(addresses)

    return ReportingRunner


class ReportWebServerBindTest(unittest.IsolatedAsyncioTestCase):
    """The socket may only bind a loopback address."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = ReportService(str(Path(self._tmp.name) / "token_usage.db"))

    def _server(self, host: str, port: int = 0) -> ReportWebServer:
        # addAsyncCleanup rather than a loop in asyncTearDown: cleanups all run
        # even when one of them raises, where the loop stopped at the first
        # failure and left every later server listening for the rest of the
        # process.
        server = ReportWebServer(self.service, host=host, port=port)
        self.addAsyncCleanup(server.stop)
        return server

    async def _nothing_is_listening(self, port: int) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(
                    f"http://127.0.0.1:{port}/",
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except aiohttp.ClientConnectorError:
            return True
        return False

    async def test_a_wildcard_literal_is_refused_without_opening_a_socket(self):
        # The pre-check's whole value is that it refuses without binding: the
        # post-bind check would catch 0.0.0.0 too, but only after the socket
        # has briefly existed, and a connection arriving in that window does
        # get accepted. The recorder is what makes the difference observable --
        # both paths end with nothing listening, and only one of them ever
        # opened a socket.
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                site, opened = _socket_recorder()
                server = self._server(host)

                # assertLogs both captures the refusal -- it is the operator's
                # only signal, and a silent refusal would be its own defect --
                # and keeps it out of the suite's output.
                with patch.object(report_web_server.web, "TCPSite", site):
                    with self.assertLogs("src.services.report_web_server", "ERROR") as logs:
                        await server.start()
                self.assertIn("loopback", "".join(logs.output))

                self.assertEqual(
                    opened, [],
                    f"{host} was refused, but a socket was opened on {opened}",
                )
                self.assertFalse(server.is_running)
                self.assertEqual(server.bound_addresses, [])

    async def test_the_refusal_does_not_raise_so_the_bot_still_boots(self):
        # A raise here is caught by on_ready's `except Exception` and rendered
        # as a failure with a traceback. Refusing an address is a decision, not
        # a fault, and the rest of the bot is unaffected by it.
        server = self._server("0.0.0.0")

        with self.assertLogs("src.services.report_web_server", "ERROR"):
            await server.start()  # must not raise

        self.assertFalse(server.is_running)

    async def test_the_shipped_loopback_default_still_serves_the_page(self):
        report = self.service.create_report(
            report_type="issue",
            description="CANARY-DESCRIPTION",
            reporter_id=7,
            reporter_name="Reporter",
            guild_id=111,
            channel_id=1,
        )
        server = self._server("127.0.0.1")

        await server.start()

        self.assertTrue(server.is_running)
        port = server.bound_addresses[0][1]
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as response:
                body = await response.text()
                self.assertEqual(response.status, 200)
        self.assertIn("CANARY-DESCRIPTION", body)
        self.assertIn(str(report.id), body)

    async def test_a_host_name_that_resolves_off_box_never_opens_a_socket(self):
        # The case only the resolution can judge, and the reason it exists.
        # `"0"` is not an address literal -- ipaddress.ip_address rejects it,
        # so the literal pre-check has nothing to say about it -- and
        # getaddrinfo resolves it to 0.0.0.0, so aiohttp handed the string
        # would bind every interface.
        #
        # This is the hermetic stand-in for the real shape, a host name whose
        # A record is off-box: `"0"` needs no DNS and no non-loopback address
        # on this machine, where the machine's own host name self-skips on any
        # host that has none, leaving the mutant alive for an environment
        # reason rather than a code one.
        #
        # Until the resolution landed this bound 0.0.0.0 for real and tore it
        # down again a moment later, which meant every run of this suite --
        # on any machine, shared or not -- opened a listener on every
        # interface. `opened == []` is both the security property and the
        # reason the suite no longer does that.
        self.assertFalse(_parses_as_ip("0"), "the pre-check must not be able to judge '0'")
        site, opened = _socket_recorder()
        server = self._server("0")

        with patch.object(report_web_server.web, "TCPSite", site):
            with self.assertLogs("src.services.report_web_server", "ERROR") as logs:
                await server.start()
        self.assertIn("0.0.0.0", "".join(logs.output))

        self.assertEqual(
            opened, [], f"'0' was refused, but a socket was opened on {opened}"
        )
        self.assertFalse(server.is_running)
        self.assertEqual(server.bound_addresses, [])

    async def test_a_host_name_that_does_not_resolve_is_refused_not_raised(self):
        # Resolving the name here is what makes the refusal atomic, and it
        # introduces a failure the old code could not have: getaddrinfo raises.
        # Left to propagate it reaches on_ready's handler as "Failed to start
        # report web UI" plus a traceback, which reads as a bug in this server
        # rather than as a typo in the config -- the same reasoning as the
        # unbindable-address path below.
        #
        # `"-"` is rejected by the resolver locally, as an invalid host name,
        # so this costs no DNS query and cannot hang on a slow resolver.
        site, opened = _socket_recorder()
        server = self._server("-")

        with patch.object(report_web_server.web, "TCPSite", site):
            with self.assertLogs("src.services.report_web_server", "ERROR") as logs:
                await server.start()  # must not raise
        self.assertIn("could not resolve", "".join(logs.output))

        self.assertEqual(opened, [])
        self.assertFalse(server.is_running)

    async def test_a_bind_the_runner_reports_as_off_box_is_torn_down(self):
        # The `exposed` half of the post-bind read-back. 203.0.113.7 is
        # TEST-NET-3, and off this box. The socket really is opened on
        # 127.0.0.1, and the assertion is that it really is closed again.
        site, opened = _socket_recorder()

        server = self._server("127.0.0.1")
        with patch.object(report_web_server.web, "TCPSite", site), patch.object(
            report_web_server.web, "AppRunner", _runner_reporting([("203.0.113.7", 8080)])
        ):
            with self.assertLogs("src.services.report_web_server", "ERROR") as logs:
                await server.start()
        self.assertIn("203.0.113.7", "".join(logs.output))

        self.assertFalse(server.is_running)
        self.assertEqual(server.bound_addresses, [])
        self.assertEqual(len(opened), 1, f"expected one socket, got {opened}")
        self.assertTrue(
            await self._nothing_is_listening(opened[0][1]),
            f"the bind reported as off-box is still listening on {opened[0][1]}",
        )

    async def test_a_bind_that_cannot_be_read_back_is_torn_down(self):
        # The `not bound` half, which had no test of its own: M-DAB143 and
        # M-DAB143B delete the whole branch and are killed through `exposed`,
        # so nothing distinguished "refuse an off-box address" from "refuse an
        # address list you cannot read". They are not the same rule. An empty
        # list names nothing to complain about, and a bind that cannot be read
        # back cannot be claimed to be loopback -- the alternative is a server
        # that either publishes a `.urls` it never bound or, as the isolating
        # mutant M-DAB143G shows, dies on `bound[0][1]` inside on_ready.
        #
        # Reachable without injection only if getaddrinfo returns an empty list
        # instead of raising, which POSIX does not allow it to do. That is what
        # a backstop is.
        site, opened = _socket_recorder()

        server = self._server("127.0.0.1")
        with patch.object(report_web_server.web, "TCPSite", site), patch.object(
            report_web_server.web, "AppRunner", _runner_reporting([])
        ):
            with self.assertLogs("src.services.report_web_server", "ERROR") as logs:
                await server.start()  # must refuse, not raise
        self.assertIn("bound no readable address", "".join(logs.output))

        self.assertFalse(server.is_running)
        self.assertEqual(server.bound_addresses, [])
        self.assertEqual(len(opened), 1, f"expected one socket, got {opened}")
        self.assertTrue(
            await self._nothing_is_listening(opened[0][1]),
            f"the unreadable bind is still listening on {opened[0][1]}",
        )

    async def test_a_host_that_needs_stripping_still_starts(self):
        # config_helpers strips only for its emptiness test and stores the
        # value as written, so " 127.0.0.1 " reaches here intact and would go
        # to getaddrinfo as a host name -- gaierror, and a traceback.
        server = self._server(" 127.0.0.1 ")

        await server.start()

        self.assertTrue(server.is_running)
        self.assertEqual([host for host, _ in server.bound_addresses], ["127.0.0.1"])

    async def test_a_host_name_that_binds_only_loopback_is_allowed(self):
        # `localhost` cannot be judged without resolving it, and this is the
        # case the resolution exists to allow rather than refuse: it commonly
        # resolves to both 127.0.0.1 and ::1, and both are loopback, so both
        # get bound.
        server = self._server("localhost")

        await server.start()

        self.assertTrue(server.is_running)
        self.assertTrue(server.bound_addresses)
        for host, _port in server.bound_addresses:
            self.assertIn(host, {"127.0.0.1", "::1"})


class ReportWebServerUrlTest(unittest.IsolatedAsyncioTestCase):
    """DAB-144: the URL the operator is shown must be the address in use."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = ReportService(str(Path(self._tmp.name) / "token_usage.db"))

    async def test_a_wildcard_bind_is_never_displayed_as_loopback(self):
        # The old property rewrote 0.0.0.0 and :: to 127.0.0.1, so at the one
        # moment the operator was looking, a service on every interface
        # described itself as local-only.
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                server = ReportWebServer(self.service, host=host, port=8080)

                self.assertNotIn("127.0.0.1", server.url)
                self.assertIn(host, server.url)

    async def test_a_running_server_reports_the_address_it_actually_bound(self):
        server = ReportWebServer(self.service, host="127.0.0.1", port=0)

        await server.start()
        try:
            self.assertTrue(server.is_running)
            bound_port = server.bound_addresses[0][1]
            # Port 0 is what was configured; the URL must show what was bound.
            self.assertNotEqual(bound_port, 0)
            self.assertEqual(server.url, f"http://127.0.0.1:{bound_port}/")
        finally:
            await server.stop()

    async def test_an_ipv6_address_is_bracketed_rather_than_crashing(self):
        # runner.addresses yields a 2-tuple for IPv4 and a 4-tuple for IPv6, so
        # unpacking it raises ValueError -- after the socket is already bound --
        # for the perfectly legal `web_host: "::1"`.
        server = ReportWebServer(self.service, host="::1", port=0)

        await server.start()
        try:
            self.assertTrue(server.is_running)
            bound_port = server.bound_addresses[0][1]
            self.assertEqual(server.url, f"http://[::1]:{bound_port}/")
        finally:
            await server.stop()


class ReportWebServerRequestGuardTest(unittest.IsolatedAsyncioTestCase):
    """PPR-11: only this page may change a report.

    `_update_status` reads `await request.post()`, an
    `application/x-www-form-urlencoded` body, which is a *simple* content type:
    a browser submits it cross-origin with no CORS preflight, and there is no
    cookie to make SameSite. So on the shipped loopback default, any page the
    operator visits while the bot is running could silently reclassify or
    annotate any report by id. Before this guard that request returned 303 and
    changed the row.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = ReportService(str(Path(self._tmp.name) / "token_usage.db"))
        self.report = self.service.create_report(
            report_type="issue",
            description="a report",
            reporter_id=7,
            reporter_name="Reporter",
            guild_id=111,
            channel_id=1,
        )
        self.server = ReportWebServer(self.service, host="127.0.0.1", port=0)
        self.addAsyncCleanup(self.server.stop)
        await self.server.start()
        self.assertTrue(self.server.is_running)
        self.port = self.server.bound_addresses[0][1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.session = aiohttp.ClientSession()
        # Registered separately, and after the server, so that a session that
        # fails to close cannot leave the listening socket behind: the teardown
        # this replaced awaited both in one coroutine, in that order.
        self.addAsyncCleanup(self.session.close)

    async def _post_status(self, headers):
        return await self.session.post(
            f"{self.origin}/reports/{self.report.id}/status",
            data={"status": "declined", "admin_notes": "written by the request"},
            headers=headers,
            allow_redirects=False,
        )

    def _stored(self):
        report = self.service.get_report(self.report.id)
        return report.status, report.admin_notes

    async def test_a_cross_origin_form_post_cannot_change_a_report(self):
        before = self._stored()

        response = await self._post_status({"Origin": "https://evil.example"})

        self.assertEqual(response.status, 403)
        self.assertEqual(self._stored(), before)

    async def test_an_origin_of_null_cannot_change_a_report(self):
        # An attacker's form can force this: per Fetch Standard section 3.2, a
        # `no-referrer` referrer policy sets the serialised origin to the
        # literal `null` rather than omitting the header, and a sandboxed
        # iframe or a data: URL produces the same value.
        before = self._stored()

        response = await self._post_status({"Origin": "null"})

        self.assertEqual(response.status, 403)
        self.assertEqual(self._stored(), before)

    async def test_a_post_with_no_origin_at_all_cannot_change_a_report(self):
        # Refusing this cannot break the UI: by Fetch Standard section 3.2 the
        # Origin append is unconditional for any method that is not GET or
        # HEAD, so a browser form always carries one. Only a non-browser client
        # reaches this branch, and allowing it would make the guard optional.
        before = self._stored()

        response = await self._post_status({})

        self.assertEqual(response.status, 403)
        self.assertEqual(self._stored(), before)

    async def test_a_rebound_dns_name_cannot_change_a_report(self):
        # DNS rebinding is what makes "compare Origin to the Host the client
        # used" insufficient on its own: the attacker points evil.example at
        # 127.0.0.1, so their page's form POST carries Host: evil.example AND
        # Origin: http://evil.example. Those agree with each other, and an
        # Origin-equals-Host check passes them. Measured at 303, row mutated,
        # before the Host allowlist was added.
        before = self._stored()

        response = await self._post_status(
            {
                "Host": f"evil.example:{self.port}",
                "Origin": f"http://evil.example:{self.port}",
            }
        )

        self.assertEqual(response.status, 421)
        self.assertEqual(self._stored(), before)

    async def test_an_absent_host_header_cannot_change_a_report(self):
        # Without the allowlist this was the sharpest bypass of the lot: an
        # empty Host makes the expected origin `http://`, and `Origin: null`
        # parsed to an empty netloc, so the two compared equal and the single
        # value the guard most wants to reject was accepted.
        before = self._stored()

        response = await self._post_status({"Host": "", "Origin": "null"})

        self.assertEqual(response.status, 421)
        self.assertEqual(self._stored(), before)

    async def test_an_origin_that_only_looks_right_cannot_change_a_report(self):
        # These are the shapes a lenient URL parse lets through: they all yield
        # the correct netloc, and none of them is an origin a browser can
        # produce. The comparison is an exact string match for that reason.
        for suffix in ("/", "/path", "\thttp://evil.example", " "):
            with self.subTest(origin=repr(self.origin + suffix)):
                before = self._stored()

                response = await self._post_status({"Origin": self.origin + suffix})

                self.assertEqual(response.status, 403)
                self.assertEqual(self._stored(), before)

    async def test_an_uppercased_scheme_cannot_change_a_report(self):
        before = self._stored()

        response = await self._post_status({"Origin": self.origin.replace("http", "HTTP")})

        self.assertEqual(response.status, 403)
        self.assertEqual(self._stored(), before)

    async def test_the_page_own_form_post_still_changes_the_report(self):
        # The direction that matters most. If this ever fails the guard is
        # worthless, because the operator will turn it off.
        response = await self._post_status({"Origin": self.origin})

        self.assertEqual(response.status, 303)
        self.assertEqual(self._stored(), ("declined", "written by the request"))

    async def test_reaching_the_page_by_name_still_changes_the_report(self):
        # `web_host` is 127.0.0.1 but the operator typed localhost. Comparing
        # the Origin against the *configured* host rejects this, and it is the
        # obvious way to write the check.
        response = await self._post_status(
            {
                "Host": f"localhost:{self.port}",
                "Origin": f"http://localhost:{self.port}",
            }
        )

        self.assertEqual(response.status, 303)
        self.assertEqual(self._stored(), ("declined", "written by the request"))

    async def test_reaching_the_page_through_a_port_forward_still_works(self):
        # `ssh -L 9000:127.0.0.1:8080` is the supported way to reach this UI
        # from another machine, and it is why no non-loopback bind flag is
        # offered. The browser then sees port 9000, which the server never
        # bound, so the guard must not compare ports.
        response = await self._post_status(
            {"Host": "localhost:9000", "Origin": "http://localhost:9000"}
        )

        self.assertEqual(response.status, 303)
        self.assertEqual(self._stored(), ("declined", "written by the request"))

    async def test_reading_the_page_needs_no_origin(self):
        # GET changes nothing, so requiring an Origin on it would break every
        # ordinary visit for no gain.
        async with self.session.get(f"{self.origin}/") as response:
            self.assertEqual(response.status, 200)

    async def test_the_page_cannot_be_framed(self):
        # Clickjacking reaches the same mutation and the Origin check cannot
        # see it: a framed copy of this page submits its own form, so the
        # Origin is genuinely correct.
        async with self.session.get(f"{self.origin}/") as response:
            self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(
                response.headers.get("Content-Security-Policy"),
                "frame-ancestors 'none'",
            )

    async def test_the_page_does_not_suppress_its_own_origin(self):
        # A Referrer-Policy of no-referrer is the tempting next security header
        # to add here, and by Fetch Standard section 3.2 it would make this
        # page's own form send `Origin: null` -- so the server would 403 every
        # save. Pinned so that lands as a test failure, not as a broken UI.
        async with self.session.get(f"{self.origin}/") as response:
            self.assertNotEqual(
                response.headers.get("Referrer-Policy", ""), "no-referrer"
            )


class ReportWebServerStartupResidualsTest(unittest.IsolatedAsyncioTestCase):
    """Four residuals the Phase 2.3 review found and Phase 2.3 did not close.

    None is a security boundary -- the loopback refusal and the Origin guard
    both hold. They are the surrounding machinery being wrong in ways an
    operator or a reconnect can reach.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = ReportService(str(Path(self._tmp.name) / "token_usage.db"))

    def _server(self, host, port=0):
        server = ReportWebServer(self.service, host=host, port=port)
        self.addAsyncCleanup(server.stop)
        return server

    async def test_two_concurrent_starts_leave_no_socket_behind(self):
        # `start()` awaits twice between checking `_runner` and setting it, so
        # both callers passed the guard and both bound. Measured before the fix
        # with `ss -ltnp`: two listening sockets, and `stop()` closed only the
        # one that won the assignment -- the other stayed open for the life of
        # the process. `on_ready` re-fires on every gateway reconnect, which is
        # where the second call comes from.
        #
        # Every port the server opens is recorded, not just the one it kept.
        # The first draft probed `bound_addresses[0][1]`, which is the winner's
        # port and is closed correctly either way -- so it never saw the leak,
        # and the missing-lock mutant survived it. The leaked socket is on a
        # port the server does not report.
        site, opened = _socket_recorder()

        server = self._server("127.0.0.1")
        with patch.object(report_web_server.web, "TCPSite", site):
            await asyncio.gather(server.start(), server.start())
        try:
            self.assertTrue(server.is_running)
        finally:
            await server.stop()

        self.assertEqual(
            len(opened), 1,
            f"{len(opened)} sockets were bound by two concurrent start() calls",
        )
        for _host, port in opened:
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertNotEqual(
                    probe.connect_ex(("127.0.0.1", port)), 0,
                    f"a listening socket on port {port} survived stop()",
                )

    async def test_an_ephemeral_port_is_adopted_after_the_bind(self):
        # With `web_port: 0` the kernel picks the port. `self.port` stayed 0, so
        # the refusal message's `ssh -L 0:127.0.0.1:0` remedy was nonsense and
        # any later reader of `.port` got the request rather than the result.
        server = self._server("127.0.0.1", 0)

        await server.start()
        try:
            self.assertNotEqual(server.port, 0)
            self.assertEqual(server.port, server.bound_addresses[0][1])
            self.assertIn(str(server.port), server.urls[0])
        finally:
            await server.stop()

    async def test_an_unbindable_loopback_address_is_refused_not_raised(self):
        # `::ffff:127.0.0.1` is loopback as far as `ipaddress` is concerned, so
        # it clears the pre-check, and the kernel then refuses the bind with
        # `OSError: [Errno 22] invalid argument`. Propagated, that reaches
        # `on_ready` as "Failed to start report web UI" plus a traceback, which
        # reads as a bug in this server rather than an address to fix.
        server = self._server("::ffff:127.0.0.1")

        with self.assertLogs("src.services.report_web_server", "ERROR") as captured:
            await server.start()

        self.assertFalse(server.is_running)
        self.assertEqual(server.bound_addresses, [])
        self.assertIn("could not bind", captured.output[0])

    async def test_the_pre_check_refusal_does_not_claim_a_socket_was_bound(self):
        # The pre-check path opens no socket at all, and the message used to say
        # the host "binds 0.0.0.0" -- sending an operator to look for a listener
        # that never existed.
        server = self._server("0.0.0.0")

        with self.assertLogs("src.services.report_web_server", "ERROR") as captured:
            await server.start()

        self.assertFalse(server.is_running)
        message = captured.output[0]
        self.assertIn("is not a loopback literal", message)
        self.assertNotIn("socket has been closed", message)


if __name__ == "__main__":
    unittest.main()
