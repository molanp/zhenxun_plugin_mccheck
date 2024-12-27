from zhenxun.configs.config import Config # type: ignore
import os
import ujson

def readInfo(file: str) -> dict:
    with open(os.path.join(os.path.dirname(__file__), file), "r", encoding="utf-8") as f:
        return ujson.loads((f.read()).strip())

message_type = Config.get_config("mc_check", "type", 0)
lang = Config.get_config("mc_check", "LANGUAGE", "zh-cn")
lang_data = readInfo("language.json")
VERSION = "1.23"