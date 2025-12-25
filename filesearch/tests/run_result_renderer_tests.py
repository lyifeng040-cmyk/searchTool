import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.result_renderer import apply_filter_logic, paginate_items


def test_filter_logic():
    items = [
        {"filename": "a.txt", "type_code": 2, "size": 1024, "mtime": 1000},
        {"filename": "b.zip", "type_code": 1, "size": 5 * 1024 * 1024, "mtime": 2000},
        {"filename": "dir", "type_code": 0, "size": 0, "mtime": 3000},
    ]
    # no filter
    out = apply_filter_logic(items, None, 0, 0)
    assert len(out) == 3
    # size filter >1MB should remove a.txt
    out = apply_filter_logic(items, None, 1 << 20, 0)
    assert len(out) == 2
    # ext filter for folder
    out = apply_filter_logic(items, "📂文件夹", 0, 0)
    assert len(out) == 1


def test_paginate_items():
    items = list(range(25))
    page, total_pages = paginate_items([{"i": x} for x in items], 10, 1)
    assert len(page) == 10
    assert total_pages == 3
    page2, _ = paginate_items([{"i": x} for x in items], 10, 3)
    assert len(page2) == 5


if __name__ == '__main__':
    test_filter_logic()
    test_paginate_items()
    print('ResultRenderer pure-function tests passed')
