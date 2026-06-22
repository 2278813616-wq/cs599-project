import os
import html
import re
import hashlib
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

class FoodieReportGenerator:
    def __init__(self):
        self.enabled_pdf = HAS_REPORTLAB
        self.chinese_font = "Helvetica"
        if self.enabled_pdf:
            self.chinese_font = self._register_chinese_font()
        else:
            print("【ReportLab 警告】未安装 reportlab 模块，将自动生成 Markdown 格式的就餐决策报告。")

    def _register_chinese_font(self) -> str:
        """
        在 Windows 系统中自动检索并注册中文字体，返回成功注册的字体名。
        """
        possible_fonts = [
            ("MSYH", "C:/Windows/Fonts/msyh.ttc"),
            ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
            ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
            ("MSYH_TTF", "C:/Windows/Fonts/msyh.ttf"),
            ("SimSun_TTF", "C:/Windows/Fonts/simsun.ttf"),
        ]
        for name, path in possible_fonts:
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(name, path))
                    # 测试性注册验证
                    return name
                except Exception as e:
                    print(f"【ReportLab 警告】加载中文字体 {name} ({path}) 失败: {e}")
        print("【ReportLab 严重警告】未能在系统目录找到常用中文字体，PDF 报告中的中文可能会显示为乱码。")
        return "Helvetica"

    def generate_report(self, session_id: str, data: dict, output_path: str) -> str:
        """
        生成就餐决策书文件。
        如果支持 PDF 且安装了 reportlab，生成 PDF 格式；
        否则自动降级生成精美的 Markdown 文本格式，并更新 output_path。
        返回最终生成的文件路径。
        """
        if self.enabled_pdf:
            try:
                return self._generate_pdf(session_id, data, output_path)
            except Exception as e:
                print(f"【PDF 失败】生成 PDF 抛出异常 ({e})，降级为 Markdown 格式。")
                import traceback
                traceback.print_exc()
                
        # 降级为 Markdown
        md_path = output_path.replace(".pdf", ".md")
        return self._generate_markdown(session_id, data, md_path)

    def _generate_pdf(self, session_id: str, data: dict, output_path: str) -> str:
        """使用 reportlab 渲染 PDF"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        doc = SimpleDocTemplate(output_path, pagesize=letter,
                                rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        
        styles = getSampleStyleSheet()
        story = []

        # 声明中文字体 Style
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontName=self.chinese_font,
            fontSize=20,
            leading=24,
            textColor=colors.HexColor('#2E4053'),
            spaceAfter=15
        )
        
        normal_chinese = ParagraphStyle(
            'NormalChinese',
            parent=styles['Normal'],
            fontName=self.chinese_font,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#2C3E50')
        )
        
        bold_chinese = ParagraphStyle(
            'BoldChinese',
            parent=styles['Normal'],
            fontName=self.chinese_font,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#2C3E50')
        )
        
        sect_style = ParagraphStyle(
            'SectStyle',
            parent=styles['Heading2'],
            fontName=self.chinese_font,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor('#1E8449'),
            spaceBefore=12,
            spaceAfter=8
        )
        
        body_chinese = ParagraphStyle(
            'BodyChinese',
            parent=styles['BodyText'],
            fontName=self.chinese_font,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor('#34495E')
        )

        is_home = data.get("mode") == "home_cooking"
        mode_str = "家庭烹饪指南" if is_home else "外出探店决策书"
        story.append(Paragraph(f"SuperFoodie {mode_str}", title_style))
        story.append(Spacer(1, 10))

        # 元数据表格
        meta_data = [
            [Paragraph("<b>会话 ID:</b>", bold_chinese), Paragraph(session_id, normal_chinese)],
            [Paragraph("<b>用户 ID:</b>", bold_chinese), Paragraph(data.get("user_id", "匿名用户"), normal_chinese)],
            [Paragraph("<b>生成时间:</b>", bold_chinese), Paragraph(data.get("time", ""), normal_chinese)]
        ]
        t = Table(meta_data, colWidths=[100, 350])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F2F4F4')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('LINEBELOW', (0,0), (-1,-2), 0.4, colors.HexColor('#D6DBDF'))
        ]))
        story.append(t)
        story.append(Spacer(1, 15))

        if is_home:
            story.append(Paragraph("菜品信息与烹饪步骤", sect_style))
            selected_recipes = data.get("selected_recommendations") or []
            if selected_recipes:
                for idx, recipe in enumerate(selected_recipes, 1):
                    recipe_name = html.escape(str(recipe.get("name") or f"备选菜品 {idx}"))
                    story.append(Paragraph(f"<b>{idx}. {recipe_name}</b>", body_chinese))
                    self._append_local_image(story, self._first_image_url(recipe))
                    blocks = self._recipe_pdf_blocks(recipe)
                    if blocks:
                        for title, content in blocks:
                            story.append(Paragraph(f"<b>{html.escape(title)}：</b>{self._to_reportlab_paragraph_text(content)}", body_chinese))
                            story.append(Spacer(1, 6))
                    else:
                        story.append(Paragraph("该菜品已加入备选菜单，暂无更详细的烹饪步骤。", body_chinese))
                    story.append(Spacer(1, 12))
            else:
                story.append(Paragraph("尚未加入备选菜单。请先在页面中点击“加入备选菜单”，再导出家庭烹饪指南。", body_chinese))
            story.append(Spacer(1, 15))

            story.append(Paragraph("饮食安全提示", sect_style))
            rag_path = data.get("graph_rag_path", [])
            if rag_path:
                path_str = " -> ".join(rag_path)
                story.append(Paragraph(f"<b>图谱拦截路径:</b> {path_str}", body_chinese))
                story.append(Spacer(1, 5))
            health_text = self._to_reportlab_paragraph_text(data.get("health_explanation", "安全：暂未检测到食物相克或过敏冲突。"))
            story.append(Paragraph(health_text, body_chinese))
        else:
            self._append_local_image(story, data.get("image_url", ""))
            selected = data.get("selected_recommendation") or {}
            store_name = selected.get("store_name") or selected.get("name") or "已选餐厅"
            restaurant_lines = [
                f"<b>店名:</b> {html.escape(str(store_name))}",
                f"<b>推荐菜:</b> {html.escape(str(selected.get('recommended_dishes') or data.get('taste_query') or '到店优先看招牌菜/高赞菜'))}",
                f"<b>具体地址:</b> {html.escape(str(selected.get('address') or '待到店确认'))}",
            ]
            if selected.get("rating"):
                restaurant_lines.append(f"<b>高德评分:</b> {html.escape(str(selected.get('rating')))}")
            if selected.get("avg_cost"):
                restaurant_lines.append(f"<b>人均参考:</b> {html.escape(str(selected.get('avg_cost')))} 元")

            story.append(Paragraph("餐厅决策卡片", sect_style))
            story.append(Paragraph("<br/>".join(restaurant_lines), body_chinese))
            story.append(Spacer(1, 12))

            if selected.get("description"):
                story.append(Paragraph("小红书/高德摘要", sect_style))
                desc_text = self._to_reportlab_paragraph_text(selected.get("description"))
                story.append(Paragraph(desc_text, body_chinese))
                story.append(Spacer(1, 12))

            after_places = selected.get("after_meal_places") or data.get("after_meal_places") or []
            story.append(Paragraph("饭后顺路安排", sect_style))
            if after_places:
                after_lines = []
                for place in after_places[:3]:
                    after_lines.append(
                        f"- {html.escape(str(place.get('name') or '饭后去处'))}："
                        f"{html.escape(str(place.get('description') or place.get('address') or '附近可顺路休息或逛玩'))}"
                    )
                story.append(Paragraph("<br/>".join(after_lines), body_chinese))
            else:
                story.append(Paragraph("暂未选择饭后安排，可到店后根据时间补充。", body_chinese))

        # 构建 PDF
        doc.build(story)
        return output_path

    def _first_image_url(self, item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        return (
            item.get("image_url")
            or (item.get("source_image_urls") or [""])[0]
            or (item.get("raw_source_image_urls") or [""])[0]
        )

    def _append_local_image(self, story: list, img_url: str) -> None:
        """Append local or downloaded image to the report when a usable image exists."""
        if not img_url:
            return
        image_path = self._resolve_report_image_path(img_url)
        if not image_path or not os.path.exists(image_path):
            return
        try:
            img = Image(image_path)
            self._scale_report_image(img)
            img.hAlign = 'LEFT'
            story.append(img)
            story.append(Spacer(1, 12))
        except Exception as img_err:
            print(f"【PDF 图片绘制失败】: {img_err}")

    def _resolve_report_image_path(self, img_url: str) -> str:
        img_url = str(img_url or "").strip()
        if not img_url:
            return ""

        if img_url.startswith("/api/foodie/image-proxy"):
            parsed = urlparse(img_url)
            proxied = parse_qs(parsed.query).get("url", [""])[0]
            img_url = unquote(proxied)

        if img_url.startswith("/"):
            local_img_path = os.path.join("src/api/static", img_url.lstrip("/"))
            return local_img_path if os.path.exists(local_img_path) else ""

        if img_url.startswith("http://") or img_url.startswith("https://"):
            cache_dir = os.path.join("logs", "report_image_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parsed = urlparse(img_url)
            ext = os.path.splitext(parsed.path)[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            cache_path = os.path.join(cache_dir, f"{hashlib.sha1(img_url.encode('utf-8')).hexdigest()}{ext}")
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                return cache_path
            try:
                req = Request(
                    img_url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.xiaohongshu.com/",
                    },
                )
                with urlopen(req, timeout=8) as resp:
                    content = resp.read(5 * 1024 * 1024)
                if content:
                    with open(cache_path, "wb") as f:
                        f.write(content)
                    return cache_path
            except Exception as exc:
                print(f"【PDF 图片下载失败】: {exc}")
        return ""

    def _scale_report_image(self, img: Image, max_width: float = 390, max_height: float = 260) -> None:
        width = float(getattr(img, "imageWidth", 0) or 0)
        height = float(getattr(img, "imageHeight", 0) or 0)
        if width <= 0 or height <= 0:
            img.drawWidth = 260
            img.drawHeight = 180
            return
        scale = min(max_width / width, max_height / height, 1.0)
        if width < 180 or height < 120:
            scale = min(max_width / width, max_height / height)
        img.drawWidth = width * scale
        img.drawHeight = height * scale

    def _clean_pdf_text(self, value: str) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u20E3]+", "", text)
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
        text = text.replace("：：", "：").replace("()", "")
        return text.strip()

    def _list_block(self, items) -> str:
        if not items:
            return ""
        if isinstance(items, str):
            items = [part.strip(" -") for part in re.split(r"[\n；;]", items) if part.strip()]
        lines = []
        for idx, item in enumerate(items, 1):
            clean = self._clean_pdf_text(str(item)).strip(" -")
            if clean:
                lines.append(f"{idx}. {clean}")
        return "\n".join(lines)

    def _recipe_pdf_blocks(self, recipe: dict) -> list[tuple[str, str]]:
        blocks = []
        desc = self._clean_pdf_text(recipe.get("description") or recipe.get("source_summary") or "")
        if desc:
            blocks.append(("菜品说明", desc))

        ingredients = self._list_block(recipe.get("ingredients"))
        if ingredients:
            blocks.append(("食材", ingredients))

        condiments = self._list_block(recipe.get("condiments"))
        if condiments:
            blocks.append(("调料", condiments))

        steps = self._list_block(recipe.get("steps"))
        if steps:
            blocks.append(("做法步骤", steps))

        if blocks:
            return blocks

        fallback = recipe.get("recommendation_text") or recipe.get("detail_text") or recipe.get("content") or ""
        fallback = self._clean_recipe_fallback_text(fallback)
        return [("做法摘要", fallback)] if fallback else []

    def _clean_recipe_fallback_text(self, value: str) -> str:
        text = self._clean_pdf_text(value)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"【超级吃货[^】]*】[^。\n]*[。\n]?", "", text)
        text = re.sub(r"\(来源平台：[^)]*\)", "", text)
        text = re.sub(r"来源平台：[^。\n]*", "", text)
        text = re.sub(r"【小红书来源】.*", "", text)
        text = re.sub(r"从小红书正文提取：\s*", "", text)
        text = re.sub(r"请参考步骤与原文用量", "参考原笔记用量", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _to_reportlab_paragraph_text(self, value: str) -> str:
        """
        Convert UI-oriented HTML fragments into safe ReportLab Paragraph markup.
        ReportLab does not support CSS style attributes on span/div tags.
        """
        text = str(value or "")
        text = self._clean_pdf_text(text)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>|</div\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = html.escape(text)
        return text.replace("\n", "<br/>")

    def _generate_markdown(self, session_id: str, data: dict, output_path: str) -> str:
        """生成 Markdown 格式报告"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        is_home = data.get("mode") == "home_cooking"
        mode_str = "家庭烹饪指南" if is_home else "外出探店决策书"
        
        md_content = []
        md_content.append(f"# SuperFoodie {mode_str}")
        md_content.append("")
        md_content.append(f"* **会话 ID**: {session_id}")
        md_content.append(f"* **用户 ID**: {data.get('user_id', '匿名用户')}")
        md_content.append(f"* **生成时间**: {data.get('time', '')}")
        md_content.append("")
        md_content.append("---")
        md_content.append("")

        if is_home:
            md_content.append("## 菜品信息与烹饪步骤")
            selected_recipes = data.get("selected_recommendations") or []
            if selected_recipes:
                for idx, recipe in enumerate(selected_recipes, 1):
                    md_content.append(f"### {idx}. {recipe.get('name') or '备选菜品'}")
                    img_url = self._first_image_url(recipe)
                    if img_url:
                        md_content.append(f"![{recipe.get('name') or '菜品图片'}]({img_url})")
                    if recipe.get("description"):
                        md_content.append(recipe.get("description"))
                    md_content.append(recipe.get("recommendation_text") or recipe.get("detail_text") or recipe.get("content") or "暂无更详细的烹饪步骤。")
                    md_content.append("")
            else:
                md_content.append("尚未加入备选菜单。请先在页面中点击“加入备选菜单”，再导出家庭烹饪指南。")
            md_content.append("")
            md_content.append("## 饮食安全提示")
            rag_path = data.get("graph_rag_path", [])
            if rag_path:
                md_content.append(f"**图谱拦截路径**: `{' -> '.join(rag_path)}`")
                md_content.append("")
            md_content.append(data.get("health_explanation", "安全：暂未检测到食物相克或过敏冲突。"))
            md_content.append("")
        else:
            selected = data.get("selected_recommendation") or {}
            store_name = selected.get("store_name") or selected.get("name") or "已选餐厅"
            md_content.append("## 餐厅决策卡片")
            md_content.append(f"* **店名**: {store_name}")
            md_content.append(f"* **推荐菜**: {selected.get('recommended_dishes') or data.get('taste_query') or '到店优先看招牌菜/高赞菜'}")
            md_content.append(f"* **具体地址**: {selected.get('address') or '待到店确认'}")
            if selected.get("rating"):
                md_content.append(f"* **高德评分**: {selected.get('rating')}")
            if selected.get("avg_cost"):
                md_content.append(f"* **人均参考**: {selected.get('avg_cost')} 元")
            md_content.append("")
            if selected.get("description"):
                md_content.append("## 小红书/高德摘要")
                md_content.append(selected.get("description"))
                md_content.append("")
            md_content.append("## 饭后顺路安排")
            after_places = selected.get("after_meal_places") or data.get("after_meal_places") or []
            if after_places:
                for place in after_places[:3]:
                    md_content.append(f"* **{place.get('name', '饭后去处')}**: {place.get('description') or place.get('address') or '附近可顺路休息或逛玩'}")
            else:
                md_content.append("暂未选择饭后安排。")
            
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_content))
        return output_path
