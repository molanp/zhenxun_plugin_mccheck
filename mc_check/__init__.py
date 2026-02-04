import itertools

from nonebot.plugin import PluginMetadata
from nonebot import require
require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    CommandMeta,
    Match,
    Text,
    UniMessage,
    on_alconna,
    SupportScope
)
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginCdBlock, PluginExtraData, RegisterConfig

from .configs import VERSION, lang, lang_data
from .utils import (
    change_language_to,
    get_message_list,
    handle_exception,
    valid_urlparse,
)

__plugin_meta__ = PluginMetadata(
    name="Minecraft查服",
    description="Minecraft服务器状态查询，支持IPv6",
    usage="""
    Minecraft服务器状态查询，支持IPv6
    用法：
        查服 [ip]:[端口] / 查服 [ip]
        设置语言 zh-cn
        当前语言
        语言列表
    eg:
        mcheck ip:port / mcheck ip
        set_lang en
        lang_now
        lang_list
    """.strip(),
    extra=PluginExtraData(
        author="molanp",
        version=VERSION,
        limits=[PluginCdBlock(result=None)],
        menu_type="一些工具",
        configs=[
            RegisterConfig(
                key="LANGUAGE",
                value="zh-cn",
                help="Change the language(zh-cn, zh-tw, en etc.)",
                default_value="zh-cn",
            ),
            RegisterConfig(
                key="type",
                value=0,
                help="结果发送类型，0为发送HTML，1为发送文本",
                default_value=0,
                type=int,
            ),
        ],
    ).dict(),
)

check = on_alconna(
    Alconna("mcheck", Args["host?", str], meta=CommandMeta(compact=True)),
    aliases={"查服"},
    priority=5,
    block=True,
)


lang_change = on_alconna(
    Alconna("set_lang", Args["language", str], meta=CommandMeta(compact=True)),
    aliases={"设置语言"},
    priority=5,
    block=True,
)

lang_now = on_alconna(
    Alconna("lang_now", meta=CommandMeta(compact=True)),
    aliases={"当前语言"},
    priority=5,
    block=True,
)

lang_list = on_alconna(
    Alconna("lang_list", meta=CommandMeta(compact=True)),
    aliases={"语言列表"},
    priority=5,
    block=True,
)


@check.handle()
async def _(host: Match[str], session: Uninfo):
    if not host.available:
        await check.finish(Text(f"{lang_data[lang]['where_ip']}"), reply_to=True)
    try:
        address, port = valid_urlparse(host.result)
    except ValueError:
        await check.finish(Text(f"{lang_data[lang]['where_ip']}"), reply_to=True)

    if port and not (0 < port <= 65535):
        await check.finish(Text(f"{lang_data[lang]['where_port']}"), reply_to=True)
    try:
        message_list = await get_message_list(address, port)
        if session.scope == SupportScope.qq_api:
            for m in message_list:
                await check.send(UniMessage(m), reply_to=True)
        else:
            message_list = list(itertools.chain.from_iterable(message_list))
            await check.send(UniMessage(message_list), reply_to=True)
    except BaseException as e:
        await check.send(handle_exception(e), reply_to=True)


@lang_change.handle()
async def _(language: str):
    if language:
        await lang_change.send(Text(change_language_to(language)), reply_to=True)
    else:
        await lang_change.send(Text("Language?"), reply_to=True)


@lang_now.handle()
async def _():
    await lang_now.send(Text(f"Language: {lang}."), reply_to=True)


@lang_list.handle()
async def _():
    i = "\n".join(list(lang_data.keys()))
    await lang_list.send(Text(f"Language List:\n{i}"), reply_to=True)
