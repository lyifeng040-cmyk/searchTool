// ============= 状态管理 =============
const state = {
    results: [],
    selectedIndex: -1,
    searchMode: 'index', // 默认使用索引搜索
    isIndexBuilding: false,
};

const DOM = {
    searchInput: document.getElementById('searchInput'),
    resultsList: document.getElementById('resultsList'),
    modeLabel: document.getElementById('modeLabel'),
    deleteBtn: document.getElementById('deleteBtn'),
    previewInfo: document.getElementById('previewInfo'),
};

// ============= 搜索函数 =============
async function performSearch(keywords) {
    if (!keywords.trim()) {
        DOM.resultsList.innerHTML = '<div class="empty-state">输入关键词开始搜索...</div>';
        state.results = [];
        state.selectedIndex = -1;
        updatePreview();
        return;
    }

    // 显示搜索中状态
    if (state.isIndexBuilding) {
        DOM.resultsList.innerHTML = '<div class="empty-state">⏳ 正在构建索引，请稍候...</div>';
        return;
    }

    DOM.resultsList.innerHTML = '<div class="empty-state">🔍 搜索中...</div>';
    state.isIndexBuilding = true;

    try {
        // 解析关键词和过滤器
        const keywordList = [];
        const filters = {
            ext: null,
            size_min: null,
            size_max: null,
            date_modified: null,
        };

        const parts = keywords.toLowerCase().split(/\s+/);
        for (const part of parts) {
            if (part.startsWith('ext:')) {
                filters.ext = [part.substring(4)];
            } else if (part.startsWith('size:')) {
                parseSizeFilter(part.substring(5), filters);
            } else if (part.startsWith('dm:')) {
                filters.date_modified = part.substring(3);
            } else if (part.length > 0) {
                keywordList.push(part);
            }
        }

        const request = {
            keywords: keywordList,
            filters,
            mode: state.searchMode === 'index' ? 'index' : 'realtime',
        };

        // 调用 Tauri 命令
        const results = await window.__TAURI__.tauri.invoke('search_files', { request });
        
        state.results = results;
        state.selectedIndex = -1;
        state.isIndexBuilding = false;
        renderResults();
        updatePreview();
    } catch (error) {
        console.error('搜索错误:', error);
        state.isIndexBuilding = false;
        DOM.resultsList.innerHTML = `<div class="empty-state">搜索出错: ${error}</div>`;
    }
}

function parseSizeFilter(sizeStr, filters) {
    // 匹配 >10mb, <5kb, =100b 等
    const match = sizeStr.match(/^([<>=]{1,2})(\d+)([kmg]?b)?$/i);
    if (!match) return;
    
    const operator = match[1];
    const value = parseInt(match[2]);
    const unit = match[3] || 'b';
    
    const multipliers = { b: 1, k: 1024, m: 1024 * 1024, g: 1024 * 1024 * 1024 };
    const bytes = value * multipliers[unit.toLowerCase()[0] || 'b'];
    
    if (operator.includes('>')) filters.size_min = bytes;
    if (operator.includes('<')) filters.size_max = bytes;
    if (operator === '=') {
        filters.size_min = bytes;
        filters.size_max = bytes;
    }
}

// ============= 渲染结果 =============
function renderResults() {
    if (state.results.length === 0) {
        DOM.resultsList.innerHTML = '<div class="empty-state">未找到匹配的文件</div>';
        return;
    }

    // 获取搜索关键词用于高亮
    const keywords = DOM.searchInput.value
        .toLowerCase()
        .split(/\s+/)
        .filter(k => k && !k.startsWith('ext:') && !k.startsWith('size:') && !k.startsWith('dm:'));

    const html = state.results.map((result, index) => {
        const highlightedFilename = highlightKeywords(result.filename, keywords);
        return `
        <div class="result-item ${index === state.selectedIndex ? 'selected' : ''}" data-index="${index}">
            <span class="result-icon">${getFileIcon(result.filename)}</span>
            <div class="result-info">
                <div class="result-filename">${highlightedFilename}</div>
                <div class="result-path">${escapeHtml(result.fullpath)}</div>
            </div>
        </div>
    `;
    }).join('');

    DOM.resultsList.innerHTML = html;

    // 绑定点击事件
    document.querySelectorAll('.result-item').forEach(item => {
        item.addEventListener('click', () => {
            const index = parseInt(item.dataset.index);
            selectItem(index);
        });
    });
}

function renderSelectedItem() {
    document.querySelectorAll('.result-item').forEach((item, index) => {
        if (index === state.selectedIndex) {
            item.classList.add('selected');
            item.scrollIntoView({ block: 'nearest' });
        } else {
            item.classList.remove('selected');
        }
    });
}

function selectItem(index) {
    if (index >= 0 && index < state.results.length) {
        state.selectedIndex = index;
        renderSelectedItem();
        updatePreview();
    }
}

function updatePreview() {
    if (state.selectedIndex >= 0 && state.selectedIndex < state.results.length) {
        const result = state.results[state.selectedIndex];
        const sizeStr = formatFileSize(result.size);
        const drive = getDrive(result.fullpath);
        DOM.previewInfo.textContent = `📄 ${result.filename} | ${drive} | ${sizeStr}`;
    } else {
        DOM.previewInfo.textContent = '';
    }
}

function getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const icons = {
        'pdf': '📕', 'doc': '📘', 'docx': '📘', 'xls': '📗', 'xlsx': '📗',
        'txt': '📄', 'md': '📝', 'py': '🐍', 'js': '⚙️', 'json': '📋',
        'png': '🖼️', 'jpg': '🖼️', 'jpeg': '🖼️', 'gif': '🖼️',
        'zip': '📦', 'rar': '📦', '7z': '📦',
        'mp3': '🎵', 'mp4': '🎬', 'avi': '🎬', 'mov': '🎬',
    };
    return icons[ext] || '📄';
}

function formatFileSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let size = bytes;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex++;
    }
    return `${size.toFixed(2)} ${units[unitIndex]}`;
}

function getDrive(fullpath) {
    // Windows: C:\, D:\  Mac/Linux: /
    const match = fullpath.match(/^([A-Z]:|\/)/);
    return match ? match[1] : '/';
}

function escapeHtml(text) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// ============= 键盘事件 =============
DOM.searchInput.addEventListener('keydown', async (e) => {
    // 在搜索框中的事件
    if (e.key === 'Enter') {
        e.preventDefault();
        performSearch(e.target.value);
        if (state.results.length > 0) {
            state.selectedIndex = 0;
            renderSelectedItem();
            updatePreview();
        }
        return;
    }

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectItem(Math.min(state.results.length - 1, state.selectedIndex + 1));
        return;
    }

    if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectItem(Math.max(0, state.selectedIndex - 1));
        return;
    }

    if (e.key === 'Escape') {
        e.preventDefault();
        if (e.target.value.trim()) {
            e.target.value = '';
            state.results = [];
            state.selectedIndex = -1;
            DOM.resultsList.innerHTML = '<div class="empty-state">输入关键词开始搜索...</div>';
            updatePreview();
        } else {
            window.close();
        }
        return;
    }

    // F5: 刷新搜索
    if (e.key === 'F5') {
        e.preventDefault();
        if (e.target.value.trim()) {
            performSearch(e.target.value);
        }
        return;
    }
});

// 全局键盘快捷键（包括结果列表）
document.addEventListener('keydown', async (e) => {
    // 如果焦点在搜索框，某些快捷键由搜索框处理
    const isSearchBoxFocused = document.activeElement === DOM.searchInput;

    // F5: 刷新搜索
    if (e.key === 'F5' && !isSearchBoxFocused) {
        e.preventDefault();
        if (DOM.searchInput.value.trim()) {
            performSearch(DOM.searchInput.value);
        }
    }

    // Enter in results list: 打开选中文件
    if (e.key === 'Enter' && !isSearchBoxFocused && state.selectedIndex >= 0) {
        e.preventDefault();
        await openFile(state.results[state.selectedIndex].fullpath);
    }

    // ↑/↓: 选择上下文件
    if (e.key === 'ArrowUp' && !isSearchBoxFocused) {
        e.preventDefault();
        selectItem(Math.max(0, state.selectedIndex - 1));
    }

    if (e.key === 'ArrowDown' && !isSearchBoxFocused) {
        e.preventDefault();
        selectItem(Math.min(state.results.length - 1, state.selectedIndex + 1));
    }

    // Ctrl+A: 全选
    if ((e.ctrlKey || e.metaKey) && e.key === 'a' && !isSearchBoxFocused) {
        e.preventDefault();
        state.allSelected = !state.allSelected;
        const items = document.querySelectorAll('.result-item');
        items.forEach(item => {
            if (state.allSelected) {
                item.classList.add('selected');
            } else {
                item.classList.remove('selected');
            }
        });
    }

    // Ctrl/Cmd + C: 复制路径
    if ((e.ctrlKey || e.metaKey) && e.key === 'c' && !e.shiftKey) {
        e.preventDefault();
        if (state.selectedIndex >= 0) {
            const path = state.results[state.selectedIndex].fullpath;
            navigator.clipboard.writeText(path);
        }
    }

    // Ctrl/Cmd + Shift + C: 复制文件
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
        e.preventDefault();
        if (state.selectedIndex >= 0) {
            const file = state.results[state.selectedIndex];
            // 这需要在 Rust 后端中实现
            console.log('Copy file command:', file.fullpath);
        }
    }

    // Ctrl/Cmd + L: 定位文件
    if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
        e.preventDefault();
        if (state.selectedIndex >= 0) {
            await locateFile(state.results[state.selectedIndex].fullpath);
        }
    }

    // Ctrl/Cmd + E: 导出结果
    if ((e.ctrlKey || e.metaKey) && e.key === 'e' && !isSearchBoxFocused) {
        e.preventDefault();
        exportResults();
    }

    // Delete: 删除文件
    if (e.key === 'Delete' && !isSearchBoxFocused) {
        e.preventDefault();
        if (state.selectedIndex >= 0) {
            const file = state.results[state.selectedIndex];
            if (confirm(`确定要删除 ${file.filename} 吗？`)) {
                try {
                    await window.__TAURI__.tauri.invoke('delete_file', { path: file.fullpath });
                    state.results.splice(state.selectedIndex, 1);
                    state.selectedIndex = Math.min(state.selectedIndex, state.results.length - 1);
                    renderResults();
                    updatePreview();
                } catch (error) {
                    alert(`删除失败: ${error}`);
                }
            }
        }
    }

    // Ctrl/Cmd + T: 在终端打开
    if ((e.ctrlKey || e.metaKey) && e.key === 't') {
        e.preventDefault();
        if (state.selectedIndex >= 0) {
            const path = state.results[state.selectedIndex].fullpath;
            // 提取目录路径
            const dirPath = path.substring(0, path.lastIndexOf('\\'));
            await openTerminal(dirPath);
        }
    }
});

DOM.searchInput.addEventListener('input', (e) => {
    performSearch(e.target.value);
});

DOM.deleteBtn.addEventListener('click', async () => {
    if (state.selectedIndex >= 0) {
        const file = state.results[state.selectedIndex];
        if (confirm(`确定要删除 ${file.filename} 吗？`)) {
            try {
                await window.__TAURI__.tauri.invoke('delete_file', { path: file.fullpath });
                state.results.splice(state.selectedIndex, 1);
                state.selectedIndex = Math.min(state.selectedIndex, state.results.length - 1);
                renderResults();
                updatePreview();
            } catch (error) {
                alert(`删除失败: ${error}`);
            }
        }
    }
});

// ============= Tauri 命令 =============
async function openFile(path) {
    try {
        await window.__TAURI__.tauri.invoke('open_file', { path });
    } catch (error) {
        alert(`打开失败: ${error}`);
    }
}

async function locateFile(path) {
    try {
        await window.__TAURI__.tauri.invoke('locate_file', { path });
    } catch (error) {
        alert(`定位失败: ${error}`);
    }
}

async function openTerminal(dirPath) {
    try {
        // 通过命令行打开终端（需要在 commands.rs 中添加支持）
        // 简化版：直接在当前目录打开文件管理器
        await locateFile(dirPath);
    } catch (error) {
        alert(`打开终端失败: ${error}`);
    }
}

// 导出搜索结果为 CSV
function exportResults() {
    if (state.results.length === 0) {
        alert('没有搜索结果可导出');
        return;
    }

    // 生成 CSV 内容
    const headers = ['文件名', '完整路径', '大小 (字节)', '大小 (可读)', '修改时间'];
    const rows = state.results.map(result => [
        result.filename,
        result.fullpath,
        result.size,
        formatFileSize(result.size),
        new Date(result.mtime * 1000).toLocaleString()
    ]);

    let csv = headers.join(',') + '\n';
    rows.forEach(row => {
        csv += row.map(cell => `"${cell}"`).join(',') + '\n';
    });

    // 下载 CSV 文件
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `search_results_${Date.now()}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// 高亮显示关键词
function highlightKeywords(text, keywords) {
    if (!keywords || keywords.length === 0) {
        return escapeHtml(text);
    }

    let highlighted = escapeHtml(text);
    keywords.forEach(keyword => {
        const regex = new RegExp(`(${keyword})`, 'gi');
        highlighted = highlighted.replace(regex, '<mark>$1</mark>');
    });
    return highlighted;
}

// ============= 初始化 =============
document.addEventListener('DOMContentLoaded', () => {
    console.log('SearchTool 已初始化');
    // 聚焦搜索框
    DOM.searchInput.focus();
});
