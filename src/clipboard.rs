// Windows 文件剪贴板：复制/剪切（资源管理器可 Ctrl+V）
// - CF_HDROP: 文件列表
// - Preferred DropEffect: 1=Copy, 2=Move

use anyhow::{anyhow, Result};

#[cfg(windows)]
pub fn set_clipboard_files(paths: &[String], cut: bool) -> Result<()> {
    use std::ffi::OsStr;
    use std::mem::{size_of, MaybeUninit};
    use std::os::windows::ffi::OsStrExt;

    use windows::core::w;
    use windows::Win32::Foundation::{BOOL, HANDLE, HWND, POINT};
    use windows::Win32::System::DataExchange::{
        CloseClipboard, EmptyClipboard, OpenClipboard, RegisterClipboardFormatW, SetClipboardData,
    };
    use windows::Win32::System::Memory::{
        GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE, GMEM_ZEROINIT,
    };

    // winuser.h
    const CF_HDROP: u32 = 15;

    // shellapi.h
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct DROPFILES {
        pFiles: u32,
        pt: POINT,
        fNC: BOOL,
        fWide: BOOL,
    }

    if paths.is_empty() {
        return Err(anyhow!("paths 为空"));
    }

    // DROPFILES + 双 0 结尾 UTF-16 路径列表
    let mut wide: Vec<u16> = Vec::new();
    for p in paths {
        let mut wpath: Vec<u16> = OsStr::new(p).encode_wide().collect();
        // 单个字符串以 \0 结尾
        wpath.push(0);
        wide.extend(wpath);
    }
    // 整个列表以额外 \0 结尾
    wide.push(0);

    let dropfiles_size = size_of::<DROPFILES>();
    let data_bytes = wide.len() * size_of::<u16>();
    let total = dropfiles_size + data_bytes;

    unsafe {
        let hglobal = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total)
            .map_err(|e| anyhow!("GlobalAlloc 失败: {e:?}"))?;
        let ptr = GlobalLock(hglobal);
        if ptr.is_null() {
            return Err(anyhow!("GlobalLock 失败"));
        }

        // 写 DROPFILES 头
        let mut df = MaybeUninit::<DROPFILES>::zeroed();
        let mut dfv = df.assume_init();
        dfv.pFiles = dropfiles_size as u32;
        dfv.pt = POINT { x: 0, y: 0 };
        dfv.fNC = BOOL(0);
        dfv.fWide = BOOL(1);
        std::ptr::copy_nonoverlapping(&dfv as *const DROPFILES as *const u8, ptr as *mut u8, dropfiles_size);

        // 写 UTF-16 路径数据
        let data_ptr = (ptr as *mut u8).add(dropfiles_size) as *mut u16;
        std::ptr::copy_nonoverlapping(wide.as_ptr(), data_ptr, wide.len());

        let _ = GlobalUnlock(hglobal);

        // 打开剪贴板
        OpenClipboard(HWND(0)).map_err(|e| anyhow!("OpenClipboard 失败: {e:?}"))?;
        EmptyClipboard().map_err(|e| anyhow!("EmptyClipboard 失败: {e:?}"))?;

        // 设置 CF_HDROP（剪贴板接管 hglobal 所有权）
        let hset = SetClipboardData(CF_HDROP, HANDLE(hglobal.0 as isize))
            .map_err(|e| anyhow!("SetClipboardData(CF_HDROP) 失败: {e:?}"))?;
        if hset.0 == 0 {
            let _ = CloseClipboard();
            return Err(anyhow!("SetClipboardData(CF_HDROP) 返回空句柄"));
        }

        // 设置 Preferred DropEffect（复制/剪切）
        let fmt = RegisterClipboardFormatW(w!("Preferred DropEffect"));
        if fmt != 0 {
            let effect: u32 = if cut { 2 } else { 1 };
            let h2 = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size_of::<u32>());
            if let Ok(h2) = h2 {
                let p2 = GlobalLock(h2);
                if !p2.is_null() {
                    std::ptr::copy_nonoverlapping(&effect as *const u32 as *const u8, p2 as *mut u8, size_of::<u32>());
                    let _ = GlobalUnlock(h2);
                    let _ = SetClipboardData(fmt, HANDLE(h2.0 as isize));
                }
            }
        }

        CloseClipboard().map_err(|e| anyhow!("CloseClipboard 失败: {e:?}"))?;
    }

    Ok(())
}

#[cfg(not(windows))]
pub fn set_clipboard_files(_paths: &[String], _cut: bool) -> Result<()> {
    Err(anyhow!("仅支持 Windows"))
}

/// 设置剪贴板文本（用于“复制路径/文件名”等）。
#[cfg(windows)]
pub fn set_clipboard_text(text: &str) -> Result<()> {
    use std::mem::size_of;
    use std::os::windows::ffi::OsStrExt;
    use std::{ffi::OsStr, ptr};

    use windows::Win32::Foundation::{HANDLE, HWND};
    use windows::Win32::System::DataExchange::{
        CloseClipboard, EmptyClipboard, OpenClipboard, SetClipboardData,
    };
    use windows::Win32::System::Memory::{
        GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE, GMEM_ZEROINIT,
    };

    // winuser.h
    const CF_UNICODETEXT: u32 = 13;

    let mut wide: Vec<u16> = OsStr::new(text).encode_wide().collect();
    wide.push(0);
    let total = wide.len() * size_of::<u16>();

    unsafe {
        let hglobal = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total)
            .map_err(|e| anyhow!("GlobalAlloc 失败: {e:?}"))?;
        let ptr_mem = GlobalLock(hglobal);
        if ptr_mem.is_null() {
            return Err(anyhow!("GlobalLock 失败"));
        }

        ptr::copy_nonoverlapping(wide.as_ptr() as *const u8, ptr_mem as *mut u8, total);
        let _ = GlobalUnlock(hglobal);

        OpenClipboard(HWND(0)).map_err(|e| anyhow!("OpenClipboard 失败: {e:?}"))?;
        EmptyClipboard().map_err(|e| anyhow!("EmptyClipboard 失败: {e:?}"))?;

        let hset = SetClipboardData(CF_UNICODETEXT, HANDLE(hglobal.0 as isize))
            .map_err(|e| anyhow!("SetClipboardData(CF_UNICODETEXT) 失败: {e:?}"))?;
        if hset.0 == 0 {
            let _ = CloseClipboard();
            return Err(anyhow!("SetClipboardData 返回空句柄"));
        }

        CloseClipboard().map_err(|e| anyhow!("CloseClipboard 失败: {e:?}"))?;
    }

    Ok(())
}

#[cfg(not(windows))]
pub fn set_clipboard_text(_text: &str) -> Result<()> {
    Err(anyhow!("仅支持 Windows"))
}


