use file_scanner_engine::commands;

#[tokio::test]
async fn test_get_drives() {
    let result = commands::get_all_drives().await;
    println!("驱动器列表: {:?}", result);
    assert!(result.is_ok());
    assert!(!result.unwrap().is_empty());
}

#[tokio::test]
async fn test_realtime_search_all_drives() {
    println!("\n========== 测试全盘实时搜索 ==========");
    let result = commands::realtime_search("test".to_string(), None).await;
    match &result {
        Ok(results) => {
            println!("✅ 实时搜索成功，找到 {} 个结果", results.len());
            if results.len() > 0 {
                println!("前5个结果:");
                for (i, r) in results.iter().take(5).enumerate() {
                    println!("  {}. {} ({})", i+1, r.filename, r.fullpath);
                }
            }
        }
        Err(e) => println!("❌ 失败: {}", e),
    }
    assert!(result.is_ok());
}

#[tokio::test]
async fn test_build_index_all_drives() {
    println!("\n========== 测试全盘索引构建 ==========");
    let result = commands::build_index(None).await;
    match &result {
        Ok(msg) => println!("✅ {}", msg),
        Err(e) => println!("❌ 失败: {}", e),
    }
    assert!(result.is_ok());
}

#[tokio::test]
async fn test_index_search_all_drives() {
    println!("\n========== 测试全盘索引搜索 ==========");
    // 先构建索引
    let _ = commands::build_index(None).await;
    
    // 然后搜索
    let result = commands::search_files("test".to_string(), None).await;
    match &result {
        Ok(results) => {
            println!("✅ 索引搜索成功，找到 {} 个结果", results.len());
            if results.len() > 0 {
                println!("前5个结果:");
                for (i, r) in results.iter().take(5).enumerate() {
                    println!("  {}. {} ({})", i+1, r.filename, r.fullpath);
                }
            }
        }
        Err(e) => println!("❌ 失败: {}", e),
    }
    assert!(result.is_ok());
}
