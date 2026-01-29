# ✨ Minecraft_server_check_plugins for [zhenxun_bot](https://github.com/hibikier/zhenxun_bot)

[Nonebot Version](https://github.com/molanp/nonebot_plugin_mccheck/)

English|[简体中文](README.md)

## 🤔 If you have any good functional suggestions, please put forward them in [Issues](https://github.com/molanp/zhenxun_chafu_Minecraft/issues)

## 📈 Implemented functions

- [x] IPv6 support
- [x] Support for all platform adapters
- [x] Support for all Unicode fonts and glyphs
- [x] Motd-style rendering
- [x] Support for double queries to interconnected servers
- [x] Multilingual
- [x] SRV support

## 📑 Future functions

- [ ] And more...

## 🖼️ Renderings

v1.25

![Image_31020983743694.png](https://github.com/user-attachments/assets/2db47c9a-7ba1-4ce7-a31c-b65f6e848308)
![image](https://github.com/user-attachments/assets/d0830fe9-c690-4017-b601-f46a1d7e1894)

## 💿 Install

- Put `mc_check` folder in `plugins` folder or custom folder.

## 🎉 Usage

|   Command   |        Parameter        |       Scope        |                            Description                            |
| :---------: | :---------------------: | :----------------: | :---------------------------------------------------------------: |
|  `mcheck`   | `[ip]:[port]` or `[ip]` | Private/Group Chat |                   Check Minecraft server status                   |
| `set_lang`  |      Language name      | Private/Group Chat |     Set the language used by the plugin for rendering images      |
| `lang_now`  |          None           | Private/Group Chat | View the current language used by the plugin for rendering images |
| `lang_list` |          None           | Private/Group Chat |        View the list of languages supported by the plugin         |

### 🎈 Special Notes

Querying an IPv6 server

```
mcheck [2001:db8:85a3::8a2e:370:7334]:25565  <- IPv6 server address and port, the port and colon can be omitted
```

or

```
mcheck [2001:db8:85a3::8a2e:370:7334]  <- IPv6 server address
```

## ⚙️ Configuration

| Configuration Item | Required | Default Value |                                         Description                                          |
| :----------------: | :------: | :-----------: | :------------------------------------------------------------------------------------------: |
|     `language`     |  False   |    `zh-cn`    | Languages used by the plugin to render images<br>Available languages: [`zh-cn`,`zh-tw`,`en`] |
|       `type`       |  False   |      `0`      |              The type of message the plugin sends (`0` for HTML, `1` for text)               |

## 🎲 Comparison of message types

| Type | Special Styles | Favicon | Colored underline/strikethrough | Full Unicode font support |
| :--: | :------------: | :-----: | :-----------------------------: | :-----------------------: |
| Text |       ❌       |   ⭕    |               ❌                |            ⭕             |
| HTML |       ⭕       |   ⭕    |               ⭕                |            ⭕             |

# [Download](https://github.com/molanp/zhenxun_plugin_mccheck/releases)

## Requirement

```shell
pip install -r requirements.txt
```

## Thanks

- [minestat](https://github.com/FragLand/minestat): A multi-platform Minecraft server query module.The main function of this plugin is to implement the magic change in this script.
- [@tzdtwsj](https://github.com/tzdtwsj): For the project, suggestions for image rendering function, color rendering function and implementation ideas of interoperability query scheme are proposed.
