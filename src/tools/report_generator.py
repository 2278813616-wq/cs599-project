import os

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

class FoodieReportGenerator:
    def __init__(self):
        self.enabled_pdf = HAS_REPORTLAB
        if not self.enabled_pdf:
            print("【ReportLab 警告】未安装 reportlab 模块，将自动生成 Markdown 格式的就餐决策报告。")

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

        # 标题
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontSize=22,
            leading=26,
            textColor=colors.HexColor('#2E4053'),
            spaceAfter=15
        )
        mode_str = "家庭主厨做菜" if data.get("mode") == "home_cooking" else "外出探店聚餐"
        story.append(Paragraph(f"SuperFoodie 吃货就餐决策书 - {mode_str}", title_style))
        story.append(Spacer(1, 10))

        # 元数据表格
        meta_data = [
            [Paragraph("<b>会话 ID:</b>", styles['Normal']), Paragraph(session_id, styles['Normal'])],
            [Paragraph("<b>用户 ID:</b>", styles['Normal']), Paragraph(data.get("user_id", "匿名用户"), styles['Normal'])],
            [Paragraph("<b>生成时间:</b>", styles['Normal']), Paragraph(data.get("time", ""), styles['Normal'])]
        ]
        t = Table(meta_data, colWidths=[100, 350])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F2F4F4')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#BDC3C7'))
        ]))
        story.append(t)
        story.append(Spacer(1, 15))

        # 主推荐文本
        sect_style = ParagraphStyle(
            'SectStyle',
            parent=styles['Heading2'],
            fontSize=14,
            leading=18,
            textColor=colors.HexColor('#1E8449'),
            spaceBefore=10,
            spaceAfter=8
        )
        story.append(Paragraph("🍴 核心美食推荐", sect_style))
        story.append(Paragraph(data.get("recommendation_text", "暂无推荐内容"), styles['BodyText']))
        story.append(Spacer(1, 15))

        # Graph-RAG 健康前置安全网
        story.append(Paragraph("🍏 Graph-RAG 健康图谱审查", sect_style))
        rag_path = data.get("graph_rag_path", [])
        if rag_path:
            path_str = " -> ".join(rag_path)
            story.append(Paragraph(f"<b>图谱拦截路径:</b> {path_str}", styles['BodyText']))
        story.append(Paragraph(data.get("health_explanation", "安全：暂未检测到食物相克或过敏冲突。"), styles['BodyText']))
        story.append(Spacer(1, 15))

        # 高德地图出行规划
        story.append(Paragraph("🚗 地图出行规划", sect_style))
        nav = data.get("navigation_info", {})
        if nav:
            nav_text = f"<b>从</b> '{nav.get('origin')}' <b>到</b> '{nav.get('destination')}' ({nav.get('mode')})<br/>" \
                       f"<b>总耗时:</b> {nav.get('duration_minutes')} 分钟 | <b>距离:</b> {nav.get('distance_km')} 公里<br/>" \
                       f"<b>路线参考:</b> {nav.get('description')}"
            story.append(Paragraph(nav_text, styles['BodyText']))
        else:
            story.append(Paragraph("无需出行（家庭烹饪指导模式）", styles['BodyText']))

        # 构建 PDF
        doc.build(story)
        return output_path

    def _generate_markdown(self, session_id: str, data: dict, output_path: str) -> str:
        """生成 Markdown 格式报告"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        mode_str = "家庭主厨做菜" if data.get("mode") == "home_cooking" else "外出探店聚餐"
        
        md_content = []
        md_content.append(f"# SuperFoodie 吃货就餐决策书 - {mode_str}")
        md_content.append("")
        md_content.append(f"* **会话 ID**: {session_id}")
        md_content.append(f"* **用户 ID**: {data.get('user_id', '匿名用户')}")
        md_content.append(f"* **生成时间**: {data.get('time', '')}")
        md_content.append("")
        md_content.append("---")
        md_content.append("")
        md_content.append("## 🍴 核心美食推荐")
        md_content.append(data.get("recommendation_text", "暂无推荐内容"))
        md_content.append("")
        md_content.append("## 🍏 Graph-RAG 健康图谱审查")
        rag_path = data.get("graph_rag_path", [])
        if rag_path:
            md_content.append(f"**图谱拦截路径**: `{' -> '.join(rag_path)}`")
            md_content.append("")
        md_content.append(data.get("health_explanation", "安全：暂未检测到食物相克或过敏冲突。"))
        md_content.append("")
        md_content.append("## 🚗 地图出行规划")
        nav = data.get("navigation_info", {})
        if nav:
            md_content.append(f"* **从**: '{nav.get('origin')}' **到**: '{nav.get('destination')}' ({nav.get('mode')})")
            md_content.append(f"* **总耗时**: {nav.get('duration_minutes')} 分钟 | **距离**: {nav.get('distance_km')} 公里")
            md_content.append(f"* **路线参考**: {nav.get('description')}")
        else:
            md_content.append("无需出行（家庭烹饪指导模式）")
            
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_content))
        return output_path
