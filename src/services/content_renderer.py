"""
Content rendering service for the Discord Grok Bot.

Handles post-processing of AI responses to render LaTeX expressions as images
and convert markdown tables to Discord-friendly code block format.
"""

import io
import re
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import discord

logger = logging.getLogger(__name__)

# LaTeX to Unicode mapping for simple inline expressions
LATEX_UNICODE_MAP = {
    r'\alpha': '\u03B1', r'\beta': '\u03B2', r'\gamma': '\u03B3',
    r'\delta': '\u03B4', r'\epsilon': '\u03B5', r'\zeta': '\u03B6',
    r'\eta': '\u03B7', r'\theta': '\u03B8', r'\iota': '\u03B9',
    r'\kappa': '\u03BA', r'\lambda': '\u03BB', r'\mu': '\u03BC',
    r'\nu': '\u03BD', r'\xi': '\u03BE', r'\pi': '\u03C0',
    r'\rho': '\u03C1', r'\sigma': '\u03C3', r'\tau': '\u03C4',
    r'\upsilon': '\u03C5', r'\phi': '\u03C6', r'\chi': '\u03C7',
    r'\psi': '\u03C8', r'\omega': '\u03C9',
    r'\Alpha': '\u0391', r'\Beta': '\u0392', r'\Gamma': '\u0393',
    r'\Delta': '\u0394', r'\Theta': '\u0398', r'\Lambda': '\u039B',
    r'\Pi': '\u03A0', r'\Sigma': '\u03A3', r'\Phi': '\u03A6',
    r'\Psi': '\u03A8', r'\Omega': '\u03A9',
    r'\infty': '\u221E', r'\pm': '\u00B1', r'\mp': '\u2213',
    r'\times': '\u00D7', r'\div': '\u00F7', r'\cdot': '\u00B7',
    r'\leq': '\u2264', r'\geq': '\u2265', r'\neq': '\u2260',
    r'\approx': '\u2248', r'\equiv': '\u2261', r'\sim': '\u223C',
    r'\in': '\u2208', r'\notin': '\u2209', r'\subset': '\u2282',
    r'\supset': '\u2283', r'\cup': '\u222A', r'\cap': '\u2229',
    r'\emptyset': '\u2205', r'\forall': '\u2200', r'\exists': '\u2203',
    r'\nabla': '\u2207', r'\partial': '\u2202',
    r'\sum': '\u2211', r'\prod': '\u220F', r'\int': '\u222B',
    r'\sqrt': '\u221A', r'\rightarrow': '\u2192', r'\leftarrow': '\u2190',
    r'\Rightarrow': '\u21D2', r'\Leftarrow': '\u21D0',
    r'\leftrightarrow': '\u2194', r'\Leftrightarrow': '\u21D4',
    r'\to': '\u2192', r'\gets': '\u2190',
    r'\land': '\u2227', r'\lor': '\u2228', r'\neg': '\u00AC',
    r'\dots': '\u2026', r'\cdots': '\u22EF', r'\ldots': '\u2026',
}

# Superscript/subscript Unicode mappings
SUPERSCRIPT_MAP = str.maketrans('0123456789+-=()niabcdefghjklmoprstuvwxyz',
                                 '\u2070\u00B9\u00B2\u00B3\u2074\u2075\u2076\u2077\u2078\u2079\u207A\u207B\u207C\u207D\u207E\u207F\u2071\u1D43\u1D47\u1D9C\u1D48\u1D49\u1DA0\u1D4D\u02B0\u02B2\u1D4F\u02E1\u1D50\u1D52\u1D56\u02B3\u02E2\u1D57\u1D58\u1D5B\u02B7\u02E3\u02B8\u1DBB')
SUBSCRIPT_MAP = str.maketrans('0123456789+-=()aeijoruvx',
                               '\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u208A\u208B\u208C\u208D\u208E\u2090\u2091\u2C7C\u2C7C\u2092\u1D63\u1D64\u1D65\u2093')


@dataclass
class RenderedContent:
    """Result of processing AI response content."""
    text: str
    attachments: List[discord.File] = field(default_factory=list)


class ContentRenderer:
    """
    Processes AI response text to render LaTeX and format tables for Discord.

    LaTeX block expressions ($$...$$) are rendered to PNG images via matplotlib.
    Simple inline LaTeX ($...$) is converted to Unicode approximations.
    Complex inline LaTeX is also rendered to images.
    Markdown tables are converted to fixed-width code blocks.
    """

    # Regex patterns
    BLOCK_LATEX_RE = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
    INLINE_LATEX_RE = re.compile(r'(?<!\$)\$(?!\$)(.+?)\$(?!\$)')
    TABLE_RE = re.compile(
        r'((?:^\|.+\|$\n?)+)',
        re.MULTILINE
    )
    SEPARATOR_ROW_RE = re.compile(r'^\|[\s\-:|]+\|$')

    def __init__(self):
        self._matplotlib_available = False
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            import matplotlib.pyplot as plt
            self._matplotlib_available = True
            logger.info("ContentRenderer: matplotlib available for LaTeX rendering")
        except ImportError:
            logger.warning("ContentRenderer: matplotlib not available, LaTeX will use Unicode-only fallback")

    def process_response(self, text: str) -> RenderedContent:
        """
        Process AI response text, rendering LaTeX and formatting tables.

        Args:
            text: Raw AI response text

        Returns:
            RenderedContent with processed text and any image attachments
        """
        attachments: List[discord.File] = []

        # 1. Render block LaTeX ($$...$$) to images
        text = self._process_block_latex(text, attachments)

        # 2. Process inline LaTeX ($...$) — Unicode where possible, image fallback
        text = self._process_inline_latex(text, attachments)

        # 3. Convert markdown tables to code blocks
        text = self._process_tables(text)

        return RenderedContent(text=text, attachments=attachments)

    # ── LaTeX Processing ──────────────────────────────────────────────

    def _process_block_latex(self, text: str, attachments: List[discord.File]) -> str:
        """Replace $$...$$ blocks with rendered PNG images."""
        if not self.BLOCK_LATEX_RE.search(text):
            return text

        def _replace_block(match: re.Match) -> str:
            latex_expr = match.group(1).strip()
            if not latex_expr:
                return match.group(0)

            img_bytes = self._render_latex_to_png(latex_expr, fontsize=16)
            if img_bytes:
                idx = len(attachments) + 1
                filename = f"equation_{idx}.png"
                attachments.append(discord.File(io.BytesIO(img_bytes), filename=filename))
                return f"[equation {idx} - see attached image]"
            else:
                # Fallback: wrap in code block
                return f"`{latex_expr}`"

        return self.BLOCK_LATEX_RE.sub(_replace_block, text)

    def _process_inline_latex(self, text: str, attachments: List[discord.File]) -> str:
        """Replace $...$ with Unicode approximation or rendered image."""
        if not self.INLINE_LATEX_RE.search(text):
            return text

        def _replace_inline(match: re.Match) -> str:
            latex_expr = match.group(1).strip()
            if not latex_expr:
                return match.group(0)

            # Try Unicode conversion first
            unicode_result = self._latex_to_unicode(latex_expr)
            if unicode_result is not None:
                return unicode_result

            # Fall back to image rendering for complex expressions
            img_bytes = self._render_latex_to_png(latex_expr, fontsize=14)
            if img_bytes:
                idx = len(attachments) + 1
                filename = f"equation_{idx}.png"
                attachments.append(discord.File(io.BytesIO(img_bytes), filename=filename))
                return f"[eq. {idx}]"
            else:
                return f"`{latex_expr}`"

        return self.INLINE_LATEX_RE.sub(_replace_inline, text)

    def _latex_to_unicode(self, expr: str) -> Optional[str]:
        """
        Convert simple LaTeX to Unicode text.
        Returns None if the expression is too complex for Unicode.
        """
        result = expr

        # Replace known symbols
        for latex_cmd, unicode_char in LATEX_UNICODE_MAP.items():
            result = result.replace(latex_cmd, unicode_char)

        # Handle simple superscripts: x^2, x^{10}
        result = re.sub(
            r'\^{([^}]+)}',
            lambda m: self._to_superscript(m.group(1)),
            result
        )
        result = re.sub(
            r'\^([0-9a-zA-Z])',
            lambda m: self._to_superscript(m.group(1)),
            result
        )

        # Handle simple subscripts: x_1, x_{12}
        result = re.sub(
            r'_{([^}]+)}',
            lambda m: self._to_subscript(m.group(1)),
            result
        )
        result = re.sub(
            r'_([0-9a-zA-Z])',
            lambda m: self._to_subscript(m.group(1)),
            result
        )

        # Handle fractions: \frac{a}{b} -> a/b
        result = re.sub(r'\\frac{([^}]*)}{([^}]*)}', r'(\1)/(\2)', result)

        # If there are still backslash commands we couldn't convert, it's too complex
        if re.search(r'\\[a-zA-Z]+', result):
            return None

        # Remove remaining braces
        result = result.replace('{', '').replace('}', '')

        return result

    @staticmethod
    def _to_superscript(text: str) -> str:
        """Convert text to Unicode superscript characters where possible."""
        try:
            return text.translate(SUPERSCRIPT_MAP)
        except Exception:
            return f'^({text})'

    @staticmethod
    def _to_subscript(text: str) -> str:
        """Convert text to Unicode subscript characters where possible."""
        try:
            return text.translate(SUBSCRIPT_MAP)
        except Exception:
            return f'_({text})'

    def _render_latex_to_png(self, latex_expr: str, fontsize: int = 14) -> Optional[bytes]:
        """
        Render a LaTeX expression to PNG bytes using matplotlib.

        Args:
            latex_expr: LaTeX expression (without $ delimiters)
            fontsize: Font size for rendering

        Returns:
            PNG image bytes, or None on failure
        """
        if not self._matplotlib_available:
            return None

        try:
            import matplotlib.pyplot as plt
            import matplotlib

            fig, ax = plt.subplots(figsize=(0.1, 0.1))
            ax.axis('off')
            fig.patch.set_facecolor('white')

            # Render the LaTeX expression
            text_obj = ax.text(
                0.5, 0.5,
                f"${latex_expr}$",
                fontsize=fontsize,
                ha='center', va='center',
                transform=ax.transAxes,
                color='black'
            )

            # Fit the figure to the text
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            bbox = text_obj.get_window_extent(renderer=renderer)

            # Convert bbox from display coords to inches
            dpi = fig.dpi
            width_in = bbox.width / dpi + 0.4  # padding
            height_in = bbox.height / dpi + 0.4

            fig.set_size_inches(max(width_in, 1.0), max(height_in, 0.5))

            # Save to bytes
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                        pad_inches=0.15, facecolor='white', edgecolor='none')
            plt.close(fig)
            buf.seek(0)

            logger.debug(f"Rendered LaTeX to PNG: {latex_expr[:50]}...")
            return buf.read()

        except Exception as e:
            logger.warning(f"Failed to render LaTeX '{latex_expr[:80]}': {e}")
            try:
                plt.close('all')
            except Exception:
                pass
            return None

    # ── Table Processing ──────────────────────────────────────────────

    def _process_tables(self, text: str) -> str:
        """Convert markdown tables to Discord code blocks for proper rendering."""
        if '|' not in text:
            return text

        def _replace_table(match: re.Match) -> str:
            table_text = match.group(1).strip()
            return self._format_table_as_code_block(table_text)

        return self.TABLE_RE.sub(_replace_table, text)

    def _format_table_as_code_block(self, table_text: str) -> str:
        """
        Convert a markdown table to a fixed-width code block.

        Input:
            | Name | Value |
            |------|-------|
            | foo  | bar   |

        Output:
            ```
            Name   | Value
            -------+------
            foo    | bar
            ```
        """
        lines = table_text.strip().split('\n')
        if len(lines) < 2:
            return table_text  # Not a real table

        # Parse rows into cells
        rows: List[List[str]] = []
        separator_idx: Optional[int] = None

        for i, line in enumerate(lines):
            line = line.strip()
            if self.SEPARATOR_ROW_RE.match(line):
                separator_idx = i
                continue

            # Split by | and strip whitespace, ignoring empty first/last cells
            cells = [cell.strip() for cell in line.split('|')]
            # Remove empty strings from leading/trailing |
            if cells and cells[0] == '':
                cells = cells[1:]
            if cells and cells[-1] == '':
                cells = cells[:-1]

            if cells:
                rows.append(cells)

        if not rows:
            return table_text

        # Calculate column widths
        num_cols = max(len(row) for row in rows)
        col_widths = [0] * num_cols
        for row in rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], len(cell))

        # Build formatted output
        formatted_lines = []
        for row_idx, row in enumerate(rows):
            # Pad each cell to column width
            padded = []
            for i in range(num_cols):
                cell = row[i] if i < len(row) else ''
                padded.append(cell.ljust(col_widths[i]))
            formatted_lines.append(' | '.join(padded))

            # Add separator after header row (first row)
            if row_idx == 0:
                sep_parts = ['-' * w for w in col_widths]
                formatted_lines.append('-+-'.join(sep_parts))

        return '```\n' + '\n'.join(formatted_lines) + '\n```'
