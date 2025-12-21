# test_interactive.py
import os
import re
import json
import logging
from pathlib import Path
import sqlite3
import sys

# ==================== 从您的代码中复制的、未经修改的后端部分 ====================

LOG_DIR = Path.home() / ".filesearch"
LOG_DIR.mkdir(exist_ok=True)

SKIP_DIRS_LOWER = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "appdata", "boot",
    "node_modules", ".git", "__pycache__", "site-packages", "sys",
    "recovery", "config.msi", "$windows.~bt", "$windows.~ws",
    "cache", "caches", "temp", "tmp", "logs", "log",
    ".vscode", ".idea", ".vs", "obj", "bin", "debug", "release",
    "packages", ".nuget", "bower_components",
}

SKIP_EXTS = {
    ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini", ".lsp_bak",
    ".cuix", ".arx", ".crx", ".fx", ".dbx", ".kid", ".ico", ".rz",
    ".dll", ".sys", ".tmp", ".log", ".dat", ".db", ".pdb", ".obj",
    ".pyc", ".class", ".cache", ".lock",
}

def is_in_allowed_paths(path_lower, allowed_paths_lower):
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + '\\') or path_lower == ap:
            return True
    return False

def should_skip_path(path_lower, allowed_paths_lower=None):
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False
    if any(part in SKIP_DIRS_LOWER for part in path_lower.replace('/', '\\').split('\\')):
        return True
    return False

def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    if name_lower in SKIP_DIRS_LOWER:
        return True
    return False

class IndexManager:
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = str(LOG_DIR / "index.db")
        else:
            self.db_path = db_path
        self.conn = None
        self.has_fts = False
        self._init_db()

    def _init_db(self):
        try:
            self.conn = sqlite3.connect(self.db_path)
            cursor = self.conn.cursor()
            try:
                cursor.execute("CREATE VIRTUAL TABLE fts_test USING fts5(content)")
                cursor.execute("DROP TABLE fts_test")
                self.has_fts = True
            except sqlite3.OperationalError:
                self.has_fts = False
        except Exception as e:
            print(f"数据库初始化失败: {e}")
            self.conn = None

    def search(self, keywords, scope_targets, limit=50000):
        if not self.conn:
            print("错误：数据库未连接")
            return None
        try:
            cursor = self.conn.cursor()
            if self.has_fts and keywords:
                fts_query = " AND ".join(f'"{kw}"' for kw in keywords)
                sql = "SELECT f.filename, f.full_path, f.size, f.mtime, f.is_dir FROM files f INNER JOIN files_fts fts ON f.id = fts.rowid WHERE files_fts MATCH ? LIMIT ?"
                params = (fts_query, limit)
            else:
                wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                sql = f"SELECT filename, full_path, size, mtime, is_dir FROM files WHERE {wheres} LIMIT ?"
                params = tuple([f"%{kw}%" for kw in keywords] + [limit])

            print(f"\n[调试信息] 执行SQL: {sql}")
            print(f"[调试信息] SQL参数: {params}")
            
            raw_results = list(cursor.execute(sql, params))
            print(f"[调试信息] 数据库查询返回 {len(raw_results)} 条原始结果。")

            filtered = []
            scope_targets_lower = [t.lower().rstrip("\\") for t in scope_targets] if scope_targets else None
            print(f"[调试信息] 内存过滤范围: {scope_targets_lower}")

            for row in raw_results:
                path_lower = row[1].lower()
                
                if scope_targets_lower and not is_in_allowed_paths(path_lower, scope_targets_lower):
                    continue
                
                if should_skip_path(path_lower, scope_targets_lower):
                    continue
                
                name_lower = row[0].lower()
                if row[4]:
                    if should_skip_dir(name_lower, path_lower, scope_targets_lower):
                        continue
                else:
                    if os.path.splitext(name_lower)[1] in SKIP_EXTS:
                        continue
                
                filtered.append(row)
            
            print(f"[调试信息] 内存过滤后剩余 {len(filtered)} 条结果。")
            return filtered
        except Exception as e:
            print(f"[调试信息] 搜索方法出错: {e}")
            return None

# ==================== 交互式测试代码 ====================

def interactive_test():
    print("--- 启动交互式搜索测试工具 ---")
    print("您的索引数据库路径为:", str(LOG_DIR / "index.db"))
    
    try:
        im = IndexManager()
        if not im.conn:
            print("无法连接到数据库，测试中止。")
            return
    except Exception as e:
        print(f"创建 IndexManager 失败: {e}")
        return

    print("\n测试开始！输入 'exit' 或按 Ctrl+C 退出。")
    
    while True:
        try:
            # 1. 输入关键词
            keyword_str = input("\n请输入搜索关键词 (多个词用空格隔开): ")
            if keyword_str.lower() == 'exit':
                break
            test_keywords = keyword_str.lower().split()

            # 2. 输入搜索范围
            scope_str = input("请输入搜索范围 (例如 D:\\ 或 E:\\my_folder, 直接回车代表所有磁盘): ")
            if scope_str.lower() == 'exit':
                break
            
            if not scope_str:
                # 模拟 "所有磁盘" 的情况
                test_scope = [f"{d}:\\" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
                print(f"范围为空，将模拟搜索所有磁盘: {test_scope}")
            else:
                test_scope = [scope_str]

            print("-" * 20)
            print(f"开始测试: keywords={test_keywords}, scope={test_scope}")
            
            # 3. 调用 search 方法
            results = im.search(keywords=test_keywords, scope_targets=test_scope)
            
            # 4. 打印结果
            if results is not None:
                print(f"\n[最终结果] 搜索完成，共找到 {len(results)} 条结果。")
                if results:
                    print("前 10 条结果示例:")
                    for r in results[:10]:
                        print(f"  - {r[1]}") # 打印完整路径
            else:
                print("\n[最终结果] 搜索方法返回了 None，可能在执行过程中出现了错误。")
            print("-" * 20)

        except KeyboardInterrupt:
            print("\n测试退出。")
            break
        except Exception as e:
            print(f"\n出现意外错误: {e}")

if __name__ == "__main__":
    interactive_test()
