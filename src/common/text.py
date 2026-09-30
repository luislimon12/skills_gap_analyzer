"""
Shared text cleaning used by Role 1 (postings) and Role 2 (resumes).

Everything the skill matcher sees should come out of clean_text(), so both
sides of the comparison are formatted the same way (see "Formatting &
normalization" in docs/sparknotes_main.md).
"""

import html
import re
import unicodedata
from html.parser import HTMLParser

# Tags that end a line of text. Anything else (<b>, <span>, <a>) is inline.
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "table", "section", "article", "header", "footer", "blockquote",
}
_BULLET_CHARS = "•●▪■◦○◆◇►▸‣⁃∙·*"
_BULLET_LINE = re.compile(rf"^\s*[{re.escape(_BULLET_CHARS)}]\s*")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0  # inside <script>/<style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS and tag != "li":  # the next <li> or </ul> already breaks the line
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Strip HTML to plain text, keeping line breaks at block tags and "- " bullets for <li>.

    Greenhouse returns HTML that is itself HTML-escaped ("&lt;p&gt;..."), so the
    input is unescaped once before parsing. Already-unescaped HTML is unaffected.
    """
    if not raw:
        return ""
    if "&lt;" in raw:
        raw = html.unescape(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return clean_text("".join(parser.parts))


def clean_text(text: str | None) -> str:
    """Normalize plain text: unicode forms, bullets, whitespace, blank lines."""
    if not text:
        return ""
    # NFKC folds ligatures (ﬁ -> fi) and full-width chars that PDFs love to emit
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("​", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = _BULLET_LINE.sub("- ", line)
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
