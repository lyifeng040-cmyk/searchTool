import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.column_manager import compute_base_widths, compute_fill_extra


def test_compute_base_widths_defaults():
    w = 1200
    base = compute_base_widths(w)
    assert len(base) == 4
    assert base[0] >= 260
    assert base[1] >= 360


def test_compute_base_with_saved_ratios():
    w = 800
    saved = [0.4, 0.3, 0.2, 0.1]
    base = compute_base_widths(w, saved)
    assert len(base) == 4
    assert base[0] >= int(0.4 * w) - 10


def test_compute_fill_extra():
    viewport = 1000
    widths = [200, 300, 200, 300]
    new = compute_fill_extra(viewport, widths)
    assert new == widths

    viewport = 1200
    widths = [260, 360, 140, 150]
    new = compute_fill_extra(viewport, widths)
    assert new[2] > widths[2]
    assert new[3] > widths[3]
    assert sum(new) == viewport
