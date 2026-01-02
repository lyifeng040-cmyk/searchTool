// Tauri命令模块 - 前后端通信接口
use crate::{AppState, config::*, database::FileRecord, utils};
use tauri::{Manager, State};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MiniTransfer {
    pub keyword: String,
    pub mode: String,
    pub results: Vec<FileRecord>,
    pub truncated: bool,
    pub original_len: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SearchParams {
    pub keyword: String,
    pub scope: String,
    pub use_fts: bool,
    pub use_regex: bool,
    pub fuzzy: bool,
    #[serde(default)]
    pub fuzzy_min_score: u32,
    pub limit: usize,
}

#[derive(Debug, Serialize)]
pub struct SearchResult {
    pub results: Vec<FileRecord>,
    pub total: usize,
    pub elapsed_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct IndexStats {
    pub total_files: usize,
    pub indexed_files: usize,
    pub last_build_time: Option<String>,
    pub last_build_ms: Option<u64>,
    pub has_fts: bool,
}

// ==================== 搜索命令 ====================

#[tauri::command]
pub async fn search_files(
    params: SearchParams,
    state: State<'_, AppState>,
) -> Result<SearchResult, String> {
    let start = std::time::Instant::now();
    use std::collections::HashSet;

    // 统一解析增强语法（主窗口/mini 共用）
    let parsed = utils::parse_advanced_query(&params.keyword);
    let effective_keyword = parsed.keyword.trim().to_string();

    // 若使用 dm: 语法，必须保证索引里有有效 mtime；老索引可能全是 0。
    // 这里做一次性补全（写 meta 标记），避免让用户“必须重建索引”。
    if parsed.min_mtime.is_some() {
        let hydrated_flag = state
            .database
            .get_meta("mtime_hydrated")
            .map_err(|e| e.to_string())?;
        if hydrated_flag.as_deref() != Some("1") {
            let hs = std::time::Instant::now();
            let hydrated = state
                .database
                .hydrate_missing_meta(0)
                .map_err(|e| e.to_string())?;
            let hms = hs.elapsed().as_millis() as u64;
            state
                .database
                .set_meta("mtime_hydrated", "1")
                .map_err(|e| e.to_string())?;
            state
                .database
                .set_meta("mtime_hydrated_count", &hydrated.to_string())
                .map_err(|e| e.to_string())?;
            state
                .database
                .set_meta("mtime_hydrated_ms", &hms.to_string())
                .map_err(|e| e.to_string())?;
        }
    }

    log::info!(
        "搜索: {} => '{}' (FTS: {}, 正则: {}, 模糊: {}, ext:{:?}, dm:{:?}, size:{:?}-{:?}, name:{:?}, path:{:?}, is_dir:{}, is_file:{})",
        params.keyword,
        effective_keyword,
        params.use_fts,
        params.use_regex,
        params.fuzzy,
        parsed.exts,
        parsed.min_mtime,
        parsed.min_size,
        parsed.max_size,
        parsed.name_terms,
        parsed.path_terms,
        parsed.is_dir,
        parsed.is_file
    );

    // 保护：前端传 100000 会导致 UI 和内存抖动；这里做硬性上限
    let limit = params.limit.min(20000).max(1);
    let has_filters = !parsed.exts.is_empty()
        || parsed.min_mtime.is_some()
        || parsed.min_size.is_some()
        || parsed.max_size.is_some()
        || !parsed.name_terms.is_empty()
        || !parsed.path_terms.is_empty()
        || parsed.is_dir
        || parsed.is_file;
    // 若带过滤，为避免“先 limit 再过滤导致 0 结果”，扩大候选集再截断
    let candidate_limit = if has_filters {
        (limit.saturating_mul(10)).min(200_000)
    } else {
        limit
    };

    // 先做范围过滤（能显著减少结果量，提高搜索速度）
    // - all：不加限制
    // - 目录/盘符：用 full_path 前缀过滤
    let scope = params.scope.trim().to_string();
    let scope_prefixes: Vec<String> = if scope.is_empty() || scope == "all" {
        Vec::new()
    } else {
        utils::parse_search_scope(&scope)
    };
    if !(scope.is_empty() || scope == "all") && scope_prefixes.is_empty() {
        return Err("无效的搜索范围".to_string());
    }

    // 分词（对齐 Python：空格拆词）
    let keywords: Vec<String> = effective_keyword
        .to_lowercase()
        .split_whitespace()
        .map(|s| s.to_string())
        .collect();

    // 模糊候选集策略：
    // - 中文/非 ASCII：逐字 LIKE，扩大候选集，使阈值真正生效（0=更全，100=更严）
    // - 纯 ASCII：按 token LIKE，避免候选集爆炸
    let fuzzy_use_char_like = params.fuzzy
        && !params.use_regex
        && keywords.iter().any(|t| t.chars().any(|c| !c.is_ascii()) && t.chars().count() >= 2);

    // 语法-only（没有普通关键词但有过滤/作用域 token）：走 SQL/LIKE 过滤查询，确保索引模式也能用
    let mut results = if effective_keyword.is_empty() && has_filters {
        let prefixes: Vec<String> = scope_prefixes.clone();

        let mut all: Vec<FileRecord> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();

        let push_unique = |out: &mut Vec<FileRecord>, seen: &mut HashSet<String>, mut v: Vec<FileRecord>| {
            for r in v.drain(..) {
                if seen.insert(r.full_path.clone()) {
                    out.push(r);
                }
            }
        };

        let tokens_path: Vec<String> = parsed.path_terms.iter().map(|s| s.to_lowercase()).collect();
        let tokens_name: Vec<String> = parsed.name_terms.iter().map(|s| s.to_lowercase()).collect();

        if prefixes.is_empty() {
            // 全局
            if !tokens_path.is_empty() {
                let v = state
                    .database
                    .search_path_like_tokens(&tokens_path, candidate_limit)
                    .map_err(|e| e.to_string())?;
                push_unique(&mut all, &mut seen, v);
            } else if !tokens_name.is_empty() {
                let v = state
                    .database
                    .search_like_tokens(&tokens_name, candidate_limit)
                    .map_err(|e| e.to_string())?;
                push_unique(&mut all, &mut seen, v);
            } else {
                let v = state
                    .database
                    .search_filters_v2(
                        &parsed.exts,
                        parsed.min_mtime,
                        parsed.min_size,
                        parsed.max_size,
                        parsed.is_dir,
                        parsed.is_file,
                        candidate_limit,
                    )
                    .map_err(|e| e.to_string())?;
                push_unique(&mut all, &mut seen, v);
            }
        } else {
            // 多前缀：逐个查询再合并（去重）
            for pfx in &prefixes {
                if !tokens_path.is_empty() {
                    let v = state
                        .database
                        .search_scoped_path_like_tokens(&tokens_path, pfx, candidate_limit)
                        .map_err(|e| e.to_string())?;
                    push_unique(&mut all, &mut seen, v);
                } else if !tokens_name.is_empty() {
                    let v = state
                        .database
                        .search_scoped_like_tokens(&tokens_name, pfx, candidate_limit)
                        .map_err(|e| e.to_string())?;
                    push_unique(&mut all, &mut seen, v);
                } else {
                    let v = state
                        .database
                        .search_scoped_filters_v2(
                            pfx,
                            &parsed.exts,
                            parsed.min_mtime,
                            parsed.min_size,
                            parsed.max_size,
                            parsed.is_dir,
                            parsed.is_file,
                            candidate_limit,
                        )
                        .map_err(|e| e.to_string())?;
                    push_unique(&mut all, &mut seen, v);
                }
            }
        }

        Ok(all)
    } else if scope.is_empty() || scope == "all" {
        // 正则模式：先从正则里提取一个“普通文本片段”用于候选集过滤
        // 否则把原正则串直接丢给 LIKE 会出现 `LIKE %a.*b%` 这种错误候选集（几乎 0 结果）
        let db_keyword = if params.use_regex {
            if effective_keyword.is_empty() {
                return Err("正则表达式为空".to_string());
            }
            let seed = utils::extract_regex_seed(&effective_keyword);
            if seed.is_empty() {
                String::new()
            } else {
                seed.to_lowercase()
            }
        } else {
            effective_keyword.to_lowercase()
        };

        if params.fuzzy && !params.use_regex {
            // 仅语法（ext/dm）没有普通关键词时：不能走 search_like_tokens([])（会直接空）
            if keywords.is_empty() {
                state.database.search("", false, false, candidate_limit)
            } else if fuzzy_use_char_like {
                state.database.search_like_char_tokens(&keywords, candidate_limit)
            } else {
                state.database.search_like_tokens(&keywords, candidate_limit)
            }
        } else {
            state.database
                .search(&db_keyword, params.use_fts && !params.use_regex, false, candidate_limit)
        }
    } else {
        let prefixes = if scope_prefixes.is_empty() {
            vec![crate::utils::normalize_scope_path(&scope)]
        } else {
            scope_prefixes.clone()
        };
        let db_keyword = if params.use_regex {
            if effective_keyword.is_empty() {
                return Err("正则表达式为空".to_string());
            }
            let seed = utils::extract_regex_seed(&effective_keyword);
            if seed.is_empty() {
                String::new()
            } else {
                seed.to_lowercase()
            }
        } else {
            effective_keyword.to_lowercase()
        };

        let mut all: Vec<FileRecord> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for prefix in prefixes {
            let v = if params.fuzzy && !params.use_regex {
                if keywords.is_empty() {
                    // 仅语法（ext/dm/size/is/name/path）没有普通关键词：取该范围内全部（再做过滤）
                    state
                        .database
                        .search_scoped("", &prefix, false, false, candidate_limit)
                        .map_err(|e| e.to_string())?
                } else if fuzzy_use_char_like {
                    state
                        .database
                        .search_scoped_like_char_tokens(&keywords, &prefix, candidate_limit)
                        .map_err(|e| e.to_string())?
                } else {
                    state
                        .database
                        .search_scoped_like_tokens(&keywords, &prefix, candidate_limit)
                        .map_err(|e| e.to_string())?
                }
            } else {
                state
                    .database
                    .search_scoped(&db_keyword, &prefix, params.use_fts && !params.use_regex, false, candidate_limit)
                    .map_err(|e| e.to_string())?
            };
            for r in v {
                if seen.insert(r.full_path.clone()) {
                    all.push(r);
                }
            }
        }
        Ok(all)
    }
        .map_err(|e| e.to_string())?;

    // ===== 增强语法过滤：ext / dm（尽量提前，减少后续正则/模糊开销）=====
    if !parsed.exts.is_empty() {
        results.retain(|r| parsed.exts.iter().any(|e| e == &r.extension));
    }
    if let Some(min_mtime) = parsed.min_mtime {
        results.retain(|r| r.mtime > 0.0 && r.mtime >= min_mtime);
    }
    // ===== Tier2：is / size / name / path =====
    if parsed.is_dir {
        results.retain(|r| r.is_dir);
    } else if parsed.is_file {
        results.retain(|r| !r.is_dir);
    }
    if parsed.min_size.is_some() || parsed.max_size.is_some() {
        // 目录 size 语义不明确：遇到 size: 则排除目录
        results.retain(|r| !r.is_dir);
        if let Some(mins) = parsed.min_size {
            results.retain(|r| r.size >= mins);
        }
        if let Some(maxs) = parsed.max_size {
            results.retain(|r| r.size <= maxs);
        }
    }
    if !parsed.name_terms.is_empty() {
        let terms: Vec<String> = parsed.name_terms.iter().map(|s| s.to_lowercase()).collect();
        results.retain(|r| terms.iter().all(|t| r.filename_lower.contains(t)));
    }
    if !parsed.path_terms.is_empty() {
        let terms: Vec<String> = parsed.path_terms.iter().map(|s| s.to_lowercase()).collect();
        results.retain(|r| {
            let p = r.full_path.to_lowercase();
            terms.iter().all(|t| p.contains(t))
        });
    }

    // 二次过滤：正则/模糊
    let results = if params.use_regex {
        // 正则过滤
        let re = match regex::Regex::new(&effective_keyword) {
            Ok(re) => re,
            Err(e) => {
                log::warn!("正则表达式编译失败: {} - {}", effective_keyword, e);
                return Err(format!("正则表达式无效: {}", e));
            }
        };
        results.into_iter()
            .filter(|r| re.is_match(&r.filename))
            .collect()
    } else if params.fuzzy {
        // 模糊：默认不丢结果，只做排序；若用户调了阈值（0-100），再按阈值过滤
        // 规则：每个 token 的 fuzzy_score 必须 >= 阈值（阈值为 0 时不过滤）
        let min_score = params.fuzzy_min_score.min(100);
        let mut scored: Vec<(u32, FileRecord)> = results
            .into_iter()
            .filter_map(|r| {
                let name = r.filename.to_lowercase();
                let mut sum = 0u32;
                for kw in &keywords {
                    let s = utils::fuzzy_score(kw, &name);
                    if min_score > 0 && s < min_score {
                        return None;
                    }
                    sum += s;
                }
                Some((sum, r))
            })
            .collect();
        scored.sort_by(|a, b| b.0.cmp(&a.0));
        scored.into_iter().map(|(_, r)| r).collect()
    } else {
        results
    };

    // 最终截断到请求 limit（candidate_limit 可能更大）
    let mut results = results;
    if results.len() > limit {
        results.truncate(limit);
    }

    let total = results.len();
    let elapsed_ms = start.elapsed().as_millis() as u64;

    Ok(SearchResult {
        results,
        total,
        elapsed_ms,
    })
}

#[tauri::command]
pub async fn search_realtime(
    params: SearchParams,
    state: State<'_, AppState>,
) -> Result<SearchResult, String> {
    let start = std::time::Instant::now();
    // 统一解析增强语法（主窗口/mini 共用）
    let parsed = utils::parse_advanced_query(&params.keyword);
    let effective_keyword = parsed.keyword.trim().to_string();

    log::info!(
        "实时搜索: {} => '{}' (scope: {}, ext:{:?}, dm:{:?}, size:{:?}-{:?}, name:{:?}, path:{:?}, is_dir:{}, is_file:{})",
        params.keyword,
        effective_keyword,
        params.scope,
        parsed.exts,
        parsed.min_mtime,
        parsed.min_size,
        parsed.max_size,
        parsed.name_terms,
        parsed.path_terms,
        parsed.is_dir,
        parsed.is_file
    );

    // 解析搜索范围
    let mut scope_targets = utils::parse_search_scope(&params.scope);
    if scope_targets.is_empty() {
        return Err("无效的搜索范围".to_string());
    }

    // Python 逻辑：全盘时 C 盘只扫配置的目录（避免扫系统目录）
    // 这里尽量对齐：如果 scope 是 all，则把 C:\ 替换为启用的 c_scan_paths
    if params.scope == "all" || params.scope.is_empty() {
        let cfg = state.config.read().clone();
        let mut replaced: Vec<String> = Vec::new();
        for t in scope_targets.into_iter() {
            let t_norm = utils::normalize_scope_path(&t);
            if t_norm.to_ascii_lowercase().starts_with("c:\\") {
                // 用配置的 C 盘目录
                let c_paths: Vec<String> = cfg
                    .c_scan_paths
                    .paths
                    .iter()
                    .filter(|p| p.enabled)
                    .map(|p| utils::normalize_scope_path(&p.path))
                    .collect();
                if !c_paths.is_empty() {
                    replaced.extend(c_paths);
                    continue;
                }
            }
            replaced.push(t_norm);
        }
        scope_targets = replaced;
    }

    let has_filters = !parsed.exts.is_empty()
        || parsed.min_mtime.is_some()
        || parsed.min_size.is_some()
        || parsed.max_size.is_some()
        || !parsed.name_terms.is_empty()
        || !parsed.path_terms.is_empty()
        || parsed.is_dir
        || parsed.is_file;
    // 允许仅语法查询（ext/dm/size/is/name/path），否则返回错误
    if effective_keyword.is_empty() && !has_filters {
        return Err("请输入搜索关键词".to_string());
    }

    // 之前的 try_recv 会导致“线程还没产出就退出”，直接 0 结果；改为稳定的遍历收集
    let keywords: Vec<String> = effective_keyword
        .to_lowercase()
        .split_whitespace()
        .map(|s| s.to_string())
        .collect();

    let re = if params.use_regex {
        regex::Regex::new(&effective_keyword).ok()
    } else {
        None
    };

    let mut results: Vec<FileRecord> = Vec::new();

    'outer: for target in scope_targets {
        for entry in walkdir::WalkDir::new(target)
            .follow_links(false)
            .into_iter()
            // 对齐 Python：只在“目录层”用 should_skip_path；文件不做这个判断（否则结果会偏少）
            .filter_entry(|e| {
                if e.file_type().is_dir() {
                    !utils::should_skip_path(e.path())
                } else {
                    true
                }
            })
        {
            let entry = match entry {
                Ok(e) => e,
                Err(_) => continue,
            };

            let filename = entry.file_name().to_string_lossy().to_string();
            if filename.is_empty() {
                continue;
            }
            // Python: 跳过 . 和 $ 开头
            if filename.starts_with('.') || filename.starts_with('$') {
                continue;
            }
            let filename_lower2 = filename.to_lowercase();

            // name:/path: 作用域匹配（path 需要完整路径）
            let full_path_str = entry.path().to_string_lossy().to_string();
            let full_path_lower = full_path_str.to_lowercase();

            let mut matched = true;

            // 纯关键词（默认匹配文件名）
            if !keywords.is_empty() {
                let ok = if let Some(re) = &re {
                    re.is_match(&filename)
                } else if params.fuzzy {
                    // Python: all(fuzzy_match(kw, filename) >= 50)
                    keywords.iter().all(|kw| utils::fuzzy_match(kw, &filename_lower2))
                } else {
                    keywords.iter().all(|kw| filename_lower2.contains(kw))
                };
                matched = matched && ok;
            }

            // name: token（当普通文本）
            if !parsed.name_terms.is_empty() {
                let terms: Vec<String> = parsed.name_terms.iter().map(|s| s.to_lowercase()).collect();
                matched = matched && terms.iter().all(|t| filename_lower2.contains(t));
            }

            // path: token（当普通文本）
            if !parsed.path_terms.is_empty() {
                let terms: Vec<String> = parsed.path_terms.iter().map(|s| s.to_lowercase()).collect();
                matched = matched && terms.iter().all(|t| full_path_lower.contains(t));
            }

            if !matched {
                continue;
            }

            let md = match entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };

            // is:dir / is:file
            if parsed.is_dir && !md.is_dir() {
                continue;
            }
            if parsed.is_file && md.is_dir() {
                continue;
            }

            let ext = entry
                .path()
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| format!(".{}", e.to_lowercase()))
                .unwrap_or_default();

            // ext: 过滤（尽量早）
            if !parsed.exts.is_empty() && !parsed.exts.iter().any(|x| x == &ext) {
                continue;
            }
            // 对齐 Python：跳过部分扩展（硬编码规则，索引/实时同一套）
            if !md.is_dir() && utils::should_skip_ext(&ext) {
                continue;
            }

            let mtime = md
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);

            // dm: 过滤（最近修改）
            if let Some(min_mtime) = parsed.min_mtime {
                if !(mtime > 0.0 && mtime >= min_mtime) {
                    continue;
                }
            }

            // size: 过滤（目录 size 语义不明确：遇到 size: 则排除目录）
            if parsed.min_size.is_some() || parsed.max_size.is_some() {
                if md.is_dir() {
                    continue;
                }
                let sz = md.len();
                if let Some(mins) = parsed.min_size {
                    if sz < mins {
                        continue;
                    }
                }
                if let Some(maxs) = parsed.max_size {
                    if sz > maxs {
                        continue;
                    }
                }
            }

            results.push(FileRecord {
                id: None,
                filename,
                filename_lower: filename_lower2,
                full_path: full_path_str,
                parent_dir: entry
                    .path()
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default(),
                extension: ext,
                size: md.len(),
                mtime,
                is_dir: md.is_dir(),
            });

            if results.len() >= params.limit {
                break 'outer;
            }
        }
    }

    let total = results.len();
    let elapsed_ms = start.elapsed().as_millis() as u64;

    Ok(SearchResult {
        results,
        total,
        elapsed_ms,
    })
}

// ==================== 文件信息命令 ====================

#[tauri::command]
pub fn get_file_info(path: String) -> Result<FileRecord, String> {
    use std::fs;
    use std::path::Path;

    let path_obj = Path::new(&path);
    let metadata = fs::metadata(path_obj).map_err(|e| e.to_string())?;

    let filename = path_obj.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_string();

    let parent_dir = path_obj.parent()
        .and_then(|p| p.to_str())
        .unwrap_or("")
        .to_string();

    let is_dir = metadata.is_dir();
    let size = if is_dir { 0 } else { metadata.len() };
    let mtime = metadata.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    Ok(FileRecord {
        id: None,
        filename: filename.clone(),
        filename_lower: filename.to_lowercase(),
        full_path: path,
        parent_dir,
        extension: utils::get_extension(&filename),
        size,
        mtime,
        is_dir,
    })
}

#[tauri::command]
pub fn get_file_info_batch(paths: Vec<String>) -> Result<Vec<FileRecord>, String> {
    use rayon::prelude::*;

    let results: Vec<FileRecord> = paths.par_iter()
        .filter_map(|path| get_file_info(path.clone()).ok())
        .collect();

    Ok(results)
}

// ==================== 索引管理命令 ====================

#[tauri::command]
pub async fn scan_drive(
    drive: String,
    state: State<'_, AppState>,
) -> Result<usize, String> {
    log::info!("开始扫描驱动器: {}", drive);

    let drive_char = drive.chars().next().ok_or("无效的驱动器")?;
    
    let records = state.scanner
        .scan_drive(drive_char, None)
        .map_err(|e| e.to_string())?;

    let count = records.len();
    
    state.database
        .batch_insert(&records)
        .map_err(|e| e.to_string())?;

    log::info!("驱动器 {} 扫描完成，插入 {} 条记录", drive, count);

    Ok(count)
}

#[tauri::command]
pub async fn rebuild_index(
    drives: Vec<String>,
    state: State<'_, AppState>,
) -> Result<usize, String> {
    let start = std::time::Instant::now();
    log::info!("开始重建索引: {:?}", drives);

    // 清空数据库
    state.database.clear().map_err(|e| e.to_string())?;

    let mut total = 0;
    for drive in drives {
        let count = scan_drive(drive, state.clone()).await?;
        total += count;
    }

    // 关键：补全 mtime/size（解决 mtime=0 导致 dm: 语法搜不出）
    // 只补 mtime<=0 的记录，避免重复全量 stat；使用 GetFileAttributesExW，整体开销可控。
    let hydrate_start = std::time::Instant::now();
    let hydrated = state
        .database
        .hydrate_missing_meta(0)
        .map_err(|e| e.to_string())?;
    let hydrate_ms = hydrate_start.elapsed().as_millis() as u64;
    state
        .database
        .set_meta("last_hydrate_count", &hydrated.to_string())
        .map_err(|e| e.to_string())?;
    state
        .database
        .set_meta("last_hydrate_ms", &hydrate_ms.to_string())
        .map_err(|e| e.to_string())?;
    state
        .database
        .set_meta("mtime_hydrated", "1")
        .map_err(|e| e.to_string())?;

    // 优化数据库
    state.database.optimize().map_err(|e| e.to_string())?;

    let elapsed_ms = start.elapsed().as_millis() as u64;
    // 写入元数据（用于 UI 展示）
    let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    state.database.set_meta("last_build_time", &now).map_err(|e| e.to_string())?;
    state.database.set_meta("last_build_ms", &elapsed_ms.to_string()).map_err(|e| e.to_string())?;

    log::info!("索引重建完成，共 {} 条记录", total);

    Ok(total)
}

#[tauri::command]
pub fn get_index_stats(state: State<'_, AppState>) -> Result<IndexStats, String> {
    let (total, files) = state.database.get_stats().map_err(|e| e.to_string())?;

    let last_build_time = state.database
        .get_meta("last_build_time")
        .map_err(|e| e.to_string())?;

    let last_build_ms = state.database
        .get_meta("last_build_ms")
        .map_err(|e| e.to_string())?
        .and_then(|s| s.parse::<u64>().ok());

    Ok(IndexStats {
        total_files: total,
        indexed_files: files,
        last_build_time,
        last_build_ms,
        has_fts: true,
    })
}

// ==================== 配置管理命令 ====================

#[tauri::command]
pub fn get_config(state: State<'_, AppState>) -> Result<Config, String> {
    let config = state.config.read();
    Ok(config.clone())
}

#[tauri::command]
pub fn save_config(config: Config, state: State<'_, AppState>) -> Result<(), String> {
    let mut current_config = state.config.write();
    *current_config = config.clone();
    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn add_favorite(name: String, path: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.write();
    config.add_favorite(name, path);
    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn remove_favorite(path: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.write();
    config.remove_favorite(&path);
    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn get_favorites(state: State<'_, AppState>) -> Result<Vec<Favorite>, String> {
    let config = state.config.read();
    Ok(config.favorites.clone())
}

#[tauri::command]
pub fn add_history(keyword: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.write();
    config.add_history(keyword);
    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn get_history(state: State<'_, AppState>) -> Result<Vec<String>, String> {
    let config = state.config.read();
    Ok(config.search_history.clone())
}

// ==================== 文件操作命令 ====================

#[tauri::command]
pub fn export_results(results: Vec<FileRecord>, path: String) -> Result<(), String> {
    use std::fs::File;
    use std::io::Write;

    let mut file = File::create(&path).map_err(|e| e.to_string())?;

    writeln!(file, "文件名,完整路径,大小,修改时间").map_err(|e| e.to_string())?;

    for record in results {
        writeln!(
            file,
            "{},{},{},{}",
            record.filename,
            record.full_path,
            utils::format_size(record.size),
            utils::format_time(record.mtime)
        ).map_err(|e| e.to_string())?;
    }

    log::info!("结果已导出到: {}", path);
    Ok(())
}

#[tauri::command]
pub fn scan_large_files(
    path: String,
    min_size_mb: u64,
    state: State<'_, AppState>,
) -> Result<Vec<FileRecord>, String> {
    let records = state.scanner
        .scan_directory(&path, true)
        .map_err(|e| e.to_string())?;

    let min_size = min_size_mb * 1024 * 1024;
    let large_files: Vec<FileRecord> = records.into_iter()
        .filter(|r| !r.is_dir && r.size >= min_size)
        .collect();

    Ok(large_files)
}

#[tauri::command]
pub fn batch_rename(
    files: Vec<String>,
    mode: String,
    params: serde_json::Value,
) -> Result<Vec<String>, String> {
    use std::fs;
    use std::path::Path;

    let mut renamed = Vec::new();

    for (i, old_path) in files.iter().enumerate() {
        let path = Path::new(old_path);
        let parent = path.parent().ok_or("无法获取父目录")?;
        let extension = path.extension()
            .and_then(|e| e.to_str())
            .unwrap_or("");

        let new_name = match mode.as_str() {
            "prefix" => {
                let prefix = params["prefix"].as_str().unwrap_or("file");
                let start = params["start"].as_u64().unwrap_or(1) as usize;
                let width = params["width"].as_u64().unwrap_or(3) as usize;
                let num = start + i;
                if extension.is_empty() {
                    format!("{}{:0width$}", prefix, num, width = width)
                } else {
                    format!("{}{:0width$}.{}", prefix, num, extension, width = width)
                }
            }
            "replace" => {
                let find = params["find"].as_str().unwrap_or("");
                let replace = params["replace"].as_str().unwrap_or("");
                let old_name = path.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("");
                old_name.replace(find, replace)
            }
            _ => return Err("不支持的重命名模式".to_string()),
        };

        let new_path = parent.join(&new_name);
        fs::rename(old_path, &new_path).map_err(|e| e.to_string())?;
        renamed.push(new_path.to_str().unwrap_or("").to_string());
    }

    log::info!("批量重命名完成: {} 个文件", renamed.len());
    Ok(renamed)
}

#[tauri::command]
pub fn delete_files(paths: Vec<String>, to_trash: bool) -> Result<usize, String> {
    let mut deleted = 0;

    for path in paths {
        if to_trash {
            #[cfg(feature = "trash")]
            {
                trash::delete(&path).map_err(|e| e.to_string())?;
            }
            #[cfg(not(feature = "trash"))]
            {
                std::fs::remove_file(&path).or_else(|_| std::fs::remove_dir_all(&path))
                    .map_err(|e| e.to_string())?;
            }
        } else {
            std::fs::remove_file(&path).or_else(|_| std::fs::remove_dir_all(&path))
                .map_err(|e| e.to_string())?;
        }
        deleted += 1;
    }

    log::info!("删除完成: {} 个文件/目录", deleted);
    Ok(deleted)
}

#[tauri::command]
pub fn copy_files(sources: Vec<String>, dest_dir: String) -> Result<usize, String> {
    use std::fs;
    use std::path::Path;

    let dest = Path::new(&dest_dir);
    if !dest.exists() {
        fs::create_dir_all(dest).map_err(|e| e.to_string())?;
    }

    let mut copied = 0;
    for source in sources {
        let source_path = Path::new(&source);
        let filename = source_path.file_name().ok_or("无效的文件名")?;
        let dest_path = dest.join(filename);

        fs::copy(&source, &dest_path).map_err(|e| e.to_string())?;
        copied += 1;
    }

    log::info!("复制完成: {} 个文件", copied);
    Ok(copied)
}

#[tauri::command]
pub fn open_file(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(&["/C", "start", "", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub fn open_folder(path: String) -> Result<(), String> {
    use std::path::Path;

    let path_obj = Path::new(&path);
    let folder = if path_obj.is_dir() {
        path
    } else {
        path_obj.parent()
            .and_then(|p| p.to_str())
            .ok_or("无法获取父目录")?
            .to_string()
    };

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&folder)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&folder)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&folder)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub fn reveal_in_folder(path: String) -> Result<(), String> {
    use std::path::Path;
    let p = Path::new(&path);

    #[cfg(target_os = "windows")]
    {
        if p.exists() && p.is_file() {
            std::process::Command::new("explorer")
                .args(&["/select,", &path])
                .spawn()
                .map_err(|e| e.to_string())?;
        } else {
            // 目录或不存在：退化为打开父目录
            return open_folder(path);
        }
    }

    #[cfg(target_os = "macos")]
    {
        // macOS: open -R
        std::process::Command::new("open")
            .args(&["-R", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "linux")]
    {
        // Linux: 尝试 xdg-open 打开父目录
        return open_folder(path);
    }

    Ok(())
}

// ==================== 系统命令 ====================

#[tauri::command]
pub fn get_drives() -> Result<Vec<String>, String> {
    Ok(utils::get_drives())
}

#[tauri::command]
pub fn start_watcher(drives: Vec<String>, state: State<'_, AppState>, app: tauri::AppHandle) -> Result<(), String> {
    let mut watcher = state.watcher.write();
    watcher.start(drives, Some(app)).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn stop_watcher(state: State<'_, AppState>) -> Result<(), String> {
    let mut watcher = state.watcher.write();
    watcher.stop();
    Ok(())
}

#[tauri::command]
pub fn get_usn_changes(state: State<'_, AppState>) -> Result<Vec<crate::watcher::FileChange>, String> {
    let watcher = state.watcher.read();
    Ok(watcher.get_changes())
}

#[tauri::command]
pub async fn show_main_and_search(
    keyword: String,
    mode: String,
    app: tauri::AppHandle,
) -> Result<(), String> {
    // 对齐 Python：Tab 切主窗口时先隐藏 mini（mini 是 alwaysOnTop，不隐藏会把主窗口盖住）
    if let Some(mini) = app.get_window("mini") {
        let _ = mini.hide();
    }

    // 显示主窗口（尽量“稳”，不要因为焦点/事件失败导致用户觉得“没反应”）
    if let Some(main_window) = app.get_window("main") {
        let _ = main_window.show();
        let _ = main_window.unminimize();
        let _ = main_window.set_focus();

        // 发送搜索事件到主窗口前端（失败也不要中断）
        let _ = main_window.emit(
            "mini-search",
            serde_json::json!({
                "keyword": keyword,
                "mode": mode
            }),
        );
    }

    Ok(())
}

#[tauri::command]
pub async fn show_main_with_results(
    keyword: String,
    mode: String,
    results: Vec<FileRecord>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    // 同时隐藏 mini（避免前端依赖 window API）
    if let Some(mini) = app.get_window("mini") {
        let _ = mini.hide();
    }

    // 显示主窗口（焦点失败不应影响显示）
    if let Some(main_window) = app.get_window("main") {
        let _ = main_window.show();
        let _ = main_window.unminimize();
        let _ = main_window.set_focus();

        // 发送搜索结果到主窗口前端（失败也不要中断）
        let _ = main_window.emit(
            "mini-results",
            serde_json::json!({
                "keyword": keyword,
                "mode": mode,
                "results": results
            }),
        );
    }
    
    Ok(())
}

/// mini -> main：先把结果存到后端缓存，再让主窗口主动拉取（不依赖 event.listen，且避免大数组 IPC 失败）
#[tauri::command]
pub fn mini_prepare_transfer(
    keyword: String,
    mode: String,
    mut results: Vec<FileRecord>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    const MAX: usize = 20_000;
    let original_len = results.len();
    let truncated = original_len > MAX;
    if truncated {
        results.truncate(MAX);
    }

    let mut slot = state.mini_transfer.lock();
    *slot = Some(MiniTransfer {
        keyword,
        mode,
        results,
        truncated,
        original_len,
    });
    Ok(())
}

/// 主窗口拉取并清空一次性 transfer
#[tauri::command]
pub fn mini_take_transfer(state: State<'_, AppState>) -> Result<Option<MiniTransfer>, String> {
    let mut slot = state.mini_transfer.lock();
    Ok(slot.take())
}

/// 仅显示主窗口（不触发搜索），用于 mini Tab 切换
#[tauri::command]
pub async fn show_main_only(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(mini) = app.get_window("mini") {
        let _ = mini.hide();
    }
    if let Some(main) = app.get_window("main") {
        let _ = main.show();
        let _ = main.unminimize();
        let _ = main.set_focus();
    }
    Ok(())
}

#[tauri::command]
pub async fn hide_mini(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(mini) = app.get_window("mini") {
        mini.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn set_mini_expanded(expanded: bool, app: tauri::AppHandle) -> Result<(), String> {
    use tauri::{LogicalSize, Size};
    if let Some(mini) = app.get_window("mini") {
        let w = 720.0;
        // UI 已去掉状态/提示行：收起更紧凑，展开展示列表
        let h = if expanded { 440.0 } else { 86.0 };
        mini.set_size(Size::Logical(LogicalSize { width: w, height: h }))
            .map_err(|e| e.to_string())?;
        // 不要在展开/收起时改窗口位置：允许用户拖到任意位置持续搜索
        let _ = mini.set_focus();
    }
    Ok(())
}

#[tauri::command]
pub fn start_drag_mini(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(mini) = app.get_window("mini") {
        mini.start_dragging().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn clipboard_copy_files(paths: Vec<String>) -> Result<(), String> {
    crate::clipboard::set_clipboard_files(&paths, false).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn clipboard_cut_files(paths: Vec<String>) -> Result<(), String> {
    crate::clipboard::set_clipboard_files(&paths, true).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn clipboard_set_text(text: String) -> Result<(), String> {
    crate::clipboard::set_clipboard_text(&text).map_err(|e| e.to_string())
}

