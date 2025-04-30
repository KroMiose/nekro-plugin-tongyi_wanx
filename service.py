import asyncio
import time
from typing import Dict, List, Optional, Tuple

import httpx

from nekro_agent.api import message
from nekro_agent.api.core import logger
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.services.message.message_service import message_service

from .conf import config, store
from .models import (
    ChatSessionData,
    GlobalTaskData,
    HistoryRecord,
    TaskStatus,
    VideoTask,
)


async def load_global_tasks() -> GlobalTaskData:
    """加载全局任务数据"""
    data = await store.get(chat_key="global", store_key="tongyi_wanx_tasks")
    return GlobalTaskData.model_validate_json(data) if data else GlobalTaskData()


async def save_global_tasks(tasks_data: GlobalTaskData):
    """保存全局任务数据"""
    await store.set(chat_key="global", store_key="tongyi_wanx_tasks", value=tasks_data.model_dump_json())


async def load_chat_data(chat_key: str) -> ChatSessionData:
    """加载聊天会话数据"""
    data = await store.get(chat_key=chat_key, store_key="tongyi_wanx")
    return ChatSessionData.model_validate_json(data) if data else ChatSessionData()


async def save_chat_data(chat_key: str, data: ChatSessionData):
    """保存聊天会话数据"""
    await store.set(chat_key=chat_key, store_key="tongyi_wanx", value=data.model_dump_json())


async def get_next_task_id() -> str:
    """获取下一个任务ID"""
    global_tasks = await load_global_tasks()
    task_id = global_tasks.get_next_task_id()
    await save_global_tasks(global_tasks)
    return task_id


async def create_video_task(
    task_id: str,
    prompt: str,
    ctx: AgentCtx,
    reason: str,
    model: str,
    size: str,
    duration: int,
) -> VideoTask:
    """创建视频生成任务"""
    # 加载全局任务数据
    global_tasks = await load_global_tasks()

    # 创建新任务
    task = VideoTask.create(task_id, ctx.from_chat_key, prompt, reason, model, size, duration)
    global_tasks.add_task(task)
    await save_global_tasks(global_tasks)

    # 保存到聊天会话数据
    chat_data = await load_chat_data(ctx.from_chat_key)
    chat_data.current_task_id = task_id
    await save_chat_data(ctx.from_chat_key, chat_data)

    if config.REQUIRE_ADMIN_APPROVAL:
        # 发送审批消息给管理员
        manager_message = (
            f"【视频生成申请】\n"
            f"任务ID: {task_id}\n"
            f"会话: {ctx.from_chat_key}\n"
            f"提示词: {prompt}\n"
            f"原因: {reason}\n"
            f"模型: {model}\n"
            f"尺寸: {size}\n"
            f"时长: {duration}秒\n\n"
            f"使用 `/wanx-y {task_id}` 批准请求\n"
            f"使用 `/wanx-n {task_id}` 拒绝请求"
        )
        try:
            await message.send_text(
                chat_key=config.MANAGER_CHAT_KEY if config.MANAGER_CHAT_KEY else ctx.from_chat_key,
                message=manager_message,
                ctx=ctx,
                record=False,
            )
            logger.info(f"已发送视频生成审批请求: {task_id}")
        except Exception as e:
            logger.error(f"发送管理员消息失败: {e}")
    else:
        # 不需要审批，直接进入处理
        await update_task_status(task_id, TaskStatus.APPROVED)
        asyncio.create_task(process_video_task(task_id))

    return task


async def update_task_status(task_id: str, status: TaskStatus, **kwargs):
    """更新任务状态"""
    global_tasks = await load_global_tasks()
    if not global_tasks.update_task(task_id, status=status, **kwargs):
        logger.warning(f"尝试更新不存在的任务: {task_id}")
        return

    await save_global_tasks(global_tasks)
    task = global_tasks.get_task(task_id)
    if not task:
        return

    # 如果任务完成，添加到历史记录
    if status == TaskStatus.COMPLETED and task.video_url:
        chat_data = await load_chat_data(task.chat_key)
        chat_data.add_history(task.prompt, task.video_url, task_id)
        chat_data.current_task_id = None
        await save_chat_data(task.chat_key, chat_data)

        # 通知用户任务已完成
        completion_message = (
            f"【视频生成完成】\n"
            f"任务ID: {task_id}\n"
            f"提示词: {task.prompt}\n"
            f"视频已生成完毕!\n"
            f"视频URL: {task.video_url}"
        )

        try:
            await message_service.push_system_message(
                chat_key=task.chat_key,
                agent_messages=completion_message,
                trigger_agent=True,
            )
            logger.info(f"任务完成通知已发送: {task_id}")
        except Exception as e:
            logger.error(f"发送任务完成通知失败: {e}")

    # 如果任务失败，通知用户
    elif status == TaskStatus.FAILED:
        chat_data = await load_chat_data(task.chat_key)
        chat_data.current_task_id = None
        await save_chat_data(task.chat_key, chat_data)

        error_msg = task.error_message or "未知错误"
        failure_message = f"【视频生成失败】\n任务ID: {task_id}\n提示词: {task.prompt}\n错误信息: {error_msg}"

        try:
            await message_service.push_system_message(
                chat_key=task.chat_key,
                agent_messages=failure_message,
                trigger_agent=True,
            )
            logger.info(f"任务失败通知已发送: {task_id}")
        except Exception as e:
            logger.error(f"发送任务失败通知失败: {e}")


async def approve_task(task_id: str) -> bool:
    """批准任务"""
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(task_id)
    if not task:
        logger.warning(f"尝试批准不存在的任务: {task_id}")
        return False

    if task.status != TaskStatus.PENDING:
        logger.warning(f"尝试批准非待审批状态的任务: {task_id}, 当前状态: {task.status}")
        return False

    await update_task_status(task_id, TaskStatus.APPROVED)

    # 启动异步任务处理
    asyncio.create_task(process_video_task(task_id))
    return True


async def reject_task(task_id: str) -> bool:
    """拒绝任务"""
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(task_id)
    if not task:
        logger.warning(f"尝试拒绝不存在的任务: {task_id}")
        return False

    if task.status != TaskStatus.PENDING:
        logger.warning(f"尝试拒绝非待审批状态的任务: {task_id}, 当前状态: {task.status}")
        return False

    await update_task_status(task_id, TaskStatus.REJECTED, error_message="管理员拒绝了请求")
    return True


async def create_video_synthesis_task(task: VideoTask) -> Tuple[str, bool]:
    """创建视频合成任务"""
    url = f"{config.API_BASE_URL}/services/aigc/video-generation/video-synthesis"
    headers = {
        "X-DashScope-Async": "enable",
        "Authorization": f"Bearer {config.BAILIAN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": task.model,
        "input": {
            "prompt": task.prompt,
        },
        "parameters": {
            "size": task.size,
        },
    }

    # 如果提供了duration参数且非默认值
    if task.duration and task.duration != 5:
        payload["parameters"]["duration"] = task.duration

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                task_api_id = data.get("output", {}).get("task_id")
                return task_api_id, True
            error_msg = response.text
            logger.error(f"创建视频合成任务失败: {error_msg}")
            return f"错误: {error_msg}", False
    except Exception as e:
        logger.exception(f"创建视频合成任务异常: {e}")
        return f"异常: {e!s}", False


async def check_video_task_status(task_api_id: str) -> Tuple[TaskStatus, Optional[str], Optional[str]]:
    """检查视频任务状态"""
    url = f"{config.API_BASE_URL}/tasks/{task_api_id}"
    headers = {
        "Authorization": f"Bearer {config.BAILIAN_API_KEY}",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                api_status = data.get("output", {}).get("task_status")

                if api_status == "SUCCEEDED":
                    video_url = data.get("output", {}).get("video_url")
                    return TaskStatus.COMPLETED, video_url, None
                if api_status == "FAILED":
                    error_msg = str(data.get("output", {}).get("error", "未知错误"))
                    return TaskStatus.FAILED, None, error_msg
                return TaskStatus.PROCESSING, None, None
            error_msg = response.text
            logger.error(f"检查视频任务状态失败: {error_msg}")
            return TaskStatus.FAILED, None, f"状态检查失败: {error_msg}"
    except Exception as e:
        logger.exception(f"检查视频任务状态异常: {e}")
        return TaskStatus.PROCESSING, None, None  # 出错时继续尝试


async def process_video_task(task_id: str):
    """处理视频任务"""
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(task_id)
    if not task:
        logger.warning(f"尝试处理不存在的任务: {task_id}")
        return

    # 创建视频合成任务
    logger.info(f"开始处理任务 {task_id}: {task.prompt}")
    await update_task_status(task_id, TaskStatus.PROCESSING)

    task_api_id, success = await create_video_synthesis_task(task)
    if not success:
        await update_task_status(task_id, TaskStatus.FAILED, error_message=task_api_id)
        return

    # 更新任务API ID
    await update_task_status(task_id, TaskStatus.PROCESSING, task_api_id=task_api_id)

    # 轮询任务状态
    for _ in range(config.MAX_POLL_ATTEMPTS):
        # 等待一段时间
        await asyncio.sleep(config.POLL_INTERVAL)

        # 检查任务状态
        status, video_url, error_message = await check_video_task_status(task_api_id)

        if status == TaskStatus.COMPLETED:
            await update_task_status(task_id, status, video_url=video_url)
            logger.info(f"任务 {task_id} 完成: {video_url}")
            break
        if status == TaskStatus.FAILED:
            await update_task_status(task_id, status, error_message=error_message)
            logger.error(f"任务 {task_id} 失败: {error_message}")
            break
    else:
        # 超过最大尝试次数
        await update_task_status(task_id, TaskStatus.FAILED, error_message="任务超时")
        logger.warning(f"任务 {task_id} 超时")


async def validate_video_params(model: str, size: str, duration: int) -> Tuple[bool, str]:
    """验证视频参数有效性"""
    # 验证模型
    if model not in ["wanx2.1-t2v-turbo", "wanx2.1-t2v-plus"]:
        return False, f"不支持的模型: {model}，可选模型: wanx2.1-t2v-turbo, wanx2.1-t2v-plus"

    # 验证时长
    if duration <= 0 or duration > 10:
        return False, f"视频时长 {duration} 无效，必须在 1-10 秒范围内"

    # 预设分辨率组合
    valid_480p_ratios = ["832*480", "480*832", "624*624"]
    valid_720p_ratios = ["1280*720", "720*1280", "960*960", "832*1088", "1088*832"]

    # 验证尺寸
    if model == "wanx2.1-t2v-turbo":
        # turbo 模型支持 480P 和 720P
        if size not in valid_480p_ratios and size not in valid_720p_ratios:
            return False, (
                f"模型 {model} 不支持分辨率 {size}，"
                f"支持的 480P 分辨率: {', '.join(valid_480p_ratios)}, "
                f"支持的 720P 分辨率: {', '.join(valid_720p_ratios)}"
            )
    else:  # wanx2.1-t2v-plus
        # plus 模型只支持 720P
        if size not in valid_720p_ratios:
            return False, f"模型 {model} 不支持分辨率 {size}，支持的分辨率: {', '.join(valid_720p_ratios)}"

    return True, ""


def format_task_info(task: VideoTask) -> str:
    """格式化任务信息"""
    create_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.create_time))
    update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.update_time))

    info = (
        f"任务ID: {task.task_id}\n"
        f"会话: {task.chat_key}\n"
        f"提示词: {task.prompt}\n"
        f"状态: {task.status.value}\n"
        f"模型: {task.model}\n"
        f"尺寸: {task.size}\n"
        f"时长: {task.duration}秒\n"
        f"创建时间: {create_time}\n"
        f"更新时间: {update_time}\n"
    )

    if task.video_url:
        info += f"视频URL: {task.video_url}\n"

    if task.error_message:
        info += f"错误信息: {task.error_message}\n"

    return info


async def get_tasks_page(page: int) -> Tuple[List[VideoTask], int, int]:
    """获取任务分页数据"""
    global_tasks = await load_global_tasks()
    tasks_page = global_tasks.get_tasks_page(page, config.ITEMS_PER_PAGE)
    total_pages = global_tasks.get_total_pages(config.ITEMS_PER_PAGE)
    total_tasks = len(global_tasks.get_all_tasks())
    return tasks_page, total_pages, total_tasks
