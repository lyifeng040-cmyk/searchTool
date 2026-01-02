// 极速文件搜索 V2.0 - 前端应用逻辑

// Tauri API (将在DOMContentLoaded中初始化)
let invoke, open, save, message, listen, confirmDialog;

function resolveInvoke() {
    const t = window.__TAURI__;
    const candidates = [
        t && t.invoke,
        t && t.tauri && t.tauri.invoke,
        t && t.core && t.core.invoke,
        // 一些环境会把 invoke 暴露在单独的全局变量上
        window.__TAURI_INVOKE__,
        t && t.__TAURI_INVOKE__,
        t && t.__internal && t.__internal.invoke,
        t && t.ipc && (t.ipc.invoke || t.ipc.call),
        t && t.__TAURI_IPC__ && t.__TAURI_IPC__.invoke,
    ];
    for (const fn of candidates) {
        if (typeof fn === 'function') return fn;
    }
    return null;
}

function resolveListen() {
    const t = window.__TAURI__;
    const candidates = [
        t && t.event && t.event.listen,
        t && t.event && t.event.listenEvent,
        t && t.tauri && t.tauri.event && t.tauri.event.listen,
        t && t.core && t.core.event && t.core.event.listen,
        // 少数注入会把 event 挂到 __TAURI_EVENT__
        window.__TAURI_EVENT__ && window.__TAURI_EVENT__.listen,
    ];
    for (const fn of candidates) {
        if (typeof fn === 'function') return fn;
    }
    return null;
}

function describeTauriGlobals() {
    const t = window.__TAURI__;
    const safeKeys = (obj) => (obj && typeof obj === 'object') ? Object.keys(obj) : [];
    try {
        return JSON.stringify({
            has__TAURI__: !!t,
            tauriKeys: safeKeys(t),
            tauriSubKeys: safeKeys(t && t.tauri),
            coreKeys: safeKeys(t && t.core),
            dialogKeys: safeKeys(t && t.dialog),
            eventKeys: safeKeys(t && t.event),
            has__TAURI_INVOKE__: typeof window.__TAURI_INVOKE__ === 'function',
        }, null, 2);
    } catch (_) {
        return String(t);
    }
}

// 应用状态
const appState = {
    allResults: [],
    filteredResults: [],
    currentPage: 1,
    // 默认每页行数太大 WebView 会抖；先降到 200，用户可在下拉里调大
    pageSize: 200,
    totalPages: 1,
    isSearching: false,
    selectedRows: new Set(),
    sortColumn: -1,
    sortAscending: true,
    // 记录上一次搜索信息（用于“全盘后切盘符=就地过滤，而不是重新搜索”）
    lastSearchKeyword: '',
    lastSearchScope: 'all',
    lastSearchRealtime: false,
};

function setTopResultCount(n) {
    const el = document.getElementById('topResultCount');
    if (el) el.textContent = `共 ${Number(n || 0).toLocaleString()} 条`;
}

// ==================== 初始化 ====================

document.addEventListener('DOMContentLoaded', async () => {
    // 初始化 Tauri API（兼容不同版本/注入方式）
    const t = window.__TAURI__;
    if (!t) {
        alert('Tauri API未加载（缺少 tauri.js）。');
        return;
    }

    invoke = resolveInvoke();
    // event.listen 在不同注入版本位置不一致；兜底避免 “listen is not a function” 直接把主窗口脚本打死
    listen = resolveListen();

    // dialog 模块：未启用时会抛错，所以统一降级为 prompt/alert，避免 “Dialog module is not enabled” 把 UI 打死
    open = async () => null;
    save = async () => null;
    message = async (msg) => alert(msg);
    confirmDialog = async (msg) => window.confirm(String(msg || '确认？'));

    // 若 dialog 已启用，尽量用原生对话框（目录多选对 multi-scope 很关键）
    try {
        const d = (t && t.dialog) || null;
        if (d) {
            if (typeof d.open === 'function') open = d.open;
            if (typeof d.save === 'function') save = d.save;
            if (typeof d.message === 'function') message = d.message;
            if (typeof d.confirm === 'function') confirmDialog = d.confirm;
        }
    } catch (_) {}

    if (typeof invoke !== 'function') {
        alert('invoke 未初始化成功。\n\n可用的 __TAURI__ 信息：\n' + describeTauriGlobals());
        return;
    }

    await loadConfig();
    
    // 加载驱动器列表
    await loadDrives();
    
    // 加载收藏列表
    await loadFavorites();
    
    // 获取索引状态
    await updateIndexStatus();
    
    // 绑定事件
    bindEvents();
    
    // 监听后端事件（listen 不可用时直接跳过，不影响搜索/语法）
    if (typeof listen === 'function') {
        listenToBackend();
    } else {
        console.warn('listen 不可用：跳过 listenToBackend（不影响搜索）');
        // 兜底：仅当 listen 不可用时，低频刷新索引状态（避免“必须重启才看到数量变化”）
        startIndexStatsPoller();
    }

    // mini -> main 结果导入兜底：不依赖 listen（因为部分环境 listen 注入不稳定）
    startMiniTransferPoller();
    
    // 初始化完成：默认用“每页”下拉里的模式
    changePageSize();
});

function removeDeletedPathsFromResults(paths) {
    const dels = (paths || []).map(String).filter(Boolean);
    if (dels.length === 0) return;
    const delSet = new Set(dels.map(p => p.toLowerCase()));
    const delPrefixes = dels
        .map(p => p.replace(/\//g, '\\').replace(/\\+$/, '') + '\\')
        .map(p => p.toLowerCase());

    const shouldRemove = (fullPath) => {
        const p = String(fullPath || '').toLowerCase();
        if (!p) return false;
        if (delSet.has(p)) return true;
        for (const pre of delPrefixes) {
            if (p.startsWith(pre)) return true;
        }
        return false;
    };

    appState.allResults = (appState.allResults || []).filter(r => !shouldRemove(r.full_path));
    appState.filteredResults = (appState.filteredResults || []).filter(r => !shouldRemove(r.full_path));
    appState.selectedRows.clear();
    appState.currentPage = 1;
    appState.totalPages = Math.ceil(appState.filteredResults.length / appState.pageSize);
    setTopResultCount(appState.filteredResults.length);
    updateExtensionFilter();
    updateScopeCountsInDropdown();
    renderResults();
    updateResultCount();
}

let _indexStatsTimer = null;
function startIndexStatsPoller() {
    if (_indexStatsTimer) return;
    _indexStatsTimer = setInterval(async () => {
        try {
            if (typeof invoke !== 'function') return;
            if (document.visibilityState && document.visibilityState !== 'visible') return;
            const stats = await invoke('get_index_stats');
            const n = Number(stats && stats.indexed_files || 0);
            if (appState._lastIndexedFiles == null || appState._lastIndexedFiles !== n) {
                appState._lastIndexedFiles = n;
                await updateIndexStatus();
            }
        } catch (e) {
            console.warn('index stats poll failed:', e);
        }
    }, 1000);
}

// ==================== Scope（搜索范围）说明 ====================
// 维持“之前的形式”：scopeInput 只代表当前范围（all / 某盘符 / 某目录），不做自动拼接累加。

let _miniTransferTimer = null;
function startMiniTransferPoller() {
    if (_miniTransferTimer) return;
    _miniTransferTimer = setInterval(async () => {
        try {
            if (typeof invoke !== 'function') return;
            // 页面不可见时跳过，减少无意义轮询
            if (document.visibilityState && document.visibilityState !== 'visible') return;
            const payload = await invoke('mini_take_transfer');
            if (!payload) return;

            const { keyword, mode, results, truncated, original_len } = payload;

            // 设置搜索框/模式
            const input = document.getElementById('searchInput');
            if (input) input.value = keyword || '';
            document.getElementById('realtimeCheck').checked = (mode === 'realtime');

            // 直接显示结果（对齐 Python：Tab 导入结果）
            appState.allResults = results || [];
            appState.filteredResults = [...appState.allResults];
            updateExtensionFilter();
            renderResults();

            const extra = truncated ? `（已截断：${Number(original_len).toLocaleString()} → ${Number(appState.allResults.length).toLocaleString()}）` : '';
            updateStatus(`✅ 从迷你窗口导入 ${Number(appState.allResults.length).toLocaleString()} 条结果${extra}`, '');

            // 导入后聚焦搜索框，方便继续输入
            if (input) {
                input.focus();
                input.select();
            }
        } catch (e) {
            // 轮询兜底：不要弹窗影响用户，只在控制台记录
            console.warn('mini transfer poll failed:', e);
        }
    }, 250);
}

async function loadConfig() {
    try {
        const config = await invoke('get_config');
        document.getElementById('themeSelect').value = config.theme || 'light';
        applyTheme(config.theme || 'light');
    } catch (error) {
        console.error('加载配置失败:', error);
    }
}

async function loadDrives() {
    try {
        const drives = await invoke('get_drives');
        const sel = document.getElementById('scopeInput');
        if (sel) {
            const cur = String(sel.value || 'all');
            sel.innerHTML = '<option value="all">全部</option>';
            drives.forEach(drive => {
                const option = document.createElement('option');
                option.value = drive;
                option.textContent = drive;
                sel.appendChild(option);
            });
            sel.value = cur;
            if (!sel.value) sel.value = 'all';
        }
    } catch (error) {
        console.error('加载驱动器失败:', error);
    }
}

function getDriveLetterFromPath(p) {
    const s = String(p || '');
    if (s.length >= 2 && s[1] === ':') return s.slice(0, 2).toUpperCase();
    return '';
}

function updateScopeCountsInDropdown() {
    const sel = document.getElementById('scopeInput');
    if (!sel) return;

    // 只在“上一次搜索范围=all 且有结果”时显示计数（避免干扰日常选择范围）
    if (String(appState.lastSearchScope || 'all').toLowerCase() !== 'all') return;
    if (!appState.allResults || appState.allResults.length === 0) return;

    const counts = new Map();
    for (const f of appState.allResults) {
        const d = getDriveLetterFromPath(f.full_path || '');
        if (!d) continue;
        counts.set(d, (counts.get(d) || 0) + 1);
    }

    for (const opt of Array.from(sel.options)) {
        const v = String(opt.value || '').toUpperCase();
        if (!v || v.toLowerCase() === 'all') {
            opt.textContent = '全部';
            continue;
        }
        const n = counts.get(v) || 0;
        opt.textContent = n > 0 ? `${v} (${n.toLocaleString()})` : `${v}`;
    }
}

// 驱动器选择改变时的处理
function onScopeChange() {
    const keyword = document.getElementById('searchInput').value.trim();
    const scope = String(document.getElementById('scopeInput')?.value || 'all').trim() || 'all';
    
    if (!keyword) return;

    // 如果“上一次是全盘搜索同一关键词”，此时切盘符是“就地过滤当前结果”，不重新搜索
    if (
        String(appState.lastSearchScope || 'all').toLowerCase() === 'all' &&
        String(appState.lastSearchKeyword || '') === keyword &&
        Array.isArray(appState.allResults) &&
        appState.allResults.length > 0
    ) {
        applyFilter();
        return;
    }

    // 否则按原逻辑：切换范围后重搜
    startSearch();
}

async function loadFavorites() {
    try {
        const favorites = await invoke('get_favorites');
        const select = document.getElementById('favoriteSelect');
        select.innerHTML = '<option value="">⭐ 收藏</option>';
        
        favorites.forEach(fav => {
            const option = document.createElement('option');
            option.value = fav.path;
            option.textContent = fav.name;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('加载收藏失败:', error);
    }
}

async function updateIndexStatus() {
    try {
        const stats = await invoke('get_index_stats');
        const statusEl = document.getElementById('indexStatus');
        statusEl.textContent = `📊 索引: ${stats.indexed_files.toLocaleString()} 文件`;
        statusEl.style.color = stats.indexed_files > 0 ? '#4CAF50' : '#999';
    } catch (error) {
        console.error('获取索引状态失败:', error);
        document.getElementById('indexStatus').textContent = '❌ 索引错误';
    }
}

function bindEvents() {
    // 搜索框回车
    document.getElementById('searchInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            startSearch();
        }
    });

    // 输入即搜（仅索引模式；实时搜索太重不跟随输入）
    let _typeTimer = null;
    document.getElementById('searchInput').addEventListener('input', () => {
        const realtime = document.getElementById('realtimeCheck')?.checked;
        if (realtime) return;
        if (_typeTimer) clearTimeout(_typeTimer);
        _typeTimer = setTimeout(() => {
            const kw = document.getElementById('searchInput').value.trim();
            if (kw) {
                startSearch();
            } else {
                // 清空关键词：立即清空结果（并通过 _seq 取消未完成请求）
                startSearch._seq = (startSearch._seq || 0) + 1;
                appState.allResults = [];
                appState.filteredResults = [];
                appState.currentPage = 1;
                appState.totalPages = 1;
                setTopResultCount(0);
                renderResults();
                updateStatus('⌨️ 请输入关键词开始搜索', '');
            }
        }, 120);
    });
    
    // 表格行点击
    document.getElementById('resultsBody').addEventListener('click', (e) => {
        const row = e.target.closest('tr');
        if (row && row.dataset.index) {
            handleRowClick(row, e);
        }
    });
    
    // 表格行双击
    document.getElementById('resultsBody').addEventListener('dblclick', (e) => {
        const row = e.target.closest('tr');
        if (row && row.dataset.index) {
            const index = parseInt(row.dataset.index);
            const file = appState.filteredResults[index];
            if (file) {
                openFile(file.full_path);
            }
        }
    });
    
    // 右键菜单
    document.getElementById('resultsBody').addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const row = e.target.closest('tr');
        if (row && row.dataset.index) {
            // 右键落点不在已选中行时：先把该行设为单选（对齐主流工具行为）
            const p = row.dataset.path;
            if (p && !appState.selectedRows.has(p)) {
                appState.selectedRows.clear();
                document.querySelectorAll('#resultsBody tr.selected').forEach(r => r.classList.remove('selected'));
                appState.selectedRows.add(p);
                row.classList.add('selected');
                updateResultCount();
            }
            showContextMenu(e.clientX, e.clientY);
        }
    });
    
    // 点击其他地方关闭右键菜单
    document.addEventListener('click', () => {
        document.getElementById('contextMenu').style.display = 'none';
    });
    
    // 键盘快捷键
    document.addEventListener('keydown', (e) => {
        const isTypingTarget = (el) => {
            if (!el) return false;
            const tag = (el.tagName || '').toLowerCase();
            return tag === 'input' || tag === 'textarea' || el.isContentEditable;
        };

        // Tab：回主屏（关闭弹窗并聚焦搜索框）
        if (e.key === 'Tab' && !e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey) {
            const dialogs = ['indexManagerDialog', 'cDriveDialog', 'batchRenameDialog'];
            let closedAny = false;
            for (const id of dialogs) {
                const el = document.getElementById(id);
                if (el && el.style.display && el.style.display !== 'none') {
                    closeDialog(id);
                    closedAny = true;
                }
            }
            const input = document.getElementById('searchInput');
            if (closedAny || (document.activeElement && document.activeElement !== input)) {
                e.preventDefault();
                input?.focus();
                return;
            }
        }

        // 结果快捷键：不要抢输入框的按键
        if (!isTypingTarget(document.activeElement)) {
            // Enter：打开选中项（双击等价）
            if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey) {
                const selected = Array.from(appState.selectedRows);
                if (selected.length > 0) {
                    e.preventDefault();
                    invoke('open_file', { path: selected[0] });
                    return;
                }
            }
            // Ctrl+Enter：定位文件
            if ((e.key === 'Enter' || e.key === 'Return') && e.ctrlKey) {
                const selected = Array.from(appState.selectedRows);
                if (selected.length > 0) {
                    e.preventDefault();
                    invoke('reveal_in_folder', { path: selected[0] });
                    return;
                }
            }
            // Ctrl+C：复制路径（多选换行）
            if ((e.key === 'c' || e.key === 'C') && e.ctrlKey) {
                const selected = Array.from(appState.selectedRows);
                if (selected.length > 0) {
                    e.preventDefault();
                    const text = selected.join('\r\n');
                    copyTextToClipboard(text);
                    return;
                }
            }
            // Ctrl+X：剪切文件到剪贴板（资源管理器可粘贴）
            if ((e.key === 'x' || e.key === 'X') && e.ctrlKey) {
                const selected = Array.from(appState.selectedRows);
                if (selected.length > 0) {
                    e.preventDefault();
                    invoke('clipboard_cut_files', { paths: selected });
                    return;
                }
            }
        }

        if (e.ctrlKey && e.key === 'a') {
            e.preventDefault();
            selectAll();
        } else if (e.key === 'Delete') {
            deleteSelected();
        } else if (e.key === 'Escape') {
            stopSearch();
        }
    });
}

// （忽略规则 UI 已移除：实时/索引回到同一套硬编码过滤原则）

// （取消自动累加逻辑）

async function copyTextToClipboard(text) {
    const s = String(text ?? '');
    if (!s) return false;
    // 1) 优先 Web API
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(s);
            return true;
        }
    } catch (_) {}
    // 2) 兜底走后端（Windows 稳）
    try {
        if (typeof invoke === 'function') {
            await invoke('clipboard_set_text', { text: s });
            return true;
        }
    } catch (_) {}
    // 3) 最后兜底 execCommand（老 WebView）
    try {
        const ta = document.createElement('textarea');
        ta.value = s;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        return true;
    } catch (_) {}
    return false;
}

function listenToBackend() {
    // 监听重建索引事件
    listen('rebuild-index', async () => {
        console.log('收到重建索引事件');
        await rebuildIndex();
    });
    
    // 监听刷新状态事件
    listen('refresh-status', async () => {
        console.log('收到刷新状态事件');
        await updateIndexStatus();
    });
    
    // 监听 mini 窗口发起的搜索
    listen('mini-search', async (event) => {
        console.log('收到 mini 搜索事件:', event.payload);
        const { keyword, mode } = event.payload;
        
        // 设置搜索框内容
        document.getElementById('searchInput').value = keyword;
        
        // 设置搜索模式
        if (mode === 'realtime') {
            document.getElementById('realtimeCheck').checked = true;
        } else {
            document.getElementById('realtimeCheck').checked = false;
        }
        
        // 执行搜索
        await startSearch();
    });
    
    // 监听 mini 窗口传递的搜索结果
    listen('mini-results', async (event) => {
        console.log('收到 mini 结果事件:', event.payload);
        const { keyword, mode, results } = event.payload;
        
        // 设置搜索框内容
        document.getElementById('searchInput').value = keyword;
        
        // 设置搜索模式
        if (mode === 'realtime') {
            document.getElementById('realtimeCheck').checked = true;
        } else {
            document.getElementById('realtimeCheck').checked = false;
        }
        
        // 直接显示结果
        appState.allResults = results || [];
        appState.filteredResults = [...appState.allResults];
        appState.lastSearchKeyword = String(keyword || '');
        appState.lastSearchScope = 'all';
        appState.lastSearchRealtime = (mode === 'realtime');
        updateExtensionFilter();
        updateScopeCountsInDropdown();
        renderResults();
        updateStatus(`✅ 共 ${appState.allResults.length} 条结果（来自Mini窗口）`, '');
    });
}

// ==================== 搜索功能 ====================

async function startSearch() {
    const keyword = document.getElementById('searchInput').value.trim();
    if (!keyword) {
        await message('请输入搜索关键词', { title: '提示', type: 'warning' });
        return;
    }
    
    appState.isSearching = true;
    updateSearchButtons(true);
    updateStatus('🔍 搜索中...', '');
    
    const scope = (document.getElementById('scopeInput')?.value || 'all').trim() || 'all';
    const fuzzy = document.getElementById('fuzzyCheck').checked;
    const fuzzyMinScore = parseInt(document.getElementById('fuzzyThreshold')?.value || '0', 10) || 0;
    const regex = document.getElementById('regexCheck').checked;
    const realtime = document.getElementById('realtimeCheck').checked;
    
    // 防止旧请求覆盖新请求（输入即搜会并发）
    startSearch._seq = (startSearch._seq || 0) + 1;
    const seq = startSearch._seq;

    try {
        let result;
        
        if (realtime) {
            // 实时搜索
            result = await invoke('search_realtime', {
                params: {
                    keyword: keyword,
                    // scope=all 交给后端按“所有磁盘 + C盘目录白名单”处理
                    scope: scope,
                    use_fts: false,
                    use_regex: regex,
                    fuzzy: fuzzy,
                    fuzzy_min_score: fuzzyMinScore,
                    limit: 20000,
                }
            });
        } else {
            // 索引搜索
            result = await invoke('search_files', {
                params: {
                    keyword: keyword,
                    scope: scope,
                    use_fts: !regex,
                    use_regex: regex,
                    fuzzy: fuzzy,
                    fuzzy_min_score: fuzzyMinScore,
                    limit: 20000,
                }
            });
        }
        
        if (seq !== startSearch._seq) return;

        appState.allResults = result.results;
        appState.filteredResults = result.results;
        appState.lastSearchKeyword = keyword;
        appState.lastSearchScope = scope;
        appState.lastSearchRealtime = !!realtime;
        appState.currentPage = 1;
        appState.totalPages = Math.ceil(result.results.length / appState.pageSize);
        setTopResultCount(result.total ?? result.results.length);
        
        // 先渲染列表，再异步更新扩展名筛选（避免同步更新下拉导致明显“跳动”）
        renderResults();
        requestAnimationFrame(() => {
            updateExtensionFilter();
            updateScopeCountsInDropdown();
        });
        
        updateStatus(
            `✅ 找到 ${result.total.toLocaleString()} 个结果`,
            `用时: ${result.elapsed_ms}ms`
        );
        
        // 保存搜索历史
        await invoke('add_history', { keyword: keyword });
        
    } catch (error) {
        console.error('搜索失败:', error);
        updateStatus('❌ 搜索失败', error.toString());
        await message('搜索失败: ' + error, { title: '错误', type: 'error' });
    } finally {
        appState.isSearching = false;
        updateSearchButtons(false);
    }
}

function refreshSearch() {
    startSearch();
}

function stopSearch() {
    appState.isSearching = false;
    updateSearchButtons(false);
    updateStatus('⏹ 已停止', '');
}

function togglePause() {
    // TODO: 实现暂停功能
}

function updateSearchButtons(searching) {
    document.getElementById('refreshBtn').disabled = searching;
    document.getElementById('pauseBtn').disabled = !searching;
    document.getElementById('stopBtn').disabled = !searching;
}

// ==================== 结果渲染 ====================

function renderResults() {
    renderResultsPaged();
}

function renderEmpty(tbody) {
    tbody.innerHTML = `
        <tr>
            <td colspan="4" class="empty-message">
                <div class="welcome-message">
                    <h2>😔 没有找到结果</h2>
                    <p>请尝试其他关键词或调整筛选条件</p>
                </div>
            </td>
        </tr>
    `;
}

function fillRowWithFile(row, file, absoluteIndex) {
    row.dataset.index = String(absoluteIndex);
    row.dataset.path = file.full_path || '';
    row.classList.toggle('selected', appState.selectedRows.has(file.full_path));

    const name = file.filename || '';
    const dir = file.parent_dir || '';
    const sizeText = file.is_dir ? '📁 文件夹' : formatSize(file.size);
    const timeText = formatTime(file.mtime);

    const tds = row.querySelectorAll('td');
    // 这里的 row 预先建好 4 个 td
    tds[0].title = name;
    // 主窗口：默认只高亮文件名（不做开关）
    const tokens = getHighlightTokens(document.getElementById('searchInput')?.value || '');
    const nameSpan = tds[0].querySelector('.cell-ellipsis');
    if (tokens.length > 0) {
        nameSpan.innerHTML = highlightHtml(name, tokens);
    } else {
        nameSpan.textContent = name;
    }
    tds[1].title = dir;
    const dirSpan = tds[1].querySelector('.cell-ellipsis');
    // 路径不高亮：只高亮文件名，避免“整行发光”影响扫读
    dirSpan.textContent = dir;
    tds[2].title = sizeText;
    tds[2].querySelector('.cell-ellipsis').textContent = sizeText;
    tds[3].title = timeText;
    tds[3].querySelector('.cell-ellipsis').textContent = timeText;
}

function escapeRegExp(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 从“增强语法”里剥离真正需要高亮的 token（ext:/dm:/size:/name:/path:/is:）
function getHighlightTokens(query) {
    const raw = String(query || '').trim();
    if (!raw) return [];
    const parts = raw.split(/\s+/).filter(Boolean);
    const out = [];
    for (const p of parts) {
        const lower = p.toLowerCase();
        if (
            lower.startsWith('ext:') ||
            lower.startsWith('dm:') ||
            lower.startsWith('size:') ||
            lower.startsWith('name:') ||
            lower.startsWith('path:') ||
            lower.startsWith('is:')
        ) {
            continue;
        }
        out.push(p);
    }
    // 长词优先，避免短词把长词拆碎；同时限制数量避免正则过大
    out.sort((a, b) => b.length - a.length);
    return out.slice(0, 10);
}

function highlightHtml(text, tokens) {
    const s = String(text ?? '');
    if (!s || !tokens || tokens.length === 0) return escapeHtml(s);
    const pat = tokens.map(escapeRegExp).join('|');
    if (!pat) return escapeHtml(s);
    const re = new RegExp(pat, 'gi');

    let out = '';
    let last = 0;
    let m;
    while ((m = re.exec(s)) !== null) {
        const start = m.index;
        const end = start + m[0].length;
        if (start > last) out += escapeHtml(s.slice(last, start));
        out += `<span class="hl">${escapeHtml(s.slice(start, end))}</span>`;
        last = end;
        // 防止空匹配死循环
        if (re.lastIndex === start) re.lastIndex = start + 1;
    }
    if (last < s.length) out += escapeHtml(s.slice(last));
    return out;
}

function createDataRow() {
    const row = document.createElement('tr');
    row.innerHTML = `
        <td><span class="cell-ellipsis"></span></td>
        <td><span class="cell-ellipsis"></span></td>
        <td><span class="cell-ellipsis"></span></td>
        <td><span class="cell-ellipsis"></span></td>
    `;
    return row;
}

// 虚拟滚动按需求移除：保留分页模式（配合边输边搜）即可满足“几千结果”快速定位与稳定呈现

function renderResultsPaged() {
    const tbody = document.getElementById('resultsBody');
    tbody.innerHTML = '';

    if (appState.filteredResults.length === 0) {
        renderEmpty(tbody);
        updatePagination();
        setTopResultCount(0);
        return;
    }

    const bar = document.getElementById('paginationBar');
    if (bar) bar.style.display = 'flex';

    const start = (appState.currentPage - 1) * appState.pageSize;
    const end = Math.min(start + appState.pageSize, appState.filteredResults.length);
    const pageResults = appState.filteredResults.slice(start, end);

    const frag = document.createDocumentFragment();
    for (let i = 0; i < pageResults.length; i++) {
        const file = pageResults[i];
        const row = createDataRow();
        fillRowWithFile(row, file, start + i);
        frag.appendChild(row);
    }
    tbody.appendChild(frag);

    updatePagination();
    updateResultCount();
    hydrateVisibleFileInfo(pageResults);
}

let _hydrateTimer = null;
async function hydrateVisibleFileInfo(pageResults) {
    if (!invoke) return;
    if (_hydrateTimer) clearTimeout(_hydrateTimer);

    _hydrateTimer = setTimeout(async () => {
        try {
            const need = pageResults
                .filter(f => !f.is_dir && (!f.size || f.size === 0) && (!f.mtime || f.mtime === 0))
                .map(f => f.full_path)
                .slice(0, 800); // 避免一次请求过大

            if (need.length === 0) return;

            const infos = await invoke('get_file_info_batch', { paths: need });
            const map = new Map();
            infos.forEach(i => map.set(i.full_path, i));

            // 更新内存数据 + 只更新 DOM，不整页重绘
            for (const fp of need) {
                const info = map.get(fp);
                if (!info) continue;

                // 更新 appState 中对应条目
                const idx = appState.allResults.findIndex(x => x.full_path === fp);
                if (idx >= 0) {
                    appState.allResults[idx].size = info.size;
                    appState.allResults[idx].mtime = info.mtime;
                }

                const idx2 = appState.filteredResults.findIndex(x => x.full_path === fp);
                if (idx2 >= 0) {
                    appState.filteredResults[idx2].size = info.size;
                    appState.filteredResults[idx2].mtime = info.mtime;
                }

                const row = document.querySelector(`#resultsBody tr[data-path="${cssEscape(fp)}"]`);
                if (row) {
                    const tds = row.querySelectorAll('td');
                    if (tds.length >= 4) {
                        const sizeText = formatSize(info.size);
                        const timeText = formatTime(info.mtime);
                        tds[2].title = sizeText;
                        tds[2].querySelector('.cell-ellipsis').textContent = sizeText;
                        tds[3].title = timeText;
                        tds[3].querySelector('.cell-ellipsis').textContent = timeText;
                    }
                }
            }
        } catch (e) {
            // 静默失败，不影响主搜索体验
            console.debug('hydrateVisibleFileInfo failed', e);
        }
    }, 50);
}

function cssEscape(s) {
    // 最简 escape：处理 " 和 \，足够用于 data-path 选择器
    return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function updatePagination() {
    document.getElementById('pageInfo').textContent = 
        `第 ${appState.currentPage} / ${appState.totalPages} 页`;
    
    document.getElementById('firstBtn').disabled = appState.currentPage === 1;
    document.getElementById('prevBtn').disabled = appState.currentPage === 1;
    document.getElementById('nextBtn').disabled = appState.currentPage === appState.totalPages;
    document.getElementById('lastBtn').disabled = appState.currentPage === appState.totalPages;
}

function updateResultCount() {
    const total = appState.filteredResults.length;
    const selected = appState.selectedRows.size;
    
    let text = `共 ${total.toLocaleString()} 个结果`;
    if (selected > 0) {
        text += ` (已选 ${selected})`;
    }
    
    document.getElementById('resultCount').textContent = text;
}

function updateStatus(text, path) {
    document.getElementById('statusText').textContent = text;
    document.getElementById('statusPath').textContent = path;
}

// ==================== 筛选功能 ====================

function applyFilter() {
    const extFilter = document.getElementById('extFilter').value;
    const sizeFilter = parseInt(document.getElementById('sizeFilter').value) || 0;
    const dateFilter = parseInt(document.getElementById('dateFilter').value) || 0;
    const scopeSel = String(document.getElementById('scopeInput')?.value || 'all').trim() || 'all';
    const kwNow = String(document.getElementById('searchInput')?.value || '').trim();
    
    appState.filteredResults = appState.allResults.filter(file => {
        // 当“上次是全盘同一关键词”时，scope 下拉切盘符=就地过滤当前结果
        if (
            String(appState.lastSearchScope || 'all').toLowerCase() === 'all' &&
            String(appState.lastSearchKeyword || '') === kwNow &&
            scopeSel.toLowerCase() !== 'all' &&
            /^[A-Za-z]:$/.test(scopeSel)
        ) {
            const d = getDriveLetterFromPath(file.full_path || '');
            if (d !== scopeSel.toUpperCase()) return false;
        }

        // 扩展名筛选
        if (extFilter && file.extension !== extFilter) {
            return false;
        }
        
        // 大小筛选
        if (sizeFilter > 0) {
            const minSize = sizeFilter * 1024 * 1024;
            if (file.size < minSize) {
                return false;
            }
        }
        
        // 时间筛选
        if (dateFilter > 0) {
            const now = Date.now() / 1000;
            const days = dateFilter;
            const minTime = now - (days * 24 * 60 * 60);
            if (file.mtime < minTime) {
                return false;
            }
        }
        
        return true;
    });
    
    appState.currentPage = 1;
    appState.totalPages = Math.ceil(appState.filteredResults.length / appState.pageSize);
    renderResults();
    
    // 更新筛选信息
    const info = [];
    // 盘符过滤状态提示（不显示数量）
    if (
        String(appState.lastSearchScope || 'all').toLowerCase() === 'all' &&
        String(appState.lastSearchKeyword || '') === kwNow &&
        scopeSel.toLowerCase() !== 'all' &&
        /^[A-Za-z]:$/.test(scopeSel)
    ) {
        info.push(`盘符: ${scopeSel.toUpperCase()}`);
    }
    if (extFilter) info.push(`格式: ${extFilter}`);
    if (sizeFilter) info.push(`大小: >${sizeFilter}MB`);
    if (dateFilter) info.push(`时间: ${dateFilter}天内`);
    
    document.getElementById('filterInfo').textContent = 
        info.length > 0 ? `已筛选: ${info.join(', ')}` : '';
}

function clearFilter() {
    document.getElementById('extFilter').value = '';
    document.getElementById('sizeFilter').value = '';
    document.getElementById('dateFilter').value = '';
    applyFilter();
}

function updateExtensionFilter() {
    const extensions = new Set();
    appState.allResults.forEach(file => {
        if (file.extension) {
            extensions.add(file.extension);
        }
    });
    
    const select = document.getElementById('extFilter');
    const currentValue = select.value;
    select.innerHTML = '<option value="">全部</option>';
    
    Array.from(extensions).sort().forEach(ext => {
        const option = document.createElement('option');
        option.value = ext;
        option.textContent = ext;
        select.appendChild(option);
    });
    
    if (currentValue && extensions.has(currentValue)) {
        select.value = currentValue;
    }
}

// ==================== 排序功能 ====================

function sortColumn(columnIndex) {
    if (appState.sortColumn === columnIndex) {
        appState.sortAscending = !appState.sortAscending;
    } else {
        appState.sortColumn = columnIndex;
        appState.sortAscending = true;
    }
    
    appState.filteredResults.sort((a, b) => {
        let valA, valB;
        
        switch (columnIndex) {
            case 0: // 文件名
                valA = a.filename.toLowerCase();
                valB = b.filename.toLowerCase();
                break;
            case 1: // 目录
                valA = a.parent_dir.toLowerCase();
                valB = b.parent_dir.toLowerCase();
                break;
            case 2: // 大小
                valA = a.size;
                valB = b.size;
                break;
            case 3: // 时间
                valA = a.mtime;
                valB = b.mtime;
                break;
            default:
                return 0;
        }
        
        if (valA < valB) return appState.sortAscending ? -1 : 1;
        if (valA > valB) return appState.sortAscending ? 1 : -1;
        return 0;
    });
    
    renderResults();
}

// ==================== 分页功能 ====================

function goPage(action) {
    switch (action) {
        case 'first':
            appState.currentPage = 1;
            break;
        case 'prev':
            appState.currentPage = Math.max(1, appState.currentPage - 1);
            break;
        case 'next':
            appState.currentPage = Math.min(appState.totalPages, appState.currentPage + 1);
            break;
        case 'last':
            appState.currentPage = appState.totalPages;
            break;
    }
    
    renderResults();
}

function changePageSize() {
    const v = document.getElementById('pageSizeSelect').value;
    appState.pageSize = parseInt(v, 10) || 200;
    appState.currentPage = 1;
    appState.totalPages = Math.ceil(appState.filteredResults.length / appState.pageSize);
    renderResults();
}

// ==================== 选择功能 ====================

function handleRowClick(row, event) {
    const path = row.dataset.path;
    if (path) {
        // 单击时在状态栏展示完整路径（解决被截断看不全的问题）
        updateStatus(document.getElementById('statusText').textContent, path);
    }
    
    if (event.ctrlKey) {
        // Ctrl+点击：多选
        if (appState.selectedRows.has(path)) {
            appState.selectedRows.delete(path);
            row.classList.remove('selected');
        } else {
            appState.selectedRows.add(path);
            row.classList.add('selected');
        }
    } else if (event.shiftKey) {
        // Shift+点击：范围选择
        // TODO: 实现范围选择
    } else {
        // 普通点击：单选
        appState.selectedRows.clear();
        document.querySelectorAll('#resultsBody tr.selected').forEach(r => {
            r.classList.remove('selected');
        });
        appState.selectedRows.add(path);
        row.classList.add('selected');
    }
    
    updateResultCount();
}

function selectAll() {
    appState.selectedRows.clear();
    appState.filteredResults.forEach(file => {
        appState.selectedRows.add(file.full_path);
    });
    
    document.querySelectorAll('#resultsBody tr[data-path]').forEach(row => {
        row.classList.add('selected');
    });
    
    updateResultCount();
}

// ==================== 右键菜单 ====================

function showContextMenu(x, y) {
    const menu = document.getElementById('contextMenu');
    menu.style.display = 'block';
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
}

async function contextAction(action) {
    const selected = Array.from(appState.selectedRows);
    
    if (selected.length === 0) {
        await message('请先选择文件', { title: '提示', type: 'warning' });
        return;
    }
    
    try {
        switch (action) {
            case 'open':
                await invoke('open_file', { path: selected[0] });
                break;
            case 'reveal':
                await invoke('reveal_in_folder', { path: selected[0] });
                break;
            case 'copyPath':
                await copyTextToClipboard(selected.join('\r\n'));
                break;
            case 'copyName': {
                const names = selected.map(p => (p.split(/[\\/]/).pop() || p));
                await copyTextToClipboard(names.join('\r\n'));
                break;
            }
            case 'copyDir': {
                const dirs = selected.map(p => (p.replace(/[\\/][^\\/]+$/, '')));
                await copyTextToClipboard(dirs.join('\r\n'));
                break;
            }
            case 'copyFiles':
                await invoke('clipboard_copy_files', { paths: selected });
                break;
            case 'cutFiles':
                await invoke('clipboard_cut_files', { paths: selected });
                break;
            case 'delete':
                // 注意：Tauri 的 message() 不返回 bool；必须用 confirm()
                if (typeof confirmDialog !== 'function') {
                    confirmDialog = async (msg) => window.confirm(String(msg || '确认？'));
                }
                const ok = await confirmDialog('确定要将选中的文件/文件夹移动到回收站吗？', { title: '确认', type: 'warning' });
                if (ok) {
                    await invoke('delete_files', { paths: selected, toTrash: true });
                    removeDeletedPathsFromResults(selected);
                }
                break;
            case 'favorite':
                const file = appState.filteredResults.find(f => f.full_path === selected[0]);
                if (file) {
                    await invoke('add_favorite', { name: file.filename, path: file.full_path });
                    await loadFavorites();
                    await message('已添加到收藏', { title: '成功', type: 'info' });
                }
                break;
        }
    } catch (error) {
        console.error('操作失败:', error);
        await message('操作失败: ' + error, { title: '错误', type: 'error' });
    }
}

// ==================== 文件操作 ====================

async function openFile(path) {
    try {
        await invoke('open_file', { path: path });
    } catch (error) {
        console.error('打开文件失败:', error);
        await message('打开文件失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function deleteSelected() {
    await contextAction('delete');
}

// ==================== 工具功能 ====================

async function exportResults() {
    if (appState.filteredResults.length === 0) {
        await message('没有可导出的结果', { title: '提示', type: 'warning' });
        return;
    }
    
    try {
        const path = await save({
            defaultPath: 'search_results.csv',
            filters: [{ name: 'CSV', extensions: ['csv'] }]
        });
        
        if (path) {
            await invoke('export_results', {
                results: appState.filteredResults,
                path: path
            });
            await message('导出成功', { title: '成功', type: 'info' });
        }
    } catch (error) {
        console.error('导出失败:', error);
        await message('导出失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function showIndexManager() {
    try {
        const stats = await invoke('get_index_stats');
        
        document.getElementById('indexFileCount').textContent = stats.indexed_files.toLocaleString();
        document.getElementById('lastBuildTime').textContent = stats.last_build_time || '从未';
        document.getElementById('lastBuildCost').textContent =
            (stats.last_build_ms != null) ? `${stats.last_build_ms} ms` : '-';
        
        document.getElementById('indexManagerDialog').style.display = 'flex';
    } catch (error) {
        await message('获取索引统计失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function clearIndex() {
    try {
        if (!confirm('确定要清空索引吗？此操作不可恢复！')) {
            return;
        }
        await invoke('rebuild_index', { drives: [] });
        await updateIndexStatus();
        closeDialog('indexManagerDialog');
        await message('索引已清空', { title: '成功', type: 'info' });
    } catch (error) {
        console.error('清空索引失败:', error);
        await message('清空索引失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function showCDriveSettings() {
    try {
        const config = await invoke('get_config');
        const pathList = document.getElementById('cDrivePathList');
        pathList.innerHTML = '';
        
        if (config.c_scan_paths && config.c_scan_paths.paths) {
            config.c_scan_paths.paths.forEach(pathConfig => {
                const div = document.createElement('div');
                div.className = 'path-item';
                div.innerHTML = `
                    <input type="checkbox" ${pathConfig.enabled ? 'checked' : ''} 
                           data-path="${pathConfig.path}" />
                    <span>${pathConfig.path}</span>
                `;
                pathList.appendChild(div);
            });
        }
        
        document.getElementById('cDriveDialog').style.display = 'flex';
    } catch (error) {
        console.error('加载C盘设置失败:', error);
        await message('加载C盘设置失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function saveCDrivePaths() {
    try {
        const checkboxes = document.querySelectorAll('#cDrivePathList input[type="checkbox"]');
        const paths = Array.from(checkboxes).map(cb => ({
            path: cb.dataset.path,
            enabled: cb.checked
        }));
        
        const config = await invoke('get_config');
        config.c_scan_paths.paths = paths;
        await invoke('save_config', { config });
        
        closeDialog('cDriveDialog');
        await message('C盘设置已保存', { title: '成功', type: 'info' });
    } catch (error) {
        console.error('保存C盘设置失败:', error);
        await message('保存C盘设置失败: ' + error, { title: '错误', type: 'error' });
    }
}

function closeDialog(dialogId) {
    document.getElementById(dialogId).style.display = 'none';
}

async function rebuildIndex() {
    try {
        closeDialog('indexManagerDialog');
        updateStatus('🔄 重建索引中...', '');
        const drives = await invoke('get_drives');
        const count = await invoke('rebuild_index', { drives: drives });
        await updateIndexStatus();
        updateStatus('✅ 索引重建完成', `共 ${count.toLocaleString()} 个文件`);
        await message(`索引重建完成，共 ${count.toLocaleString()} 个文件`, {
            title: '成功',
            type: 'info'
        });
    } catch (error) {
        console.error('重建索引失败:', error);
        updateStatus('❌ 重建失败', error.toString());
        await message('重建索引失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function showBatchRename() {
    const selectedCount = appState.selectedRows.size;
    if (selectedCount === 0) {
        await message('请先选择要重命名的文件', { title: '提示', type: 'warning' });
        return;
    }
    document.getElementById('renameFileCount').textContent = selectedCount;
    document.getElementById('batchRenameDialog').style.display = 'flex';
}

async function executeBatchRename() {
    try {
        const findText = document.getElementById('renameFindText').value;
        const replaceText = document.getElementById('renameReplaceText').value;
        const useRegex = document.getElementById('renameRegex').checked;
        
        if (!findText) {
            await message('请输入要查找的文本', { title: '提示', type: 'warning' });
            return;
        }
        
        const selectedFiles = Array.from(appState.selectedRows).map(idx => 
            appState.filteredResults[idx].full_path
        );
        
        const result = await invoke('batch_rename', {
            files: selectedFiles,
            find: findText,
            replace: replaceText,
            useRegex
        });
        
        closeDialog('batchRenameDialog');
        await message(`成功重命名 ${result} 个文件`, { title: '成功', type: 'info' });
        await startSearch(); // 刷新结果
    } catch (error) {
        console.error('批量重命名失败:', error);
        await message('批量重命名失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function scanLargeFiles() {
    try {
        updateStatus('🔍 扫描大文件中...', '');
        const result = await invoke('scan_large_files', {
            minSize: 100 * 1024 * 1024, // 100MB
            limit: 1000
        });
        
        appState.allResults = result.results;
        appState.filteredResults = result.results;
        appState.currentPage = 1;
        updatePagination();
        renderResults();
        updateStatus('✅ 扫描完成', `找到 ${result.total} 个大文件`);
    } catch (error) {
        console.error('扫描大文件失败:', error);
        updateStatus('❌ 扫描失败', error.toString());
        await message('扫描大文件失败: ' + error, { title: '错误', type: 'error' });
    }
}

async function syncNow() {
    await updateIndexStatus();
    await message('状态已同步', { title: '成功', type: 'info' });
}

// ==================== 其他功能 ====================

async function browsePath() {
    try {
        // 维持“单范围”为主：选择一个目录就直接替换 scopeInput
        const picked = (typeof open === 'function')
            ? await open({ directory: true, multiple: false, title: '选择搜索范围目录' })
            : null;

        if (picked) {
            document.getElementById('scopeInput').value = String(picked);
            onScopeChange();
            return;
        }

        // dialog 未启用时用 prompt 让用户手动输入目录/盘符
        const input = prompt('请输入要搜索的目录路径（例如：C:\\\\Users 或 D:\\\\Work；全盘请输入 all）：');
        if (input) document.getElementById('scopeInput').value = input;
    } catch (error) {
        console.error('选择目录失败:', error);
    }
}

function onFavoriteSelect() {
    const path = document.getElementById('favoriteSelect').value;
    if (path) {
        document.getElementById('scopeInput').value = path;
        onScopeChange();
    }
}

function changeTheme() {
    const theme = document.getElementById('themeSelect').value;
    applyTheme(theme);
    
    // 保存配置
    invoke('get_config').then(config => {
        config.theme = theme;
        invoke('save_config', { config: config });
    });
}

function applyTheme(theme) {
    if (theme === 'dark') {
        document.body.classList.add('dark-theme');
    } else {
        document.body.classList.remove('dark-theme');
    }
}

// ==================== 工具函数 ====================

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
}

function formatTime(timestamp) {
    if (!timestamp || timestamp === 0) return '';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

