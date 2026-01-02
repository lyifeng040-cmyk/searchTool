"""
Utility helpers extracted from legacy implementation.
"""

import datetime
import json
import logging
import os
import re
from pathlib import Path

from .constants import (
    CAD_PATTERN,
    AUTOCAD_PATTERN,
    SKIP_DIRS_LOWER,
)

logger = logging.getLogger(__name__)


def get_c_scan_dirs(config_mgr=None):
    """Get default/whitelisted C: scan directories."""
    if config_mgr:
        return config_mgr.get_enabled_c_paths()

    default_dirs = [
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Documents"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
    ]
    dirs = []
    for p in default_dirs:
        if p and os.path.isdir(p):
            p = os.path.normpath(p)
            if p not in dirs:
                dirs.append(p)
    return dirs


def is_in_allowed_paths(path_lower, allowed_paths_lower):
    """Check if a path is within allowed paths."""
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + "\\") or path_lower == ap:
            return True
    return False


def should_skip_path(path_lower, allowed_paths_lower=None):
    """Return True if path should be skipped."""
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False

    path_parts = path_lower.replace("/", "\\").split("\\")
    for part in path_parts:
        if part in SKIP_DIRS_LOWER:
            return True

    if "site-packages" in path_lower:
        return True
    if CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower):
        return True
    if "tangent" in path_lower:
        return True

    return False


def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    """Return True if directory should be skipped."""
    if CAD_PATTERN.search(name_lower):
        return True
    if AUTOCAD_PATTERN.search(name_lower):
        return True
    if "tangent" in name_lower:
        return True

    if path_lower and allowed_paths_lower:
        if is_in_allowed_paths(path_lower, allowed_paths_lower):
            return False

    if name_lower in SKIP_DIRS_LOWER:
        return True

    return False


def format_size(size):
    """Format file size."""
    if size <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(timestamp):
    """Format epoch seconds to string."""
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError) as e:
        logger.warning(f"时间戳格式化失败: {timestamp}, {e}")
        return "-"


def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    """Parse search scope string into list of targets."""
    targets = []
    if "所有磁盘" in scope_str:
        for d in get_drives_fn():
            if d.upper().startswith("C:"):
                targets.extend(get_c_scan_dirs(config_mgr))
            else:
                norm = os.path.normpath(d).rstrip("\\/ ")
                targets.append(norm)
    else:
        s = scope_str.strip()
        if os.path.isdir(s):
            norm = os.path.normpath(s).rstrip("\\/ ")
            targets.append(norm)
        else:
            targets.append(s)
    return targets


# Note: scoring utilities removed from renderer — search now uses precise substring/regex matching only.


def apply_theme(app, theme_name):
    """Apply light/dark theme to a Qt app."""
    if theme_name == "dark":
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #2d2d2d; color: #ffffff; }
            QTreeWidget { background-color: #3d3d3d; color: #ffffff; alternate-background-color: #454545; }
            QTreeWidget::item:selected { background-color: #0078d4; }
            QLineEdit, QComboBox, QSpinBox { background-color: #3d3d3d; color: #ffffff; border: 1px solid #555; padding: 4px; }
            QPushButton { background-color: #4d4d4d; color: #ffffff; border: 1px solid #666; padding: 5px 10px; }
            QPushButton:hover { background-color: #5d5d5d; }
            QLabel { color: #ffffff; }
            QGroupBox { color: #ffffff; border: 1px solid #555; }
            QCheckBox, QRadioButton { color: #ffffff; }
            QMenu { background-color: #3d3d3d; color: #ffffff; }
            QMenu::item:selected { background-color: #0078d4; }
            QStatusBar { background-color: #2d2d2d; color: #aaaaaa; }
            QHeaderView::section { background-color: #3d3d3d; color: #ffffff; padding: 4px; border: 1px solid #555; }
            QScrollBar { background-color: #2d2d2d; }
        """
        )
    else:
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #ffffff; }
            QTreeWidget { alternate-background-color: #f8f9fa; }
            QTreeWidget::item:selected { background-color: #0078d4; color: white; }
            QHeaderView::section { background-color: #f0f0f0; padding: 4px; border: 1px solid #dcdcdc; font-weight: bold; }
            QTreeWidget { border: 1px solid #dcdcdc; }
        """
        )


### Boolean expression / predicate utilities for advanced search
def _tokenize_search_expr(s: str):
    import re
    tokens = []
    # Handle quoted phrases
    pattern = re.compile(r'"([^"]+)"|(\()|(\))|(\|)|(!)|([^\s()|!]+)')
    for m in pattern.finditer(s):
        grp = m.group(0)
        if grp is None:
            continue
        tokens.append(grp)
    return tokens


def compile_search_predicate(expr: str):
    """Compile a boolean search expression into a predicate function.

    Supports: parentheses, ! (NOT), | (OR), implicit AND (space).
    Tokens may contain wildcards (*, ?) which are converted to regex.
    If a token starts with 're:' the remainder is treated as a raw regex.
    Returns a callable f(text)->bool
    """
    import re

    def token_to_func(tok: str):
        tok = tok.strip()
        if not tok:
            return lambda t: True
        if tok.lower().startswith('re:'):
            pat = tok.split(':', 1)[1]
            try:
                cre = re.compile(pat, re.IGNORECASE)
            except re.error:
                cre = re.compile(re.escape(pat), re.IGNORECASE)
            return lambda text: bool(cre.search(text))
        # quoted phrase
        if tok.startswith('"') and tok.endswith('"'):
            phrase = tok[1:-1]
            return lambda text: phrase.lower() in text.lower()
        # wildcard -> regex
        if '*' in tok or '?' in tok:
            esc = re.escape(tok)
            esc = esc.replace(r'\*', '.*').replace(r'\?', '.')
            try:
                cre = re.compile(esc, re.IGNORECASE)
            except re.error:
                cre = re.compile(re.escape(tok), re.IGNORECASE)
            return lambda text: bool(cre.search(text))
        # plain token
        return lambda text: tok.lower() in text.lower()

    # shunting-yard to RPN
    def precedence(op):
        if op == '!':
            return 3
        if op == 'AND':
            return 2
        if op == 'OR' or op == '|':
            return 1
        return 0

    toks = []
    # simple tokenizer: split but keep parentheses and |
    cur = ''
    i = 0
    while i < len(expr):
        c = expr[i]
        if c.isspace():
            if cur:
                toks.append(cur)
                cur = ''
            i += 1
            continue
        if c in '()|!':
            if cur:
                toks.append(cur)
                cur = ''
            toks.append(c)
            i += 1
            continue
        if c == '"':
            # quoted phrase
            j = i + 1
            while j < len(expr) and expr[j] != '"':
                j += 1
            phrase = expr[i:j+1] if j < len(expr) else expr[i:]
            toks.append(phrase)
            i = j + 1
            continue
        cur += c
        i += 1
    if cur:
        toks.append(cur)

    # convert implicit spaces to AND operators
    out = []
    prev_was_token = False
    for t in toks:
        if t == '|' or t == '|' or t == 'OR':
            out.append('OR')
            prev_was_token = False
            continue
        if t == '!':
            out.append('!')
            prev_was_token = False
            continue
        if t == '(':
            out.append(t)
            prev_was_token = False
            continue
        if t == ')':
            out.append(t)
            prev_was_token = True
            continue
        # token
        if prev_was_token:
            out.append('AND')
        out.append(t)
        prev_was_token = True

    # to RPN
    output_q = []
    op_stack = []
    for tok in out:
        if tok == 'AND' or tok == 'OR' or tok == '!':
            while op_stack and op_stack[-1] != '(' and precedence(op_stack[-1]) >= precedence(tok):
                output_q.append(op_stack.pop())
            op_stack.append(tok)
        elif tok == '(':
            op_stack.append(tok)
        elif tok == ')':
            while op_stack and op_stack[-1] != '(':
                output_q.append(op_stack.pop())
            if op_stack and op_stack[-1] == '(':
                op_stack.pop()
        else:
            output_q.append(tok)
    while op_stack:
        output_q.append(op_stack.pop())

    # build predicate from RPN
    def build_from_rpn(rpn):
        stack = []
        for tok in rpn:
            if tok == 'AND':
                b = stack.pop()
                a = stack.pop()
                stack.append(lambda text, a=a, b=b: a(text) and b(text))
            elif tok == 'OR':
                b = stack.pop()
                a = stack.pop()
                stack.append(lambda text, a=a, b=b: a(text) or b(text))
            elif tok == '!':
                a = stack.pop()
                stack.append(lambda text, a=a: not a(text))
            else:
                stack.append(token_to_func(tok))
        if not stack:
            return lambda text: True
        return stack[0]

    pred = build_from_rpn(output_q)
    # predicate expects a single text (we'll pass filename + '\n' + fullpath)
    return pred


__all__ = [
    "get_c_scan_dirs",
    "is_in_allowed_paths",
    "should_skip_path",
    "should_skip_dir",
    "format_size",
    "format_time",
    "parse_search_scope",
    "apply_theme",
]
