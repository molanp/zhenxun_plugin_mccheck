# minestat.py - A Minecraft server status checker
# Copyright (C) 2016-2023 Lloyd Dilley, Felix Ern (MindSolve)
# http://www.dilley.me/
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# 本文件由 @molanp 进行优化与需求定制
#
# 由于 wiki.vg 站点已关闭，现在你可以在
# https://minecraft.wiki/w/Minecraft_Wiki:Projects/wiki.vg_merge#Project_pages
# 找到原始内容的副本

import base64
from enum import Enum
import io
import json
import random
import re
import socket
import struct
from time import perf_counter, time


class ConnStatus(Enum):
    """
    包含可能的连接状态
    - `SUCCESS`：指定的 SLP 连接成功（请求和响应解析正常）
    - `CONNFAIL`：无法建立到服务器的套接字连接。服务器离线、主机名或端口错误？
    - `TIMEOUT`：连接超时。（服务器负载过高？防火墙规则是否正确？）
    - `UNKNOWN`：连接已建立，但服务器使用了未知或不支持的 SLP 协议
    """

    def __str__(self) -> str:
        return str(self.name)

    SUCCESS = 0
    """指定的 SLP 连接成功（请求和响应解析正常）"""

    CONNFAIL = -1
    """无法建立与服务器的套接字连接。（服务器离线，主机名或端口错误？）"""

    TIMEOUT = -2
    """连接超时。（服务器负载过高？防火墙规则是否正确？）"""

    UNKNOWN = -3
    """连接已建立，但服务器使用了未知或不支持的 SLP 协议"""


class SlpProtocols(Enum):
    """
    包含可能的 SLP（服务器列表 Ping）协议。

    - `ALL`：尝试所有协议。

      尝试使用所有可用协议连接到远程服务器，直到收到可接受的响应或失败为止。

    - `QUERY`：用于 Minecraft Java 服务器的 Query / GameSpot4 / UT3 协议。
      需要在 Minecraft 服务器上启用。
      Query 类似于 SLP，但会返回更多技术相关的数据。

      *自 Minecraft 1.9 起可用*

    - `BEDROCK_RAKNET`：Minecraft 基岩版/教育版协议。

      *适用于所有 Minecraft 基岩版版本，与 Java 版不兼容。*

    - `JSON`：最新且当前支持的 SLP 协议。

      使用（包装的）JSON 作为负载。复杂查询，详见 `json_query()` 的协议实现。

      *自 Minecraft 1.7 起可用*
    - `EXTENDED_LEGACY`：上一代 SLP 协议

      Minecraft 1.6 使用，所有新版本服务器仍兼容。
      需要复杂查询，详见 `extended_legacy_query()` 的完整协议细节。

      *自 Minecraft 1.6 起可用*
    - `LEGACY`：传统 SLP 协议。

      Minecraft 1.4 和 1.5 使用，是第一个包含服务器版本号的协议。
      非常简单的协议调用（2 字节），简单的响应解码。
      详见 `legacy_query()` 的完整实现和协议细节。

      *自 Minecraft 1.4 起可用*
    - `BETA`：第一个 SLP 协议。

      Minecraft Beta 1.8 到 Release 1.3 使用，是最早的 SLP 协议。
      包含的信息很少，没有服务器版本，仅有 MOTD、最大和在线玩家数。

      *自 Minecraft Beta 1.8 起可用*
    """

    def __str__(self) -> str:
        return str(self.name)

    ALL = 5
    """
    尝试所有协议。

    尝试使用所有可用协议连接到远程服务器，直到收到可接受的响应或失败为止。
    """

    QUERY = 6
    """
    用于 Minecraft Java 服务器的 Query / GameSpot4 / UT3 协议。
    需要在 Minecraft 服务器上启用。

    Query 类似于 SLP，但会返回更多技术相关的数据。

    *自 Minecraft 1.9 起可用*
    """

    BEDROCK_RAKNET = 4
    """
    Minecraft 基岩版/教育版协议。

    目前为实验性支持。
    """

    JSON = 3
    """
    最新且当前支持的 SLP 协议。

    使用（包装的）JSON 作为负载。复杂查询，详见 `json_query()` 的协议实现。

    *自 Minecraft 1.7 起可用*
    """

    EXTENDED_LEGACY = 2
    """
    上一代 SLP 协议

    Minecraft 1.6 使用，所有新版本服务器仍兼容。
    需要复杂查询，详见 `extended_legacy_query()` 的完整协议细节。

    *自 Minecraft 1.6 起可用*
    """

    LEGACY = 1
    """
    传统 SLP 协议。

    Minecraft 1.4 和 1.5 使用，是第一个包含服务器版本号的协议。
    非常简单的协议调用（2 字节），简单的响应解码。
    详见 `legacy_query()` 的完整实现和协议细节。

    *自 Minecraft 1.4 起可用*
    """

    BETA = 0
    """
    第一个 SLP 协议。

    Minecraft Beta 1.8 到 Release 1.3 使用，是最早的 SLP 协议。
    包含的信息很少，没有服务器版本，仅有 MOTD、最大和在线玩家数。

    *自 Minecraft Beta 1.8 起可用*
    """


class MineStat:
    VERSION = "2.6.4@molanp"
    """MineStat 版本"""
    DEFAULT_TCP_PORT = 25565
    """SLP 查询的默认 TCP 端口"""
    DEFAULT_BEDROCK_PORT_V4 = 19132
    """Bedrock/MCPE IPv4 服务器的默认 UDP 端口"""
    DEFAULT_BEDROCK_PORT_V6 = 19133
    """Bedrock/MCPE IPv6 服务器的默认 UDP 端口"""
    DEFAULT_TIMEOUT = 5
    """默认 TCP 超时时间（秒）"""

    def __init__(
        self,
        address: str,
        port: int = 0,
        timeout: int = DEFAULT_TIMEOUT,
        query_protocol: SlpProtocols = SlpProtocols.ALL,
        refer: str | None = None,
        use_ipv6: bool = False,
    ) -> None:
        """
        Minecraft 状态检查器,支持 Minecraft Java 版和基岩版/Education/PE 服务器

        :param address: Minecraft 服务器的 IP 地址。
        :param port: Minecraft 服务器的端口。默认为自动检测
        :param timeout: 每次连接尝试的超时时间。默认为 5 秒。
        :param query_protocol: 使用的协议。详见 minestat.SlpProtocols。默认为ALL
        :param refer: 发送数据包时使用的 IP 来源。默认使用 address。
        :param use_ipv6: 是否使用 IPv6 进行套接字连接。默认为 False。
        """

        self.refer: str
        """已发送数据包中 IP 的来源"""

        self.use_ipv6: bool = use_ipv6
        """是否使用 IPv6 进行套接字连接"""

        self.refer = address if refer is None else refer
        self.address: str = address
        """Minecraft 服务器的 IP 地址"""

        autoport: bool = False
        if not port:
            autoport = True
            if query_protocol is SlpProtocols.BEDROCK_RAKNET:
                if use_ipv6:
                    port = self.DEFAULT_BEDROCK_PORT_V6
                else:
                    port = self.DEFAULT_BEDROCK_PORT_V4
            else:
                port = self.DEFAULT_TCP_PORT

        self.port: int = port
        """Minecraft 服务器接受连接的端口号"""
        self.online: bool = False
        """在线或离线"""
        self.version: str | None = None
        """服务器版本号"""
        self.plugins: list[str] | None = None
        """由 Query 协议返回的插件列表，可能为空"""
        self.motd: str | None = None
        """当天消息，服务器响应保持不变（包括格式代码/JSON）"""
        self.stripped_motd: str | None = None
        """每日消息，已去除所有格式（人类可读）"""
        self.current_players: int | None = None
        """当前在线玩家人数"""
        self.max_players: int | None = None
        """最大玩家容量"""
        self.player_list: list[str] | None = None
        """在线玩家列表，即使`current_players`大于0，也可能为空"""
        self.map: str | None = None
        """服务器运行的地图名称，仅由 Query 和 Bedrock 协议支持"""
        self.latency: int | None = None
        """到服务器的延迟时间（毫秒）"""
        self.timeout: int = timeout
        """套接字超时"""
        self.slp_protocol: SlpProtocols | None = None
        """服务器列表 ping 协议"""
        self.protocol_version: int | None = None
        """服务器协议版本"""
        self.favicon_b64: str | None = None
        """可能包含在 JSON 1.7 响应中的 base64 编码图标"""
        self.favicon: str | None = None
        """解码后的网站图标数据"""
        self.gamemode: str | None = None
        """基岩版特有：当前游戏模式（Creative/Survival/Adventure)）"""
        self.connection_status: ConnStatus | None = None
        """连接状态 ("SUCCESS", "CONNFAIL", "TIMEOUT", 或 "UNKNOWN")"""
        self.edition: str | None = None
        """基岩版特有：服务器类型（MCPE/MCEE）"""

        # 未来改进：IPv4/IPv6，多地址
        # 如果主机有多个IP地址或同时拥有IPv4和IPv6地址，
        # socket.connect 会选择DNS返回的第一个IPv4地址。
        # 如果Minecraft服务器通过IPv4不可用，则会失败并显示“离线”。
        # 或者在某些环境中，DNS返回外部地址和内部地址，
        # 但从内部客户端只能访问内部地址。
        # 详见 https://docs.python.org/3/library/socket.html#socket.getaddrinfo

        # 如果用户希望使用特定协议，仅使用该协议。
        result = ConnStatus.UNKNOWN
        if query_protocol is not SlpProtocols.ALL:
            if query_protocol is SlpProtocols.BETA:
                result = self.beta_query()
            elif query_protocol is SlpProtocols.LEGACY:
                result = self.legacy_query()
            elif query_protocol is SlpProtocols.EXTENDED_LEGACY:
                result = self.extended_legacy_query()
            elif query_protocol is SlpProtocols.JSON:
                result = self.json_query()
            elif query_protocol is SlpProtocols.BEDROCK_RAKNET:
                result = self.bedrock_raknet_query()
            elif query_protocol is SlpProtocols.QUERY:
                result = self.fullstat_query()
            self.connection_status = result

            return

        # 注意：此处 Java 版本的顺序不幸地非常重要。
        # 某些较老的 MC 版本在接收到无法识别的数据包后的几秒钟内不接受新的数据包。
        # 例如 MC 1.4：在发送 JSON 请求后，任何操作都不能立即生效。
        # 单独的传统查询则可以正常工作。

        # Minecraft Bedrock/Pocket/Education Edition (MCPE/MCEE)
        if autoport and not self.port:
            if use_ipv6:
                self.port = self.DEFAULT_BEDROCK_PORT_V6
            else:
                self.port = self.DEFAULT_BEDROCK_PORT_V4

        result = self.bedrock_raknet_query()
        self.connection_status = result

        if result is ConnStatus.SUCCESS:
            return

        if autoport and not self.port:
            self.port = self.DEFAULT_TCP_PORT

        # Minecraft 1.4 & 1.5 (legacy SLP)
        result = self.legacy_query()

        # Minecraft Beta 1.8 to Release 1.3 (beta SLP)
        if result not in [ConnStatus.CONNFAIL, ConnStatus.SUCCESS]:
            result = self.beta_query()

        # Minecraft 1.6 (extended legacy SLP)
        if result is not ConnStatus.CONNFAIL:
            result = self.extended_legacy_query()

        # Minecraft 1.7+ (JSON SLP)
        if result is not ConnStatus.CONNFAIL:
            self.json_query()

        self.connection_status = ConnStatus.SUCCESS if self.online else result

    @staticmethod
    def motd_strip_formatting(raw_motd: str | dict) -> str:
        """
        用于去除 MOTD 中所有格式代码的函数。
        支持 JSON 聊天组件（以字典形式）以及旧版格式代码

        :param raw_motd: 原始 MOTD，可以是字符串或字典
        """
        stripped_motd = ""

        if isinstance(raw_motd, str):
            stripped_motd = re.sub(r"§.", "", raw_motd)

        elif isinstance(raw_motd, dict):
            stripped_motd = raw_motd.get("text", "")

            if raw_motd.get("extra"):
                for sub in raw_motd["extra"]:
                    stripped_motd += MineStat.motd_strip_formatting(sub)

        return stripped_motd

    def bedrock_raknet_query(self) -> ConnStatus:
        """
        用于查询基岩版服务器（Minecraft PE、Windows 10 或教育版）的方法。
        该协议基于 RakNet 协议。

        详见 https://wiki.vg/Raknet_Protocol#Unconnected_Ping

        注意：此方法目前的实现假设连接通过 TCP 处理（即假定不会发生数据包丢失）。
        应实现数据包丢失处理（如重发机制）。
        """

        RAKNET_MAGIC = bytearray(
            [
                0x00,
                0xFF,
                0xFF,
                0x00,
                0xFE,
                0xFE,
                0xFE,
                0xFE,
                0xFD,
                0xFD,
                0xFD,
                0xFD,
                0x12,
                0x34,
                0x56,
                0x78,
            ]
        )

        # Create socket with type DGRAM (for UDP)
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(self.timeout)

        # Construct the `Unconnected_Ping` packet
        # Packet ID - 0x01
        req_data = bytearray([0x01])
        # current unix timestamp in ms as signed long (64-bit) LE-encoded
        req_data += struct.pack("<q", int(time() * 1000))
        # RakNet MAGIC (0x00ffff00fefefefefdfdfdfd12345678)
        req_data += RAKNET_MAGIC
        # Client GUID - as signed long (64-bit) LE-encoded
        req_data += struct.pack("<q", 0x02)

        # Do all the receiving in a try-catch, to reduce duplication of error handling

        # response packet:
        # byte - 0x1C - Unconnected Pong
        # long - timestamp
        # long - server GUID
        # 16 byte - magic
        # short - Server ID string length
        # string - Server ID string
        start_time = perf_counter()  # 记录发送时间
        try:
            sock.connect((self.address, self.port))
            sock.send(req_data)

            response_buffer, response_addr = sock.recvfrom(1024)
            response_stream = io.BytesIO(response_buffer)

            # Receive packet id
            packet_id = response_stream.read(1)

            # Response packet ID should always be 0x1c
            if packet_id != b"\x1c":
                return ConnStatus.UNKNOWN

            # Receive (& ignore) response timestamp
            response_timestamp = struct.unpack("<q", response_stream.read(8))

            # Server GUID
            response_server_guid = struct.unpack("<q", response_stream.read(8))

            # Magic
            response_magic = response_stream.read(16)
            if response_magic != RAKNET_MAGIC:
                return ConnStatus.UNKNOWN

            # Server ID string length
            response_id_string_length = struct.unpack(">h", response_stream.read(2))

            # Receive server ID string
            response_id_string = response_stream.read().decode("utf8")

        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionResetError, ConnectionAbortedError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        finally:
            sock.close()
            elapsed_time = perf_counter() - start_time
            self.latency = round(elapsed_time * 1000)  # 设置延迟

        # Set protocol version
        self.slp_protocol = SlpProtocols.BEDROCK_RAKNET

        # Parse and save to object attributes
        return self.__parse_bedrock_payload(response_id_string)

    def __parse_bedrock_payload(self, payload_str: str) -> ConnStatus:
        motd_index = [
            "edition",
            "motd_1",
            "protocol_version",
            "version",
            "current_players",
            "max_players",
            "server_uid",
            "motd_2",
            "gamemode",
            "gamemode_numeric",
            "port_ipv4",
            "port_ipv6",
        ]
        payload = dict(zip(motd_index, payload_str.split(";")))

        self.online = True
        self.protocol_version = int(payload["protocol_version"])

        self.current_players = int(payload["current_players"])
        self.max_players = int(payload["max_players"])
        self.version = payload["version"]
        self.motd = payload["motd_1"]
        # 旧版 Bedrock 服务器不会用第二条服务器信息（MotD）进行响应。
        self.map = payload.get("motd_2")
        self.edition = payload["edition"]
        self.stripped_motd = self.motd_strip_formatting(self.motd)

        # 旧版 Bedrock 服务器不会返回游戏模式。
        self.gamemode = payload.get("gamemode")

        return ConnStatus.SUCCESS

    def fullstat_query(self) -> ConnStatus:
        """
        用于通过 fullstat Query / GameSpot4 / UT3 协议
        查询 Minecraft Java 版服务器的方法
        需要在 Minecraft 服务器上启用此功能，
        方法是在服务器的 "server.properties" 文件中添加以下配置:

        `enable-query=true`

        该方法仅支持完整的状态查询。
        协议文档详见：https://wiki.vg/Query
        """
        # protocol:
        #   send handshake request
        #   receive challenge token
        #   send full stat request
        #   receive status data

        # Create UDP socket and set timeout
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)

        # padding that is prefixes to every packet
        magic = b"\xfe\xfd"

        # packettypes for the multiple packets send by the client
        handshake_packettype = struct.pack("!B", 9)
        stat_packettype = struct.pack("!B", 0)

        # generate session id
        session_id_int = random.randint(0, 2147483648) & 0x0F0F0F0F
        session_id_bytes = struct.pack(">l", session_id_int)

        # handshake packet:
        #   contains 0xFE0xFD as a prefix
        #   contains type of the packet, 9 for hanshaking in this case (encoded in Bytes as a big-endian)
        #   contains session id (is generated randomly at the begining)

        # construct the handshake packet
        handshake_packet = magic
        handshake_packet += handshake_packettype
        handshake_packet += session_id_bytes

        # send packet to server
        sock.sendto(handshake_packet, (self.address, self.port))

        try:
            # receive the handshake response
            handshake_res = sock.recv(24)

            # extract the challenge token from the server. The beginning of the packet can be ignored.
            challenge_token = handshake_res[5:].rstrip(b"\00")

            # pack the challenge token into a big-endian long (int32)
            challenge_token_bytes = struct.pack(">l", int(challenge_token))

            # full stat request packet:
            #   contains 0xFE0xFD as a prefix
            #   contains type of the packet, 0 for hanshaking in this case (encoded as a big-endian integer)
            #   contains session id (is generated randomly at the beginning)
            #   contains challenge token (received during the handshake)
            #   contains 0x00 0x00 0x00 0x00 as padding (a basic stat request does not include these bytes)

            # construct the request packet
            req_packet = magic
            req_packet += stat_packettype
            req_packet += session_id_bytes
            req_packet += challenge_token_bytes
            req_packet += b"\x00\x00\x00\x00"

            # send packet to server
            sock.sendto(req_packet, (self.address, self.port))

            # receive requested status data
            raw_res = sock.recv(4096)

            # close the socket
            sock.close()

        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionResetError, ConnectionAbortedError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        finally:
            sock.close()

        return self.__parse_query_payload(raw_res)

    def __parse_query_payload(self, raw_res) -> ConnStatus:
        """
        用于解析 Query 请求响应的辅助方法。

        详见 https://wiki.vg/Query 获取详细信息。

        此实现并未解析 Query 协议返回的所有值。
        """
        try:
            self.__extracted_from___parse_query_payload_11(raw_res)
        except Exception:
            return ConnStatus.UNKNOWN

        self.online = True
        self.slp_protocol = SlpProtocols.QUERY
        return ConnStatus.SUCCESS

    def __extracted_from___parse_query_payload_11(self, raw_res):
        # remove uneccessary padding
        res = raw_res[11:]

        # split stats from players
        raw_stats, raw_players = res.split(b"\x00\x00\x01player_\x00\x00")

        # split stat keys and values into individual elements and remove unnecessary padding
        stat_list = raw_stats.split(b"\x00")[2:]

        # move keys and values into a dictonary, the keys are also decoded
        key = True
        stats = {}
        for index, key_name in enumerate(stat_list):
            if key:
                stats[key_name.decode("utf-8")] = stat_list[index + 1]
                key = False
            else:
                key = True

        # extract motd, the motd is named "hostname" in the Query protocol
        if "hostname" in stats:
            self.motd = stats["hostname"].decode("iso_8859_1")

        # the "MOTD" key is used in a basic stats query reponse
        elif "MOTD" in stats:
            self.motd = stats["MOTD"].decode("iso_8859_1")

        if self.motd is not None:
            # remove potential formatting
            self.stripped_motd = self.motd_strip_formatting(self.motd)

        # extract the servers Minecraft version
        if "version" in stats:
            self.version = stats["version"].decode("utf-8")

            # extract list of plugins
        if "plugins" in stats:
            raw_plugins = stats["plugins"].decode("utf-8")
            if raw_plugins != "":
                # the plugins are separated by " ;"
                self.plugins = raw_plugins.split(" ;")
                # there may be information about the server software in the first plugin element
                # example: ["Paper on 1.19.3: AnExampleMod 7.3", "AnotherExampleMod 4.2", ...]
                # more information on https://wiki.vg/Query
                if ":" in self.plugins[0]:  # type: ignore
                    self.version, self.plugins[0] = self.plugins[0].split(": ")  # type: ignore

        # extract the name of the map the server is running on
        if "map" in stats:
            self.map = stats["map"].decode("utf-8")

        if "numplayers" in stats:
            self.current_players = int(stats["numplayers"])
            self.max_players = int(stats["maxplayers"])

        # split players (seperated by 0x00)
        players = raw_players.split(b"\x00")

        # decode players and sort out empty elements
        self.player_list = [
            player.decode("utf-8") for player in players[:-2] if player != b""
        ]

    def json_query(self) -> ConnStatus:
        """
        Method for querying a modern (MC Java >= 1.7) server with the SLP protocol.
        This protocol is based on encoded JSON, see the documentation at wiki.vg below
        for a full packet description.

        See https://wiki.vg/Server_List_Ping#Current
        """
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            self._extracted_from_beta_query_19(sock)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except OSError:
            return ConnStatus.CONNFAIL

        # Construct Handshake packet
        req_data = bytearray([0x00])
        # Add protocol version. If pinging to determine version, use `25565`
        req_data += bytearray([0xDD, 0xC7, 0x01])
        # Add server address length
        req_data += self._pack_varint(len(self.refer))
        # Server address. Encoded with UTF8
        req_data += bytearray(self.refer, "utf8")
        # Server port
        req_data += struct.pack(">H", self.port)
        # Next packet state (1 for status, 2 for login)
        req_data += bytearray([0x01])

        # Prepend full packet length
        req_data = self._pack_varint(len(req_data)) + req_data

        # Now actually send the constructed client request
        sock.send(req_data)

        # Now send empty "Request" packet
        # varint len, 0x00
        sock.send(bytearray([0x01, 0x00]))

        # Do all the receiving in a try-catch, to reduce duplication of error handling
        try:
            # Receive answer: full packet length as varint
            packet_len = self._unpack_varint(sock)

            # Check if full packet length seems acceptable
            if packet_len < 3:
                return ConnStatus.UNKNOWN

            # Receive actual packet id
            packet_id = self._unpack_varint(sock)

            # If we receive a packet with id 0x19, something went wrong.
            # Usually the payload is JSON text, telling us what exactly.
            # We could stop here, and display something to the user, as this is not normal
            # behaviour, maybe a bug somewhere here.

            # Instead I am just going to check for the correct packet id: 0x00
            if packet_id != 0:
                return ConnStatus.UNKNOWN

            # Receive & unpack payload length
            content_len = self._unpack_varint(sock)

            # Receive full payload
            payload_raw = self._recv_exact(sock, content_len)

        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionResetError, ConnectionAbortedError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        finally:
            sock.close()

        # Set protocol version
        self.slp_protocol = SlpProtocols.JSON

        # Parse and save to object attributes
        return self.__parse_json_payload(payload_raw)

    def __parse_json_payload(self, payload_raw: bytes | bytearray) -> ConnStatus:
        """
        Helper method for parsing the modern JSON-based SLP protocol.
        In use for Minecraft Java >= 1.7, see `json_query()` above for details regarding the protocol.

        :param payload_raw: The raw SLP payload, without header and string lenght
        """
        try:
            payload_obj = json.loads(payload_raw.decode("utf8"))
        except json.JSONDecodeError:
            return ConnStatus.UNKNOWN

        # Now that we have the status object, set all fields
        self.version = payload_obj.get("version", {}).get("name")
        self.protocol_version = payload_obj.get("version", {}).get("protocol", -1)

        # The motd might be a string directly, not a json object
        if isinstance(payload_obj.get("description", ""), str):
            self.motd = payload_obj.get("description", "")
        else:
            self.motd = json.dumps(payload_obj["description"])
        self.stripped_motd = self.motd_strip_formatting(
            payload_obj.get("description", "")
        )

        players = payload_obj.get("players", {})
        self.max_players = players.get("max", -1)
        self.current_players = players.get("online", -1)

        # There may be a "sample" field in the "players" object that contains a sample list of online players
        if "sample" in players:
            self.player_list = [player["name"] for player in players["sample"]]

        try:
            self.favicon_b64 = payload_obj["favicon"]
            if self.favicon_b64:
                self.favicon = str(
                    base64.b64decode(self.favicon_b64.split("base64,")[1]), "ISO-8859–1"
                )
        except KeyError:
            self.favicon_b64 = None
            self.favicon = None

        # If we got here, everything is in order.
        self.online = True
        return ConnStatus.SUCCESS

    def _unpack_varint(self, sock: socket.socket) -> int:
        """Small helper method for unpacking an int from an varint (streamed from socket)."""
        data = 0
        for i in range(5):
            ordinal = sock.recv(1)

            if len(ordinal) == 0:
                break

            byte = ord(ordinal)
            data |= (byte & 0x7F) << 7 * i

            if not byte & 0x80:
                break

        return data

    def _pack_varint(self, data) -> bytes:
        """Small helper method for packing a varint from an int."""
        ordinal = b""

        while True:
            byte = data & 0x7F
            data >>= 7
            ordinal += struct.pack("B", byte | (0x80 if data > 0 else 0))

            if data == 0:
                break

        return ordinal

    def extended_legacy_query(self) -> ConnStatus:
        """
        Minecraft 1.6 SLP query, extended legacy ping protocol.
        All modern servers are currently backwards compatible with this protocol.

        See https://wiki.vg/Server_List_Ping#1.6
        :return:
        """
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            self._extracted_from_beta_query_19(sock)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except OSError:
            return ConnStatus.CONNFAIL

        # Send 0xFE as packet identifier,
        # 0x01 as ping packet content
        # 0xFA as packet identifier for a plugin message
        # 0x00 0x0B as strlen of following string
        req_data = bytearray([0xFE, 0x01, 0xFA, 0x00, 0x0B])
        # the string 'MC|PingHost' as UTF-16BE encoded string
        req_data += bytearray("MC|PingHost", "utf-16-be")
        # 0xXX 0xXX byte count of rest of data, 7+len(serverhostname), as short
        req_data += struct.pack(">h", 7 + (len(self.refer) * 2))
        # 0xXX [legacy] protocol version (before netty rewrite)
        # Used here: 74 (MC 1.6.2)
        req_data += bytearray([0x49])
        # strlen of serverhostname (big-endian short)
        req_data += struct.pack(">h", len(self.refer))
        # the hostname of the server
        req_data += bytearray(self.refer, "utf-16-be")
        # port of the server, as int (4 byte)
        req_data += struct.pack(">i", self.port)

        # Now send the contructed client requests
        sock.send(req_data)

        try:
            # Receive answer packet id (1 byte)
            packet_id = self._recv_exact(sock, 1)

            # Check packet id (should be "kick packet 0xFF")
            if packet_id[0] != 0xFF:
                return ConnStatus.UNKNOWN

            # Receive payload lengh (signed big-endian short; 2 byte)
            raw_payload_len = self._recv_exact(sock, 2)

            # Extract payload length
            # Might be empty, if the server keeps the connection open but doesn't send anything
            content_len = struct.unpack(">h", raw_payload_len)[0]

            # Check if payload length is acceptable
            if content_len < 3:
                return ConnStatus.UNKNOWN

            # Receive full payload and close socket
            payload_raw = self._recv_exact(sock, content_len * 2)

        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionResetError, ConnectionAbortedError, struct.error):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        finally:
            sock.close()

        # Set protocol version
        self.slp_protocol = SlpProtocols.EXTENDED_LEGACY

        # Parse and save to object attributes
        return self.__parse_legacy_payload(payload_raw)

    def legacy_query(self) -> ConnStatus:
        """
        Minecraft 1.4-1.5 SLP query, server response contains more info than beta SLP

        See https://wiki.vg/Server_List_Ping#1.4_to_1.5

        :return: ConnStatus
        """
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            self._extracted_from_beta_query_19(sock)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except OSError:
            return ConnStatus.CONNFAIL

        # Send 0xFE 0x01 as packet id
        sock.send(bytearray([0xFE, 0x01]))

        # Receive answer packet id (1 byte) and payload lengh (signed big-endian short; 2 byte)
        try:
            raw_header = self._recv_exact(sock, 3)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionAbortedError, ConnectionResetError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL

        # Extract payload length
        # Might be empty, if the server keeps the connection open but doesn't send anything
        try:
            content_len = struct.unpack(">xh", raw_header)[0]
        except struct.error:
            return ConnStatus.UNKNOWN
        try:
            # Receive full payload and close socket
            payload_raw = bytearray(self._recv_exact(sock, content_len * 2))
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionAbortedError, ConnectionResetError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        sock.close()

        # Set protocol version
        self.slp_protocol = SlpProtocols.LEGACY

        # Parse and save to object attributes
        return self.__parse_legacy_payload(payload_raw)

    def __parse_legacy_payload(self, payload_raw: bytearray | bytes) -> ConnStatus:
        """
        Internal helper method for parsing the legacy SLP payload (legacy and extended legacy).

        :param payload_raw: The extracted legacy SLP payload as bytearray/bytes
        """
        # According to wiki.vg, beta, legacy and extended legacy use UTF-16BE as "payload" encoding
        payload_str = payload_raw.decode("utf-16-be")

        # This "payload" contains six fields delimited by a NUL character:
        # - a fixed prefix '§1'
        # - the protocol version
        # - the server version
        # - the MOTD
        # - the online player count
        # - the max player count
        payload_list = payload_str.split("\x00")

        # Check for count of string parts, expected is 6 for this protocol version
        if len(payload_list) != 6:
            return ConnStatus.UNKNOWN

        # - a fixed prefix '§1'
        # - the protocol version
        self.protocol_version = int(payload_list[1][1:]) if payload_list[1] else 0
        # - the server version
        self.version = payload_list[2]
        # - the MOTD
        self.motd = payload_list[3]
        self.stripped_motd = self.motd_strip_formatting(payload_list[3])
        # - the online player count
        self.current_players = int(payload_list[4])
        # - the max player count
        self.max_players = int(payload_list[5])

        # If we got here, everything is in order
        self.online = True
        return ConnStatus.SUCCESS

    def beta_query(self) -> ConnStatus:
        """
        Minecraft Beta 1.8 to Release 1.3 SLP protocol
        See https://wiki.vg/Server_List_Ping#Beta_1.8_to_1.3

        :return: ConnStatus
        """
        if self.use_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            self._extracted_from_beta_query_19(sock)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except OSError:
            return ConnStatus.CONNFAIL

        # Send 0xFE as packet id
        sock.send(bytearray([0xFE]))

        # Receive answer packet id (1 byte) and payload lengh (signed big-endian short; 2 byte)
        try:
            raw_header = self._recv_exact(sock, 3)
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionResetError, ConnectionAbortedError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL

        # Extract payload length
        # Might be empty, if the server keeps the connection open but doesn't send anything
        try:
            content_len = struct.unpack(">xh", raw_header)[0]
        except struct.error:
            return ConnStatus.UNKNOWN
        try:
            # Receive full payload and close socket
            payload_raw = bytearray(self._recv_exact(sock, content_len * 2))
        except TimeoutError:
            return ConnStatus.TIMEOUT
        except (ConnectionAbortedError, ConnectionResetError):
            return ConnStatus.UNKNOWN
        except OSError:
            return ConnStatus.CONNFAIL
        sock.close()

        # Set protocol version
        self.slp_protocol = SlpProtocols.BETA

        # According to wiki.vg, beta, legacy and extended legacy use UTF-16BE as "payload" encoding
        payload_str = payload_raw.decode("utf-16-be")
        # This "payload" contains three values:
        # The MOTD, the max player count, and the online player count
        payload_list = payload_str.split("§")

        # Check for count of string parts, expected is 3 for this protocol version
        # Note: We could check here if the list has the len() one, as that is most probably an error message.
        # e.g. ['Protocol error']
        if len(payload_list) < 3:
            return ConnStatus.UNKNOWN

        # The last value is the max player count
        self.max_players = int(payload_list[-1])
        # The second(-to-last) value is the online player count
        self.current_players = int(payload_list[-2])
        # The first value it the server MOTD
        # This could contain '§' itself, thats the reason for the join here
        self.motd = "§".join(payload_list[:-2])
        self.stripped_motd = self.motd_strip_formatting("§".join(payload_list[:-2]))

        # Set general version, as the protocol doesn't contain the server version
        self.version = ">=1.8b/1.3"

        # If we got here, everything is in order
        self.online = True

        return ConnStatus.SUCCESS

    def _extracted_from_beta_query_19(self, sock):
        start_time = perf_counter()
        sock.connect((self.address, self.port))
        self.latency = round((perf_counter() - start_time) * 1000)

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytearray:
        """
        Helper function for receiving a specific amount of data. Works around the problems of `socket.recv`.
        Throws a ConnectionAbortedError if the connection was closed while waiting for data.

        :param sock: Open socket to receive data from
        :param size: Amount of bytes of data to receive
        :return: bytearray with the received data
        """
        data = bytearray()

        while len(data) < size:
            if temp_data := bytearray(sock.recv(size - len(data))):
                data += temp_data

            else:
                raise ConnectionAbortedError

        return data
