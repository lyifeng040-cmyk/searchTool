"""
Pure utilities to compute column widths for the main results tree.
These functions are UI-independent and easy to unit-test.
"""
from typing import List, Optional


def compute_base_widths(viewport_w: int, saved_ratios: Optional[List[float]] = None) -> List[int]:
    """
    Compute base widths for 4 columns given viewport width and optional saved ratios.
    Mirrors logic previously in `SearchApp._apply_ratio_resize`.
    """
    min_widths = [260, 360, 140, 150]
    ratios = saved_ratios if saved_ratios and len(saved_ratios) >= 4 else [0.33, 0.39, 0.14, 0.14]
    base = [max(int(viewport_w * r), m) for r, m in zip(ratios, min_widths)]
    total_base = sum(base)
    if total_base != viewport_w:
        extra = viewport_w - total_base
        base[-1] = max(min_widths[-1], base[-1] + extra)
    return base


def compute_fill_extra(viewport_w: int, widths: List[int]) -> List[int]:
    """
    Given current widths for 4 columns and viewport width, if there is extra space (>8),
    distribute it to column 2 and column 3 (indexes 2 and 3) the same way as
    `_fill_extra_space` in the original code: 40% to column 2, rest to column 3.

    Returns the new widths list (copy of input with modifications applied).
    """
    if not widths or len(widths) < 4:
        return widths
    total = sum(widths)
    gap = viewport_w - total
    new_widths = widths.copy()
    if gap > 8:
        add_size = int(gap * 0.4)
        add_time = gap - add_size
        new_widths[2] = new_widths[2] + add_size
        new_widths[3] = new_widths[3] + add_time
    return new_widths
