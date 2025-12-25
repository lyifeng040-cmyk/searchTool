"""
文件内容全文搜索引擎
支持文本文件、代码文件的内容搜索，支持正则表达式
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import mimetypes

logger = logging.getLogger(__name__)


class ContentSearchEngine:
    """文件内容搜索引擎"""
    
    # 支持的文本文件扩展名
    TEXT_EXTENSIONS = {
        '.txt', '.log', '.md', '.markdown', '.rst',
        '.py', '.pyw', '.js', '.jsx', '.ts', '.tsx',
        '.java', '.c', '.cpp', '.h', '.hpp', '.cs',
        '.go', '.rs', '.php', '.rb', '.pl', '.sh', '.bash',
        '.html', '.htm', '.xml', '.css', '.scss', '.sass',
        '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
        '.sql', '.r', '.m', '.swift', '.kt', '.scala',
        '.vue', '.svelte', '.bat', '.cmd', '.ps1',
        '.tex', '.bib', '.asm', '.s', '.vb', '.vbs',
        '.csv', '.tsv', '.properties', '.conf',
    }
    
    # 最大文件大小 (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024
    
    # 常见文本编码
    ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'utf-16', 'ascii', 'latin-1']
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
    
    def is_text_file(self, file_path: str) -> bool:
        """判断是否为文本文件"""
        ext = Path(file_path).suffix.lower()
        if ext in self.TEXT_EXTENSIONS:
            return True
        
        # 使用 mimetypes 判断
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type and mime_type.startswith('text/'):
            return True
        
        return False
    
    def read_file_content(self, file_path: str) -> Optional[str]:
        """读取文件内容，自动检测编码"""
        try:
            # 检查文件大小
            file_size = os.path.getsize(file_path)
            if file_size > self.MAX_FILE_SIZE:
                logger.debug(f"File too large: {file_path} ({file_size} bytes)")
                return None
            
            # 尝试不同编码
            for encoding in self.ENCODINGS:
                try:
                    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                        content = f.read()
                    return content
                except (UnicodeDecodeError, LookupError):
                    continue
            
            logger.debug(f"Failed to decode file: {file_path}")
            return None
            
        except Exception as e:
            logger.debug(f"Error reading file {file_path}: {e}")
            return None
    
    def search_in_file(
        self, 
        file_path: str, 
        pattern: str, 
        is_regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 2
    ) -> Optional[Dict]:
        """在单个文件中搜索"""
        if not self.is_text_file(file_path):
            return None
        
        content = self.read_file_content(file_path)
        if not content:
            return None
        
        try:
            # 构建正则表达式
            if is_regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                regex = re.compile(pattern, flags)
            else:
                escaped_pattern = re.escape(pattern)
                flags = 0 if case_sensitive else re.IGNORECASE
                regex = re.compile(escaped_pattern, flags)
            
            # 按行搜索
            lines = content.split('\n')
            matches = []
            
            for line_num, line in enumerate(lines, 1):
                if regex.search(line):
                    # 获取上下文
                    start = max(0, line_num - context_lines - 1)
                    end = min(len(lines), line_num + context_lines)
                    context = lines[start:end]
                    
                    matches.append({
                        'line_number': line_num,
                        'line_content': line.rstrip(),
                        'context': context,
                        'context_start': start + 1,
                    })
            
            if matches:
                return {
                    'file_path': file_path,
                    'match_count': len(matches),
                    'matches': matches[:100],  # 限制最多返回100个匹配
                }
            
        except re.error as e:
            logger.error(f"Invalid regex pattern: {pattern} - {e}")
        except Exception as e:
            logger.debug(f"Error searching in {file_path}: {e}")
        
        return None
    
    def search_in_files(
        self,
        file_paths: List[str],
        pattern: str,
        is_regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 2,
        progress_callback=None
    ) -> List[Dict]:
        """在多个文件中搜索"""
        results = []
        total = len(file_paths)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.search_in_file,
                    file_path,
                    pattern,
                    is_regex,
                    case_sensitive,
                    context_lines
                ): file_path
                for file_path in file_paths
            }
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                    
                    if progress_callback:
                        progress_callback(i, total)
                        
                except Exception as e:
                    file_path = futures[future]
                    logger.debug(f"Error processing {file_path}: {e}")
        
        # 按匹配数量排序
        results.sort(key=lambda x: x['match_count'], reverse=True)
        return results
    
    def search_in_directory(
        self,
        directory: str,
        pattern: str,
        is_regex: bool = False,
        case_sensitive: bool = False,
        recursive: bool = True,
        file_pattern: str = None,
        context_lines: int = 2,
        progress_callback=None
    ) -> List[Dict]:
        """在目录中搜索"""
        # 收集所有文本文件
        file_paths = []
        
        if recursive:
            for root, dirs, files in os.walk(directory):
                # 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    if file.startswith('.'):
                        continue
                    
                    file_path = os.path.join(root, file)
                    
                    # 文件名过滤
                    if file_pattern:
                        if not re.search(file_pattern, file, re.IGNORECASE):
                            continue
                    
                    if self.is_text_file(file_path):
                        file_paths.append(file_path)
        else:
            try:
                for entry in os.scandir(directory):
                    if entry.is_file() and not entry.name.startswith('.'):
                        # 文件名过滤
                        if file_pattern:
                            if not re.search(file_pattern, entry.name, re.IGNORECASE):
                                continue
                        
                        if self.is_text_file(entry.path):
                            file_paths.append(entry.path)
            except Exception as e:
                logger.error(f"Error scanning directory {directory}: {e}")
                return []
        
        logger.info(f"Found {len(file_paths)} text files in {directory}")
        
        return self.search_in_files(
            file_paths,
            pattern,
            is_regex,
            case_sensitive,
            context_lines,
            progress_callback
        )


__all__ = ['ContentSearchEngine']
