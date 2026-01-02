#!/usr/bin/env python3
"""
强制重建 Rust 搜索索引
删除所有驱动器上的旧 .search_index.bin 文件，下次搜索时自动重建
"""

import os
import sys

def rebuild_index():
    """删除所有驱动器的 Rust 索引文件"""
    # 检测所有逻辑驱动器
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive_path = f"{letter}:\\"
        if os.path.exists(drive_path):
            drives.append(letter)
    
    print(f"🔍 检测到以下驱动器: {', '.join(drives)}")
    
    # 删除索引文件
    deleted = []
    not_found = []
    
    for drive in drives:
        index_file = f"{drive}:\\.search_index.bin"
        if os.path.exists(index_file):
            try:
                os.remove(index_file)
                deleted.append(drive)
                print(f"✅ 已删除 {index_file}")
            except Exception as e:
                print(f"❌ 删除 {index_file} 失败: {e}")
        else:
            not_found.append(drive)
    
    print("\n" + "="*60)
    if deleted:
        print(f"✅ 已删除 {len(deleted)} 个索引文件: {', '.join(f'{d}盘' for d in deleted)}")
    else:
        print("⚠️ 没有找到任何索引文件")
    
    if not_found:
        print(f"ℹ️ 以下驱动器没有索引文件: {', '.join(f'{d}盘' for d in not_found)}")
    
    print("\n📊 下次搜索时，程序将自动重建索引（包含正确的元数据）")
    print("   这可能需要几秒到几十秒，具体取决于文件数量")

if __name__ == "__main__":
    rebuild_index()
