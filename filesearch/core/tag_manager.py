"""
智能标签管理系统
支持给文件/文件夹打标签，按标签搜索，标签云展示
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class TagManager:
    """标签管理器"""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        初始化标签管理器
        
        Args:
            db_path: 标签数据库文件路径，默认为用户目录下的 .filesearch_tags.json
        """
        if db_path is None:
            db_path = os.path.join(
                os.path.expanduser('~'),
                '.filesearch_tags.json'
            )
        
        self.db_path = db_path
        self.tags_data = self._load_tags()
    
    def _load_tags(self) -> Dict:
        """加载标签数据"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"Loaded {len(data.get('file_tags', {}))} tagged items")
                    return data
            except Exception as e:
                logger.error(f"Error loading tags database: {e}")
        
        return {
            'file_tags': {},  # {file_path: [tag1, tag2, ...]}
            'tag_files': {},  # {tag: [file1, file2, ...]}
            'tag_colors': {},  # {tag: color_hex}
            'tag_descriptions': {},  # {tag: description}
            'metadata': {
                'version': '1.0',
                'created_at': datetime.now().isoformat(),
                'last_modified': datetime.now().isoformat(),
            }
        }
    
    def _save_tags(self) -> bool:
        """保存标签数据"""
        try:
            self.tags_data['metadata']['last_modified'] = datetime.now().isoformat()
            
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.tags_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Saved tags database to {self.db_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving tags database: {e}")
            return False
    
    def add_tag(self, file_path: str, tag: str) -> bool:
        """给文件添加标签"""
        # 标准化路径
        file_path = os.path.abspath(file_path)
        tag = tag.strip().lower()
        
        if not tag:
            return False
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return False
        
        # 添加到 file_tags
        if file_path not in self.tags_data['file_tags']:
            self.tags_data['file_tags'][file_path] = []
        
        if tag not in self.tags_data['file_tags'][file_path]:
            self.tags_data['file_tags'][file_path].append(tag)
        
        # 添加到 tag_files
        if tag not in self.tags_data['tag_files']:
            self.tags_data['tag_files'][tag] = []
        
        if file_path not in self.tags_data['tag_files'][tag]:
            self.tags_data['tag_files'][tag].append(file_path)
        
        return self._save_tags()
    
    def remove_tag(self, file_path: str, tag: str) -> bool:
        """从文件移除标签"""
        file_path = os.path.abspath(file_path)
        tag = tag.strip().lower()
        
        # 从 file_tags 移除
        if file_path in self.tags_data['file_tags']:
            if tag in self.tags_data['file_tags'][file_path]:
                self.tags_data['file_tags'][file_path].remove(tag)
            
            # 如果没有标签了，删除该文件记录
            if not self.tags_data['file_tags'][file_path]:
                del self.tags_data['file_tags'][file_path]
        
        # 从 tag_files 移除
        if tag in self.tags_data['tag_files']:
            if file_path in self.tags_data['tag_files'][tag]:
                self.tags_data['tag_files'][tag].remove(file_path)
            
            # 如果没有文件了，删除该标签记录
            if not self.tags_data['tag_files'][tag]:
                del self.tags_data['tag_files'][tag]
        
        return self._save_tags()
    
    def get_file_tags(self, file_path: str) -> List[str]:
        """获取文件的所有标签"""
        file_path = os.path.abspath(file_path)
        return self.tags_data['file_tags'].get(file_path, [])
    
    def get_files_by_tag(self, tag: str) -> List[str]:
        """获取具有指定标签的所有文件"""
        tag = tag.strip().lower()
        files = self.tags_data['tag_files'].get(tag, [])
        
        # 过滤不存在的文件
        return [f for f in files if os.path.exists(f)]
    
    def get_files_by_tags(self, tags: List[str], match_all: bool = False) -> List[str]:
        """
        获取具有指定标签的文件
        
        Args:
            tags: 标签列表
            match_all: True=文件必须包含所有标签(AND), False=包含任一标签(OR)
        """
        tags = [t.strip().lower() for t in tags if t.strip()]
        
        if not tags:
            return []
        
        if match_all:
            # AND 逻辑：文件必须包含所有标签
            file_sets = [set(self.get_files_by_tag(tag)) for tag in tags]
            if file_sets:
                result_files = set.intersection(*file_sets)
                return list(result_files)
            return []
        else:
            # OR 逻辑：文件包含任一标签
            result_files = set()
            for tag in tags:
                result_files.update(self.get_files_by_tag(tag))
            return list(result_files)
    
    def search_tags(self, query: str) -> List[str]:
        """搜索标签（模糊匹配）"""
        query = query.strip().lower()
        if not query:
            return self.get_all_tags()
        
        return [tag for tag in self.tags_data['tag_files'].keys() if query in tag]
    
    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        return list(self.tags_data['tag_files'].keys())
    
    def get_tag_count(self, tag: str) -> int:
        """获取标签关联的文件数量"""
        tag = tag.strip().lower()
        return len(self.get_files_by_tag(tag))
    
    def get_tag_cloud(self) -> List[Dict]:
        """
        获取标签云数据
        
        Returns:
            [{'tag': 'work', 'count': 15, 'color': '#ff0000'}, ...]
        """
        cloud = []
        for tag in self.tags_data['tag_files'].keys():
            count = self.get_tag_count(tag)
            if count > 0:
                cloud.append({
                    'tag': tag,
                    'count': count,
                    'color': self.tags_data['tag_colors'].get(tag, '#1976D2'),
                    'description': self.tags_data['tag_descriptions'].get(tag, ''),
                })
        
        # 按文件数量排序
        cloud.sort(key=lambda x: x['count'], reverse=True)
        return cloud
    
    def set_tag_color(self, tag: str, color: str) -> bool:
        """设置标签颜色"""
        tag = tag.strip().lower()
        self.tags_data['tag_colors'][tag] = color
        return self._save_tags()
    
    def set_tag_description(self, tag: str, description: str) -> bool:
        """设置标签描述"""
        tag = tag.strip().lower()
        self.tags_data['tag_descriptions'][tag] = description
        return self._save_tags()
    
    def rename_tag(self, old_tag: str, new_tag: str) -> bool:
        """重命名标签"""
        old_tag = old_tag.strip().lower()
        new_tag = new_tag.strip().lower()
        
        if old_tag == new_tag or not new_tag:
            return False
        
        if old_tag not in self.tags_data['tag_files']:
            return False
        
        # 更新 tag_files
        files = self.tags_data['tag_files'][old_tag]
        self.tags_data['tag_files'][new_tag] = files
        del self.tags_data['tag_files'][old_tag]
        
        # 更新 file_tags
        for file_path in files:
            if file_path in self.tags_data['file_tags']:
                tags = self.tags_data['file_tags'][file_path]
                if old_tag in tags:
                    tags[tags.index(old_tag)] = new_tag
        
        # 更新颜色和描述
        if old_tag in self.tags_data['tag_colors']:
            self.tags_data['tag_colors'][new_tag] = self.tags_data['tag_colors'][old_tag]
            del self.tags_data['tag_colors'][old_tag]
        
        if old_tag in self.tags_data['tag_descriptions']:
            self.tags_data['tag_descriptions'][new_tag] = self.tags_data['tag_descriptions'][old_tag]
            del self.tags_data['tag_descriptions'][old_tag]
        
        return self._save_tags()
    
    def delete_tag(self, tag: str) -> bool:
        """删除标签（从所有文件中移除）"""
        tag = tag.strip().lower()
        
        if tag not in self.tags_data['tag_files']:
            return False
        
        # 从所有文件中移除该标签
        files = self.tags_data['tag_files'][tag].copy()
        for file_path in files:
            self.remove_tag(file_path, tag)
        
        # 删除相关元数据
        if tag in self.tags_data['tag_colors']:
            del self.tags_data['tag_colors'][tag]
        
        if tag in self.tags_data['tag_descriptions']:
            del self.tags_data['tag_descriptions'][tag]
        
        return self._save_tags()
    
    def cleanup_missing_files(self) -> int:
        """清理已删除的文件，返回清理的数量"""
        removed_count = 0
        missing_files = []
        
        # 找出不存在的文件
        for file_path in list(self.tags_data['file_tags'].keys()):
            if not os.path.exists(file_path):
                missing_files.append(file_path)
        
        # 移除不存在的文件
        for file_path in missing_files:
            tags = self.tags_data['file_tags'][file_path]
            
            # 从每个标签中移除该文件
            for tag in tags:
                if tag in self.tags_data['tag_files']:
                    if file_path in self.tags_data['tag_files'][tag]:
                        self.tags_data['tag_files'][tag].remove(file_path)
                    
                    # 如果标签没有文件了，删除标签
                    if not self.tags_data['tag_files'][tag]:
                        del self.tags_data['tag_files'][tag]
            
            # 删除文件记录
            del self.tags_data['file_tags'][file_path]
            removed_count += 1
        
        if removed_count > 0:
            self._save_tags()
            logger.info(f"Cleaned up {removed_count} missing files")
        
        return removed_count
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_files': len(self.tags_data['file_tags']),
            'total_tags': len(self.tags_data['tag_files']),
            'avg_tags_per_file': (
                sum(len(tags) for tags in self.tags_data['file_tags'].values()) / 
                len(self.tags_data['file_tags'])
            ) if self.tags_data['file_tags'] else 0,
            'most_used_tag': max(
                self.tags_data['tag_files'].items(),
                key=lambda x: len(x[1])
            )[0] if self.tags_data['tag_files'] else None,
        }


__all__ = ['TagManager']
