import time
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .conf import config


# 任务状态枚举
class TaskStatus(str, Enum):
    PENDING = "等待审批"
    APPROVED = "已批准"
    REJECTED = "已拒绝"
    PROCESSING = "处理中"
    COMPLETED = "已完成"
    FAILED = "失败"
    CANCELED = "已取消"


# 任务数据模型
class VideoTask(BaseModel):
    """视频生成任务"""

    task_id: str
    chat_key: str
    prompt: str
    reason: str
    status: TaskStatus
    task_api_id: Optional[str] = None
    video_url: Optional[str] = None
    error_message: Optional[str] = None
    create_time: int
    update_time: int
    model: str
    size: str
    duration: Optional[int] = 5  # 默认5秒

    @classmethod
    def create(cls, task_id: str, chat_key: str, prompt: str, reason: str, model: str, size: str, duration: int = 5):
        """创建一个新任务"""
        current_time = int(time.time())
        return cls(
            task_id=task_id,
            chat_key=chat_key,
            prompt=prompt,
            reason=reason,
            status=TaskStatus.PENDING,
            create_time=current_time,
            update_time=current_time,
            model=model,
            size=size,
            duration=duration,
        )


# 历史记录数据模型
class HistoryRecord(BaseModel):
    """视频生成历史记录"""

    prompt: str
    video_url: str
    create_time: int
    task_id: str

    @classmethod
    def create(cls, prompt: str, video_url: str, task_id: str):
        """创建一个新历史记录"""
        return cls(
            prompt=prompt,
            video_url=video_url,
            create_time=int(time.time()),
            task_id=task_id,
        )


# 聊天会话数据模型
class ChatSessionData(BaseModel):
    """聊天会话数据"""

    current_task_id: Optional[str] = None
    history_records: List[HistoryRecord] = []

    def add_history(self, prompt: str, video_url: str, task_id: str) -> HistoryRecord:
        """添加历史记录"""
        record = HistoryRecord.create(prompt, video_url, task_id)
        self.history_records.append(record)
        # 保留最近的记录
        if len(self.history_records) > config.MAX_HISTORY:
            self.history_records = self.history_records[-config.MAX_HISTORY :]
        return record


# 全局任务管理数据模型
class GlobalTaskData(BaseModel):
    """全局任务管理数据"""

    tasks: Dict[str, VideoTask] = {}
    task_counter: int = 0

    def add_task(self, task: VideoTask) -> None:
        """添加任务"""
        self.tasks[task.task_id] = task

    def get_task(self, task_id: str) -> Optional[VideoTask]:
        """获取任务"""
        return self.tasks.get(task_id)

    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务状态"""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        task.update_time = int(time.time())

        # 更新其他字段
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        return True

    def get_next_task_id(self) -> str:
        """获取下一个任务ID"""
        # 递增计数器
        self.task_counter += 1
        # 返回格式化的任务ID
        return f"W{self.task_counter:04d}"

    def get_all_tasks(self) -> List[VideoTask]:
        """获取所有任务"""
        return list(self.tasks.values())

    def get_tasks_page(self, page: int, items_per_page: int) -> List[VideoTask]:
        """获取指定页的任务"""
        all_tasks = sorted(self.tasks.values(), key=lambda x: x.create_time, reverse=True)
        start = (page - 1) * items_per_page
        end = start + items_per_page
        return all_tasks[start:end]

    def get_total_pages(self, items_per_page: int) -> int:
        """获取总页数"""
        total_tasks = len(self.tasks)
        return (total_tasks + items_per_page - 1) // items_per_page  # 向上取整
