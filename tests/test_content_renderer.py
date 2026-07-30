"""Offline regression coverage for Discord content rendering."""

import unittest
from unittest.mock import Mock, patch

from src.constants import MAX_LATEX_FIGURE_HEIGHT_INCHES
from src.services.content_renderer import ContentRenderer


class ContentRendererTest(unittest.TestCase):
    """Exercise text formatting without network access or pixel snapshots."""

    def setUp(self):
        self.renderer = object.__new__(ContentRenderer)
        self.renderer._matplotlib_available = False

    def test_plain_text_is_unchanged(self):
        result = self.renderer.process_response("A plain response without markup.")

        self.assertEqual(result.text, "A plain response without markup.")
        self.assertEqual(result.attachments, [])

    def test_markdown_table_is_formatted_as_fixed_width_code_block(self):
        source = (
            "Results:\n"
            "| Name | Value |\n"
            "|:-----|------:|\n"
            "| short | 7 |\n"
            "| longer | 12 |"
        )

        result = self.renderer.process_response(source)

        self.assertEqual(
            result.text,
            "Results:\n"
            "```\n"
            "Name   | Value\n"
            "-------+------\n"
            "short  | 7    \n"
            "longer | 12   \n"
            "```",
        )
        self.assertEqual(result.attachments, [])

    def test_table_preserves_newline_before_following_prose(self):
        source = (
            "| Name | Value |\n"
            "|------|-------|\n"
            "| foo | bar |\n"
            "Done."
        )

        result = self.renderer.process_response(source)

        self.assertEqual(
            result.text,
            "```\n"
            "Name | Value\n"
            "-----+------\n"
            "foo  | bar  \n"
            "```\n"
            "Done.",
        )

    def test_table_preserves_crlf_before_following_prose(self):
        source = (
            "| Name | Value |\r\n"
            "|------|-------|\r\n"
            "| foo | bar |\r\n"
            "Done."
        )

        result = self.renderer.process_response(source)

        self.assertEqual(
            result.text,
            "```\n"
            "Name | Value\n"
            "-----+------\n"
            "foo  | bar  \n"
            "```\r\n"
            "Done.",
        )

    def test_crlf_table_at_eof_adds_no_trailing_boundary(self):
        source = (
            "| Name | Value |\r\n"
            "|------|-------|\r\n"
            "| foo | bar |"
        )

        result = self.renderer.process_response(source)

        self.assertEqual(
            result.text,
            "```\n"
            "Name | Value\n"
            "-----+------\n"
            "foo  | bar  \n"
            "```",
        )

    def test_separate_tables_keep_one_blank_line_between_code_blocks(self):
        source = (
            "| A |\n"
            "|---|\n"
            "| 1 |\n"
            "\n"
            "| B |\n"
            "|---|\n"
            "| 2 |"
        )

        result = self.renderer.process_response(source)

        self.assertEqual(
            result.text,
            "```\nA\n-\n1\n```\n\n```\nB\n-\n2\n```",
        )

    def test_simple_inline_latex_uses_unicode_without_attachment(self):
        result = self.renderer.process_response(r"For $x^2 + \alpha_1$, continue.")

        self.assertEqual(result.text, "For x\u00b2 + \u03b1\u2081, continue.")
        self.assertEqual(result.attachments, [])

    def test_complex_inline_latex_falls_back_to_inline_code(self):
        result = self.renderer.process_response(r"Use $\operatorname{rank}(A)$ here.")

        self.assertEqual(result.text, r"Use `\operatorname{rank}(A)` here.")
        self.assertEqual(result.attachments, [])

    def test_block_latex_creates_one_attachment_and_closes_cleanly(self):
        self.renderer._render_combined_latex_image = Mock(return_value=b"png-bytes")

        result = self.renderer.process_response("Equation:\n$$E=mc^2$$")

        self.assertEqual(result.text, "Equation:\n**[1]**")
        self.assertEqual(len(result.attachments), 1)
        attachment = result.attachments[0]
        self.assertEqual(attachment.filename, "equations.png")
        attachment.fp.seek(0)
        self.assertEqual(attachment.fp.read(), b"png-bytes")
        self.renderer._render_combined_latex_image.assert_called_once_with(
            [("[1]", "E=mc^2")]
        )

        # discord.File deliberately leaves caller-owned buffers open. Restore
        # its closer first, then release the BytesIO owned by this test.
        attachment.close()
        attachment.fp.close()
        self.assertTrue(attachment.fp.closed)

    def test_identical_block_and_inline_latex_use_their_own_positions(self):
        self.renderer._render_combined_latex_image = Mock(return_value=b"png")

        result = self.renderer.process_response("$$x+1$$ then $x+1$")

        self.assertEqual(result.text, "**[1]** then x+1")
        self.assertEqual(len(result.attachments), 1)
        result.attachments[0].close()

    def test_duplicate_complex_expressions_keep_block_then_inline_numbering(self):
        self.renderer._render_combined_latex_image = Mock(return_value=b"png")

        result = self.renderer.process_response(
            r"$$\operatorname{rank}(A)$$ then $\operatorname{rank}(A)$"
        )

        self.assertEqual(result.text, "**[1]** then **[2]**")
        self.renderer._render_combined_latex_image.assert_called_once_with(
            [
                ("[1]", r"\operatorname{rank}(A)"),
                ("[2]", r"\operatorname{rank}(A)"),
            ]
        )
        result.attachments[0].close()

    def test_complex_inline_before_block_uses_text_order_numbering(self):
        self.renderer._render_combined_latex_image = Mock(return_value=b"png")

        result = self.renderer.process_response(
            r"$\operatorname{rank}(A)$ then $$E=mc^2$$"
        )

        self.assertEqual(result.text, "**[1]** then **[2]**")
        self.renderer._render_combined_latex_image.assert_called_once_with(
            [
                ("[1]", r"\operatorname{rank}(A)"),
                ("[2]", "E=mc^2"),
            ]
        )
        result.attachments[0].close()

    def test_interleaved_block_and_complex_inline_keep_text_order(self):
        self.renderer._render_combined_latex_image = Mock(return_value=b"png")

        result = self.renderer.process_response(
            r"$$a+b$$, $\operatorname{rank}(A)$, then $$c+d$$"
        )

        self.assertEqual(result.text, "**[1]**, **[2]**, then **[3]**")
        self.renderer._render_combined_latex_image.assert_called_once_with(
            [
                ("[1]", "a+b"),
                ("[2]", r"\operatorname{rank}(A)"),
                ("[3]", "c+d"),
            ]
        )
        result.attachments[0].close()

    def test_unclosed_latex_delimiters_are_left_unchanged(self):
        source = "An unfinished equation $$x + 1"

        result = self.renderer.process_response(source)

        self.assertEqual(result.text, source)
        self.assertEqual(result.attachments, [])

    def test_image_renderer_closes_figure_after_success(self):
        try:
            import matplotlib
            import matplotlib.pyplot as plt
        except ImportError:
            self.skipTest("matplotlib is not installed")

        renderer = object.__new__(ContentRenderer)
        renderer._matplotlib_available = True
        original_usetex = matplotlib.rcParams["text.usetex"]
        original_fontset = matplotlib.rcParams["mathtext.fontset"]
        self.addCleanup(
            matplotlib.rcParams.__setitem__,
            "text.usetex",
            original_usetex,
        )
        self.addCleanup(
            matplotlib.rcParams.__setitem__,
            "mathtext.fontset",
            original_fontset,
        )

        figure = Mock()
        axis = Mock()
        axis.transAxes = object()
        figure.savefig.side_effect = lambda buffer, **_kwargs: buffer.write(b"png")

        with (
            patch.object(plt, "subplots", return_value=(figure, axis)),
            patch.object(plt, "close") as close_mock,
        ):
            rendered = renderer._render_combined_latex_image([("[1]", "x^2")])

        self.assertEqual(rendered, b"png")
        close_mock.assert_called_once_with(figure)

    def test_image_renderer_closes_figures_and_returns_none_on_error(self):
        try:
            import matplotlib
            import matplotlib.pyplot as plt
        except ImportError:
            self.skipTest("matplotlib is not installed")

        renderer = object.__new__(ContentRenderer)
        renderer._matplotlib_available = True
        original_usetex = matplotlib.rcParams["text.usetex"]
        original_fontset = matplotlib.rcParams["mathtext.fontset"]
        self.addCleanup(
            matplotlib.rcParams.__setitem__,
            "text.usetex",
            original_usetex,
        )
        self.addCleanup(
            matplotlib.rcParams.__setitem__,
            "mathtext.fontset",
            original_fontset,
        )

        figure = Mock()
        axis = Mock()
        axis.transAxes = object()
        figure.savefig.side_effect = RuntimeError("render failed")

        with (
            patch.object(plt, "subplots", return_value=(figure, axis)),
            patch.object(plt, "close") as close_mock,
        ):
            rendered = renderer._render_combined_latex_image([("[1]", "x^2")])

        self.assertIsNone(rendered)
        close_mock.assert_called_once_with("all")


class MultiEquationRenderTest(unittest.TestCase):
    """DAB-114: any response with two or more equations failed to render.

    The separator line between rows passed `transform=ax.transAxes` to
    `axhline`, which builds its own blended transform and rejects the keyword.
    The exception was caught and the renderer degraded to code blocks, so the
    advertised LaTeX feature was dead for every multi-equation answer.

    The existing tests above cannot see it, for two independent reasons: they
    mock `plt.subplots`, so a Mock axis happily accepts the illegal keyword, and
    they pass a single expression, so the `if i < n - 1` separator branch is
    never reached. These use the real matplotlib path with two expressions.
    """

    def _renderer(self):
        try:
            import matplotlib
        except ImportError:  # pragma: no cover
            self.skipTest("matplotlib is not installed")
        matplotlib.use("Agg", force=False)

        renderer = object.__new__(ContentRenderer)
        renderer._matplotlib_available = True
        for key in ("text.usetex", "mathtext.fontset"):
            self.addCleanup(
                matplotlib.rcParams.__setitem__, key, matplotlib.rcParams[key]
            )
        return renderer

    def test_two_equations_render_to_a_real_png(self):
        renderer = self._renderer()

        rendered = renderer._render_combined_latex_image(
            [("[1]", "x^2 + y^2 = z^2"), ("[2]", "a + b = c")]
        )

        self.assertIsNotNone(rendered, "multi-equation rendering is broken again")
        self.assertTrue(rendered.startswith(b"\x89PNG"), "not a PNG")
        self.assertGreater(len(rendered), 1000)

    def test_a_single_equation_still_renders(self):
        renderer = self._renderer()

        rendered = renderer._render_combined_latex_image([("[1]", "E = mc^2")])

        self.assertIsNotNone(rendered)
        self.assertTrue(rendered.startswith(b"\x89PNG"))


class LatexFigureHeightCapTest(unittest.TestCase):
    """DAB-128: the computed figure height is attacker-influenced.

    One row is stacked per expression with no upper bound on the count, so a
    long answer asks for an enormous canvas: 400 expressions computes to a
    1500x42060 pixel figure, measured at 5.9 s and 568 MB resident.

    These assert the refusal *without* allocating anything, by spying on
    `plt.subplots` rather than letting it run. A test that actually rendered the
    pathological case would itself burn hundreds of megabytes.
    """

    def _renderer(self):
        try:
            import matplotlib
        except ImportError:  # pragma: no cover
            self.skipTest("matplotlib is not installed")
        matplotlib.use("Agg", force=False)

        renderer = object.__new__(ContentRenderer)
        renderer._matplotlib_available = True
        for key in ("text.usetex", "mathtext.fontset"):
            self.addCleanup(
                matplotlib.rcParams.__setitem__, key, matplotlib.rcParams[key]
            )
        return renderer

    def test_an_oversized_figure_is_refused_before_any_canvas_is_allocated(self):
        import matplotlib.pyplot as plt

        renderer = self._renderer()
        # Comfortably past the cap: 0.4 padding plus at least 0.7 each.
        exprs = [(f"[{i}]", f"x = {i}") for i in range(400)]

        with patch.object(plt, "subplots") as subplots_mock:
            rendered = renderer._render_combined_latex_image(exprs)

        self.assertIsNone(rendered, "an unbounded figure was rendered")
        subplots_mock.assert_not_called()

    def test_a_realistic_answer_stays_under_the_cap(self):
        import matplotlib.pyplot as plt

        renderer = self._renderer()
        exprs = [(f"[{i}]", f"x_{i} = {i}") for i in range(8)]

        with patch.object(plt, "subplots", wraps=plt.subplots) as subplots_mock:
            rendered = renderer._render_combined_latex_image(exprs)

        self.assertIsNotNone(rendered, "a normal multi-equation answer was refused")
        subplots_mock.assert_called_once()
        _, kwargs = subplots_mock.call_args
        self.assertLessEqual(kwargs["figsize"][1], MAX_LATEX_FIGURE_HEIGHT_INCHES)

    def test_the_cap_is_a_sane_size(self):
        # Guards against someone "fixing" a refusal by raising the cap until the
        # bomb comes back. 40 in at the renderer's 150 dpi is 6000 px.
        self.assertGreaterEqual(MAX_LATEX_FIGURE_HEIGHT_INCHES, 10)
        self.assertLessEqual(MAX_LATEX_FIGURE_HEIGHT_INCHES, 100)


if __name__ == "__main__":
    unittest.main()
