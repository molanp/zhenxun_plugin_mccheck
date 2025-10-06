import asyncio
import base64
import contextlib
import os
import re
import traceback
from typing import Literal

import dns.asyncresolver
import dns.exception
import dns.resolver
import idna
from nonebot import require
from nonebot_plugin_uninfo import Uninfo
import ujson

from zhenxun.services.log import logger

from .configs import VERSION, lang, lang_data, message_type
from .data_source import ConnStatus, MineStat, SlpProtocols

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
    ms: MineStat, address: str, type: int = 0
) -> list[Image | Text]:
    """
    根据类型构建并返回查询结果。

    :params ms: 包含服务器信息的对象。
    :params type: 结果类型，决定返回结果的格式，默认为0。
    """
    if type == 0:
        result = {
            "favicon": ms.favicon_b64 if ms.favicon else "no_favicon.png",
            "version": parse_motd2html(ms.version),
            "slp_protocol": str(ms.slp_protocol),
            "protocol_version": ms.protocol_version,
            "address": address,
            "ip": ms.address,
            "port": ms.port,
            "delay": f"{ms.latency}ms",
            "gamemode": ms.gamemode,
            "motd": parse_motd2html(ms.motd),
            "players": f"{ms.current_players}/{ms.max_players}",
            "player_list": (
                parse_motd2html("§r, ".join(ms.player_list)) if ms.player_list else None
            ),
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
        motd_part = f"\n{lang_data[lang]['motd']}{ms.stripped_motd}"
        version_part = f"\n{lang_data[lang]['version']}{ms.version}"

    base_result = (
        f"{version_part}"
        f"\n{lang_data[lang]['slp_protocol']}{ms.slp_protocol}"
        f"\n{lang_data[lang]['protocol_version']}{ms.protocol_version}"
        f"\n{lang_data[lang]['address']}{address}"
        f"\n{lang_data[lang]['ip']}{ms.address}"
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
    result += (
        f"\n{lang_data[lang]['player_list']}{', '.join(ms.player_list)}"
        if ms.player_list
        else ""
    )
    return (
        [
            Text(result),
            Text("\nFavicon:"),
            Image(raw=base64.b64decode(ms.favicon_b64.split(",")[1])),
        ]
        if ms.favicon and ms.favicon_b64
        else [Text(result)]
    )


async def get_mc(
    ip: str, port: int, ip_type: str, refer, timeout: int = 5
) -> list[tuple[MineStat | None, ConnStatus | None]]:
    """
    获取Java版和Bedrock版的MC服务器信息。

    :params ip: 服务器的IP地址。
    :params port: 服务器的端口。
    :params ip_type: 服务器的IP类型。
    :params refer: 服务器来源地址
    :params timeout: 请求超时时间，默认为5秒。

    返回:
    - list: 包含Java版和Bedrock版服务器信息的列表。
    """
    if ip_type.startswith("SRV"):
        return [await asyncio.to_thread(get_java, ip, port, ip_type, refer, timeout)]

    return list(
        await asyncio.gather(
            asyncio.to_thread(get_java, ip, port, ip_type, refer, timeout),
            asyncio.to_thread(get_bedrock, ip, port, ip_type, refer, timeout),
        )
    )


async def get_message_list(
    ip: str, port: int, timeout: int = 5
) -> list[list[Text | Image]]:
    """
    根据IP和端口获取消息列表。

    :params ip: 服务器的IP地址。
    :params port: 服务器的端口。
    :params timeout: 超时时间，默认为5秒。

    :returns: 包含消息的列表。
    """
    ip_groups = await get_origin_address(ip, port)
    messages = []
    results = await asyncio.gather(
        *(
            get_mc(ip_group[0], ip_group[1], ip_group[2], ip_group[3], timeout)
            for ip_group in ip_groups
        )
    )

    for ms in results:
        for i in ms:
            if i[0] is not None:
                messages.append(await build_result(i[0], ip, message_type))
    if not messages:
        messages.append(
            next(
                (
                    Text(f"{lang_data[lang][str(item[1])]}")
                    for ms in results
                    for item in ms
                    if item[1] != ConnStatus.CONNFAIL
                ),
                Text(f"{lang_data[lang][str(ConnStatus.CONNFAIL)]}"),
            )
        )
    return messages


def get_bedrock(
    host: str, port: int, ip_type: str, refer: str, timeout: int = 5
) -> tuple[MineStat | None, ConnStatus | None]:
    """
    通过指定的主机名、端口和超时时间获取Minecraft Bedrock版服务器状态。

    :params host: 服务器的主机名。
    :params port: 服务器的端口号。
    :params ip_type: 服务器地址类型。
    :params refer: 服务器地址来源。
    :params timeout: 连接超时时间，默认为5秒。

    :returns:
    - MineStat实例，包含服务器状态信息，如果服务器不在线返回None。
    - ConnStatus实例，包含服务器连接状态，如果服务器不在线返回None。
    """
    v6 = "IPv6" in ip_type
    result = MineStat(host, port, timeout, SlpProtocols.BEDROCK_RAKNET, refer, v6)

    if result.online:
        return result, ConnStatus.SUCCESS
    return None, result.connection_status


def get_java(
    host: str, port: int, ip_type: str, refer: str, timeout: int = 5
) -> tuple[MineStat | None, ConnStatus | None]:
    """
    通过指定的主机名、端口和超时时间获取Minecraft Java版服务器状态。

    :params host: 服务器的主机名。
    :params port: 服务器的端口号。
    :params ip_type: 服务器地址类型。
    :params refer: 服务器地址来源。
    :params timeout: 连接超时时间，默认为5秒。

    :returns:
    - MineStat实例，包含服务器状态信息，如果服务器不在线返回None。
    - ConnStatus实例，包含服务器连接状态，如果服务器不在线返回None。
    """
    v6 = "IPv6" in ip_type

    # Minecraft 1.4 & 1.5 (legacy SLP)
    result = MineStat(host, port, timeout, SlpProtocols.LEGACY, refer, v6)

    # Minecraft Beta 1.8 to Release 1.3 (beta SLP)
    if result.connection_status not in [ConnStatus.CONNFAIL, ConnStatus.SUCCESS]:
        result = MineStat(host, port, timeout, SlpProtocols.BETA, refer, v6)

    # Minecraft 1.6 (extended legacy SLP)
    if result.connection_status is not ConnStatus.CONNFAIL:
        result = MineStat(host, port, timeout, SlpProtocols.EXTENDED_LEGACY, refer, v6)

    # Minecraft 1.9+ (QUERY SLP)
    if result.connection_status is not ConnStatus.CONNFAIL:
        result = MineStat(host, port, timeout, SlpProtocols.QUERY, refer, v6)

    # Minecraft 1.7+ (JSON SLP)
    if result.connection_status is not ConnStatus.CONNFAIL:
        result = MineStat(host, port, timeout, SlpProtocols.JSON, refer, v6)

    if result.online:
        return result, ConnStatus.SUCCESS
    return None, result.connection_status


async def parse_host(host_name: str) -> tuple[str, int]:
    """
    解析主机名（可选端口）。

    该函数尝试从主机名中提取IP地址和端口号。如果主机名中未指定端口，
    则默认端口号为0。

    :params host_name: 主机名，可能包含端口。

    :returns: 一个元组，包含两个元素：
    - 第一个元素是主机的地址
    - 第二个元素是主机的端口号，如果主机名中未指定端口，则为0。
    """
    pattern = r"(?:\[(.+?)\]|(.+?))(?:[:：](\d+))?$"
    if not (match := re.match(pattern, host_name)):
        return host_name, 0

    address = match[1] or match[2]
    port = int(match[3]) if match[3] else None

    port = port if port is not None else 0

    return address, port


def is_validity_address(address: str) -> bool:
    """
    判断给定的地址是否为有效的域名或IP地址。

    :params address: 需要验证的地址，可以是域名地址或IP地址。

    :returns: 如果地址有效则返回True，否则返回False。
    """

    return (is_domain(address)) or (is_ipv4(address)) or (is_ipv6(address))


def is_domain(address: str) -> bool:
    """
    判断给定的地址是否为域名。

    :params address: 需要验证的地址。

    :returns: 如果地址为域名则返回True，否则返回False。
    """
    try:
        punycode_address = idna.encode(address).decode("utf-8")
    except idna.IDNAError:
        return False

    domain_pattern = re.compile(
        r"^(?!-)(?:[A-Za-z0-9-]{1,63}\.)+(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]{2,})$|^(localhost)$"
    )
    return bool(domain_pattern.match(punycode_address))


def is_ipv4(address: str) -> bool:
    """
    判断给定的地址是否为IPv4地址。

    :params address: 需要验证的地址。

    :returns: 如果地址为IPv4地址则返回True，否则返回False。
    """
    ipv4_pattern = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    match_ipv4 = ipv4_pattern.match(address)

    if not match_ipv4:
        return False

    parts = address.split(".")
    return not any(not part.isdigit() or not 0 <= int(part) <= 255 for part in parts)


def is_ipv6(address: str) -> bool:
    """
    判断给定的地址是否为IPv6地址。

    :params address: 需要验证的地址。

    :returns: 如果地址为IPv6地址则返回True，否则返回False。
    """
    ipv6_pattern = re.compile(
        r"^\s*((([0-9A-Fa-f]{1,4}:){7}([0-9A-Fa-f]{1,4}|:))|(([0-9A-Fa-f]{1,4}:){6}(:[0-9A-Fa-f]{1,4}|((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})|:))|(([0-9A-Fa-f]{1,4}:){5}(((:[0-9A-Fa-f]{1,4}){1,2})|:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})|:))|(([0-9A-Fa-f]{1,4}:){4}(((:[0-9A-Fa-f]{1,4}){1,3})|((:[0-9A-Fa-f]{1,4})?:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){3}(((:[0-9A-Fa-f]{1,4}){1,4})|((:[0-9A-Fa-f]{1,4}){0,2}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){2}(((:[0-9A-Fa-f]{1,4}){1,5})|((:[0-9A-Fa-f]{1,4}){0,3}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(([0-9A-Fa-f]{1,4}:){1}(((:[0-9A-Fa-f]{1,4}){1,6})|((:[0-9A-Fa-f]{1,4}){0,4}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:))|(:(((:[0-9A-Fa-f]{1,4}){1,7})|((:[0-9A-Fa-f]{1,4}){0,5}:((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}))|:)))(%.+)?\s*$"
    )
    match_ipv6 = ipv6_pattern.match(address)

    return bool(match_ipv6)


def get_ip_type(address: str) -> Literal["IPv4", "IPv6", "Domain"]:
    """获取地址类型"""
    if not is_validity_address(address):
        raise ValueError("Invalid address")
    if is_ipv4(address):
        return "IPv4"
    elif is_ipv6(address):
        return "IPv6"
    else:
        return "Domain"


async def get_origin_address(
    domain: str, ip_port: int = 0, is_resolve_srv=True
) -> list[tuple[str, int, Literal["IPv4", "IPv6", "SRV", "SRV-IPv4", "SRV-IPv6"], str]]:
    """
    获取地址所解析的A或AAAA记录，如果传入不是域名直接返回。
    同时返回地址是IPv6还是IPv4。
    如果地址是域名，首先尝试解析SRV记录。

    :params domain: 需要解析的地址。
    :params ip_port: 适用于IPv4和IPv6地址的默认端口号。
    :params is_resolve_srv: 是否解析SRV，默认True

    返回:
    :returns: 一个列表，包含一个元组，元组包含四个元素：
      - 第一个元素是解析后的IP地址
      - 第二个元素是地址的端口号
      - 第三个元素是地址的类型
      - 第四个元素是解析地址的来源域名或IP
    """
    ip_type = get_ip_type(domain)
    try:
        refer_domain = idna.encode(domain).decode("utf-8")
    except idna.IDNAError:
        refer_domain = domain

    if ip_type != "Domain":
        return [(domain, ip_port, ip_type, refer_domain)]
    data = []

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 10
    resolver.retries = 3  # type: ignore

    async def resolve_srv():
        with contextlib.suppress(
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
            dns.exception.Timeout,
            dns.resolver.NoNameservers,
            IndexError,
        ):
            srv_response = await resolver.resolve(f"_minecraft._tcp.{domain}", "SRV")
            for rdata in srv_response:
                srv_address = str(rdata.target).rstrip(".")  # type: ignore
                srv_port = rdata.port  # type: ignore
                ip_type = get_ip_type(srv_address)
                try:
                    srv_refer = idna.encode(srv_address).decode("utf-8")
                except idna.IDNAError:
                    srv_refer = srv_address
                if ip_type == "Domain":
                    srv_address_ = await get_origin_address(
                        srv_address, srv_port, False
                    )
                    srv_data = (
                        srv_address_[0][0],
                        srv_address_[0][1],
                        f"SRV-{srv_address_[0][2]}",
                        srv_address_[0][3],
                    )
                else:
                    srv_data = (
                        srv_address,
                        srv_port,
                        f"SRV-{ip_type}",
                        srv_refer,
                    )
                if not any(
                    entry[0] == srv_data[0]
                    and entry[1] == srv_data[1]
                    and entry[2].replace("SRV-", "") == srv_data[2].replace("SRV-", "")
                    for entry in data
                ):
                    data.append(srv_data)
                break

    async def resolve_aaaa():
        with contextlib.suppress(
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
            dns.exception.Timeout,
            dns.resolver.NoNameservers,
        ):
            response = await resolver.resolve(domain, "AAAA")
            for rdata in response:
                data.append((str(rdata.address), ip_port, "IPv6", refer_domain))  # type: ignore
                break

    async def resolve_a():
        with contextlib.suppress(
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
            dns.exception.Timeout,
            dns.resolver.NoNameservers,
        ):
            response = await resolver.resolve(domain, "A")
            for rdata in response:
                data.append((str(rdata.address), ip_port, "IPv4", refer_domain))  # type: ignore
                break

    if is_resolve_srv:
        await asyncio.gather(resolve_aaaa(), resolve_a(), resolve_srv())
    else:
        await asyncio.gather(resolve_aaaa(), resolve_a())

    return data


def parse_motd2html(data: str | None) -> str:
    """
    解析MOTD数据并转换为带有自定义颜色的HTML字符串。

    :params data: MOTD数据。

    :returns: 带有自定义颜色的HTML字符串。
    """
    if not data:
        return ""

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
        "bold": ("<b style='color:{};'>", "</b>"),
        "italic": ("<i style='color:{};'>", "</i>"),
        "underline": ("<u style='color:{};'>", "</u>"),
        "strikethrough": ("<s style='color:{};'>", "</s>"),
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
        "§j": ('<span style="color:#443A3B;">', "</span>"),  # material netherite
        "§l": ("<b style='color:{};'>", "</b>"),  # bold
        "§m": ("<s style='color:{};'>", "</s>"),  # strikethrough
        "§n": ("<u style='color:{};'>", "</u>"),  # underline
        "§o": ("<i style='color:{};'>", "</i>"),  # italic
        "§p": ('<span style="color:#DEB12D;">', "</span>"),  # material gold
        "§q": ('<span style="color:#47A036;">', "</span>"),  # material emerald
        "§r": ("</b></i></u></s>", ""),  # reset
        "§s": ('<span style="color:#2CBAA8;">', "</span>"),  # material diamond
        "§t": ('<span style="color:#21497B;">', "</span>"),  # material lapis
        "§u": ('<span style="color:#9A5CC6;">', "</span>"),  # material amethyst
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
            if text[i : i + 2] == "\n":
                result += "<br>"
                i += 2
                continue
            result += text[i]
            i += 1

        # 在字符串末尾关闭所有打开的样式
        for tag in styles:
            result += tag

        return result

    def parse_json_motd(json, styles=[]) -> str:
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
        return result.replace("\n", "<br>")

    try:
        data = ujson.loads(data)
    except ujson.JSONDecodeError:
        return parse_text_motd(data)

    return parse_json_motd(data)


def is_qbot(session: Uninfo) -> bool:
    """判断bot是否为qq官bot

    参数:
        session: Uninfo

    返回:
        bool: 是否为官bot
    """
    return session.scope == SupportScope.qq_api
