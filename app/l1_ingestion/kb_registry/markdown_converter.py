import re
from typing import Optional


class MarkdownConverter:
    """
    Deterministic Markdown Normalization Engine.

    Responsibilities:
    - Normalize markdown formatting
    - Standardize whitespace & line endings
    - Provide deterministic output for hashing
    - Prepare structure for future docx/pdf/html conversion

    Does NOT:
    - Touch database
    - Compile policy
    - Validate KB rules
    """

    # ============================================================
    # PUBLIC ENTRY POINT
    # ============================================================

    def convert(
        self,
        raw_content: Optional[str],
        original_format: str
    ) -> str:

        if raw_content is None:
            raw_content = ""

        # Normalize format input
        format_lower = original_format.lower().replace(".", "").strip()

        if format_lower in ["md", "markdown"]:
            content = raw_content

        elif format_lower in ["txt"]:
            content = raw_content

        elif format_lower in ["html", "htm"]:
            content = self._strip_basic_html(raw_content)

        elif format_lower in ["docx", "pdf"]:
            # Placeholder — proper parsers can be added later
            content = raw_content

        else:
            # Unknown format fallback
            content = raw_content

        return self._normalize_markdown(content)

    # ============================================================
    # CORE NORMALIZATION (Deterministic)
    # ============================================================

    def _normalize_markdown(self, content: str) -> str:

        # --------------------------------------------------------
        # 1️⃣ Normalize line endings
        # --------------------------------------------------------

        content = content.replace("\r\n", "\n").replace("\r", "\n")

        # --------------------------------------------------------
        # 2️⃣ Remove trailing whitespace
        # --------------------------------------------------------

        content = "\n".join(line.rstrip() for line in content.split("\n"))

        # --------------------------------------------------------
        # 3️⃣ Collapse multiple blank lines
        # --------------------------------------------------------

        lines = content.split("\n")
        cleaned_lines = []
        blank_count = 0

        for line in lines:

            if line.strip() == "":
                blank_count += 1

                if blank_count <= 1:
                    cleaned_lines.append("")

            else:
                blank_count = 0
                cleaned_lines.append(line)

        content = "\n".join(cleaned_lines)

        # --------------------------------------------------------
        # 4️⃣ Normalize heading spacing
        # Example:
        # ##Heading → ## Heading
        # --------------------------------------------------------

        content = re.sub(
            r"^(#+)([^\s#])",
            r"\1 \2",
            content,
            flags=re.MULTILINE
        )

        # --------------------------------------------------------
        # 5️⃣ Normalize bullet spacing
        # Example:
        # -Item → - Item
        # --------------------------------------------------------

        content = re.sub(
            r"^(\s*[-*])([^\s])",
            r"\1 \2",
            content,
            flags=re.MULTILINE
        )

        # --------------------------------------------------------
        # 6️⃣ Strip leading/trailing whitespace
        # --------------------------------------------------------

        content = content.strip()

        return content

    # ============================================================
    # BASIC HTML STRIPPER (Minimal Safe)
    # ============================================================

    def _strip_basic_html(self, html_content: str) -> str:
        """
        Very basic HTML tag stripping.

        Not a full parser.
        Safe fallback for simple uploads.
        """

        if not html_content:
            return ""

        # --------------------------------------------------------
        # Remove script/style blocks
        # --------------------------------------------------------

        html_content = re.sub(
            r"<(script|style).*?>.*?</\1>",
            "",
            html_content,
            flags=re.DOTALL | re.IGNORECASE
        )

        # --------------------------------------------------------
        # Remove HTML tags
        # --------------------------------------------------------

        text = re.sub(
            r"<[^>]+>",
            "",
            html_content
        )

        return text