import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filesearch.ui.components.search_controller import SearchController


class FakeSignal:
    def __init__(self):
        self._cb = None

    def connect(self, cb):
        self._cb = cb

    def emit(self, *args, **kwargs):
        if self._cb:
            self._cb(*args, **kwargs)


class FakeWorker:
    def __init__(self):
        self.batch_ready = FakeSignal()
        self.finished = FakeSignal()
        self.error = FakeSignal()
        self.progress = FakeSignal()
        self.started = False
        self.stopped = False
        self.paused = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def toggle_pause(self, pause):
        self.paused = pause


def fake_create_worker(index_mgr, kw, scope, regex, force_realtime, main_window=None):
    return FakeWorker(), False


def test_search_controller_lifecycle():
    sc = SearchController(index_mgr=None, create_worker_func=fake_create_worker)

    called = {}

    def on_batch(batch):
        called['batch'] = batch

    def on_rt(a, b):
        called['rt'] = (a, b)

    def on_finished(total_time):
        called['fin'] = total_time

    def on_error(msg):
        called['err'] = msg

    worker, is_rt = sc.start_search('kw', ['.'], False, False, on_batch, on_rt, on_finished, on_error)
    assert sc.is_searching
    assert worker.started

    worker.batch_ready.emit([{'filename': 'a'}])
    assert 'batch' in called

    sc.toggle_pause(True)
    assert sc.is_paused
    assert worker.paused

    sc.stop()
    assert not sc.is_searching
    assert sc.worker is None
