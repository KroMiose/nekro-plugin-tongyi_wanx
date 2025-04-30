from pydantic import Field

from nekro_agent.services.plugin.base import ConfigBase, NekroPlugin

plugin = NekroPlugin(
    name="通义万相",
    module_name="tongyi_wanx",
    description="通义万相视频生成（异步版）",
    version="0.1.0",
    author="KroMiose",
    url="https://github.com/KroMiose/nekro-plugin-tongyi_wanx",
)


@plugin.mount_config()
class PluginConfig(ConfigBase):
    """基础配置"""

    BAILIAN_API_KEY: str = Field(
        default="",
        title="阿里云百炼 API Key",
        description="可在 <a href='https://bailian.console.aliyun.com/console?tab=model#/api-key'>阿里云百炼控制台</a> 获取",
        json_schema_extra={"is_secret": True},
    )
    MANAGER_CHAT_KEY: str = Field(
        default="",
        title="管理频道",
        description="用于处理视频生成审批请求的频道，未设置时将使用当前频道",
        json_schema_extra={"placeholder": "例: group_1234567890 / private_1234567890"},
    )
    REQUIRE_ADMIN_APPROVAL: bool = Field(
        default=True,
        title="是否需要管理员审批",
        description="视频生成是否需要管理员审批，关闭后将跳过审批直接开始生成任务（可能会导致 API 额度消耗剧增）",
    )
    API_BASE_URL: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        title="API 基础 URL",
        description="API 基础 URL",
    )
    VIDEO_MODEL: str = Field(
        default="wanx2.1-t2v-turbo",
        title="视频生成模型",
        description="视频生成模型，可选项: wanx2.1-t2v-turbo (高性价比/快速) 或 wanx2.1-t2v-plus (高质量/慢速)",
    )
    POLL_INTERVAL: int = Field(
        default=10,
        title="轮询间隔",
        description="任务状态轮询间隔（秒）",
    )
    MAX_POLL_ATTEMPTS: int = Field(
        default=120,
        title="最大轮询次数",
        description="最大轮询次数，默认 120 次（约 20 分钟）",
    )
    MAX_HISTORY: int = Field(
        default=99,
        title="最大历史记录",
        description="每个会话保存的最大历史生成结果数量",
    )
    DISPLAY_HISTORY: int = Field(
        default=3,
        title="显示历史数量",
        description="提示词注入中显示的历史生成结果数量",
    )
    ITEMS_PER_PAGE: int = Field(
        default=5,
        title="每页显示任务数",
        description="管理员查询任务列表时每页显示的任务数量",
    )


# 获取配置
config: PluginConfig = plugin.get_config(PluginConfig)
# 获取插件存储
store = plugin.store
