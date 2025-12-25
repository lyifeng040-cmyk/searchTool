// 主窗口 JavaScript 逻辑
console.log('=== main_window.js 开始加载 ===');
alert('JS文件开始加载');
const { invoke } = window.__TAURI__.tauri;
const { listen } = window.__TAURI__.event;
console.log('Tauri API 加载成功');

// 全局状态(模块级别声明,避免流式监听器访问时未定义)
let currentResults = [];
let filteredResults = [];
let currentPage = 1;
let pageSize = 500;
let sortColumn = 'filename';
let sortOrder = 'asc';
let searchMode = 'index'; // 'index' or 'realtime'
let isSearching = false;
let searchTimeout = null; // 实时搜索防抖计时器

// DOM 元素（稍后初始化）
let searchInput, scopeSelect, resultsBody, resultsCount;
let btnSearch, btnRefresh, btnStop, btnSync;
let chkSimpleMode, chkRegex, chkRealtime;
let filterExt, filterSize, filterDate;
let indexStatus, statusText, statsText, progressBar;
let pageInfo, btnFirstPage, btnPrevPage, btnNextPage, btnLastPage, pageSizeSelect;

// 流式搜索监听器（模块级别异步设置）
let streamListenerReady = false;
let streamBatchCount = 0;
let streamTotalReceived = 0;
let searchTimeoutId = null; // 搜索超时定时器
let renderDebounceTimer = null; // 渲染防抖定时器

// 在页面上显示调试信息
function showDebug(message, type = 'info') {
    console.log(message);
    
    // 在调试面板显示
    const debugLog = document.getElementById('debugLog');
    if (debugLog) {
        const time = new Date().toLocaleTimeString();
        const color = type === 'error' ? '#ff4444' : type === 'success' ? '#00ff00' : '#ffff00';
        const entry = `<div style="color: ${color};">[${time}] ${message}</div>`;
        debugLog.innerHTML = entry + debugLog.innerHTML;
        
        // 限制日志条数
        const entries = debugLog.children;
        if (entries.length > 50) {
            debugLog.removeChild(entries[entries.length - 1]);
        }
    }
    
    // 尝试在状态栏显示
    if (typeof statusText !== 'undefined' && statusText) {
        const prefix = type === 'error' ? '❌ ' : type === 'success' ? '✅ ' : '📝 ';
        statusText.textContent = prefix + message;
    }
}

(async () => {
    try {
        await listen('search-batch', (event) => {
            streamBatchCount++;
            showDebug(`🔥 流式事件触发！批次 #${streamBatchCount}`, 'success');
            
            const batch = event.payload;
            
            if (!Array.isArray(batch) || batch.length === 0) {
                showDebug(`批次数据无效: ${typeof batch}`, 'error');
                return;
            }
            
            streamTotalReceived += batch.length;
            showDebug(`收到 ${batch.length} 个结果，总计 ${streamTotalReceived}`, 'success');
            
            // 添加到结果集
            const beforeLength = currentResults.length;
            currentResults.push(...batch);
            const afterLength = currentResults.length;
            
            showDebug(`currentResults: ${beforeLength} → ${afterLength}`, 'info');
            
            // 立即渲染（像小窗口一样，不防抖）
            if (typeof applyFilters === 'function') {
                applyFilters();
            } else {
                filteredResults = [...currentResults];
                currentPage = 1;
                renderResults();
                updatePagination();
            }
            
            // 立即更新状态栏
            if (typeof resultsCount !== 'undefined' && resultsCount) {
                resultsCount.textContent = `已找到 ${currentResults.length} 个结果...`;
            }
            if (typeof statusText !== 'undefined' && statusText) {
                statusText.textContent = `🔍 搜索中... 已找到 ${streamTotalReceived} 个结果 (${streamBatchCount} 批次)`;
            }
            
            showDebug(`批次 #${streamBatchCount} 已渲染`, 'success');
        });
        // 监听搜索完成事件，结束搜索状态并更新UI
        await listen('search-complete', (event) => {
            // 清除超时定时器和防抖定时器
            if (searchTimeoutId) {
                clearTimeout(searchTimeoutId);
                searchTimeoutId = null;
            }
            if (renderDebounceTimer) {
                clearTimeout(renderDebounceTimer);
                renderDebounceTimer = null;
            }
            
            const total = typeof event.payload === 'number' ? event.payload : streamTotalReceived;
            showDebug(`✅ 搜索完成（共 ${total} 个结果，${streamBatchCount} 批次）`, 'success');
            
            showDebug(`准备最终渲染结果... currentResults.length=${currentResults.length}`, 'info');
            
            // 搜索完成后立即最终渲染（不等防抖）
            try {
                if (typeof applyFilters === 'function') {
                    showDebug(`调用 applyFilters()...`, 'info');
                    applyFilters();
                    showDebug(`applyFilters() 调用完成`, 'success');
                } else {
                    showDebug(`错误：applyFilters 不是函数！`, 'error');
                    // 手动渲染
                    filteredResults = [...currentResults];
                    currentPage = 1;
                    renderResults();
                    updatePagination();
                }
            } catch (error) {
                showDebug(`applyFilters 出错: ${error}`, 'error');
                console.error('applyFilters error:', error);
            }
            
            isSearching = false;
            if (btnSearch) btnSearch.disabled = false;
            if (btnStop) btnStop.disabled = true;
            if (progressBar) progressBar.style.display = 'none';
            if (statusText) statusText.textContent = `搜索完成: 找到 ${total} 个结果`;
        });
        
        // NOTE: 前端已取消直接在 UI 中展示 USN 增量变化，保留后端监控逻辑但不在这里修改状态栏。
        
        // 监听索引重建事件
        await listen('index-building', (event) => {
            const { drive, status } = event.payload;
            if (status === 'building') {
                statusText.textContent = `正在重建 ${drive} 盘索引...`;
                showDebug(`📊 正在重建 ${drive} 盘索引`, 'info');
            } else if (status === 'completed') {
                showDebug(`✅ ${drive} 盘索引重建完成`, 'success');
            } else if (status === 'failed') {
                showDebug(`❌ ${drive} 盘索引重建失败`, 'error');
            }
        });
        
        await listen('index-rebuild-finished', async (event) => {
            const { success, failed, message } = event.payload;
            statusText.textContent = message;
            showDebug(`🎉 ${message}`, 'success');
            
            // 重新启用重建按钮
            const btnRebuild = document.getElementById('btnRebuildIndex');
            if (btnRebuild) btnRebuild.disabled = false;
            
            // 刷新索引状态
            await checkIndexStatus();
        });
        
        streamListenerReady = true;
        showDebug('✅ 流式监听器已就绪', 'success');
    } catch (error) {
        showDebug('流式监听器设置失败: ' + error, 'error');
        alert('流式监听器设置失败: ' + error);
    }
})();

// DOM 加载完成后初始化
document.addEventListener('DOMContentLoaded', async () => {
    console.log('[DEBUG] 应用初始化开始');
    try {
        initElements();
        initEventListeners();
        await loadDriveList();
        await checkIndexStatus();
        updateUI();
        
        // 启动索引状态轮询（每3秒检查一次，直到索引就绪）
        const statusCheckInterval = setInterval(async () => {
            try {
                const status = await invoke('check_index_status', { scope: null });
                
                // 显示详细状态
                if (status.loading_count > 0) {
                    indexStatus.textContent = `正在加载索引... (${status.ready_count}/${status.total_drives})`;
                    indexStatus.style.color = '#ffa500';
                    showDebug(`加载中: ${status.ready_count}/${status.total_drives} 就绪, ${status.loading_count} 正在加载`, 'info');
                } else if (status.is_ready && status.total_files > 0) {
                    indexStatus.textContent = `索引就绪 (${status.total_files.toLocaleString()} 文件)`;
                    indexStatus.style.color = '#8cc84b';
                    searchMode = 'index';
                    clearInterval(statusCheckInterval); // 停止轮询
                    showDebug('✅ 索引已就绪，停止轮询', 'success');
                    
                    // 暂时禁用 USN 文件监控，避免死锁
                    // startFileMonitoring();
                } else {
                    indexStatus.textContent = '索引未初始化';
                    indexStatus.style.color = '#ff4444';
                    showDebug(`未就绪: ${status.ready_count}/${status.total_drives}`, 'info');
                }
            } catch (e) {
                console.error('轮询索引失败:', e);
            }
        }, 3000);
        
        console.log('[SUCCESS] 应用初始化完成，索引轮询已启动');
    } catch (error) {
        console.error('[FATAL] 初始化失败:', error);
        alert('应用初始化失败: ' + error);
    }
});

function initElements() {
    searchInput = document.getElementById('searchInput');
    scopeSelect = document.getElementById('scopeSelect');
    resultsBody = document.getElementById('resultsBody');
    resultsCount = document.getElementById('resultsCount');
    
    btnSearch = document.getElementById('btnSearch');
    btnRefresh = document.getElementById('btnRefresh');
    btnStop = document.getElementById('btnStop');
    btnSync = document.getElementById('btnSync');
    
    chkSimpleMode = document.getElementById('chkSimpleMode');
    chkRegex = document.getElementById('chkRegex');
    chkRealtime = document.getElementById('chkRealtime');
    
    filterExt = document.getElementById('filterExt');
    filterSize = document.getElementById('filterSize');
    filterDate = document.getElementById('filterDate');
    
    indexStatus = document.getElementById('indexStatus');
    statusText = document.getElementById('statusText');
    statsText = document.getElementById('statsText');
    progressBar = document.getElementById('progressBar');
    
    pageInfo = document.getElementById('pageInfo');
    btnFirstPage = document.getElementById('btnFirstPage');
    btnPrevPage = document.getElementById('btnPrevPage');
    btnNextPage = document.getElementById('btnNextPage');
    btnLastPage = document.getElementById('btnLastPage');
    pageSizeSelect = document.getElementById('pageSizeSelect');
    // 调试面板切换按钮
    window.btnToggleDebug = document.getElementById('btnToggleDebug');
}

function initEventListeners() {
    // 搜索相关
    btnSearch.addEventListener('click', (e) => {
        e.preventDefault();
        showDebug('搜索按钮被点击', 'info');
        performSearch();
    });
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            performSearch();
        }
    });
    
    // 实时输入搜索 - 索引模式和实时模式都支持
    searchInput.addEventListener('input', () => {
        // 清除之前的计时器
        clearTimeout(searchTimeout);
        
        const query = searchInput.value.trim();
        
        // 如果输入为空,清空结果
        if (!query) {
            currentResults = [];
            filteredResults = [];
            resultsCount.textContent = '等待输入...';
            statusText.textContent = '请输入搜索关键词';
            renderResults();
            return;
        }
        
        // 显示搜索提示
        resultsCount.textContent = `输入中...`;
        
        // 索引模式：立即搜索（100ms防抖，避免过于频繁）
        // 实时模式：稍长防抖（300ms，因为磁盘扫描较慢）
        const debounceTime = chkRealtime.checked ? 300 : 100;
        
        searchTimeout = setTimeout(() => {
            showDebug(`${chkRealtime.checked ? '实时' : '索引'}搜索自动触发: "${query}"`, 'info');
            performSearch();
        }, debounceTime);
    });
    
    btnRefresh.addEventListener('click', refreshSearch);
    btnStop.addEventListener('click', stopSearch);
    // 重建索引
    const btnRebuild = document.getElementById('btnRebuildIndex');
    if (btnRebuild) {
        btnRebuild.addEventListener('click', async () => {
            if (!confirm('确定要强制重建索引吗？这可能需要较长时间（按盘大小）。')) return;
            try {
                btnRebuild.disabled = true;
                statusText.textContent = '正在后台重建索引，请稍候...';
                showDebug('🔨 开始强制重建索引...', 'info');
                const scope = scopeSelect.value === 'all' ? null : scopeSelect.value;
                const res = await invoke('build_index', { scope });
                showDebug('🔔 ' + res, 'success');
                statusText.textContent = res;
            } catch (e) {
                console.error('重建索引失败:', e);
                showDebug('重建索引失败: ' + e, 'error');
                statusText.textContent = '重建索引失败: ' + e;
                alert('重建索引失败: ' + e);
                btnRebuild.disabled = false;
            }
            // 注意：不立即启用按钮，等待 index-rebuild-finished 事件
        });
    }
    
    // 模式切换
    chkRealtime.addEventListener('change', () => {
        searchMode = chkRealtime.checked ? 'realtime' : 'index';
        updateModeLabel();
    });

    // 调试面板切换
    if (window.btnToggleDebug) {
        window.btnToggleDebug.addEventListener('click', () => {
            const panel = document.getElementById('debugPanel');
            if (!panel) return;
            panel.style.display = panel.style.display === 'none' || !panel.style.display ? 'block' : 'none';
        });
    }
    
    // 筛选器
    filterExt.addEventListener('change', applyFilters);
    filterSize.addEventListener('change', applyFilters);
    filterDate.addEventListener('change', applyFilters);
    document.getElementById('btnClearFilter').addEventListener('click', clearFilters);
    
    // 分页
    btnFirstPage.addEventListener('click', () => goToPage(1));
    btnPrevPage.addEventListener('click', () => goToPage(currentPage - 1));
    btnNextPage.addEventListener('click', () => goToPage(currentPage + 1));
    btnLastPage.addEventListener('click', () => goToPage(Math.ceil(filteredResults.length / pageSize)));
    pageSizeSelect.addEventListener('change', () => {
        pageSize = parseInt(pageSizeSelect.value);
        goToPage(1);
    });
    
    // 工具按钮
    document.getElementById('btnSelectAll').addEventListener('click', selectAll);
    document.getElementById('btnCopyPath').addEventListener('click', copySelectedPaths);
    document.getElementById('btnLocate').addEventListener('click', locateSelected);
    document.getElementById('btnDelete').addEventListener('click', deleteSelected);
    document.getElementById('btnExport').addEventListener('click', exportResults);
    document.getElementById('btnSync').addEventListener('click', syncIndex);
    
    // 全选复选框
    document.getElementById('chkSelectAll').addEventListener('change', (e) => {
        const checkboxes = resultsBody.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(cb => cb.checked = e.target.checked);
    });
    
    // 键盘快捷键
    document.addEventListener('keydown', handleKeyboard);
}

async function loadDriveList() {
    try {
        const drives = await invoke('get_all_drives');
        scopeSelect.innerHTML = '<option value="all">所有磁盘 (全盘)</option>';
        drives.forEach(drive => {
            const option = document.createElement('option');
            option.value = drive;
            option.textContent = `${drive} 盘`;
            scopeSelect.appendChild(option);
        });
        
        // 保存驱动器列表供监控使用
        window.availableDrives = drives;
    } catch (error) {
        console.error('加载驱动器列表失败:', error);
    }
}

async function startFileMonitoring() {
    try {
        if (!window.availableDrives || window.availableDrives.length === 0) {
            showDebug('⚠️ 没有可用驱动器,跳过文件监控', 'warning');
            return;
        }
        
        showDebug(`👁️ 启动文件监控: ${window.availableDrives.join(', ')}`, 'info');
        await invoke('start_file_monitoring', { 
            drives: window.availableDrives 
        });
        showDebug('✅ 文件监控已启动', 'success');
    } catch (error) {
        showDebug('⚠️ 文件监控启动失败: ' + error, 'error');
        console.error('文件监控启动失败:', error);
    }
}

async function checkIndexStatus() {
    console.log('[DEBUG] 检查索引状态...');
    try {
        const scope = scopeSelect.value;
        const status = await invoke('check_index_status', { 
            scope: scope === 'all' ? null : scope 
        });
        
        console.log('[DEBUG] 索引状态:', status);
        
        // 显示详细状态
        if (status.loading_count > 0) {
            indexStatus.textContent = `正在加载索引... (${status.ready_count}/${status.total_drives})`;
            indexStatus.style.color = '#ffa500';
            searchMode = 'realtime'; // 加载期间使用实时搜索
        } else if (status.is_ready && status.total_files > 0) {
            indexStatus.textContent = `索引就绪 (${status.total_files.toLocaleString()} 文件)`;
            indexStatus.style.color = '#8cc84b';
            searchMode = 'index';
        } else {
            indexStatus.textContent = '索引未初始化';
            indexStatus.style.color = '#ff4444';
            searchMode = 'realtime';
        }
        
        console.log('[DEBUG] 搜索模式设置为:', searchMode);
        
        return status;
    } catch (error) {
        console.error('[ERROR] 检查索引状态失败:', error);
        indexStatus.textContent = '索引检查失败';
        indexStatus.style.color = '#ff6b6b';
        searchMode = 'realtime'; // 失败时使用实时搜索
    }
}

function updateModeLabel() {
    const modeText = searchMode === 'index' ? '索引搜索' : '实时搜索';
    // 可以添加模式标签显示
}

async function performSearch() {
    showDebug('🚀 performSearch 被调用', 'info');
    
    if (!streamListenerReady) {
        alert('⚠️ 警告：流式监听器未就绪！');
    }
    
    const query = searchInput.value.trim();
    showDebug(`搜索: "${query}" 模式: ${searchMode}`, 'info');
    
    // 重置流式统计
    streamBatchCount = 0;
    streamTotalReceived = 0;
    
    // 清除之前的超时定时器（允许新搜索打断旧搜索）
    if (searchTimeoutId) {
        clearTimeout(searchTimeoutId);
    }
    
    isSearching = true;
    btnSearch.disabled = true;
    btnStop.disabled = false;
    statusText.textContent = searchMode === 'realtime' ? '实时搜索中...' : '索引搜索中...';
    if (progressBar) progressBar.style.display = 'inline-block';
    
    // 设置搜索超时：5秒后如果仍未收到search-complete，自动重置搜索状态
    searchTimeoutId = setTimeout(() => {
        showDebug('⚠️ 搜索超时（5秒无响应），已重置搜索状态', 'error');
        isSearching = false;
        btnSearch.disabled = false;
        btnStop.disabled = true;
        if (progressBar) progressBar.style.display = 'none';
    }, 5000);
    
    // 清空之前的结果
    currentResults = [];
    filteredResults = [];
    resultsBody.innerHTML = '<tr class="empty-row"><td colspan="5" class="empty-state">搜索中...</td></tr>';
    resultsCount.textContent = '搜索中...';
    
    try {
        const scope = scopeSelect.value;
        // 发起后端调用但不等待完成，改为依赖事件驱动
        
        if (searchMode === 'realtime') {
            // 实时搜索（流式更新，边搜边显示）
            console.log('[STREAM] 开始流式实时搜索...');
            invoke('realtime_search', { 
                query: query || '',
                scope: scope === 'all' ? null : scope
            }).catch(error => {
                console.error('实时搜索失败:', error);
                showDebug('实时搜索失败: ' + error, 'error');
                statusText.textContent = '实时搜索失败: ' + error;
                // 清除超时并重置状态
                if (searchTimeoutId) clearTimeout(searchTimeoutId);
                isSearching = false;
                btnSearch.disabled = false;
                btnStop.disabled = true;
                if (progressBar) progressBar.style.display = 'none';
            });
        } else {
            // 索引搜索（也支持流式输出）
            if (!query) {
                alert('索引搜索需要输入关键词！（或切换到实时模式）');
                isSearching = false;
                btnSearch.disabled = false;
                btnStop.disabled = true;
                if (progressBar) progressBar.style.display = 'none';
                return;
            }
            console.log('[STREAM] 开始流式索引搜索...', { query, scope });
            // 索引搜索也使用流式输出
            invoke('search_files', { 
                query,
                scope: scope === 'all' ? null : scope
            }).catch(error => {
                console.error('索引搜索失败:', error);
                showDebug('索引搜索失败: ' + error, 'error');
                statusText.textContent = '索引搜索失败: ' + error;
                // 清除超时并重置状态
                if (searchTimeoutId) clearTimeout(searchTimeoutId);
                isSearching = false;
                btnSearch.disabled = false;
                btnStop.disabled = true;
                if (progressBar) progressBar.style.display = 'none';
            });
        }
    } catch (error) {
        console.error('搜索失败:', error);
        statusText.textContent = `搜索失败: ${error}`;
        alert('搜索失败: ' + error);
    } finally {
        // 保持搜索状态，直到收到 search-complete 事件
    }
}

async function applyFilters() {
    // 使用 Promise 包装，避免阻塞UI
    await new Promise(resolve => setTimeout(resolve, 0));
    let results = [...currentResults];
    
    // 扩展名筛选
    if (filterExt.value) {
        const ext = filterExt.value.toLowerCase();
        results = results.filter(r => r.fullpath.toLowerCase().endsWith(ext));
    }
    
    // 大小筛选
    if (filterSize.value) {
        const sizeFilter = filterSize.value;
        results = results.filter(r => {
            const sizeMB = r.size / (1024 * 1024);
            if (sizeFilter === '>1mb') return sizeMB > 1;
            if (sizeFilter === '>10mb') return sizeMB > 10;
            if (sizeFilter === '>100mb') return sizeMB > 100;
            if (sizeFilter === '>500mb') return sizeMB > 500;
            if (sizeFilter === '>1gb') return sizeMB > 1024;
            return true;
        });
    }
    
    // 时间筛选
    if (filterDate.value) {
        const now = Date.now();
        const dateFilter = filterDate.value;
        results = results.filter(r => {
            const mtime = r.mtime * 1000; // 转换为毫秒
            const days = (now - mtime) / (1000 * 60 * 60 * 24);
            if (dateFilter === '1d') return days <= 1;
            if (dateFilter === '3d') return days <= 3;
            if (dateFilter === '7d') return days <= 7;
            if (dateFilter === '30d') return days <= 30;
            if (dateFilter === '365d') return days <= 365;
            return true;
        });
    }
    
    filteredResults = results;
    resultsCount.textContent = `找到 ${filteredResults.length} 个结果`;
    
    // 更新筛选状态提示
    const filterStatus = document.getElementById('filterStatus');
    const activeFilters = [];
    if (filterExt.value) activeFilters.push(`格式:${filterExt.value}`);
    if (filterSize.value) activeFilters.push(`大小:${filterSize.value}`);
    if (filterDate.value) activeFilters.push(`时间:${filterDate.value}`);
    filterStatus.textContent = activeFilters.length ? `已筛选: ${activeFilters.join(', ')}` : '';
    
    goToPage(1);
}

function clearFilters() {
    filterExt.value = '';
    filterSize.value = '';
    filterDate.value = '';
    applyFilters();
}

function goToPage(page) {
    const totalPages = Math.ceil(filteredResults.length / pageSize);
    console.log(`[goToPage] page=${page}, totalPages=${totalPages}, filteredResults.length=${filteredResults.length}`);
    showDebug(`goToPage(${page}), 总页数=${totalPages}, 结果数=${filteredResults.length}`, 'info');
    
    if (page < 1 || page > totalPages) {
        showDebug(`页码无效: page=${page}, totalPages=${totalPages}`, 'error');
        return;
    }
    
    currentPage = page;
    renderResults();
    updatePagination();
}

function renderResults() {
    showDebug(`renderResults 被调用: filteredResults.length=${filteredResults.length}, currentPage=${currentPage}`, 'info');
    
    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageResults = filteredResults.slice(start, end);
    
    if (pageResults.length === 0) {
        resultsBody.innerHTML = '<tr class="empty-row"><td colspan="5" class="empty-state">没有找到匹配的文件</td></tr>';
        document.getElementById('pagination').style.display = 'none';
        return;
    }
    
    document.getElementById('pagination').style.display = 'flex';
    
    const query = searchInput ? searchInput.value.trim() : '';
    
    resultsBody.innerHTML = pageResults.map((result, idx) => {
        const filename = result.fullpath.split('\\').pop();
        const path = result.fullpath.substring(0, result.fullpath.lastIndexOf('\\'));
        const size = formatSize(result.size);
        const mtime = formatDate(result.mtime);
        
        // 高亮关键词
        const highlightedFilename = highlightText(filename, query);
        const highlightedPath = highlightText(path, query);
        
        return `
            <tr data-path="${escapeHtml(result.fullpath)}" onclick="selectRow(this, event)">
                <td class="cell-select"><input type="checkbox" onclick="event.stopPropagation()"></td>
                <td class="cell-filename" title="${escapeHtml(filename)}">${highlightedFilename}</td>
                <td class="cell-size" title="${escapeHtml(size)}">${size}</td>
                <td class="cell-date" title="${escapeHtml(mtime)}">${mtime}</td>
                <td class="cell-path" title="${escapeHtml(path)}">${highlightedPath}</td>
            </tr>
        `;
    }).join('');
}

function updatePagination() {
    const totalPages = Math.ceil(filteredResults.length / pageSize);
    pageInfo.textContent = `第 ${currentPage} / ${totalPages} 页`;
    
    btnFirstPage.disabled = currentPage === 1;
    btnPrevPage.disabled = currentPage === 1;
    btnNextPage.disabled = currentPage === totalPages;
    btnLastPage.disabled = currentPage === totalPages;
}

function selectRow(row, event) {
    if (event.ctrlKey) {
        row.classList.toggle('selected');
    } else if (event.shiftKey) {
        // TODO: 实现 Shift 多选
        row.classList.add('selected');
    } else {
        document.querySelectorAll('tbody tr').forEach(r => r.classList.remove('selected'));
        row.classList.add('selected');
    }
}

function selectAll() {
    document.querySelectorAll('tbody tr').forEach(r => r.classList.add('selected'));
    document.querySelectorAll('tbody input[type="checkbox"]').forEach(cb => cb.checked = true);
}

async function copySelectedPaths() {
    const selected = getSelectedPaths();
    if (selected.length === 0) {
        alert('请先选择要复制的文件');
        return;
    }
    
    try {
        await invoke('copy_to_clipboard', { text: selected.join('\n') });
        statusText.textContent = `已复制 ${selected.length} 个路径`;
    } catch (error) {
        console.error('复制失败:', error);
        alert('复制失败: ' + error);
    }
}

async function locateSelected() {
    const selected = getSelectedPaths();
    if (selected.length === 0) {
        alert('请先选择要定位的文件');
        return;
    }
    
    try {
        await invoke('locate_file', { path: selected[0] });
    } catch (error) {
        console.error('定位失败:', error);
        alert('定位失败: ' + error);
    }
}

async function deleteSelected() {
    const selected = getSelectedPaths();
    if (selected.length === 0) {
        alert('请先选择要删除的文件');
        return;
    }
    
    if (!confirm(`确定要删除选中的 ${selected.length} 个文件吗？\n\n注意：此操作将文件移至回收站。`)) {
        return;
    }
    
    let deleted = 0;
    for (const path of selected) {
        try {
            await invoke('delete_file', { path });
            deleted++;
        } catch (error) {
            console.error(`删除失败 ${path}:`, error);
        }
    }
    
    statusText.textContent = `已删除 ${deleted}/${selected.length} 个文件`;
    
    // 立即重新检查索引状态并更新UI
    await checkIndexStatus();
    
    // 稍后重新搜索以从结果中移除已删除文件
    setTimeout(() => {
        if (currentResults.length > 0) {
            performSearch();
        }
    }, 500);
}

async function exportResults() {
    if (filteredResults.length === 0) {
        alert('没有可导出的结果');
        return;
    }
    
    try {
        await invoke('export_csv', { results: filteredResults });
        statusText.textContent = `已导出 ${filteredResults.length} 条记录`;
    } catch (error) {
        console.error('导出失败:', error);
        alert('导出失败: ' + error);
    }
}

async function syncIndex() {
    if (isSearching) return;
    
    const confirm = window.confirm('即将构建索引，这可能需要10-60秒。是否继续？');
    if (!confirm) return;
    
    isSearching = true;
    statusText.textContent = '正在构建索引（首次约需10-60秒）...';
    if (progressBar) progressBar.style.display = 'inline-block';
    btnSync.disabled = true;
    
    try {
        const scope = scopeSelect.value;
        const result = await invoke('build_index', { 
            scope: scope === 'all' ? null : scope 
        });
        statusText.textContent = '✅ ' + result;
        // 重新检查索引状态，更新UI
        await checkIndexStatus();
        alert(result);
    } catch (error) {
        console.error('构建索引失败:', error);
        statusText.textContent = '❌ 构建索引失败';
        alert('构建索引失败: ' + error);
    } finally {
        isSearching = false;
        btnSync.disabled = false;
        if (progressBar) progressBar.style.display = 'none';
    }
}

function refreshSearch() {
    performSearch();
}

function stopSearch() {
    // TODO: 实现搜索终止
    isSearching = false;
    btnSearch.disabled = false;
    btnStop.disabled = true;
    statusText.textContent = '已停止';
}

function handleKeyboard(e) {
    // F5: 刷新
    if (e.key === 'F5') {
        e.preventDefault();
        refreshSearch();
    }
    // Esc: 清空搜索
    else if (e.key === 'Escape') {
        searchInput.value = '';
        searchInput.focus();
    }
    // Ctrl+A: 全选
    else if (e.ctrlKey && e.key === 'a') {
        e.preventDefault();
        selectAll();
    }
    // Delete: 删除选中
    else if (e.key === 'Delete') {
        deleteSelected();
    }
}

function getSelectedPaths() {
    const selected = [];
    document.querySelectorAll('tbody tr.selected').forEach(row => {
        selected.push(row.dataset.path);
    });
    return selected;
}

function highlightText(text, query) {
    if (!query) return escapeHtml(text);
    
    const regex = new RegExp(`(${escapeRegex(query)})`, 'gi');
    return escapeHtml(text).replace(regex, '<mark>$1</mark>');
}

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
}

function formatDate(timestamp) {
    const date = new Date(timestamp * 1000);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hour = String(date.getHours()).padStart(2, '0');
    const minute = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hour}:${minute}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function showError(message) {
    // 可以实现更美观的错误提示
    console.error(message);
}

function updateUI() {
    // 更新UI状态
}
