// build.rs - Tauri 构建配置
fn main() {
    // 强制跳过图标验证（避免无效图标导致运行时崩溃）
    std::env::set_var("TAURI_SKIP_ICON_VALIDATION", "true");

    // 始终嵌入应用清单，确保加载 Common-Controls v6
    embed_resource::compile(
        "build_manifest.rc",
        std::iter::empty::<String>()
    );

    // 若缺少 Windows ICO 图标，则跳过 tauri_build 以避免构建失败
    let ico_path = std::path::Path::new("icons/icon.ico");
    if ico_path.exists() {
        tauri_build::build();
    } else {
        println!(
            "cargo:warning=Skipping tauri_build: missing {}",
            ico_path.display()
        );
    }
}

