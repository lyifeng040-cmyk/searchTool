import os
import sys
# ensure project root (parent of `filesearch`) is on sys.path when executed as a script
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.column_manager import compute_base_widths, compute_fill_extra


def run():
    # test default
    w = 1200
    base = compute_base_widths(w)
    assert len(base) == 4
    # ensure min constraints honored
    assert base[0] >= 260
    assert base[1] >= 360

    # test saved ratios
    w = 800
    saved = [0.4, 0.3, 0.2, 0.1]
    base = compute_base_widths(w, saved)
    assert len(base) == 4
    # check that proportions roughly follow saved ratios (allowing min constraints)
    assert base[0] >= int(0.4 * w) - 10

    # test fill extra no gap
    viewport = 1000
    widths = [200, 300, 200, 300]
    new = compute_fill_extra(viewport, widths)
    assert new == widths

    # test fill extra with gap
    viewport = 1200
    widths = [260, 360, 140, 150]
    new = compute_fill_extra(viewport, widths)
    assert new[2] > widths[2]
    assert new[3] > widths[3]
    assert sum(new) == viewport

    print("All column_manager tests passed")


if __name__ == '__main__':
    run()
