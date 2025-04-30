import time
from typing import List

from nonebot import on_command
from nonebot.adapters import Bot, Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from nekro_agent.api.core import logger
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.matchers.command import command_guard, finish_with
from nekro_agent.services.plugin.base import SandboxMethodType
from nekro_agent.tools.common_util import limited_text_output

from .conf import config, plugin
from .models import TaskStatus
from .service import (
    approve_task,
    create_video_task,
    format_task_info,
    get_next_task_id,
    get_tasks_page,
    load_chat_data,
    load_global_tasks,
    reject_task,
    save_chat_data,
    save_global_tasks,
    validate_video_params,
)


@plugin.mount_prompt_inject_method(name="tongyi_wanx_prompt_inject")
async def tongyi_wanx_prompt_inject(_ctx: AgentCtx):
    """注入通义万相插件的上下文信息"""
    chat_data = await load_chat_data(_ctx.from_chat_key)
    global_tasks = await load_global_tasks()

    model = config.VIDEO_MODEL
    display_history = config.DISPLAY_HISTORY
    require_approval = config.REQUIRE_ADMIN_APPROVAL

    status_info = ""
    is_idle = True
    if chat_data.current_task_id:
        task = global_tasks.get_task(chat_data.current_task_id)
        if task:
            is_idle = False
            start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.create_time))
            duration = int(time.time()) - task.create_time
            status_info = (
                f"[Current Video Task]\n"
                f"- TaskID: {task.task_id}\n"
                f"- Prompt: {task.prompt}\n"
                f"- Reason: {task.reason}\n"
                f"- Model: {task.model}\n"
                f"- Size: {task.size}\n"
                f"- Status: {task.status.value}\n"
                f"- Start: {start_time}\n"
                f"- Elapsed: {duration}s\n"
                f"- Duration: {task.duration}s\n"
            )
            if task.error_message:
                status_info += f"- Error: {task.error_message}\n"
            if task.video_url:
                status_info += f"- VideoURL: {limited_text_output(task.video_url, limit=32)}\n"
    else:
        status_info = f"[No active video task]\nUse request_video_generation() to start a new task.\nDefault model: {model}.\n"

    history_info = ""
    if chat_data.history_records:
        recent_records = sorted(chat_data.history_records, key=lambda x: x.create_time, reverse=True)[:display_history]
        history_info = f"[Last {display_history} History]\n"
        for i, record in enumerate(recent_records, 1):
            create_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.create_time))
            history_info += (
                f"{i}. Prompt: {record.prompt} ({create_time})\n"
                f"   TaskID: {record.task_id}\n"
                f"   VideoURL: {limited_text_output(record.video_url, limit=32)}\n"
            )

    background_info = (
        f"[Plugin Usage]\n"
        f"- Use request_video_generation(prompt, reason, model, size, duration) to request video.\n"
        f"- Use get_video_url_by_task_id(task_id) to get the video URL by TaskID.\n"
        f"- Model: {model}, size/duration customizable.\n"
        f"- Admin approval required: {'yes' if require_approval else 'no'}.\n"
        f"- State: {'idle' if is_idle else 'busy'}.\n"
        "Notice: Injected info is only visible to YOU, not to the user."
    )

    prompt = background_info + "\n" + status_info
    if history_info:
        prompt += "\n" + history_info

    return prompt


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, name="请求视频生成", description="提交一个视频生成请求")
async def request_video_generation(
    _ctx: AgentCtx,
    prompt: str,
    reason: str,
    size: str = "1280*720",
    duration: int = 5,
):
    """Request video generation (async).

    Args:
        prompt (str): Video prompt. Please describe the scene, character actions, environment, and details as much as possible to improve video quality. 例如："一只蓝眼睛的小猫在明亮的满月下飞快奔跑，周围是安静的森林，树叶飞舞，动态镜头"。（请用用户的语言描述）
        reason (str): Reason for generation, e.g. "用户xxx想要..." (Use user language)
        size (str, optional): Video size, e.g. "1280*720". Turbo supports 480P/720P, plus only 720P. Default "1280*720".
        duration (int, optional): Video duration in seconds, default 5.

    Raises:
        ValueError: If params are invalid (unsupported model/size).
    """
    # 检查是否有正在进行的任务
    chat_data = await load_chat_data(_ctx.from_chat_key)
    global_tasks = await load_global_tasks()

    if chat_data.current_task_id:
        task = global_tasks.get_task(chat_data.current_task_id)
        if task and task.status in [TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.PROCESSING]:
            return f"已有正在进行的任务，请等待当前任务完成。当前任务状态: {task.status.value}"

    # 验证参数
    valid, error_msg = await validate_video_params(config.VIDEO_MODEL, size, duration)
    if not valid:
        raise ValueError(error_msg)

    # 获取新任务ID
    task_id = await get_next_task_id()

    # 创建新任务
    task = await create_video_task(task_id, prompt, _ctx, reason, config.VIDEO_MODEL, size, duration)

    approval_message = "(Requires admin approval)" if config.REQUIRE_ADMIN_APPROVAL else ""
    return f"Task {task_id} has been created{approval_message}. You can check the task status by injecting the prompt."


@plugin.mount_sandbox_method(
    SandboxMethodType.BEHAVIOR,
    name="cancel_current_video_task",
    description="Cancel the current video generation task for this session.",
)
async def cancel_current_video_task(_ctx: AgentCtx) -> str:
    """Cancel the current video generation task for this session.

    If there is an active task, set its status to CANCELED and clear the session's current_task_id.
    """
    chat_data = await load_chat_data(_ctx.from_chat_key)
    if not chat_data.current_task_id:
        raise ValueError("No active video task to cancel.")
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(chat_data.current_task_id)
    if not task:
        chat_data.current_task_id = None
        await save_chat_data(_ctx.from_chat_key, chat_data)
        return "No active video task to cancel."
    if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED]:
        chat_data.current_task_id = None
        await save_chat_data(_ctx.from_chat_key, chat_data)
        raise ValueError(f"Task {task.task_id} is already finished.")
    # Cancel the task
    task.status = TaskStatus.CANCELED
    chat_data.current_task_id = None
    await save_chat_data(_ctx.from_chat_key, chat_data)
    await save_global_tasks(global_tasks)
    return f"Task {task.task_id} has been canceled."


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="get_video_url_by_task_id",
    description="Get the video URL by TaskID.",
)
async def get_video_url_by_task_id(_ctx: AgentCtx, task_id: str) -> str:
    """Get the video URL for a finished video generation task by TaskID.

    Args:
        task_id (str): The TaskID of the video generation task.
    Returns:
        str: The video URL if available, or an error message.
    """
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found.")
    if not task.video_url:
        raise ValueError(f"Task {task_id} has no video URL yet. Status: {task.status.value}")
    return task.video_url


# 批准任务命令
@on_command("wanx_y", aliases={"wanx-y"}, priority=5, block=True).handle()
async def handle_approve(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """批准通义万相视频生成任务"""
    username, cmd_content, chat_key, chat_type = await command_guard(event, bot, arg, matcher)

    global_tasks = await load_global_tasks()
    pending_tasks = [t for t in global_tasks.get_all_tasks() if t.status == TaskStatus.PENDING]
    if not cmd_content or not cmd_content.strip():
        if not pending_tasks:
            await finish_with(matcher, message="当前没有待审批的任务")
        if len(pending_tasks) == 1:
            task_id = pending_tasks[0].task_id
        else:
            ids = ", ".join(t.task_id for t in pending_tasks)
            await finish_with(matcher, message=f"有多个待审批任务，请指定任务ID：{ids}")
    else:
        task_id = cmd_content.strip()
    task = global_tasks.get_task(task_id)
    if not task:
        await finish_with(matcher, message=f"任务 {task_id} 不存在")
    success = await approve_task(task_id)
    if success:
        await finish_with(matcher, message=f"已批准任务 {task_id}，开始执行视频生成")
    else:
        await finish_with(matcher, message=f"批准任务 {task_id} 失败，请检查任务状态")


# 拒绝任务命令
@on_command("wanx_n", aliases={"wanx-n"}, priority=5, block=True).handle()
async def handle_reject(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """拒绝通义万相视频生成任务"""
    username, cmd_content, chat_key, chat_type = await command_guard(event, bot, arg, matcher)

    global_tasks = await load_global_tasks()
    pending_tasks = [t for t in global_tasks.get_all_tasks() if t.status == TaskStatus.PENDING]
    if not cmd_content or not cmd_content.strip():
        if not pending_tasks:
            await finish_with(matcher, message="当前没有待审批的任务")
        if len(pending_tasks) == 1:
            task_id = pending_tasks[0].task_id
        else:
            ids = ", ".join(t.task_id for t in pending_tasks)
            await finish_with(matcher, message=f"有多个待审批任务，请指定任务ID：{ids}")
    else:
        task_id = cmd_content.strip()
    task = global_tasks.get_task(task_id)
    if not task:
        await finish_with(matcher, message=f"任务 {task_id} 不存在")
    success = await reject_task(task_id)
    if success:
        await finish_with(matcher, message=f"已拒绝任务 {task_id}")
    else:
        await finish_with(matcher, message=f"拒绝任务 {task_id} 失败，请检查任务状态")


# 查询任务列表命令
@on_command("wanx_list", aliases={"wanx-list", "wanx-ls", "wanx_ls"}, priority=5, block=True).handle()
async def handle_list(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """查询通义万相视频生成任务列表"""
    username, cmd_content, chat_key, chat_type = await command_guard(event, bot, arg, matcher)

    try:
        # 获取页码，默认为第1页
        page = 1
        if cmd_content:
            page = int(cmd_content.strip())
            if page < 1:
                page = 1
    except ValueError:
        await finish_with(matcher, message="页码必须是一个正整数")

    # 获取分页数据
    tasks_page, total_pages, total_tasks = await get_tasks_page(page)

    if not tasks_page:
        await finish_with(matcher, message="没有找到任何任务")

    # 构建任务列表信息
    tasks_info = f"任务列表 (第 {page}/{total_pages} 页，共 {total_tasks} 个任务):\n\n"

    for i, task in enumerate(tasks_page, 1):
        create_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.create_time))
        tasks_info += (
            f"{i}. 任务ID: {task.task_id}\n"
            f"   提示词: {task.prompt}\n"
            f"   状态: {task.status.value}\n"
            f"   创建时间: {create_time}\n\n"
        )

    if page < total_pages:
        tasks_info += f"使用 /wanx-list {page + 1} 查看下一页"

    await finish_with(matcher, message=tasks_info)


# 查询任务详情命令
@on_command("wanx_info", aliases={"wanx-info", "wanx-i", "wanx_i"}, priority=5, block=True).handle()
async def handle_info(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """查询通义万相视频生成任务详情"""
    username, cmd_content, chat_key, chat_type = await command_guard(event, bot, arg, matcher)

    if not cmd_content:
        await finish_with(matcher, message="请指定要查询的任务ID")

    task_id = cmd_content.strip()

    # 获取任务信息
    global_tasks = await load_global_tasks()
    task = global_tasks.get_task(task_id)
    if not task:
        await finish_with(matcher, message=f"任务 {task_id} 不存在")

    # 格式化任务详情
    task_info = f"任务详情:\n\n{format_task_info(task)}"

    await finish_with(matcher, message=task_info)


@plugin.mount_cleanup_method()
async def clean_up():
    """清理插件

    这个方法在插件卸载时会被调用，用于清理资源
    """
    logger.info("通义万相插件清理完毕")
