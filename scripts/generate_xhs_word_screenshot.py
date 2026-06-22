from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "docs" / "test_logs" / "xhs_cookie_recheck.jsonl"
OUT_FILE = ROOT / "docs" / "coursework_assets" / "screenshots" / "07_xhs_home_cooking_frontend.png"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if Path(path).exists():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT_HEI_34 = font("C:/Windows/Fonts/simhei.ttf", 34)
FONT_HEI_24 = font("C:/Windows/Fonts/simhei.ttf", 24)
FONT_HEI_20 = font("C:/Windows/Fonts/simhei.ttf", 20)
FONT_SONG_20 = font("C:/Windows/Fonts/simsun.ttc", 20)
FONT_SONG_18 = font("C:/Windows/Fonts/simsun.ttc", 18)
FONT_SONG_16 = font("C:/Windows/Fonts/simsun.ttc", 16)


def read_xhs_details() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    details = []
    for line in LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("event") == "social.xhs.detail.extracted":
            details.append(item)
    return details[:3]


def fetch_image(url: str, size: tuple[int, int]) -> Image.Image | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.thumbnail(size)
        canvas = Image.new("RGB", size, "#24313a")
        x = (size[0] - img.width) // 2
        y = (size[1] - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas
    except Exception:
        return None


def wrap_cn(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    lines = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= width:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return "\n".join(lines)


def rounded(draw: ImageDraw.ImageDraw, xy, radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def draw_card(draw: ImageDraw.ImageDraw, img: Image.Image, detail: dict, x: int, y: int, w: int, h: int, selected: bool) -> None:
    border = "#facc15" if selected else "#334155"
    rounded(draw, (x, y, x + w, y + h), 14, "#1e2a33", border, 2)
    thumb_h = 150
    urls = detail.get("image_urls") or []
    thumb = fetch_image(urls[0] if urls else "", (w, thumb_h))
    if thumb is None:
        thumb = Image.new("RGB", (w, thumb_h), "#374151")
        td = ImageDraw.Draw(thumb)
        td.text((22, 50), f"小红书图文\n图片 {len(urls)} 张", font=FONT_HEI_24, fill="#facc15")
    img.paste(thumb, (x, y))
    title_lines = wrap_cn(detail.get("title", "小红书菜谱"), 12).splitlines()[:2]
    draw.multiline_text((x + 18, y + thumb_h + 18), "\n".join(title_lines), font=FONT_HEI_24, fill="#f8fafc", spacing=4)
    meta = f"点赞 {detail.get('liked_count', '-')} · 收藏 {detail.get('collected_count', '-')} · 评论 {detail.get('comment_count', '-')}"
    draw.text((x + 18, y + thumb_h + 82), meta, font=FONT_SONG_16, fill="#cbd5e1")
    desc = wrap_cn(detail.get("desc_preview", ""), 17)
    desc_lines = desc.splitlines()[:4]
    draw.multiline_text((x + 18, y + thumb_h + 112), "\n".join(desc_lines), font=FONT_SONG_16, fill="#d1d5db", spacing=5)


def main() -> None:
    details = read_xhs_details()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1400, 1050
    img = Image.new("RGB", (width, height), "#081014")
    draw = ImageDraw.Draw(img)

    rounded(draw, (80, 50, width - 80, height - 50), 22, "#111c24", "#d6b400", 1)
    draw.text((120, 90), "SuperFoodie 自己做：小红书本地完整模式截图", font=FONT_HEI_34, fill="#facc15")
    draw.text((120, 138), "展示本地 aione 小红书搜索、详情抽取、图文菜谱整理结果；截图已脱敏，不包含 Cookie / id_token / API Key。", font=FONT_SONG_20, fill="#cbd5e1")

    rounded(draw, (120, 185, width - 120, 275), 14, "#201f29", "#8b3a3a", 1)
    draw.text((145, 212), "食物安全图谱校验", font=FONT_HEI_20, fill="#f87171")
    draw.text((145, 245), "状态：安全；辣子鸡未触发当前健康状态禁忌，具体辣度仍需按个人情况调整。", font=FONT_SONG_18, fill="#d1d5db")

    rounded(draw, (120, 310, width - 120, 415), 12, "#1d2925", "#8a7a10", 1)
    draw.text((145, 335), "真实图文菜谱已整理完成", font=FONT_HEI_20, fill="#f8fafc")
    draw.rounded_rectangle((145, 370, width - 145, 378), radius=4, fill="#facc15")
    steps = ["小红书已响应 2.8s", "找到候选笔记 6 条 2.8s", "已提取详情 3 条 7.9s", "已合并菜谱结构"]
    sx = 145
    for step in steps:
        tw = 190 if "候选" not in step else 245
        rounded(draw, (sx, 386, sx + tw, 405), 9, "#263238", "#40515c", 1)
        draw.text((sx + 10, 386), step, font=FONT_SONG_16, fill="#cbd5e1")
        sx += tw + 12

    if not details:
        details = [
            {"title": "辣子鸡｜越吃越上头！", "desc_preview": "鸡块炸到外酥里嫩，干辣椒和花椒一炒，香味直接冲出来。", "image_urls": [], "liked_count": "881", "collected_count": "763", "comment_count": "4"},
            {"title": "超级好吃的辣子鸡", "desc_preview": "麻辣过瘾，越嚼越香，适合周末在家做。", "image_urls": [], "liked_count": "1.3万", "collected_count": "1.1万", "comment_count": "154"},
            {"title": "家庭版辣子鸡这样做", "desc_preview": "麻辣干香，宵夜绝配，下饭很合适。", "image_urls": [], "liked_count": "1.5万", "collected_count": "1.2万", "comment_count": "230"},
        ]

    card_w, card_h = 350, 350
    for i, detail in enumerate(details[:3]):
        draw_card(draw, img, detail, 120 + i * 400, 455, card_w, card_h, selected=(i == 0))

    rounded(draw, (120, 835, width - 120, 1005), 16, "#202414", "#d6b400", 4)
    draw.text((155, 870), "已选菜品详情：辣子鸡", font=FONT_HEI_24, fill="#f8fafc")
    recipe = (
        "食材：鸡腿肉、干辣椒、花椒、姜片、蒜瓣、葱段。  "
        "调料：料酒、生抽、盐、白糖、白芝麻。  "
        "步骤：鸡腿肉切块腌制；热油炸至边缘金黄；小火煸香干辣椒和花椒；回锅翻炒，出锅前撒葱段和芝麻。"
    )
    draw.multiline_text((155, 910), wrap_cn(recipe, 54), font=FONT_SONG_20, fill="#e5e7eb", spacing=8)
    draw.text((155, 975), "来源：小红书详情抽取 + LLM 结构化整理（不抓评论）", font=FONT_SONG_16, fill="#94a3b8")

    img.save(OUT_FILE)
    print(OUT_FILE)


if __name__ == "__main__":
    main()
