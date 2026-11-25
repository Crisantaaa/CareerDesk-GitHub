"""Display local game artwork without bundling copyrighted assets.

The eight building PNGs were downloaded from Stardew Valley wiki image pages;
characters and terrain came from the user's installed game. All stay under
data/game_art, which is excluded from the distributable source archive.
"""

from __future__ import annotations

from base64 import b64encode
from html import escape

from careerdesk.db import ROOT


# Each scene has one wiki building on the right. Characters occupy different
# depths of the dirt path, with clear space around the building and one another.
PAGE_SCENES = {
    "求职总览": ("greenhouse", (("cat", 4, 5, 52), ("blue_chicken", 30, 47, 43))),
    "招聘信息": ("coop", (("dog", 5, 6, 56), ("chicken", 31, 49, 42))),
    "岗位管理": ("barn", (("sheep", 4, 42, 54), ("pig", 29, 5, 54))),
    "申请进度": ("stable", (("horse", 4, 4, 55), ("junimo", 27, 57, 42),
                             ("chicken", 40, 17, 29))),
    "招聘事件与待办": ("silo", (("pig", 5, 6, 55), ("blue_chicken", 26, 41, 43),
                                  ("dog", 44, 7, 42), ("junimo", 61, 68, 25))),
    "AI 试用": ("silo", (("blue_chicken", 5, 8, 47), ("junimo", 30, 50, 38))),
    "文件中心": ("shed", (("cat", 5, 5, 50), ("krobus", 31, 36, 41))),
    "个人资料库": ("mill", (("horse", 4, 6, 55), ("sheep", 28, 52, 53),
                              ("blue_chicken", 49, 11, 37))),
    "设置与备份": ("junimo_hut", (("junimo", 5, 44, 44), ("krobus", 29, 7, 41))),
}

# Small pixel plants stay in the open dirt above the characters. Their separate
# layer lets the sprites sit in front of the plants without hiding buildings.
FLORA = (
    ("weed", 8, 18, 20),
    ("flower-white", 23, 31, 21),
    ("weed", 34, 17, 16),
    ("flower-pink", 12, 37, 18),
    ("weed", 33, 43, 18),
)

FLORA_SHAPES = {
    "weed": (
        '<path fill="#315f2b" d="M11 22V12h2v10zM8 19V8h2v11zM14 19V7h2v12z"/>'
        '<path fill="#529838" d="M4 13V9h2v4h2v4H6v-2H4zm14-2h2v4h-2v3h-2v-5h2z"/>'
        '<path fill="#76b94a" d="M9 10V5h2v7H9zm5-4h2v6h-2z"/>'
    ),
    "flower-white": (
        '<path fill="#3d7d32" d="M11 22V10h2v12zM8 20v-5h3v2H9v3zm5-4h4v2h-4z"/>'
        '<path fill="#f9f0d1" d="M10 4h4v3h3v4h-3v3h-4v-3H7V7h3z"/>'
        '<path fill="#e5a745" d="M11 8h2v2h-2z"/>'
    ),
    "flower-pink": (
        '<path fill="#3d7d32" d="M11 22V10h2v12zM7 18h4v2H9v2H7zm6-1h4v2h-4z"/>'
        '<path fill="#f2b0b2" d="M10 4h4v3h3v4h-3v3h-4v-3H7V7h3z"/>'
        '<path fill="#e9d358" d="M11 8h2v2h-2z"/>'
    ),
}


def _flora_uri(plant: str) -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'shape-rendering="crispEdges">'
        f'{FLORA_SHAPES[plant]}</svg>'
    )
    return "data:image/svg+xml;base64," + b64encode(svg.encode("utf-8")).decode("ascii")

ANIMAL_LABELS = {
    "cat": "小猫",
    "dog": "小狗",
    "chicken": "小鸡",
    "pig": "小猪",
    "sheep": "小羊",
    "horse": "小马",
    "blue_chicken": "蓝色小鸡",
    "junimo": "祝尼魔",
    "krobus": "小黑（科罗布斯）",
}

BUILDING_LABELS = {
    "greenhouse": "温室",
    "coop": "鸡舍",
    "barn": "畜棚",
    "stable": "马厩",
    "silo": "筒仓",
    "shed": "大棚屋",
    "mill": "磨坊",
    "junimo_hut": "祝尼魔小屋",
}


def _image_uri(name: str) -> str | None:
    path = ROOT / "data" / "game_art" / f"{name}.png"
    if not path.is_file():
        return None
    return "data:image/png;base64," + b64encode(path.read_bytes()).decode("ascii")


def page_scene_html(page: str) -> str:
    building, animals = PAGE_SCENES[page]
    pieces = [f'<div class="cd-scene" data-building="{building}" '
              'aria-label="星露谷田园贴图装饰">']
    terrain_uri = _image_uri("terrain_grass")
    if terrain_uri:
        pieces.append(f'<img class="cd-scene-terrain" src="{terrain_uri}" alt="草地与泥土">')
    for plant, left, top, size in FLORA:
        pieces.append(
            f'<img class="cd-scene-flora" src="{_flora_uri(plant)}" alt="" '
            f'aria-hidden="true" style="left:{left}%;top:{top}%;width:{size}px">'
        )
    building_uri = _image_uri(f"{building}_wiki") or _image_uri(building)
    if building_uri:
        pieces.append(
            f'<img class="cd-scene-building" src="{building_uri}" '
            f'alt="{escape(BUILDING_LABELS[building])}" '
            f'loading="eager">'
        )
    for index, (animal, left, bottom, width) in enumerate(animals):
        uri = _image_uri(animal)
        if uri:
            extra = ' data-extra="true"' if index >= 2 else ""
            pieces.append(
            f'<img class="cd-scene-animal" data-animal="{animal}"{extra} src="{uri}" '
                f'alt="{escape(ANIMAL_LABELS[animal])}" '
                f'style="left:{left}%;bottom:{bottom}px;width:{width}px" loading="eager">'
            )
    pieces.append("</div>")
    return "".join(pieces)
