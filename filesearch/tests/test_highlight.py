import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.highlight import build_keyword_pattern, build_highlight_html


def test_build_keyword_and_html():
    pat = build_keyword_pattern(['abc', 'X'])
    assert pat is not None
    s = 'abcXabc'
    h = build_highlight_html(s, pat, False, '#000')
    assert '<span' in h and 'background-color' in h


def test_build_highlight_no_pattern():
    h2 = build_highlight_html('no match', None, False, '#111')
    assert 'no match' in h2
