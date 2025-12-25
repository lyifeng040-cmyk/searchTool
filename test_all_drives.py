"""测试全盘 Rust 搜索"""
import sys
sys.path.insert(0, '.')
from filesearch.core.rust_search import get_search_engine
import time
import string
import os

print("初始化 Rust 引擎（全盘）...")
engine = get_search_engine()

# 获取所有可用盘符
available_drives = []
for drive in string.ascii_uppercase:
    if os.path.exists(f"{drive}:\\"):
        available_drives.append(drive)
        print(f"检测到盘符: {drive}")

# 初始化所有盘符
for drive in available_drives:
    print(f"\n初始化 {drive} 盘...")
    if engine.init_drive(drive):
        time.sleep(2)  # 等待导入
        results = engine.search('', drive, max_results=100000)
        print(f"  {drive} 盘索引有 {len(results)} 个文件")
    else:
        print(f"  {drive} 盘初始化失败")

# 测试搜索"华润"（应该能在 F 盘找到）
print("\n\n测试搜索'华润'...")
all_results = []
for drive in available_drives:
    results = engine.search('华', drive, max_results=10000)
    # 过滤包含"润"的
    filtered = [r for r in results if '华' in r['name'] and '润' in r['name']]
    if filtered:
        print(f"{drive} 盘找到 {len(filtered)} 个包含'华润'的文件")
        all_results.extend(filtered)

print(f"\n总共找到 {len(all_results)} 个'华润'文件")
if all_results:
    print("\n前 5 个结果:")
    for item in all_results[:5]:
        print(f"  {item['name']}")
        print(f"    {item['path']}")
