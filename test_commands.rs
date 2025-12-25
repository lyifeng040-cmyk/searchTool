// 测试后端命令是否正常工作
use file_scanner_engine::commands;

#[tokio::main]
async fn main() {
    env_logger::init();
    
    println!("========== 测试 1: 获取驱动器列表 ==========");
    match commands::get_all_drives().await {
        Ok(drives) => println!("✅ 成功获取驱动器: {:?}", drives),
        Err(e) => println!("❌ 失败: {}", e),
    }
    
    println!("\n========== 测试 2: 实时搜索 (C盘，搜索 'test') ==========");
    match commands::realtime_search("test".to_string(), Some("C:/".to_string())).await {
        Ok(results) => {
            println!("✅ 实时搜索成功，找到 {} 个结果", results.len());
            if results.len() > 0 {
                println!("示例结果: {:?}", &results[0]);
            }
        }
        Err(e) => println!("❌ 失败: {}", e),
    }
    
    println!("\n========== 测试 3: 构建索引 (C盘) ==========");
    match commands::build_index(Some("C:/".to_string())).await {
        Ok(msg) => println!("✅ 构建索引成功: {}", msg),
        Err(e) => println!("❌ 失败: {}", e),
    }
    
    println!("\n========== 测试 4: 索引搜索 ('test') ==========");
    match commands::search_files("test".to_string(), Some("C:/".to_string())).await {
        Ok(results) => {
            println!("✅ 索引搜索成功，找到 {} 个结果", results.len());
            if results.len() > 0 {
                println!("示例结果: {:?}", &results[0]);
            }
        }
        Err(e) => println!("❌ 失败: {}", e),
    }
    
    println!("\n========== 所有测试完成 ==========");
}
