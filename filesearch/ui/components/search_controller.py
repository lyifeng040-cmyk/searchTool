"""
SearchController: manages search lifecycle (start/stop/pause) and wires worker signals
to provided callbacks. It keeps state and delegates actual worker creation to a factory
function (`create_worker`) so it can be tested with fake workers.
"""
from typing import Callable, Optional, Tuple



class SearchController:
    def __init__(self, index_mgr, create_worker_func, main_window=None):
        self.index_mgr = index_mgr
        self._create_worker = create_worker_func
        self.main_window = main_window
        self.worker = None
        self.is_searching = False
        self.is_paused = False

    def start_search(self, kw: str, scope_targets, regex: bool, force_realtime: bool,
                     on_batch_ready: Callable, on_rt_progress: Callable, on_finished: Callable,
                     on_error: Callable):
        if self.is_searching:
            return None, False
        self.worker, is_realtime = self._create_worker(
            self.index_mgr,
            kw,
            scope_targets,
            regex,
            force_realtime,
            self.main_window,
        )
        if not self.worker:
            raise RuntimeError("Failed to create worker")

        # connect signals (workers expected to have .batch_ready, .finished, .error, optionally .progress)
        try:
            if is_realtime and hasattr(self.worker, "progress") and on_rt_progress:
                try:
                    self.worker.progress.connect(on_rt_progress)
                except Exception:
                    # some fake workers might not expose PySide signals; allow direct assignment
                    self.worker.progress = on_rt_progress
        except Exception:
            pass

        try:
            if hasattr(self.worker, "batch_ready") and on_batch_ready:
                self.worker.batch_ready.connect(on_batch_ready)
        except Exception:
            # fallback if worker uses plain call
            try:
                self.worker.batch_ready = on_batch_ready
            except Exception:
                pass
        try:
            if hasattr(self.worker, "finished") and on_finished:
                # connect user callback
                self.worker.finished.connect(on_finished)
                # also ensure controller state resets when worker completes
                try:
                    self.worker.finished.connect(self._on_worker_finished)
                except Exception:
                    pass
        except Exception:
            try:
                self.worker.finished = on_finished
            except Exception:
                pass
        try:
            if hasattr(self.worker, "error") and on_error:
                self.worker.error.connect(on_error)
                try:
                    self.worker.error.connect(lambda *_: self._on_worker_finished())
                except Exception:
                    pass
        except Exception:
            try:
                self.worker.error = on_error
            except Exception:
                pass

        # start worker
        try:
            self.worker.start()
            self.is_searching = True
            self.is_paused = False
        except Exception as e:
            # if start fails, try to call error callback
            if on_error:
                try:
                    on_error(str(e))
                except Exception:
                    pass
            raise

        return self.worker, is_realtime

    def toggle_pause(self, pause: bool):
        if not self.worker or not hasattr(self.worker, "toggle_pause"):
            return
        try:
            self.worker.toggle_pause(pause)
            self.is_paused = bool(pause)
        except Exception:
            pass

    def stop(self):
        if self.worker and hasattr(self.worker, "stop"):
            try:
                self.worker.stop()
            except Exception:
                pass
        self.worker = None
        self.is_searching = False
        self.is_paused = False

    # internal: reset state when worker finishes/errors
    def _on_worker_finished(self):
        self.is_searching = False
        self.is_paused = False
        self.worker = None
