# sourcery skip: remove-redundant-constructor-in-dict-union
"""Draw server status cards with Pillow, without a browser."""

import base64
import binascii
import contextlib
import itertools
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
from typing import Literal, TypeAlias, TypedDict, cast

from PIL import Image, ImageDraw, ImageFilter, ImageFont, UnidentifiedImageError

ASSETS = Path(__file__).with_name("templates")
COLORS: dict[str, str] = dict(
    zip(
        (
            "black",
            "dark_blue",
            "dark_green",
            "dark_aqua",
            "dark_red",
            "dark_purple",
            "gold",
            "gray",
            "dark_gray",
            "blue",
            "green",
            "aqua",
            "red",
            "light_purple",
            "yellow",
            "white",
        ),
        (
            "#000000",
            "#0000AA",
            "#00AA00",
            "#00AAAA",
            "#AA0000",
            "#AA00AA",
            "#FFAA00",
            "#AAAAAA",
            "#555555",
            "#5555FF",
            "#55FF55",
            "#55FFFF",
            "#FF5555",
            "#FF55FF",
            "#FFFF55",
            "#FFFFFF",
        ),
    )
)
JAVA_COLORS: dict[str, str] = dict(zip("0123456789abcdef", COLORS.values()))
BEDROCK_COLORS: dict[str, str] = {
    **JAVA_COLORS,
    "7": "#C6C6C6",
    "g": "#DDD605",
    "h": "#E3D4D1",
    "i": "#CECACA",
    "j": "#443A3B",
    "m": "#971607",
    "n": "#B4684D",
    "p": "#DEB12D",
    "q": "#119F36",
    "s": "#2CBAA8",
    "t": "#21497B",
    "u": "#9A5CC6",
    "v": "#EB7214",
}
JAVA_SHADOWS: dict[str, str] = dict(
    zip(
        "0123456789abcdef",
        (
            "#000000",
            "#00002A",
            "#002A00",
            "#002A2A",
            "#2A0000",
            "#2A002A",
            "#402A00",
            "#2A2A2A",
            "#151515",
            "#15153F",
            "#153F15",
            "#153F3F",
            "#3F1515",
            "#3F153F",
            "#3F3F15",
            "#3F3F3F",
        ),
    )
)
BEDROCK_SHADOWS: dict[str, str] = {
    **JAVA_SHADOWS,
    "7": "#313131",
    "g": "#373501",
    "h": "#383534",
    "i": "#333232",
    "j": "#110E0E",
    "m": "#250501",
    "n": "#2D1A13",
    "p": "#372C0B",
    "q": "#04280D",
    "s": "#0B2E2A",
    "t": "#08121E",
    "u": "#261731",
    "v": "#3B1D05",
}
COLOR_CODES: dict[str, str] = dict(zip(COLORS, "0123456789abcdef")) | dict(
    zip(
        (
            "minecoin_gold",
            "material_quartz",
            "material_iron",
            "material_netherite",
            "material_redstone",
            "material_copper",
            "material_gold",
            "material_emerald",
            "material_diamond",
            "material_lapis",
            "material_amethyst",
            "material_resin",
        ),
        "ghijmnpqstuv",
    )
)


@dataclass(frozen=True)
class Style:
    color: str = "#FFFFFF"
    shadow: str | None = None
    bold: bool = False
    italic: bool = False
    underlined: bool = False
    strikethrough: bool = False
    obfuscated: bool = False


TextRun: TypeAlias = tuple[str, Style]
Glyph: TypeAlias = tuple[str, Style, ImageFont.FreeTypeFont, float]
PositionedGlyph: TypeAlias = tuple[str, Style, ImageFont.FreeTypeFont, float, float]
TextLine: TypeAlias = list[PositionedGlyph]
StyleFlag: TypeAlias = Literal[
    "bold", "italic", "underlined", "strikethrough", "obfuscated"
]
STYLE_FLAGS: tuple[StyleFlag, ...] = (
    "bold",
    "italic",
    "underlined",
    "strikethrough",
    "obfuscated",
)
JAVA_FORMATS: dict[str, StyleFlag] = {
    "k": "obfuscated",
    "l": "bold",
    "m": "strikethrough",
    "n": "underlined",
    "o": "italic",
}
BEDROCK_FORMATS: dict[str, StyleFlag] = {
    "k": "obfuscated",
    "l": "bold",
    "o": "italic",
}
LEGACY_CODES = re.compile(r"§.", re.DOTALL)
JAVA_LEGACY_CODES = re.compile(r"§x(?:§[0-9a-f]){6}|§.", re.IGNORECASE | re.DOTALL)


class StatusData(TypedDict):
    """Inputs supplied by utils.build_result to the image renderer."""

    favicon: str | None
    version: str
    protocol_version: int
    address: str
    delay: str
    gamemode: str | None
    motd: object
    players: str
    player_list: list[str]
    type: str
    lang: Mapping[str, str]
    VERSION: str


def _with_color(style: Style, color: str, bedrock: bool) -> Style:
    colors = BEDROCK_COLORS if bedrock else JAVA_COLORS
    shadows = BEDROCK_SHADOWS if bedrock else JAVA_SHADOWS
    code = COLOR_CODES.get(color.lower(), color.lower())
    if code in colors:
        return replace(style, color=colors[code], shadow=shadows[code])
    if re.fullmatch(r"#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}", color):
        if len(color) == 4:
            color = "#" + "".join(c * 2 for c in color[1:])
        shadow = "#" + "".join(f"{int(color[i:i+2], 16) // 4:02X}" for i in (1, 3, 5))
        return replace(style, color=color, shadow=shadow)
    return style


def _component_style(
    component: Mapping[str, object], inherited: Style, bedrock: bool
) -> Style:
    style = inherited
    color = component.get("color")
    if color == "reset":
        style = Style()
    elif isinstance(color, str):
        style = _with_color(style, color, bedrock)
    for key in STYLE_FLAGS:
        value = component.get(key)
        if isinstance(value, bool):
            style = replace(style, **{key: value})
    return style


def _parse_component(
    component: Mapping[str, object], bedrock: bool, inherited: Style
) -> Iterator[TextRun]:
    style = _component_style(component, inherited, bedrock)
    yield from parse_motd(
        component.get("text", component.get("translate", "")),
        bedrock=bedrock,
        inherited=style,
    )
    children = component.get("extra", [])
    if isinstance(children, (list, tuple)):
        for child in cast(Sequence[object], children):
            yield from parse_motd(child, bedrock=bedrock, inherited=style)


def _legacy_style(code: str, style: Style, bedrock: bool) -> Style | None:
    if len(code) > 2:
        return _with_color(Style(), "#" + code[2:].replace("§", ""), False)
    code = code[1].lower()
    colors = BEDROCK_COLORS if bedrock else JAVA_COLORS
    if code in colors:
        return _with_color(style if bedrock else Style(), code, bedrock)
    if code == "r":
        return Style()
    formats = BEDROCK_FORMATS if bedrock else JAVA_FORMATS
    flag = formats.get(code)
    return replace(style, **{flag: True}) if flag is not None else None


def _parse_legacy(text: str, bedrock: bool, inherited: Style) -> Iterator[TextRun]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    codes = LEGACY_CODES if bedrock else JAVA_LEGACY_CODES
    style = inherited
    start = 0
    for match in codes.finditer(text):
        new_style = _legacy_style(match[0], style, bedrock)
        if new_style is None:
            continue
        if start < match.start():
            yield text[start : match.start()], style
        style = new_style
        start = match.end()
    if start < len(text):
        yield text[start:], style


def parse_motd(
    data: object, *, bedrock: bool = False, inherited: Style = Style()
) -> Iterator[TextRun]:
    """Yield styled runs from raw JSON components or legacy section codes.

    The raw payload is untrusted JSON, so values are narrowed before use.
    Unknown scalar values retain the previous string conversion behavior.
    """
    if isinstance(data, Mapping):
        yield from _parse_component(
            cast(Mapping[str, object], data), bedrock, inherited
        )
    elif isinstance(data, (list, tuple)):
        for child in cast(Sequence[object], data):
            yield from parse_motd(child, bedrock=bedrock, inherited=inherited)
    else:
        yield from _parse_legacy(str(data), bedrock, inherited)


@lru_cache(maxsize=2)
def _font_bytes(name: str) -> bytes:
    """Cache font data after closing the file used to read it."""
    return (ASSETS / name).read_bytes()


@lru_cache(maxsize=8)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    # FreeType retains file mappings when given a path, even after drawing.
    # Pillow copies a stream's bytes into the font, so this buffer can close.
    with BytesIO(_font_bytes(name)) as buffer:
        return ImageFont.truetype(buffer, size)


@lru_cache(maxsize=256)
def _glyph_signature(char: str) -> tuple[tuple[int, int], bytes]:
    """Compare glyphs through Image's public API instead of ImagingCore bytes."""
    font = _font("Mojangles.ttf", 20)
    left, top, right, bottom = (round(value) for value in font.getbbox(char))
    image = Image.new("L", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(image).text((-left, -top), char, font=font, fill=255)
    return image.size, image.tobytes()


@lru_cache(maxsize=4096)
def _font_name(char: str) -> str:
    """Use Unifont when Mojangles has no glyph for this character."""
    if _glyph_signature(char) == _glyph_signature("\U0010ffff"):
        return "unifont-17.0.04.otf"
    return "Mojangles.ttf"


def _text_tokens(runs: Iterable[TextRun], size: int) -> Iterator[list[Glyph]]:
    """Keep ASCII words together while allowing breaks between CJK glyphs."""
    word: list[Glyph] = []
    for text, style in runs:
        for char in text:
            if style.obfuscated and not char.isspace():
                char = "?"  # A static snapshot of Minecraft's scrambled text.
            name = _font_name(char) if char != "\n" else "Mojangles.ttf"
            # Match the old CSS's size-adjust: 80% on the Unifont fallback.
            font = _font(
                name, round(size * 0.8) if name.startswith("unifont") else size
            )
            item: Glyph = (
                char,
                style,
                font,
                float(font.getlength(char)) if char != "\n" else 0.0,
            )
            if char.isascii() and not char.isspace():
                word.append(item)
            else:
                if word:
                    yield word
                    word = []
                yield [item]
    if word:
        yield word


def layout_text(runs: Iterable[TextRun], width: int, size: int = 20) -> list[TextLine]:
    """Wrap styled characters using the same advances used while drawing."""
    lines: list[TextLine] = [[]]
    x = 0.0
    for token in _text_tokens(runs, size):
        token_width = sum(item[3] for item in token)
        if lines[-1] and x + token_width > width and token[0][0] != "\n":
            lines.append([])
            x = 0.0
        for char, style, font, advance in token:
            if char == "\n":
                lines.append([])
                x = 0.0
                continue
            if lines[-1] and x + advance > width:
                lines.append([])
                x = 0.0
            lines[-1].append((char, style, font, x, advance))
            x += advance
    return lines


def _paint_glyph(
    pen: ImageDraw.ImageDraw,
    char: str,
    font: ImageFont.FreeTypeFont,
    baseline: int,
    color: str,
    bold: bool,
) -> None:
    """Embolden horizontally by one pixel without outlining the character."""
    for offset in (0, 1) if bold else (0,):
        pen.text((2 + offset, baseline), char, font=font, anchor="ls", fill=color)


def _draw_lines(
    image: Image.Image,
    lines: Sequence[Sequence[PositionedGlyph]],
    x: int,
    y: int,
    size: int = 20,
) -> None:
    draw = ImageDraw.Draw(image)
    for line in lines:
        for char, style, font, offset, advance in line:
            px = round(x + offset)
            # A small transparent tile allows italic shear with either font.
            glyph = Image.new("RGBA", (int(advance) + 12, size + 10))
            pen = ImageDraw.Draw(glyph)
            baseline = round(size * 0.9) + 2
            if style.shadow:
                _paint_glyph(pen, char, font, baseline, style.shadow, style.bold)
                # Original CSS uses text-shadow: 0 0 1px, without an offset.
                glyph = glyph.filter(ImageFilter.GaussianBlur(0.5))
                pen = ImageDraw.Draw(glyph)
            _paint_glyph(pen, char, font, baseline, style.color, style.bold)
            if style.italic:
                glyph = glyph.transform(
                    glyph.size,
                    Image.Transform.AFFINE,
                    (1, 0.2, -4, 0, 1, 0),
                    Image.Resampling.BICUBIC,
                )
            image.paste(glyph, (px - 2, y - 2), glyph)
            if style.underlined:
                draw.line((px, y + size, px + advance, y + size), fill=style.color)
            if style.strikethrough:
                draw.line(
                    (px, y + size // 2, px + advance, y + size // 2), fill=style.color
                )
        y += size + 3


def _border(
    image: Image.Image,
    filename: str,
    box: tuple[int, int, int, int],
    border: int,
    slice_size: int | None = None,
) -> None:
    """Scale a Minecraft nine-patch border without stretching its corners."""
    with Image.open(ASSETS / filename) as asset:
        source = asset.convert("RGBA")
    left, top, right, bottom = box
    slice_size = slice_size or border
    sx = (0, slice_size, source.width - slice_size, source.width)
    sy = (0, slice_size, source.height - slice_size, source.height)
    dx = (left, left + border, right - border, right)
    dy = (top, top + border, bottom - border, bottom)
    for row, col in itertools.product(range(3), range(3)):
        if row == col == 1:
            continue
        tile = source.crop((sx[col], sy[row], sx[col + 1], sy[row + 1]))
        tile = tile.resize(
            (dx[col + 1] - dx[col], dy[row + 1] - dy[row]), Image.Resampling.NEAREST
        )
        image.paste(tile, (dx[col], dy[row]), tile)


def _favicon(data: str | None) -> Image.Image:
    if data:
        with contextlib.suppress(
            ValueError,
            binascii.Error,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            raw = base64.b64decode(data.split(",", 1)[-1], validate=True)
            with Image.open(BytesIO(raw)) as icon:
                return icon.convert("RGBA").resize(
                    (150, 150), Image.Resampling.BILINEAR
                )
    with Image.open(ASSETS / "no_favicon.png") as icon:
        return icon.convert("RGBA").resize((150, 150), Image.Resampling.BILINEAR)


def render_status(data: StatusData) -> bytes:
    """Render a localized status card and return PNG bytes."""
    width = 500
    content_x = 191
    content_width = width - content_x - 23
    bedrock = data["type"] == "Bedrock"
    fields: list[tuple[str, object]] = [
        ("version", data["version"]),
        ("motd", data["motd"]),
        ("protocol_version", data["protocol_version"]),
        ("address", data["address"]),
        ("delay", data["delay"]),
    ]
    if data["gamemode"]:
        fields.append(("gamemode", data["gamemode"]))
    fields.append(("players", data["players"]))
    if data["player_list"]:
        fields.append(("player_list", data["player_list"]))

    blocks: list[list[TextLine]] = []
    for key, value in fields:
        runs: list[TextRun] = [(data["lang"][key], Style())]
        if key == "player_list":
            for index, player in enumerate(data["player_list"]):
                if index:
                    runs.append((", ", Style()))
                runs.extend(parse_motd(player, bedrock=bedrock))
        elif key in ("motd", "version"):
            runs.extend(parse_motd(value, bedrock=bedrock))
        else:
            runs.append((str(value), Style()))
        blocks.append(layout_text(runs, content_width))

    height = 46 + max(158, 5 + sum(len(lines) * 23 + 5 for lines in blocks))
    image = Image.new("RGB", (width, height), "#4D4D4D")
    _border(image, "background.png", (0, 0, width, height), 8)
    icon = _favicon(data.get("favicon"))
    image.paste(icon, (27, 27), icon)
    _border(image, "imagelesshoverbg.png", (23, 23, 181, 181), 4, 1)
    # Match the original top-right label and its CSS skew(-20deg).
    type_width = round(_font("Mojangles.ttf", 20).getlength(data["type"]))
    badge = Image.new("RGBA", (type_width + 12, 23))
    _draw_lines(badge, layout_text([(data["type"], Style())], content_width), 6, 0)
    badge = badge.transform(
        badge.size,
        Image.Transform.AFFINE,
        (1, 0.364, -4.186, 0, 1, 0),
        Image.Resampling.BICUBIC,
    )
    image.paste(badge, (width - 10 - type_width - 6, 14), badge)
    y = 28
    for lines in blocks:
        _draw_lines(image, lines, content_x, y)
        y += len(lines) * 23 + 5
    watermark = f"zhenxun_plugin_mccheck v{data['VERSION']}"
    ImageDraw.Draw(image).text(
        (width - 8, height - 10),
        watermark,
        font=_font("Mojangles.ttf", 8),
        fill="#535353C2",
        anchor="rs",
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
