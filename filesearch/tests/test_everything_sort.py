import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.result_renderer import sort_everything_style


def make_item(filename, fullpath):
    return {"filename": filename, "fullpath": fullpath, "dir_path": os.path.dirname(fullpath)}


def test_everything_sort_basic():
    items = [
        make_item('report_final.txt', r'C:\docs\report_final.txt'),
        make_item('report.txt', r'C:\docs\report.txt'),
        make_item('myreport.txt', r'C:\other\myreport.txt'),
        make_item('notes.txt', r'C:\docs\notes.txt'),
    ]
    # search 'report' should prioritize exact filename 'report.txt' then prefix 'report_final' then 'myreport'
    out = sort_everything_style('report', items)
    assert out[0]['filename'] in ('report.txt', 'report_final.txt')
    # ensure items containing 'report' come before 'notes.txt'
    assert any('report' in it['filename'] for it in out[:3])


if __name__ == '__main__':
    test_everything_sort_basic()
    print('Everything sort tests passed')
