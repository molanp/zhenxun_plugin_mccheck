import asyncio
import base64
import time
import traceback
from urllib.parse import urlparse

import httpx
from mcstatus import BedrockServer, JavaServer
from mcstatus.responses import (
    BedrockStatusResponse,
    JavaStatusResponse,
)
from nonebot import require

from zhenxun.services.log import logger

from .configs import VERSION, lang, lang_data, message_type
from .render import StatusData, parse_motd, render_status

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna import Image, Text


def handle_exception(e):
    error_message = str(e)
    logger.error(traceback.format_exc())
    return Text(f"[CrashHandle]{error_message}\n>>更多信息详见日志文件<<")


def change_language_to(language: str):
    global lang

    try:
        _ = lang_data[language]
    except KeyError:
        return f"No language named '{language}'!"
    else:
        if language == lang:
            return f"The language is already '{language}'!"
        lang = language
        return f"Change to '{language}' success!"


async def build_result(
    ms: JavaStatusResponse | BedrockStatusResponse,
    address: str,
    type: int = 0,
) -> list[Image | Text]:
    """
    根据类型构建并返回查询结果。

    :params ms: 包含服务器信息的对象。
    :params type: 结果类型，决定返回结果的格式，默认为0。
    """
    favicon = getattr(ms, "icon", None)
    delay = round(ms.latency)
    version = f"{ms.version.name} " + (getattr(ms, "map_name", None) or "")
    protocol_version = ms.version.protocol
    gamemode = getattr(ms, "gamemode", None)
    players = f"{ms.players.online}/{ms.players.max}"
    server_type = "Bedrock" if isinstance(ms, BedrockStatusResponse) else "Java"
    players_list = []
    if isinstance(ms, JavaStatusResponse):
        players_list = [pl.name for pl in ms.players.sample or []]
    if type == 0:
        image_data: StatusData = {
            "favicon": favicon,
            "version": version,
            "protocol_version": protocol_version,
            "address": address,
            "delay": f"{delay}ms",
            "gamemode": gamemode,
            "motd": ms.motd.raw,
            "players": players,
            "player_list": players_list,
            "type": server_type,
            "lang": lang_data[lang],
            "VERSION": VERSION,
        }
        pic = await asyncio.to_thread(render_status, image_data)
        return [Image(raw=pic)]
    else:
        plain_motd = "".join(
            text
            for text, _ in parse_motd(ms.motd.raw, bedrock=server_type == "Bedrock")
        )
        motd_part = f"\n{lang_data[lang]['motd']}{plain_motd}"
        version_part = f"\n{lang_data[lang]['version']}{version}"

    base_result = (
        f"<{server_type}>\n"
        f"{version_part}"
        f"\n{lang_data[lang]['protocol_version']}{protocol_version}"
        f"\n{lang_data[lang]['address']}{address}"
        f"\n{lang_data[lang]['delay']}{delay}ms"
    )

    if gamemode:
        base_result += f"\n{lang_data[lang]['gamemode']}{gamemode}"

    result = base_result + motd_part + f"\n{lang_data[lang]['players']}{players}"
    result += f"\n{lang_data[lang]['player_list']}{', '.join(players_list)}"
    return (
        [
            Text(result),
            Text("\nFavicon:"),
            Image(raw=base64.b64decode(favicon.split(",")[1])),
        ]
        if favicon
        else [Text(result)]
    )


async def get_bedrock(host: str, port: int | None) -> BedrockStatusResponse | None:
    """
    通过指定的主机名、端口和超时时间获取Minecraft Bedrock版服务器状态。

    :params host: 服务器的主机名。
    :params port: 服务器的端口号。

    :returns:
    - MineStat实例，包含服务器状态信息，如果服务器不在线返回None。
    - ConnStatus实例，包含服务器连接状态，如果服务器不在线返回None。
    """
    try:
        return await BedrockServer(host, port, 0.5).async_status(tries=11)
    except Exception:
        return None


NETHERNET_GAMEMODES = {0: "Survival", 1: "Creative", 2: "Adventure", 6: "Spectator"}


async def get_nethernet(host: str, port: int | None) -> BedrockStatusResponse | None:
    """
    查询使用 NetherNet 传输的基岩版服务器（1.26.51 起默认，1.26.60 起唯一支持）。

    这类服务器不回 RakNet ping，状态改由 TCP 端口上的 HTTP 接口 /v1/join 返回。

    :params host: 服务器的主机名。
    :params port: 服务器的端口号。

    :returns: BedrockStatusResponse实例，如果服务器不在线返回None。
    """
    url_host = f"[{host}]" if ":" in host else host
    url = f"http://{url_host}:{port or 19132}/v1/join"
    try:
        # 直连查询，避免系统代理影响结果和延迟
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            start = time.perf_counter()
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        info = resp.json()
        # 按 RakNet pong 的字段顺序组装，复用 mcstatus 的解析
        return BedrockStatusResponse.build(
            [
                "MCPE",
                str(info["name"]),
                int(info["protocol"]),
                str(info["version"]),
                int(info["players"]),
                int(info["maxPlayers"]),
                "",
                str(info.get("level") or ""),
                NETHERNET_GAMEMODES.get(info.get("gameType")),
            ],
            latency,
        )
    except Exception:
        return None


async def get_java(host: str, port: int | None) -> JavaStatusResponse | None:
    """
    通过指定的主机名、端口和超时时间获取Minecraft Java版服务器状态。

    :params host: 服务器的主机名。
    :params port: 服务器的端口号。

    :returns:
    - JavaStatusResponse实例，包含服务器状态信息，如果服务器不在线返回None。
    """
    address = f"{host}:{port}" if port else host
    try:
        return await (await JavaServer.async_lookup(address)).async_status(version=760)
    except Exception:
        return None


async def get_mc(
    ip: str, port: int | None
) -> tuple[JavaStatusResponse | None, BedrockStatusResponse | None]:
    """
    获取Java版和Bedrock版的MC服务器信息。
    基岩版优先用 RakNet ping，查不到时用 NetherNet 的结果。

    :params ip: 服务器的IP地址。
    :params port: 服务器的端口。

    返回:
    - 包含Java版和Bedrock版服务器信息的列表。
    """
    java, bedrock, nethernet = await asyncio.gather(
        asyncio.create_task(get_java(ip, port)),
        asyncio.create_task(get_bedrock(ip, port)),
        asyncio.create_task(get_nethernet(ip, port)),
    )
    return java, bedrock or nethernet


async def get_message_list(ip: str, port: int | None) -> list[list[Text | Image]]:
    """
    根据IP和端口获取消息列表。

    :params ip: 服务器的IP地址。
    :params port: 服务器的端口。

    :returns: 包含消息的列表。
    """
    results = await get_mc(ip, port)
    messages = []
    for ms in results:
        if ms:
            messages.append(await build_result(ms, ip, message_type))
    if not messages:
        messages.append(Text(f"{lang_data[lang]['CONNFAIL']}"))
    return messages


def valid_urlparse(address: str) -> tuple[str, int | None]:
    """Parses a string address like 127.0.0.1:25565 into host and port parts

    If the address doesn't have a specified port, None will be returned instead.

    :raises ValueError:
        Unable to resolve hostname of given address
    """
    tmp = urlparse(f"//{address}")
    if not tmp.hostname:
        raise ValueError(f"Invalid address '{address}', can't parse.")

    return tmp.hostname, tmp.port
