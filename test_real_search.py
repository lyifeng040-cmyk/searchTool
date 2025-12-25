"""测试实际搜索流程"""
import sys
sys.path.insert(0, '.')

# 模拟主程序的搜索流程
from filesearch.core.index_manager import IndexManager
from filesearch.config import ConfigManager

print("初始化索引管理器...")
config = ConfigManager()
index_mgr = IndexManager(config_mgr=config)

# 等待索引加载
import time
time.sleep(2)

print(f"索引状态: is_ready={index_mgr.is_ready}, file_count={index_mgr.file_count}")

if index_mgr.is_ready:
    print("\n测试 SQLite 搜索 '华润'...")
    results = index_mgr.search("华润", ["C:\\"])
    if results:
        print(f"找到 {len(list(results))} 个结果")
        count = 0
        for fn, fp, sz, mt, is_dir in results:
            if "华" in fn and "润" in fn:
                count += 1
                if count <= 5:
                    print(f"  - {fn}")
        print(f"匹配 '华润' 的结果: {count}")
    else:
        print("搜索失败或无结果")
else:
    print("索引未就绪")
