from filesearch.core.search_workers import IndexSearchWorker, RealtimeSearchWorker


def create_worker(index_mgr, kw, scope_targets, regex_var, fuzzy_var, force_realtime, main_window=None):
    """Return an appropriate worker instance and a flag whether it's realtime worker.
    The caller is responsible for connecting signals and starting the worker.
    """
    use_idx = not force_realtime and index_mgr.is_ready and not index_mgr.is_building
    if use_idx:
        worker = IndexSearchWorker(index_mgr, kw, scope_targets, regex_var, fuzzy_var)
        return worker, False
    else:
        worker = RealtimeSearchWorker(kw, scope_targets, regex_var, fuzzy_var)
        return worker, True
