import html
import re
from typing import Iterable, Optional, Pattern


def build_keyword_pattern(keywords: Iterable[str]) -> Optional[Pattern]:
    """Build a compiled regex pattern from keywords (case-insensitive).

    Returns None if no valid keywords provided.
    """
    terms = [kw for kw in (keywords or []) if kw]
    if not terms:
        return None
    joined = "|".join(re.escape(term) for term in terms)
    return re.compile(joined, re.IGNORECASE)


def build_highlight_html(text: str, pattern: Optional[Pattern], is_selected: bool, text_color: str, highlight_bg: str = "#fff176") -> str:
    """Return HTML for a given text with matches wrapped in a span with background.

    - `text_color` should be a CSS color string (e.g. `#000000`).
    - `highlight_bg` defaults to the original yellow used in the UI.
    - If `pattern` is None, returns escaped text wrapped in a div with `text_color`.
    """
    escaped = html.escape(text)
    if not pattern:
        return f"<div style=\"color:{text_color}\">{escaped}</div>"

    highlighted = pattern.sub(lambda m: f'<span style="background-color:{highlight_bg}">{m.group(0)}</span>', escaped)
    color = text_color
    return f'<div style="color:{color};">{highlighted}</div>'
