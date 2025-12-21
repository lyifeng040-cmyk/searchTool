"""
预热效果测试脚本 V2
测试冷启动 vs 热启动的性能差异（修复缓存问题）
"""

import ctypes
import time
import os
from pathlib import Path

# ============== 加载 DLL ==============

class ScanResult(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("data_len", ctypes.c_size_t),
        ("count", ctypes.c_size_t),
    ]

dll_path = Path(__file__).parent / "file_scanner_engine.dll"
if not dll_path.exists():
    dll_path = Path.cwd() / "file_scanner_engine.dll"

if not dll_path.exists():
    print(f"❌ 找不到 DLL: {dll_path}")
    exit(1)

print(f"📦 加载 DLL: {dll_path}")
engine = ctypes.CDLL(str(dll_path))

# 设置函数签名
engine.scan_drive_packed.argtypes = [ctypes.c_uint16]
engine.scan_drive_packed.restype = ScanResult

engine.free_scan_result.argtypes = [ScanResult]
engine.free_scan_result.restype = None

engine.warmup_dir_cache.argtypes = [ctypes.c_uint16]
engine.warmup_dir_cache.restype = ctypes.c_int32

engine.clear_dir_cache.argtypes = [ctypes.c_uint16]
engine.clear_dir_cache.restype = None

engine.clear_all_dir_cache.argtypes = []
engine.clear_all_dir_cache.restype = None

engine.get_engine_version.argtypes = []
engine.get_engine_version.restype = ctypes.c_uint32

# ============== 获取驱动器列表 ==============

def get_drives():
    import string
    drives = []
    for d in string.ascii_uppercase:
        if os.path.exists(f"{d}:\\"):
            drives.append(d)
    return drives

# ============== 测试函数 ==============

def test_scan_drive(drive, clear_cache_first=False):
    """测试扫描单个驱动器"""
    if clear_cache_first:
        engine.clear_dir_cache(ord(drive))
        time.sleep(0.1)  # 确保清除完成
    
    start = time.perf_counter()
    result = engine.scan_drive_packed(ord(drive))
    elapsed = time.perf_counter() - start
    
    count = result.count
    engine.free_scan_result(result)
    
    return elapsed, count

def test_warmup_drive(drive):
    """测试预热单个驱动器"""
    start = time.perf_counter()
    result = engine.warmup_dir_cache(ord(drive))
    elapsed = time.perf_counter() - start
    
    success = result == 1
    return elapsed, success

# ============== 主测试 ==============

def main():
    print("=" * 60)
    print("🧪 Rust 引擎预热效果测试 V2（修复缓存问题）")
    print("=" * 60)
    
    try:
        version = engine.get_engine_version()
        print(f"📌 引擎版本: V{version}")
    except:
        print("📌 引擎版本: 未知")
    
    drives = get_drives()
    print(f"📁 检测到驱动器: {drives}")
    print()
    
    # ============== 测试1：真正的冷启动扫描 ==============
    print("=" * 60)
    print("🧊 测试1：真正的冷启动扫描（每个盘扫描前清除缓存）")
    print("=" * 60)
    
    engine.clear_all_dir_cache()
    time.sleep(0.5)
    print("🗑️  已清除所有缓存")
    print()
    
    cold_scan_results = {}
    for drive in drives:
        # ★ 关键：每个盘扫描前清除该盘缓存
        elapsed, count = test_scan_drive(drive, clear_cache_first=True)
        cold_scan_results[drive] = (elapsed, count)
        print(f"   {drive}: {elapsed:.3f}s - {count:,} 条记录")
    
    total_cold = sum(r[0] for r in cold_scan_results.values())
    total_count = sum(r[1] for r in cold_scan_results.values())
    print(f"\n   📊 冷启动扫描总计: {total_cold:.3f}s, {total_count:,} 条")
    print()
    
    # ============== 测试2：冷启动预热 ==============
    print("=" * 60)
    print("🧊 测试2：冷启动预热（清除缓存后）")
    print("=" * 60)
    
    engine.clear_all_dir_cache()
    time.sleep(0.5)
    print("🗑️  已清除所有缓存")
    print()
    
    cold_warmup_results = {}
    for drive in drives:
        elapsed, success = test_warmup_drive(drive)
        cold_warmup_results[drive] = elapsed
        status = "✅" if success else "❌"
        print(f"   {drive}: {status} {elapsed:.3f}s")
    
    total_cold_warmup = sum(cold_warmup_results.values())
    print(f"\n   📊 冷启动预热总计: {total_cold_warmup:.3f}s")
    print()
    
    # ============== 测试3：热启动预热 ==============
    print("=" * 60)
    print("🔥 测试3：热启动预热（缓存已存在）")
    print("=" * 60)
    print()
    
    hot_warmup_results = {}
    for drive in drives:
        elapsed, success = test_warmup_drive(drive)
        hot_warmup_results[drive] = elapsed
        status = "✅" if success else "❌"
        print(f"   {drive}: {status} {elapsed:.6f}s")
    
    total_hot_warmup = sum(hot_warmup_results.values())
    print(f"\n   📊 热启动预热总计: {total_hot_warmup:.6f}s")
    print()
    
    # ============== 测试4：热启动扫描（不清除缓存）==============
    print("=" * 60)
    print("🔥 测试4：热启动扫描（缓存已存在）")
    print("=" * 60)
    print()
    
    hot_scan_results = {}
    for drive in drives:
        elapsed, count = test_scan_drive(drive, clear_cache_first=False)
        hot_scan_results[drive] = (elapsed, count)
        print(f"   {drive}: {elapsed:.3f}s - {count:,} 条记录")
    
    total_hot = sum(r[0] for r in hot_scan_results.values())
    print(f"\n   📊 热启动扫描总计: {total_hot:.3f}s")
    print()
    
    # ============== 结果汇总 ==============
    print("=" * 60)
    print("📊 结果汇总")
    print("=" * 60)
    print()
    print(f"{'驱动器':<8} {'冷扫描':<12} {'热扫描':<12} {'冷预热':<12} {'热预热':<12} {'预热提升'}")
    print("-" * 70)
    
    for drive in drives:
        cold_scan = cold_scan_results[drive][0]
        hot_scan = hot_scan_results[drive][0]
        cold_warmup = cold_warmup_results[drive]
        hot_warmup = hot_warmup_results[drive]
        
        if hot_warmup > 0:
            speedup = cold_warmup / hot_warmup
        else:
            speedup = float('inf')
        
        print(f"{drive}:       {cold_scan:<12.3f} {hot_scan:<12.3f} {cold_warmup:<12.3f} {hot_warmup:<12.6f} {speedup:.0f}x")
    
    print("-" * 70)
    
    scan_speedup = total_cold / total_hot if total_hot > 0 else 1
    warmup_speedup = total_cold_warmup / total_hot_warmup if total_hot_warmup > 0 else 1
    
    print(f"{'总计':<8} {total_cold:<12.3f} {total_hot:<12.3f} {total_cold_warmup:<12.3f} {total_hot_warmup:<12.6f}")
    print()
    
    # ============== 结论 ==============
    print("=" * 60)
    print("📝 结论")
    print("=" * 60)
    
    print(f"   扫描提升: {scan_speedup:.1f}x （冷: {total_cold:.2f}s → 热: {total_hot:.2f}s）")
    print(f"   预热提升: {warmup_speedup:.0f}x （冷: {total_cold_warmup:.2f}s → 热: {total_hot_warmup:.6f}s）")
    print()
    
    if warmup_speedup > 10:
        print("   ✅ 预热缓存效果显著！")
    else:
        print("   ⚠️ 预热缓存效果不明显")
    
    # 扫描是否受缓存影响
    if scan_speedup > 1.2:
        print(f"   ✅ 扫描也受益于缓存！")
    else:
        print(f"   ℹ️ 扫描不受缓存影响（每次都是全量 MFT 读取）")
    
    print()
    print("=" * 60)
    print("✅ 测试完成")
    print("=" * 60)

if __name__ == "__main__":
    main()