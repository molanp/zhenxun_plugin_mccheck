import asyncio
import base64
import json
import os
import re
import traceback
from typing import Any
from urllib.parse import urlparse

from mcstatus import BedrockServer, JavaServer
from mcstatus.responses import (
    BedrockStatusResponse,
    JavaStatusResponse,
)
from nonebot import require
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger

from .configs import VERSION, lang, lang_data, message_type

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna import Image, SupportScope, Text


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
    version = f"{ms.version.name} " + getattr(ms, "map_name", "")
    protocol_version = ms.version.protocol
    gamemode = getattr(ms, "gamemode", None)
    players = f"{ms.players.online}/{ms.players.max}"
    server_type = "Bedrock" if isinstance(ms, BedrockStatusResponse) else "Java"
    players_list = []
    if isinstance(ms, JavaStatusResponse):
        players_list = [pl.name for pl in ms.players.sample or []]
    if type == 0:
        result = {
            "favicon": favicon or "no_favicon.png",
            "version": parse_motd2html(version),
            "protocol_version": protocol_version,
            "address": address,
            "delay": f"{delay}ms",
            "gamemode": gamemode,
            "motd": parse_motd2html(ms.motd.raw), # pyright: ignore[reportArgumentType]
            "players": players,
            "player_list": parse_motd2html("§r, ".join(players_list))
            if players_list
            else None,
            "type": server_type,
            "lang": lang_data[lang],
            "VERSION": VERSION,
        }
        require("nonebot_plugin_htmlrender")
        from nonebot_plugin_htmlrender import template_to_pic

        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        pic = await template_to_pic(
            template_path=template_dir,
            template_name="default.html",
            templates={"data": result},
        )
        return [Image(raw=pic)]
    else:
        motd_part = f"\n{lang_data[lang]['motd']}{ms.motd.to_plain()}"
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
        return await (await JavaServer.async_lookup(address)).async_status()
    except Exception:
        return None


async def get_mc(
    ip: str, port: int | None
) -> tuple[JavaStatusResponse | None, BedrockStatusResponse | None]:
    """
    获取Java版和Bedrock版的MC服务器信息。

    :params ip: 服务器的IP地址。
    :params port: 服务器的端口。

    返回:
    - 包含Java版和Bedrock版服务器信息的列表。
    """
    return await asyncio.gather(
        asyncio.create_task(get_java(ip, port)),
        asyncio.create_task(get_bedrock(ip, port)),
    )


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


def is_qbot(session: Uninfo) -> bool:
    """判断bot是否为qq官bot

    参数:
        session: Uninfo

    返回:
        bool: 是否为官bot
    """
    return session.scope == SupportScope.qq_api


def parse_motd2html(data: str | dict) -> str:
    """
    解析MOTD数据并转换为带有自定义颜色的HTML字符串。

    :params data: MOTD数据。

    :returns: 带有自定义颜色的HTML字符串。
    """

    standard_color_map = {
        # Java Color Format, see: https://minecraft.wiki/w/Java_Edition_protocol/Chat?oldid=2763811#Colors
        "black": (
            '<span style="color:#000000; text-shadow:0 0 1px #000000;">',
            "</span>",
        ),
        "dark_blue": (
            '<span style="color:#0000AA; text-shadow:0 0 1px #00002A">',
            "</span>",
        ),
        "dark_green": (
            '<span style="color:#00AA00; text-shadow:0 0 1px #002A00">',
            "</span>",
        ),
        "dark_aqua": (
            '<span style="color:#00AAAA; text-shadow:0 0 1px #002A2A">',
            "</span>",
        ),
        "dark_red": (
            '<span style="color:#AA0000; text-shadow:0 0 1px #2A0000">',
            "</span>",
        ),
        "dark_purple": (
            '<span style="color:#AA00AA; text-shadow:0 0 1px #2A002A">',
            "</span>",
        ),
        "gold": (
            '<span style="color:#FFAA00; text-shadow:0 0 1px #2A2A00">',
            "</span>",
        ),
        "gray": (
            '<span style="color:#AAAAAA; text-shadow:0 0 1px #2A2A2A">',
            "</span>",
        ),
        "dark_gray": (
            '<span style="color:#555555; text-shadow:0 0 1px #151515">',
            "</span>",
        ),
        "blue": (
            '<span style="color:#5555FF; text-shadow:0 0 1px #15153F">',
            "</span>",
        ),
        "green": (
            '<span style="color:#55FF55; text-shadow:0 0 1px #153F15">',
            "</span>",
        ),
        "aqua": (
            '<span style="color:#55FFFF; text-shadow:0 0 1px #153F3F">',
            "</span>",
        ),
        "red": ('<span style="color:#FF5555; text-shadow:0 0 1px #3F1515">', "</span>"),
        "light_purple": (
            '<span style="color:#FF55FF; text-shadow:0 0 1px #3F153F">',
            "</span>",
        ),
        "yellow": (
            '<span style="color:#FFFF55; text-shadow:0 0 1px #3F3F15">',
            "</span>",
        ),
        "white": (
            '<span style="color:#FFFFFF; text-shadow:0 0 1px #3F3F3F">',
            "</span>",
        ),
        "reset": ("</b></i></u></s><reset/>", ""),
        "bold": ('<b style="color:{};">', "</b>"),
        "italic": ('<i style="color:{};">', "</i>"),
        "underline": ('<u style="color:{};">', "</u>"),
        "strikethrough": ('<s style="color:{};">', "</s>"),
        "obfuscated": ('<span style="color:{};" class="obfuscated">', "</span>"),
        # Bedrock Color Format, see: https://minecraft.wiki/w/Formatting_codes#Color_codes
        "§0": (
            '<span style="color:#000000; text-shadow:0 0 1px #000000">',
            "</span>",
        ),  # black
        "§1": (
            '<span style="color:#0000AA; text-shadow:0 0 1px #00002A">',
            "</span>",
        ),  # dark blue
        "§2": (
            '<span style="color:#00AA00; text-shadow:0 0 1px #002A00">',
            "</span>",
        ),  # dark green
        "§3": (
            '<span style="color:#00AAAA; text-shadow:0 0 1px #002A2A">',
            "</span>",
        ),  # dark aqua
        "§4": (
            '<span style="color:#AA0000; text-shadow:0 0 1px #2A0000">',
            "</span>",
        ),  # dark red
        "§5": (
            '<span style="color:#AA00AA; text-shadow:0 0 1px #2A002A">',
            "</span>",
        ),  # dark purple
        "§6": (
            '<span style="color:#FFAA00; text-shadow:0 0 1px #3E2A00">',
            "</span>",
        ),  # gold
        "§7": (
            '<span style="color:#C6C6C6; text-shadow:0 0 1px #313131">',
            "</span>",
        ),  # gray
        "§8": (
            '<span style="color:#555555; text-shadow:0 0 1px #151515">',
            "</span>",
        ),  # dark gray
        "§9": (
            '<span style="color:#5555FF; text-shadow:0 0 1px #15153F">',
            "</span>",
        ),  # blue
        "§a": (
            '<span style="color:#55FF55; text-shadow:0 0 1px #153F15">',
            "</span>",
        ),  # green
        "§b": (
            '<span style="color:#55FFFF; text-shadow:0 0 1px #153F3F">',
            "</span>",
        ),  # aqua
        "§c": (
            '<span style="color:#FF5555; text-shadow:0 0 1px #3F1515">',
            "</span>",
        ),  # red
        "§d": (
            '<span style="color:#FF55FF; text-shadow:0 0 1px #3F153F">',
            "</span>",
        ),  # light purple
        "§e": (
            '<span style="color:#FFFF55; text-shadow:0 0 1px #3F3F15">',
            "</span>",
        ),  # yellow
        "§f": (
            '<span style="color:#FFFFFF; text-shadow:0 0 1px #3F3F3F">',
            "</span>",
        ),  # white
        "§g": (
            '<span style="color:#DDD605; text-shadow:0 0 1px #373501">',
            "</span>",
        ),  # minecoin gold
        "§h": (
            '<span style="color:#E3D4D1; text-shadow:0 0 1px #383534">',
            "</span>",
        ),  # material quartz
        "§i": (
            '<span style="color:#CECACA; text-shadow:0 0 1px #333232">',
            "</span>",
        ),  # material iron
        "§j": (
            '<span style="color:#443A3B; text-shadow:0 0 1px #110E0E">',
            "</span>",
        ),  # material netherite
        "§k": ('<span style="color:{};" class="obfuscated">', "</span>"),  # obfuscated
        "§l": ('<b style="color:{};">', "</b>"),  # bold
        "§m": (
            '<span style="color:#971607; text-shadow:0 0 1px #250501;">',
            "</span>",
        ),  # material_redstone
        "§n": (
            '<span style="color:#B4684D; text-shadow:0 0 1px #2D1A13">',
            "</span>",
        ),  # material_copper
        "§o": ('<i style="color:{};">', "</i>"),  # italic
        "§p": (
            '<span style="color:#DEB12D; text-shadow:0 0 1px #372C0B">',
            "</span>",
        ),  # material gold
        "§q": (
            '<span style="color:#119F36; text-shadow:0 0 1px #04280D">',
            "</span>",
        ),  # material emerald
        "§r": ("</b></i></u></s><reset/>", ""),  # reset
        "§s": (
            '<span style="color:#2CBAA8; text-shadow:0 0 1px #0B2E2A">',
            "</span>",
        ),  # material diamond
        "§t": (
            '<span style="color:#21497B; text-shadow:0 0 1px #08121E">',
            "</span>",
        ),  # material lapis
        "§u": (
            '<span style="color:#9A5CC6; text-shadow:0 0 1px #261731">',
            "</span>",
        ),  # material amethyst
        "§v": (
            '<span style="color:#EB7114; text-shadow:0 0 1px #3B1D05">',
            "</span>",
        ),  # material_resin
    }

    def parse_text_motd(text: str) -> str:
        result = ""
        i = 0
        styles = []
        while i < len(text):
            if text[i] == "§":
                style_code = text[i : i + 2]
                if style_code in standard_color_map:
                    open_tag, close_tag = standard_color_map[style_code]

                    # 如果是重置，则清空样式栈
                    if open_tag == "</b></i></u></s><reset/>":
                        # 清空样式栈并关闭所有打开的样式
                        for tag in styles:
                            result += tag
                        styles.clear()
                    else:
                        styles.append(close_tag)
                        result += open_tag
                    i += 2
                    continue
            # 处理换行符
            if text[i : i + 2] == "\n":
                result += "<br/>"
                i += 2
                continue
            result += text[i]
            i += 1

        # 在字符串末尾关闭所有打开的样式
        for tag in styles:
            result += tag

        return result

    def parse_json_motd(json: dict | list | str, styles=[]) -> str:
        result = ""
        if isinstance(json, dict) and "extra" in json:
            for key in json:
                if key == "extra":
                    result += parse_json_motd(json[key], styles)
                elif key == "text":
                    result += parse_json_motd(json[key], styles)
        elif isinstance(json, dict):
            color = json.get("color", "")
            text = json.get("text", "")
            if "§" in text:
                text = parse_text_motd(text)

            # 将颜色转换为 HTML 的 font 标签
            if color.startswith("#"):
                hex_color = color[1:]
                if len(hex_color) == 3:
                    hex_color = "".join([c * 2 for c in hex_color])
                color_code = hex_color.upper()
                color_html_str = (f'<span style="color:#{color_code};">', "</span>")
            else:
                color_html_str = standard_color_map.get(color, ("", ""))
                color_code = re.search(
                    r"color:\s*(#[0-9A-Fa-f]{6});", color_html_str[0]
                )
                color_code = color_code[1] if color_code else "#FFFFFF"

            # 更新样式栈
            open_tag, close_tag = color_html_str
            if json.get("bold") is True:
                open_tag_, close_tag_ = standard_color_map["bold"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if json.get("italic") is True:
                open_tag_, close_tag_ = standard_color_map["italic"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if json.get("underline") is True:
                open_tag_, close_tag_ = standard_color_map["underline"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if json.get("strikethrough") is True:
                open_tag_, close_tag_ = standard_color_map["strikethrough"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            styles.append(close_tag)
            result += open_tag + text + close_tag
        elif isinstance(json, list):
            for item in json:
                result += parse_json_motd(item, styles)
        else:
            result += str(json)
        return result.replace("\n", "<br/>")

    try:
        if not isinstance(data, dict):
            data = json.loads(data)
    except json.JSONDecodeError:
        return parse_text_motd(data)

    return parse_json_motd(data)
