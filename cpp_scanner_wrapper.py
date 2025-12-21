# cpp_scanner_wrapper.py
import ctypes
import os

class FileRecord(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ref", ctypes.c_uint64),
        ("parent_ref", ctypes.c_uint64),
        ("filename", ctypes.c_wchar * 260),
        ("attributes", ctypes.c_uint32),
    ]

ProgressCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int64,     # count
    ctypes.c_wchar_p    # current path
)

class CppMFTScanner:
    """
    封装 C++ MFT 扫描 DLL：
    - scan_drives("CDE"): 扫描多个盘
    - get_all_records(): 取出所有 FileRecord
    - build_path(ref): 根据 ref 构建完整路径
    """

    def __init__(self, dll_path=None):
        if dll_path is None:
            # 你可以改成放在程序目录，比如：
            # dll_path = os.path.join(os.path.dirname(__file__), "scanner.dll")
            dll_path = r"G:\新建文件夹\Scanner\x64\Release\Scanner.dll"

        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"找不到 DLL: {dll_path}")

        self.dll = ctypes.CDLL(dll_path)

        # 基本函数签名
        self.dll.mft_scan_multi.argtypes = [ctypes.c_wchar_p]
        self.dll.mft_scan_multi.restype = ctypes.c_int64

        # 如果有 mft_scan_multi_ex(drives, enable_filter)
        try:
            self.dll.mft_scan_multi_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_int32]
            self.dll.mft_scan_multi_ex.restype = ctypes.c_int64
            self.has_scan_multi_ex = True
        except AttributeError:
            self.has_scan_multi_ex = False

        self.dll.mft_get_scan_time.restype = ctypes.c_double

        self.dll.mft_get_count.restype = ctypes.c_int64

        self.dll.mft_get_records.argtypes = [
            ctypes.POINTER(FileRecord),
            ctypes.c_int64,
            ctypes.c_int32
        ]
        self.dll.mft_get_records.restype = ctypes.c_int32

        self.dll.mft_build_path.argtypes = [ctypes.c_uint64, ctypes.c_wchar_p, ctypes.c_int32]
        self.dll.mft_build_path.restype = ctypes.c_int32

        self.dll.mft_clear.restype = None

    def scan_drives(self, drives: str, enable_filter: bool = False) -> int:
        """
        扫描多个盘，如 "CDE"
        返回记录总数
        """
        self.dll.mft_clear()
        drives_w = ctypes.c_wchar_p(drives)

        if enable_filter and self.has_scan_multi_ex:
            count = self.dll.mft_scan_multi_ex(drives_w, 1)
        else:
            count = self.dll.mft_scan_multi(drives_w)

        return count

    def get_all_records(self, batch_size: int = 50000):
        """扫描完成后，分批取出所有 FileRecord，返回 Python list[FileRecord]"""
        total = self.dll.mft_get_count()
        records = []
        offset = 0

        while offset < total:
            n = min(batch_size, total - offset)
            buf = (FileRecord * n)()
            got = self.dll.mft_get_records(buf, offset, n)
            if got <= 0:
                break
            for i in range(got):
                records.append(buf[i])
            offset += got

        return records

    def build_path(self, ref: int) -> str:
        """根据 ref 构建完整路径"""
        buf = ctypes.create_unicode_buffer(520)
        ret = self.dll.mft_build_path(ctypes.c_uint64(ref), buf, 520)
        if ret <= 0:
            return ""
        return buf.value

    def get_scan_time_ms(self) -> float:
        """返回 C++ 报告的扫描耗时（毫秒）"""
        return self.dll.mft_get_scan_time()