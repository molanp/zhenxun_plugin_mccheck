import contextlib
import io
import re
import ujson
import os
import idna
import dns.asyncresolver
import dns.name
import base64
import traceback
import asyncio
from zhenxun.services.log import logger  # type: ignore
from .data_source import MineStat, SlpProtocols, ConnStatus
from .configs import lang_data, lang, message_type, VERSION
from PIL import Image, ImageDraw, ImageFont
from typing import Optional, Tuple, List
from nonebot import require

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna import Text  # type: ignore # noqa: E402
from nonebot_plugin_alconna import Image as NImage  # type: ignore # noqa: E402


async def handle_exception(e):
    error_message = str(e)
    logger.error(traceback.format_exc())
    return Text(f"[CrashHandle]{error_message}\n>>更多信息详见日志文件<<")


async def change_language_to(language: str):
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


async def build_result(ms, type=0):
    """
    根据类型构建并返回查询结果。

    参数:
    - ms: 包含服务器信息的对象。
    - type: 结果类型，决定返回结果的格式，默认为0。

    返回:
    - 根据类型不同返回不同格式的查询结果。
    """
    if type == 0:
        result = {
            "favicon": ms.favicon_b64 if ms.favicon else "no_favicon.png",
            "version": await parse_motd2html(ms.version),
            "slp_protocol": str(ms.slp_protocol),
            "protocol_version": ms.protocol_version,
            "address": ms.address,
            "port": ms.port,
            "delay": f"{ms.latency}ms",
            "gamemode": ms.gamemode,
            "motd": await parse_motd2html(ms.motd),
            "players": f"{ms.current_players}/{ms.max_players}",
            "player_list": await parse_motd2html("§r, ".join(ms.player_list)) if ms.player_list else None,
            "lang": lang_data[lang],
            "VERSION": VERSION,
        }
        from nonebot_plugin_htmlrender import template_to_pic

        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        pic = await template_to_pic(
            template_path=template_dir,
            template_name="default.html",
            templates={"data": result},
        )
        return NImage(raw=pic)
    elif type == 1:
        motd_part = f"\n{lang_data[lang]['motd']}{await parse_motd2mark(ms.motd)}[#RESET]"
        version_part = (
            f"\n{lang_data[lang]['version']}{await parse_motd2mark(ms.version)}[#RESET]"
        )
    elif type == 2:
        motd_part = f"\n{lang_data[lang]['motd']}{ms.stripped_motd}"
        version_part = f"\n{lang_data[lang]['version']}{ms.version}"

    base_result = (
        f"{version_part}"
        f"\n{lang_data[lang]['slp_protocol']}{ms.slp_protocol}"
        f"\n{lang_data[lang]['protocol_version']}{ms.protocol_version}"
        f"\n{lang_data[lang]['address']}{ms.address}"
        f"\n{lang_data[lang]['port']}{ms.port}"
        f"\n{lang_data[lang]['delay']}{ms.latency}ms"
    )

    if "BEDROCK" in str(ms.slp_protocol):
        base_result += f"\n{lang_data[lang]['gamemode']}{ms.gamemode}"

    result = (
        base_result
        + motd_part
        + f"\n{lang_data[lang]['players']}{ms.current_players}/{ms.max_players}"
    )
    if type == 1:
        result += f"\n{lang_data[lang]['player_list']}{await parse_motd2mark(', '.join(ms.player_list))}[#RESET]" if ms.player_list else ""
        return (
            [
                NImage(
                    raw=(
                        await ColoredTextImage(result).draw_text_with_style()
                    ).pic2bytes()
                ),
                Text("Favicon:"),
                NImage(raw=base64.b64decode(ms.favicon_b64.split(",")[1])),
            ]
            if ms.favicon is not None
            else NImage(
                raw=(await ColoredTextImage(result).draw_text_with_style()).pic2bytes()
            )
        )
    elif type == 2:
        result += f"\n{lang_data[lang]['player_list']}{', '.join(ms.player_list)}" if ms.player_list else ""
        return (
            [
                Text(result),
                Text("\nFavicon:"),
                NImage(raw=base64.b64decode(ms.favicon_b64.split(",")[1])),
            ]
            if ms.favicon is not None
            else [Text(result)]
        )


async def get_mc(java, bedrock, timeout: int = 5
                 ) -> List[Tuple[Optional[MineStat], Optional[ConnStatus]]]:
    """
    获取Java版和Bedrock版的MC服务器信息。

    参数:
    - host (str): 服务器的IP地址。
    - port (int): 服务器的端口。
    - timeout (int): 请求超时时间，默认为5秒。

    返回:
    - list: 包含Java版和Bedrock版服务器信息的列表，如果列表为空则返回None。
    """
    loop = asyncio.get_event_loop()
    return [await loop.run_in_executor(None, get_java, java[0], java[1], timeout), await loop.run_in_executor(None, get_bedrock, bedrock[0], bedrock[1], timeout)]


async def get_message_list(ip: str, port: int, timeout: int = 5) -> List[Text]:
    """
    异步函数，根据IP和端口获取消息列表。

    参数:
    - ip (str): 服务器的IP地址。
    - port (int): 服务器的端口。
    - timeout (int, 可选): 超时时间，默认为5秒。

    返回:
    - list: 包含消息的列表。
    """
    srv = await resolve_srv(ip, port)
    assert isinstance(srv[0], str)
    messages = []
    ms = await get_mc((srv[0], int(srv[1])), (ip, port), timeout)
    for i in ms:
        if i[0] is not None:
            messages.append(await build_result(i[0], message_type))
    if not messages:
        messages.append(
            next(
                (
                    Text(f"{lang_data[lang][str(item[1])]}")
                    for item in ms
                    if item[1] != ConnStatus.CONNFAIL
                ),
                Text(f"{lang_data[lang][str(ConnStatus.CONNFAIL)]}"),
            )
        )
    return messages


def get_bedrock(
    host: str, port: int, timeout: int = 5
) -> Tuple[Optional[MineStat], Optional[ConnStatus]]:
    """
    异步函数，用于通过指定的主机名、端口和超时时间获取Minecraft Bedrock版服务器状态。

    参数:
    - host: 服务器的主机名。
    - port: 服务器的端口号。
    - timeout: 连接超时时间，默认为5秒。

    返回:
    - MineStat实例，包含服务器状态信息，如果服务器在线的话；否则可能返回None。
    """
    result = MineStat(host, port, timeout, SlpProtocols.BEDROCK_RAKNET)

    if result.online:
        return result, ConnStatus.SUCCESS
    if result.connection_status == ConnStatus.UNKNOWN:
        return None, ConnStatus.CONNFAIL
    else:
        return None, result.connection_status


def get_java(
    host: str, port: int, timeout: int = 5
) -> Tuple[Optional[MineStat], Optional[ConnStatus]]:
    """
    异步函数，用于通过指定的主机名、端口和超时时间获取Minecraft Java版服务器状态。

    参数:
    - host: 服务器的主机名。
    - port: 服务器的端口号。
    - timeout: 连接超时时间，默认为5秒。

    返回:
    - MineStat 实例，包含服务器状态信息，如果服务器在线的话；否则可能返回 None。
    """
    # Minecraft 1.4 & 1.5 (legacy SLP)
    result = MineStat(host, port, timeout, SlpProtocols.LEGACY)

    # Minecraft Beta 1.8 to Release 1.3 (beta SLP)
    if result.connection_status not in [ConnStatus.CONNFAIL, ConnStatus.SUCCESS]:
        result = MineStat(host, port, timeout, SlpProtocols.BETA)

    # Minecraft 1.6 (extended legacy SLP)
    if result.connection_status is not ConnStatus.CONNFAIL:
        result = MineStat(host, port, timeout, SlpProtocols.EXTENDED_LEGACY)

    # Minecraft 1.7+ (JSON SLP)
    if result.connection_status is not ConnStatus.CONNFAIL:
        result = MineStat(host, port, timeout, SlpProtocols.JSON)

    if result.online:
        return result, ConnStatus.SUCCESS
    if result.connection_status == ConnStatus.UNKNOWN:
        return None, ConnStatus.CONNFAIL
    else:
        return None, result.connection_status


async def parse_host(host_name) -> Tuple[str, int]:
    """
    解析主机名（可选端口）。

    该函数尝试从主机名中提取IP地址和端口号。如果主机名中未指定端口，
    则默认端口号为0。

    参数:
    host_name (str): 主机名，可能包含端口。

    返回:
    Tuple[str, int]: 一个元组，包含两个元素：
    - 第一个元素是主机的IP地址（字符串形式）。
    - 第二个元素是主机的端口号（整数形式），如果主机名中未指定端口，则为0。
    """
    pattern = r"(?:\[(.+?)\]|(.+?))(?::(\d+))?$"
    if not (match := re.match(pattern, host_name)):
        return host_name, 0

    address = match[1] or match[2]
    port = int(match[3]) if match[3] else None

    port = port if port is not None else 0

    return address, port


async def is_invalid_address(address: str) -> bool:
    """
    异步判断给定的地址是否为无效的域名或IP地址。

    参数:
    address (str): 需要验证的地址，可以是电子邮件地址或IP地址。

    返回:
    bool: 如果地址无效则返回True，否则返回False。
    """
    # 域名正则（包含普通域名与 Punycode 支持）
    if address.lower() == "localhost":
        return False
    domain_pattern = r"^(?!-)(?:[A-Za-z0-9-]{1,63}\.)+(?:[A-Za-z]{2,})$|^(xn--[A-Za-z0-9-]{1,63})\.[A-Za-z]{2,}$"
    ipv4_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
    ipv6_pattern = r"^\s*((([0-9A-Fa-f]{1,4}:){7}([0-9A-Fa-f]{1,4}|:))|(([0-9A-Fa-f]{1,4}:){6}(:[0-9A-Fa-f]{1,4}|((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})|:))|(([0-9A-Fa-f]{1,4}:){5}(((:[0-9A-Fa-f]{1,4}){1,2})|:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})|:))|(([0-9A-Fa-f]{1,4}:){4}(((:[0-9A-Fa-f]{1,4}){1,3})|((:[0-9A-Fa-f]{1,4})?:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){3}(((:[0-9A-Fa-f]{1,4}){1,4})|((:[0-9A-Fa-f]{1,4}){0,2}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){2}(((:[0-9A-Fa-f]{1,4}){1,5})|((:[0-9A-Fa-f]{1,4}){0,3}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){1}(((:[0-9A-Fa-f]{1,4}){1,6})|((:[0-9A-Fa-f]{1,4}){0,4}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(:(((:[0-9A-Fa-f]{1,4}){1,7})|((:[0-9A-Fa-f]{1,4}){0,5}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:)))(%.+)?\s*$"

    # 尝试将域名转换为Punycode
    try:
        punycode_address = idna.encode(address).decode("utf-8")
        match_domain = re.match(domain_pattern, punycode_address)
    except idna.IDNAError:
        match_domain = None

    # 对IPv4和IPv6地址进行匹配
    match_ipv4 = re.match(ipv4_pattern, address)
    match_ipv6 = re.match(ipv6_pattern, address)

    # 如果三个匹配都失败，则返回True，表示无效
    return (match_domain is None) and (match_ipv4 is None) and (match_ipv6 is None)


async def resolve_srv(ip: str, port: int = 0) -> List[str | int]:
    """
    通过DNS解析服务器地址和端口。

    如果指定的端口为0，则表示需要通过SRV记录解析端口。

    参数:
        ip (str): 服务器IP地址。
        port (int): 服务器端口，如果为0则需要通过SRV记录获取。

    返回:
        list: 包含服务器地址和端口的列表。
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 10
    resolver.retries = 3  # type: ignore
    resolver.nameservers = ["223.5.5.5", "180.184.1.1", "1.1.1.1"]

    qname = dns.name.from_text(f"_minecraft._tcp.{ip}")

    with contextlib.suppress(dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):  # type: ignore
        response = await resolver.resolve(qname, "SRV")

        if not response:
            return [ip, port]

        for rdata in response:  # type: ignore
            address = str(rdata.target).rstrip(".")
            if port == 0:
                port = rdata.port
            return [address, port]
    return [ip, port]


async def parse_motd2mark(json_data: Optional[str]) -> Optional[str]:
    """
    解析MOTD数据并转换为带有自定义十六进制颜色标记的字符串。

    参数:
    - json_data (str | None): MOTD数据。

    返回:
    - str | None: 带有自定义十六进制颜色标记的字符串。
    """
    if json_data is None:
        return None

    standard_color_map = {
        "black": "[#000]",
        "dark_blue": "[#00A]",
        "dark_green": "[#0A0]",
        "dark_aqua": "[#0AA]",
        "dark_red": "[#A00]",
        "dark_purple": "[#A0A]",
        "gold": "[#FFA]",
        "gray": "[#AAA]",
        "dark_gray": "[#555]",
        "blue": "[#00F]",
        "green": "[#0F0]",
        "aqua": "[#0FF]",
        "red": "[#F00]",
        "light_purple": "[#FAF]",
        "yellow": "[#FF0]",
        "white": "[#FFF]",
        "reset": "[#RESET]",
        "bold": "[#BOLD]",
        "italic": "[#ITALIC]",
        "underline": "[#UNDERLINE]",
        "strikethrough": "[#STRIKETHROUGH]",
        "§0": "[#000]",  # black
        "§1": "[#00A]",  # dark blue
        "§2": "[#0A0]",  # dark green
        "§3": "[#0AA]",  # dark aqua
        "§4": "[#A00]",  # dark red
        "§5": "[#A0A]",  # dark purple
        "§6": "[#FFA]",  # gold
        "§7": "[#AAA]",  # gray
        "§8": "[#555]",  # dark gray
        "§9": "[#00F]",  # blue
        "§a": "[#0F0]",  # green
        "§b": "[#0FF]",  # aqua
        "§c": "[#F00]",  # red
        "§d": "[#FAF]",  # light purple
        "§e": "[#FF0]",  # yellow
        "§f": "[#FFF]",  # white
        "§g": "[#DDD605]",  # minecoin gold
        "§h": "[#E3D4D1]",  # material quartz
        "§i": "[#CECACA]",  # material iron
        "§j": "[#443A3B]",  # material netherite
        "§l": "[#BOLD]",  # bold
        "§m": "[#STRIKETHROUGH]",  # strikethrough
        "§n": "[#UNDERLINE]",  # underline
        "§o": "[#ITALIC]",  # italic
        "§p": "[#DEB12D]",  # material gold
        "§q": "[#47A036]",  # material emerald
        "§r": "[#RESET]",  # reset
        "§s": "[#2CBAA8]",  # material diamond
        "§t": "[#21497B]",  # material lapis
        "§u": "[#9A5CC6]",  # material amethyst
    }

    try:
        json_data = ujson.loads(json_data)
    except ujson.JSONDecodeError:
        result = ""
        i = 0
        while i < len(json_data):
            if json_data[i] == "§":
                style_code = json_data[i: i + 2]
                if style_code in standard_color_map:
                    result += standard_color_map[style_code]
                    i += 2
                    continue
            result += json_data[i]
            i += 1

        return result

    async def parse_extra(extra):
        result = ""
        extra_str = ""
        if isinstance(extra, dict) and "extra" in extra:
            for key in extra:
                if key == "extra":
                    result += await parse_extra(extra[key])
                elif key == "text":
                    result += await parse_extra(extra[key])
        elif isinstance(extra, dict):
            color = extra.get("color", "")
            text = extra.get("text", "")

            if color.startswith("#"):
                hex_color = color[1:]
                if len(hex_color) == 3:
                    hex_color = "".join([c * 2 for c in hex_color])
                color_str = f"[#{hex_color.upper()}]"
            else:
                color_str = standard_color_map.get(color, "")

            if extra.get("bold") is True:
                extra_str += standard_color_map["bold"]
            if extra.get("italic") is True:
                extra_str += standard_color_map["italic"]
            if extra.get("underline") is True:
                extra_str += standard_color_map["underline"]
            if extra.get("strikethrough") is True:
                extra_str += standard_color_map["strikethrough"]

            result += f"{extra_str}{color_str}{text}[#RESET]"
        elif isinstance(extra, list):
            for item in extra:
                result += await parse_extra(item)
        else:
            result += str(extra)

        return result

    return await parse_extra(json_data)


async def parse_motd2html(json_data: Optional[str]) -> Optional[str]:
    """
    解析MOTD数据并转换为带有自定义颜色的HTML字符串。

    参数:
    - json_data (str|None): MOTD数据。

    返回:
    - str | None: 带有自定义颜色的HTML字符串。
    """
    if json_data is None:
        return None

    standard_color_map = {
        "black": ('<span style="color:#000000;">', "</span>"),
        "dark_blue": ('<span style="color:#0000AA;">', "</span>"),
        "dark_green": ('<span style="color:#00AA00;">', "</span>"),
        "dark_aqua": ('<span style="color:#00AAAA;">', "</span>"),
        "dark_red": ('<span style="color:#AA0000;">', "</span>"),
        "dark_purple": ('<span style="color:#AA00AA;">', "</span>"),
        "gold": ('<span style="color:#FFAA00;">', "</span>"),
        "gray": ('<span style="color:#AAAAAA;">', "</span>"),
        "dark_gray": ('<span style="color:#555555;">', "</span>"),
        "blue": ('<span style="color:#0000FF;">', "</span>"),
        "green": ('<span style="color:#00AA00;">', "</span>"),
        "aqua": ('<span style="color:#00AAAA;">', "</span>"),
        "red": ('<span style="color:#AA0000;">', "</span>"),
        "light_purple": ('<span style="color:#FFAAFF;">', "</span>"),
        "yellow": ('<span style="color:#FFFF00;">', "</span>"),
        "white": ('<span style="color:#FFFFFF;">', "</span>"),
        "reset": ("</b></i></u></s>", ""),
        "bold": ("<b style='color: {};'>", "</b>"),
        "italic": ("<i style='color: {};'>", "</i>"),
        "underline": ("<u style='color: {};'>", "</u>"),
        "strikethrough": ("<s style='color: {};'>", "</s>"),
        "§0": ('<span style="color:#000000;">', "</span>"),  # black
        "§1": ('<span style="color:#0000AA;">', "</span>"),  # dark blue
        "§2": ('<span style="color:#00AA00;">', "</span>"),  # dark green
        "§3": ('<span style="color:#00AAAA;">', "</span>"),  # dark aqua
        "§4": ('<span style="color:#AA0000;">', "</span>"),  # dark red
        "§5": ('<span style="color:#AA00AA;">', "</span>"),  # dark purple
        "§6": ('<span style="color:#FFAA00;">', "</span>"),  # gold
        "§7": ('<span style="color:#AAAAAA;">', "</span>"),  # gray
        "§8": ('<span style="color:#555555;">', "</span>"),  # dark gray
        "§9": ('<span style="color:#0000FF;">', "</span>"),  # blue
        "§a": ('<span style="color:#00AA00;">', "</span>"),  # green
        "§b": ('<span style="color:#00AAAA;">', "</span>"),  # aqua
        "§c": ('<span style="color:#AA0000;">', "</span>"),  # red
        "§d": ('<span style="color:#FFAAFF;">', "</span>"),  # light purple
        "§e": ('<span style="color:#FFFF00;">', "</span>"),  # yellow
        "§f": ('<span style="color:#FFFFFF;">', "</span>"),  # white
        "§g": ('<span style="color:#DDD605;">', "</span>"),  # minecoin gold
        "§h": ('<span style="color:#E3D4D1;">', "</span>"),  # material quartz
        "§i": ('<span style="color:#CECACA;">', "</span>"),  # material iron
        # material netherite
        "§j": ('<span style="color:#443A3B;">', "</span>"),
        "§l": ("<b style='color: {};'>", "</b>"),  # bold
        "§m": ("<s style='color: {};'>", "</s>"),  # strikethrough
        "§n": ("<u style='color: {};'>", "</u>"),  # underline
        "§o": ("<i style='color: {};'>", "</i>"),  # italic
        "§p": ('<span style="color:#DEB12D;">', "</span>"),  # material gold
        "§q": ('<span style="color:#47A036;">', "</span>"),  # material emerald
        "§r": ("</b></i></u></s>", ""),  # reset
        "§s": ('<span style="color:#2CBAA8;">', "</span>"),  # material diamond
        "§t": ('<span style="color:#21497B;">', "</span>"),  # material lapis
        "§u": ('<span style="color:#9A5CC6;">', "</span>"),  # material amethyst
    }

    async def parse_extra(extra, styles=[]):
        result = ""
        if isinstance(extra, dict) and "extra" in extra:
            for key in extra:
                if key == "extra":
                    result += await parse_extra(extra[key], styles)
                elif key == "text":
                    result += await parse_extra(extra[key], styles)
        elif isinstance(extra, dict):
            color = extra.get("color", "")
            text = extra.get("text", "")

            # 将颜色转换为 HTML 的 font 标签
            if color.startswith("#"):
                hex_color = color[1:]
                if len(hex_color) == 3:
                    hex_color = "".join([c * 2 for c in hex_color])
                color_code = hex_color.upper()
                color_html_str = (
                    f'<span style="color:#{color_code};">', "</span>")
            else:
                color_html_str = standard_color_map.get(color, ("", ""))
                color_code = re.search(
                    r"color:\s*#([0-9A-Fa-f]{6});", color_html_str[0]
                )
                color_code = color_code[1] if color_code else "#FFFFFF"
            # 更新样式栈
            open_tag, close_tag = color_html_str
            if extra.get("bold") is True:
                open_tag_, close_tag_ = standard_color_map["bold"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if extra.get("italic") is True:
                open_tag_, close_tag_ = standard_color_map["italic"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if extra.get("underline") is True:
                open_tag_, close_tag_ = standard_color_map["underline"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            if extra.get("strikethrough") is True:
                open_tag_, close_tag_ = standard_color_map["strikethrough"]
                open_tag += open_tag_.format(color_code)
                close_tag = close_tag_ + close_tag
            styles.append(close_tag)
            result += open_tag + text + close_tag
        elif isinstance(extra, list):
            for item in extra:
                result += await parse_extra(item, styles)
        else:
            result += str(extra)
        return result.replace("\n", "<br>")

    try:
        json_data = ujson.loads(json_data)
    except ujson.JSONDecodeError:
        result = ""
        i = 0
        styles = []
        while i < len(json_data):
            if json_data[i] == "§":
                style_code = json_data[i: i + 2]
                if style_code in standard_color_map:
                    open_tag, close_tag = standard_color_map[style_code]

                    # 如果是重置，则清空样式栈
                    if open_tag == "</b></i></u></s>":
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
            if json_data[i: i + 2] == "\n":
                result += "<br>"
                i += 2
                continue
            result += json_data[i]
            i += 1

        # 在字符串末尾关闭所有打开的样式
        for tag in styles:
            result += tag

        return result

    return await parse_extra(json_data)


class ColoredTextImage:
    def __init__(
        self,
        text: str,
        background_color: tuple[int, int, int] = (249, 246, 242),
        padding: int = 10,
    ) -> None:
        """
        初始化一个用于绘制彩色文本图像的对象。
        """
        self.text = text
        self.padding = padding
        self.background_color = background_color
        self.font_path = os.path.join(
            os.path.dirname(__file__), "font", "Regular.ttf")
        self.bold_font_path = os.path.join(
            os.path.dirname(__file__), "font", "Bold.ttf"
        )
        self.bold_and_italic_font_path = os.path.join(
            os.path.dirname(__file__), "font", "Bold_Italic.ttf"
        )
        self.italic_font_path = os.path.join(
            os.path.dirname(__file__), "font", "Italic.ttf"
        )
        self.font_size = 40

    def _calculate_dimensions(self, text: str) -> tuple[int, int]:
        """
        计算图像的宽度和高度。
        """
        # Create a temporary image and draw object for calculating dimensions
        temp_image = Image.new("RGB", (1, 1))
        temp_draw = ImageDraw.Draw(temp_image)
        font = self.get_font()

        max_width, total_height = self._calculate_plain_text_dimensions(
            text, font, temp_draw
        )

        # Add padding to the dimensions
        width = max_width + 2 * self.padding
        height = total_height + 2 * self.padding
        return int(width), int(height)

    def _calculate_plain_text_dimensions(self, text, font, temp_draw):
        """
        计算普通文本的尺寸，忽略颜色标记。
        """
        # 移除颜色标记
        plain_text = re.sub(r"\[\#[^\]]*\]", "", text)

        max_width = 0
        total_height = 0
        line_height = self.font_size * 1.5

        for line in plain_text.split("\n"):
            bbox = temp_draw.textbbox((0, 0), line, font=font)
            width = bbox[2] - bbox[0] + 50
            max_width = max(max_width, width)
            total_height += line_height

        return max_width, total_height

    def get_font(
        self, bold: bool = False, italic: bool = False
    ) -> ImageFont.FreeTypeFont:
        """
        根据指定的样式获取字体。

        :param bold: 是否使用粗体，默认为 False。
        :param italic: 是否使用斜体，默认为 False。
        :return: 返回一个 `ImageFont.FreeTypeFont` 对象。
        """
        if bold and italic:
            return ImageFont.truetype(self.bold_and_italic_font_path, self.font_size)
        elif bold:
            return ImageFont.truetype(self.bold_font_path, self.font_size)
        elif italic:
            return ImageFont.truetype(self.italic_font_path, self.font_size)
        return ImageFont.truetype(self.font_path, self.font_size)

    async def draw_text_with_style(self) -> "ColoredTextImage":
        """
        使用指定样式绘制文本。

        :param text: 需要绘制的文本字符串。
        """
        text = self.text
        width, height = self._calculate_dimensions(text)
        self.image = Image.new("RGB", (width, height),
                               self.background_color)  # type: ignore
        self.draw = ImageDraw.Draw(self.image)
        await self._parse_style(text)
        return self

    async def _parse_style(self, text: str):
        """
        解析文本中的样式。
        """
        styles = {
            "bold": False,
            "italic": False,
            "underline": False,
            "strikethrough": False,
        }
        current_color = (0, 0, 0)
        line_height = self.font_size * 1.5
        x_offset = 50
        y_offset = 0

        fonts_cache = {}

        def get_font(
            bold: bool = False, italic: bool = False
        ) -> ImageFont.FreeTypeFont:
            key = (bold, italic)
            if key not in fonts_cache:
                fonts_cache[key] = self.get_font(bold, italic)
            return fonts_cache[key]

        for line in text.split("\n"):
            i = 0
            while i < len(line):
                if line[i] == "[":
                    end_tag = line.find("]", i)
                    if end_tag == -1:
                        break
                    tag = line[i: end_tag + 1]
                    if tag == "[#BOLD]":
                        styles["bold"] = True
                    elif tag == "[#ITALIC]":
                        styles["italic"] = True
                    elif tag == "[#UNDERLINE]":
                        styles["underline"] = True
                    elif tag == "[#STRIKETHROUGH]":
                        styles["strikethrough"] = True
                    elif tag == "[#RESET]":
                        styles = {key: False for key in styles}
                        current_color = (0, 0, 0)
                    elif tag.startswith("[#") and len(tag) > 2:
                        hex_color = tag[2:-1].upper()
                        if len(hex_color) == 3:
                            hex_color = "".join([c * 2 for c in hex_color])
                        try:
                            current_color = tuple(
                                int(hex_color[j: j + 2], 16) for j in (0, 2, 4)
                            )
                        except ValueError:
                            current_color = (0, 0, 0)
                    i = end_tag + 1
                    continue

                char = line[i]
                font_mod = get_font(styles["bold"], styles["italic"])
                bbox = self.draw.textbbox(
                    (x_offset, y_offset), char, font=font_mod)
                width = bbox[2] - bbox[0]

                self.draw.text(
                    (x_offset, y_offset), char, fill=current_color, font=font_mod
                )

                if styles["underline"]:
                    underline_y = y_offset + self.font_size
                    self.draw.line(
                        (x_offset, underline_y, x_offset + width, underline_y),
                        fill=current_color,
                        width=1,
                    )

                if styles["strikethrough"]:
                    strikethrough_y = y_offset + self.font_size / 2
                    self.draw.line(
                        (x_offset, strikethrough_y,
                         x_offset + width, strikethrough_y),
                        fill=current_color,
                        width=1,
                    )

                x_offset += width
                i += 1
            y_offset += line_height
            x_offset = 50

    def save(self, filename: str) -> None:
        """
        将当前图像保存到文件。

        :param filename: 文件名，包括路径和扩展名。
        """
        self.image.save(filename, format="PNG")

    def pic2bytes(self) -> bytes:
        """
        将当前图像转换为字节流。

        :return: 返回一个包含图像数据的字节流。
        """
        byte_io = io.BytesIO()
        self.image.save(byte_io, format="PNG")
        byte_io.seek(0)
        return byte_io.getvalue()
