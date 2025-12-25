import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.event_handlers import EventHandlers


def run_tests():
    print("Running EventHandlers pure-function tests...")

    # sample dataset
    all_results = [
        {"fullpath": "C:\\data\\keep\\file1.txt", "filename": "file1.txt"},
        {"fullpath": "C:\\data\\del\\file2.txt", "filename": "file2.txt"},
        {"fullpath": "C:\\data\\del_sub\\sub\\file3.txt", "filename": "file3.txt"},
    ]

    removed_exact = {"C:\\data\\del\\file2.txt"}
    removed_prefix = ["C:\\data\\del_sub\\"]

    res = EventHandlers.finalize_delete_pure(all_results, removed_exact, removed_prefix)

    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["filename"] == "file1.txt"

    print("All EventHandlers pure-function tests passed")


if __name__ == "__main__":
    run_tests()
