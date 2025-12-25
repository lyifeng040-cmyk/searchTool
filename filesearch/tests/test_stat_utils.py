import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.stat_utils import build_batch_entries, apply_batch_results


def test_build_and_apply_batch_entries():
    page_items = [
        {'fullpath': r'C:\\a\\1.txt', 'filename': '1.txt', 'dir_path': r'C:\\a', 'type_code': 2, 'size': 0, 'mtime': 0},
        {'fullpath': r'C:\\b\\2.txt', 'filename': '2.txt', 'dir_path': r'C:\\b', 'type_code': 2, 'size': 0, 'mtime': 0},
    ]
    tmp = build_batch_entries(page_items)
    assert isinstance(tmp, list) and len(tmp) == 2
    tmp[0][5] = 123
    tmp[0][6] = 456.0
    apply_batch_results(page_items, tmp)
    assert page_items[0]['size'] == 123
    assert page_items[0]['mtime'] == 456.0
