fn main() {
    // 生成 Windows 资源/manifest（包含 Common Controls v6），避免某些系统缺少 TaskDialogIndirect 入口点
    tauri_build::build();
}

