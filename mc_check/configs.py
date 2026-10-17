from pathlib import Path
import json

from zhenxun.configs.config import Config


def readInfo(file: str) -> dict:
    with open(Path(__file__).parent / file, encoding="utf-8") as f:
        return json.loads((f.read()).strip())


message_type = Config.get_config("mc_check", "type", 0)
lang = Config.get_config("mc_check", "LANGUAGE", "zh-cn")
lang_data = readInfo("language.json")
VERSION = "3.0"
