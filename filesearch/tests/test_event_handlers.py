import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.event_handlers import EventHandlers


def test_finalize_delete_pure():
    all_results = [
        {"fullpath": r"C:\\data\\keep\\file1.txt", "filename": "file1.txt"},
        {"fullpath": r"C:\\data\\del\\file2.txt", "filename": "file2.txt"},
        {"fullpath": r"C:\\data\\del_sub\\sub\\file3.txt", "filename": "file3.txt"},
    ]

    removed_exact = {r"C:\\data\\del\\file2.txt"}
    removed_prefix = [r"C:\\data\\del_sub\\"]

    res = EventHandlers.finalize_delete_pure(all_results, removed_exact, removed_prefix)

    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["filename"] == "file1.txt"
