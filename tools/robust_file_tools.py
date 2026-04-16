import os
import threading
import time
from pathlib import Path
from typing import Union, List

from core.config import BASE_DIR
from core.logger import logger

# 用于并发保护的锁管理器
class FileLockManager:
    def __init__(self):
        self._locks = {}
        self._manager_lock = threading.Lock()

    def get_lock(self, file_path: Path) -> threading.Lock:
        with self._manager_lock:
            if file_path not in self._locks:
                self._locks[file_path] = threading.Lock()
            return self._locks[file_path]

_lock_manager = FileLockManager()

def _resolve_and_check_path(path: Union[str, Path]) -> Path:
    """
    解析路径并进行越界校验
    """
    target_path = Path(path)
    if not target_path.is_absolute():
        target_path = BASE_DIR / target_path
        
    try:
        resolved_path = target_path.resolve()
        # 校验是否在 BASE_DIR 内，防止目录遍历漏洞 (例如 ../../etc/passwd)
        if not resolved_path.is_relative_to(BASE_DIR):
            logger.warning(f"越界访问警告: 尝试访问不在项目根目录的路径: {resolved_path}")
            raise PermissionError(f"越界访问错误: 拒绝访问项目根目录外的路径 {resolved_path}")
        return resolved_path
    except Exception as e:
        if isinstance(e, PermissionError):
            raise
        logger.error(f"路径解析失败 {target_path}: {e}", exc_info=True)
        raise ValueError(f"无效的路径: {target_path}")

def list_directory(path: Union[str, Path]) -> List[str]:
    """
    列出目录内容，增加目录存在性校验与越界访问防护。
    """
    target_path = _resolve_and_check_path(path)
    
    if not target_path.exists():
        logger.warning(f"目录不存在: {target_path}")
        raise FileNotFoundError(f"目录不存在: {target_path}")
    
    if not target_path.is_dir():
        logger.warning(f"路径不是一个目录: {target_path}")
        raise NotADirectoryError(f"路径不是一个目录: {target_path}")
        
    try:
        items = [item.name for item in target_path.iterdir()]
        logger.debug(f"成功读取目录 {target_path} 内容, 共 {len(items)} 项")
        return items
    except Exception as e:
        logger.error(f"读取目录失败 {target_path}: {e}", exc_info=True)
        raise

def read_file(file_path: Union[str, Path], max_size: int = 10 * 1024 * 1024) -> str:
    """
    读取文件，增加文件大小限制、编码错误处理及异常捕获。
    默认文件大小限制为 10MB。
    """
    target_path = _resolve_and_check_path(file_path)
        
    if not target_path.exists():
        logger.warning(f"文件不存在: {target_path}")
        raise FileNotFoundError(f"文件不存在: {target_path}")
        
    if not target_path.is_file():
        logger.warning(f"路径不是一个文件: {target_path}")
        raise IsADirectoryError(f"路径不是一个文件: {target_path}")
        
    try:
        file_size = target_path.stat().st_size
        if file_size > max_size:
            logger.warning(f"文件过大: {target_path} ({file_size} bytes)，超过了限制的 {max_size} bytes")
            raise ValueError(f"文件大小 ({file_size} bytes) 超过最大允许限制 {max_size} bytes。")
            
        # errors='replace' 会把无法解码的字节替换为 Unicode 替换字符，而不是抛出异常
        with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            logger.debug(f"成功读取文件 {target_path}")
            return content
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        logger.error(f"读取文件失败 {target_path}: {e}", exc_info=True)
        raise

def write_file(file_path: Union[str, Path], content: str, max_retries: int = 3, retry_delay: float = 0.5) -> bool:
    """
    写入文件，增加并发保护、目录自动创建及安全的重试机制。
    """
    target_path = _resolve_and_check_path(file_path)
        
    # 自动创建父目录
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"为文件 {target_path} 创建父目录失败: {e}", exc_info=True)
        raise
        
    file_lock = _lock_manager.get_lock(target_path)
    
    # 带重试机制的并发安全写入
    for attempt in range(max_retries):
        try:
            with file_lock:
                with open(target_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.debug(f"成功写入文件: {target_path}")
                return True
        except Exception as e:
            logger.warning(f"尝试写入文件 {target_path} 失败 (尝试次数 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.error(f"文件写入彻底失败 {target_path}，已重试 {max_retries} 次。", exc_info=True)
                raise
                
    return False
