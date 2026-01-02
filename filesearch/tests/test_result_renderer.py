import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.result_renderer import apply_filter_logic, paginate_items, sort_everything_style


def test_filter_logic():
    items = [
        {"filename": "a.txt", "type_code": 2, "size": 1024, "mtime": 1000},
        {"filename": "b.zip", "type_code": 1, "size": 5 * 1024 * 1024, "mtime": 2000},
        {"filename": "dir", "type_code": 0, "size": 0, "mtime": 3000},
    ]
    out = apply_filter_logic(items, None, 0, 0)
    assert len(out) == 3
    out = apply_filter_logic(items, None, 1 << 20, 0)
    assert len(out) == 2
    out = apply_filter_logic(items, "📂文件夹", 0, 0)
    assert len(out) == 1


def test_paginate_items():
    items = list(range(25))
    page, total_pages = paginate_items([{"i": x} for x in items], 10, 1)
    assert len(page) == 10
    assert total_pages == 3
    page2, _ = paginate_items([{"i": x} for x in items], 10, 3)
    assert len(page2) == 5


def test_sort_everything_basic():
    items = [
        {"filename": "readme.md", "dir_path": "C:\\proj", "fullpath": "C:\\proj\\readme.md"},
        {"filename": "report.txt", "dir_path": "C:\\docs", "fullpath": "C:\\docs\\report.txt"},
        {"filename": "notes.txt", "dir_path": "C:\\proj\\notes", "fullpath": "C:\\proj\\notes\\notes.txt"},
    ]
    # search for 'rep' should put report.txt first
    out = sort_everything_style('rep', items)
    assert out[0]['filename'] == 'report.txt'
    # search for 'read' should put readme first
    out2 = sort_everything_style('read', items)
    assert out2[0]['filename'] == 'readme.md'


def test_sort_prefers_filename_over_path():
    items = [
        {"filename": "alpha.txt", "dir_path": "C:\\x\\alpha_folder", "fullpath": "C:\\x\\alpha_folder\\alpha.txt"},
        {"filename": "beta_alpha.txt", "dir_path": "C:\\alpha", "fullpath": "C:\\alpha\\beta_alpha.txt"},
    ]
    # searching 'alpha' should prioritize exact/prefix in filename before path matches
    out = sort_everything_style('alpha', items)
    assert out[0]['filename'] == 'alpha.txt'
