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

from ..constants import MAX_LATEX_FIGURE_HEIGHT_INCHES

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
        r'((?:^\|.+\|(?:\r?\n|$))+)',
        re.MULTILINE
    )
    SEPARATOR_ROW_RE = re.compile(r'^\|[\s\-:|]+\|$')

    # LaTeX commands that indicate raw (unwrapped) LaTeX in text
    RAW_LATEX_COMMANDS = [
        r'\\frac', r'\\sqrt', r'\\implies', r'\\int', r'\\sum',
        r'\\prod', r'\\lim', r'\\begin', r'\\end', r'\\left',
        r'\\right', r'\\binom', r'\\vec', r'\\hat', r'\\bar',
        r'\\dot', r'\\ddot', r'\\overline', r'\\underline',
        r'\\mathbb', r'\\mathcal', r'\\text', r'\\log',
    ]

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

        # 0. Detect raw LaTeX (no $ delimiters) and wrap in $$...$$
        text = self._wrap_raw_latex(text)

        # 1. Collect and render all LaTeX that needs image rendering into one combined image
        text = self._collect_and_render_latex(text, attachments)

        # 2. Convert markdown tables to code blocks
        text = self._process_tables(text)

        return RenderedContent(text=text, attachments=attachments)

    # ── LaTeX Processing ──────────────────────────────────────────────

    def _wrap_raw_latex(self, text: str) -> str:
        """
        Detect raw LaTeX commands in text (not wrapped in $..$ or $$..$$)
        and wrap them so the rendering pipeline can process them.
        
        Handles:
        - Multi-line environments: \\begin{aligned}...\\end{aligned}
        - LaTeX inside inline backticks: `\\frac{p}{q}`
        - Raw LaTeX commands in plain text: \\sqrt{2} = \\frac{p}{q}
        """
        # Phase 1: Wrap multi-line \begin{...}...\end{...} environments
        text = self._wrap_latex_environments(text)
        
        # Phase 2: Wrap remaining single-line raw LaTeX
        text = self._wrap_single_line_latex(text)
        
        return text
    
    def _wrap_latex_environments(self, text: str) -> str:
        """
        Split multi-line LaTeX environments (\\begin{aligned}, etc.) into
        individual equations that mathtext can render.
        """
        # Match \begin{env}...\end{env} blocks
        env_re = re.compile(
            r'\\begin\{(\w+)\}(.*?)\\end\{\1\}',
            re.DOTALL
        )
        
        def _split_environment(match: re.Match) -> str:
            env_name = match.group(1)
            content = match.group(2).strip()
            
            # Split on \\\\ (LaTeX newline)
            rows = re.split(r'\\\\', content)
            result_parts = []
            
            for row in rows:
                row = row.strip()
                if not row:
                    continue
                
                # Remove alignment markers (&)
                row = row.replace('&', ' ')
                
                # Extract \text{...} blocks and convert to plain text outside math
                text_parts = []
                remaining = row
                
                while '\\text{' in remaining:
                    idx = remaining.index('\\text{')
                    # Find matching closing brace
                    depth = 0
                    end = idx + 6  # after \text{
                    for j in range(end, len(remaining)):
                        if remaining[j] == '{':
                            depth += 1
                        elif remaining[j] == '}':
                            if depth == 0:
                                end = j
                                break
                            depth -= 1
                    
                    before = remaining[:idx].strip()
                    text_content = remaining[idx + 6:end].strip()
                    remaining = remaining[end + 1:]
                    
                    if before:
                        text_parts.append(f'$${before}$$')
                    if text_content:
                        text_parts.append(text_content)
                
                # Handle remaining math after last \text{}
                remaining = remaining.strip()
                if remaining:
                    text_parts.append(f'$${remaining}$$')
                
                if text_parts:
                    result_parts.append(' '.join(text_parts))
                
            logger.debug(f"Split \\begin{{{env_name}}} into {len(result_parts)} equations")
            return '\n'.join(result_parts)
        
        return env_re.sub(_split_environment, text)
    
    def _wrap_single_line_latex(self, text: str) -> str:
        """Wrap single-line raw LaTeX commands that aren't in $...$ or code blocks."""
        lines = text.split('\n')
        result_lines = []
        in_code_block = False
        
        for line in lines:
            # Track triple-backtick code blocks — skip contents entirely
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                result_lines.append(line)
                continue
            
            if in_code_block:
                result_lines.append(line)
                continue
            
            # Check for LaTeX inside inline backticks: `\frac{p}{q}`
            # Convert to $$...$$ if LaTeX commands are detected inside backticks
            def _unwrap_backtick_latex(m):
                content = m.group(1)
                if any(re.search(cmd, content) for cmd in self.RAW_LATEX_COMMANDS):
                    return f'$${content}$$'
                return m.group(0)
            
            line = re.sub(r'`([^`]+)`', _unwrap_backtick_latex, line)
            
            # Skip lines already containing $ (already wrapped or just converted)
            if '$' in line:
                result_lines.append(line)
                continue
            
            # Skip empty lines
            if not stripped:
                result_lines.append(line)
                continue
            
            # Check if line contains any raw LaTeX commands
            has_latex = any(
                re.search(cmd, line) for cmd in self.RAW_LATEX_COMMANDS
            )
            
            if not has_latex:
                result_lines.append(line)
                continue
            
            # Find the LaTeX portion of the line
            first_cmd_pos = len(line)
            for cmd in self.RAW_LATEX_COMMANDS:
                m = re.search(cmd, line)
                if m and m.start() < first_cmd_pos:
                    first_cmd_pos = m.start()
            
            if first_cmd_pos == 0:
                # Entire line is LaTeX
                result_lines.append(f'$${stripped}$$')
            else:
                # Mixed line: text prefix + LaTeX portion
                prefix = line[:first_cmd_pos].rstrip(' :')
                latex_part = line[first_cmd_pos:].strip()
                if prefix.strip():
                    result_lines.append(f'{prefix}: $${latex_part}$$')
                else:
                    result_lines.append(f'$${latex_part}$$')
            
            logger.debug(f"Wrapped raw LaTeX: {line[:60]}...")
        
        return '\n'.join(result_lines)

    @staticmethod
    def _clean_for_mathtext(expr: str) -> str:
        """
        Clean LaTeX expression for matplotlib mathtext compatibility.
        
        Mathtext doesn't support \\text{}, \\mathbb{}, \\begin{}, etc.
        Convert or strip them.
        """
        # Convert \text{...} to \mathrm{...} (mathtext supports \mathrm)
        expr = re.sub(r'\\text\{([^}]*)\}', r'\\mathrm{\1}', expr)
        
        # \implies -> \Rightarrow (mathtext supports this)
        expr = expr.replace('\\implies', '\\Rightarrow')
        
        # \therefore -> unicode
        expr = expr.replace('\\therefore', '\u2234')
        
        # \gcd -> \mathrm{gcd}
        expr = re.sub(r'\\gcd', r'\\mathrm{gcd}', expr)
        
        # \mathbb{X} -> \mathrm{X} (mathtext approximation)
        expr = re.sub(r'\\mathbb\{([^}]*)\}', r'\\mathrm{\1}', expr)
        
        # Remove \left and \right (mathtext auto-sizes delimiters)
        expr = expr.replace('\\left', '').replace('\\right', '')
        
        # Remove alignment markers
        expr = expr.replace('&', ' ')
        
        # Strip any remaining \begin{}/\end{} wrappers
        expr = re.sub(r'\\begin\{\w+\}', '', expr)
        expr = re.sub(r'\\end\{\w+\}', '', expr)
        
        # Clean up excessive whitespace
        expr = re.sub(r'\s+', ' ', expr).strip()
        
        return expr

    def _collect_and_render_latex(self, text: str, attachments: List[discord.File]) -> str:
        """
        Collect all LaTeX expressions, render them into one combined image,
        and replace each with a numbered citation in the text.

        Simple inline LaTeX that converts cleanly to Unicode is left inline
        with no citation.
        """
        # Collect expressions that need image rendering
        # Each entry: (start, end, latex_expr). Keeping source spans prevents an
        # identical inline expression from being mistaken for one inside a block.
        expressions_to_render: List[Tuple[int, int, str]] = []
        block_spans: List[Tuple[int, int]] = []

        # Track which inline expressions convert to Unicode (no image needed)
        unicode_replacements: List[Tuple[int, int, str]] = []

        # First pass: identify block LaTeX ($$...$$)
        for match in self.BLOCK_LATEX_RE.finditer(text):
            latex_expr = match.group(1).strip()
            if latex_expr:
                start, end = match.span()
                block_spans.append((start, end))
                expressions_to_render.append((start, end, latex_expr))

        # Second pass: identify inline LaTeX ($...$) that can't be Unicode-converted
        for match in self.INLINE_LATEX_RE.finditer(text):
            latex_expr = match.group(1).strip()
            if not latex_expr:
                continue

            # Skip if this is inside a block expression (already captured)
            if any(
                block_start <= match.start() and match.end() <= block_end
                for block_start, block_end in block_spans
            ):
                continue

            # Try Unicode conversion first
            unicode_result = self._latex_to_unicode(latex_expr)
            if unicode_result is not None:
                unicode_replacements.append((*match.span(), unicode_result))
            else:
                expressions_to_render.append((*match.span(), latex_expr))

        expressions_to_render.sort(key=lambda item: item[0])

        # If nothing needs image rendering, return early
        if not expressions_to_render:
            replacements = unicode_replacements
        else:
            replacements = list(unicode_replacements)

        # Render combined image with all expressions
        labeled_exprs = [
            (f"[{idx}]", latex_expr)
            for idx, (_start, _end, latex_expr) in enumerate(expressions_to_render, 1)
        ]
        img_bytes = (
            self._render_combined_latex_image(labeled_exprs)
            if labeled_exprs
            else None
        )

        if img_bytes:
            replacements.extend(
                (start, end, f"**[{idx}]**")
                for idx, (start, end, _latex_expr) in enumerate(
                    expressions_to_render,
                    1,
                )
            )
            attachments.append(discord.File(io.BytesIO(img_bytes), filename="equations.png"))
            logger.info(f"Rendered {len(labeled_exprs)} LaTeX expression(s) into combined image")
        elif expressions_to_render:
            # Fallback: put raw LaTeX in code blocks
            replacements.extend(
                (start, end, f"`{latex_expr}`")
                for start, end, latex_expr in expressions_to_render
            )
            logger.warning("Failed to render combined LaTeX image, falling back to code blocks")

        # Apply from the end so every source span remains valid as text changes.
        for start, end, replacement in sorted(
            replacements,
            key=lambda item: item[0],
            reverse=True,
        ):
            text = text[:start] + replacement + text[end:]

        return text

    def _render_combined_latex_image(self, labeled_exprs: List[Tuple[str, str]]) -> Optional[bytes]:
        """
        Render multiple LaTeX expressions into one vertically-stacked PNG image,
        each prefixed with its citation label.

        Args:
            labeled_exprs: List of (label, latex_expr) tuples, e.g. [("[1]", "E=mc^2")]

        Returns:
            PNG image bytes, or None on failure
        """
        if not self._matplotlib_available or not labeled_exprs:
            return None

        try:
            import matplotlib
            import matplotlib.pyplot as plt

            # Enable amsmath/amssymb for \begin{aligned}, \mathbb, etc.
            matplotlib.rcParams['text.usetex'] = False  # Use mathtext, not system LaTeX
            matplotlib.rcParams['mathtext.fontset'] = 'dejavusans'

            n = len(labeled_exprs)
            
            # Calculate height: multi-line expressions need more space
            total_height = 0.4  # padding
            row_heights = []
            for _, latex_expr in labeled_exprs:
                line_count = latex_expr.count('\\\\') + 1  # \\\\ = newline in LaTeX
                h = max(0.7, line_count * 0.45)
                row_heights.append(h)
                total_height += h
            
            fig_height = max(total_height, 1.0)
            
            # DAB-128: refuse rather than allocate an unusable canvas.
            #
            # total_height grows without bound -- 0.4 plus at least 0.7 per
            # expression, more for multi-line ones -- and nothing upstream caps
            # how many expressions a response may contain. At 150 dpi a
            # 400-expression response asks for a 1500x42060 pixel figure,
            # measured here at 5.9 s and 568 MB resident. Discord could not
            # usefully display that image even if it were free.
            #
            # Returning None degrades to inline code blocks, the same path every
            # other failure in this method takes. That preserves all the content
            # as text, which is strictly better than truncating silently.
            if fig_height > MAX_LATEX_FIGURE_HEIGHT_INCHES:
                logger.warning(
                    "Refusing to render %d LaTeX expression(s): computed figure height "
                    "%.1f in exceeds the %.1f in cap. Falling back to code blocks.",
                    n, fig_height, MAX_LATEX_FIGURE_HEIGHT_INCHES,
                )
                return None
            
            fig, ax = plt.subplots(figsize=(10, fig_height))
            ax.axis('off')
            fig.patch.set_facecolor('white')

            # Calculate y positions based on variable row heights
            cumulative = 0.2  # start padding
            y_positions = []
            for h in row_heights:
                y_positions.append(1.0 - (cumulative + h / 2) / total_height)
                cumulative += h

            # Render each expression as a labeled row
            for i, (label, latex_expr) in enumerate(labeled_exprs):
                y_pos = y_positions[i]

                # Clean the expression for mathtext compatibility
                clean_expr = self._clean_for_mathtext(latex_expr)

                # Label on the left
                ax.text(
                    0.02, y_pos, label,
                    fontsize=13, fontweight='bold',
                    ha='left', va='center',
                    transform=ax.transAxes,
                    color='#333333',
                    fontfamily='monospace'
                )

                # Render the LaTeX expression
                ax.text(
                    0.08, y_pos,
                    f"${clean_expr}$",
                    fontsize=15,
                    ha='left', va='center',
                    transform=ax.transAxes,
                    color='black'
                )

                # Subtle separator line (except after last)
                if i < n - 1:
                    sep_y = (y_positions[i] + y_positions[i + 1]) / 2
                    # No transform= here. axhline's y is already in axes
                    # coordinates and it builds its own blended transform, so
                    # passing one raises ValueError -- which killed every
                    # response containing two or more equations (DAB-114).
                    ax.axhline(y=sep_y, xmin=0.02, xmax=0.98,
                               color='#E0E0E0', linewidth=0.5)

            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                        pad_inches=0.15, facecolor='white', edgecolor='none')
            plt.close(fig)
            buf.seek(0)

            logger.debug(f"Rendered combined LaTeX image with {n} expression(s)")
            return buf.read()

        except Exception as e:
            logger.warning(f"Failed to render combined LaTeX image: {e}")
            try:
                import matplotlib.pyplot as plt
                plt.close('all')
            except Exception:
                pass
            return None

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


    # ── Table Processing ──────────────────────────────────────────────

    def _process_tables(self, text: str) -> str:
        """Convert markdown tables to Discord code blocks for proper rendering."""
        if '|' not in text:
            return text

        def _replace_table(match: re.Match) -> str:
            matched_text = match.group(1)
            table_text = matched_text.strip()
            formatted = self._format_table_as_code_block(table_text)
            # TABLE_RE may consume the line break after a table. Preserve that
            # boundary so following prose cannot attach to the closing fence.
            if matched_text.endswith('\r\n'):
                boundary = '\r\n'
            elif matched_text.endswith('\n'):
                boundary = '\n'
            else:
                boundary = ''
            return formatted + boundary

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
