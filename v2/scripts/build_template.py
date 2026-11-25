"""生成 V2 采集表模板。

运行：
    python -m v2.scripts.build_template

说明：
- 指标清单只读复用 V1 模板（docs/非上市企业ESG数据采集表.xlsx），不修改 V1；
- 披露清单由 V2 规则库自动生成；
- 碳活动数据只填用量，排放量留给计算引擎，因子必须填写来源。
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from v2.app.rules import load_ruleset

ROOT = Path(__file__).resolve().parents[2]
V1_TEMPLATE = ROOT / "docs" / "非上市企业ESG数据采集表.xlsx"
OUT_DIR = ROOT / "v2" / "templates"
OUT_FILE = OUT_DIR / "非上市企业ESG数据采集表-v2.xlsx"

FONT = "微软雅黑"
HEAD_FILL = PatternFill("solid", fgColor="1F4E5A")
HEAD_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT, size=14, bold=True, color="1F4E5A")
NOTE_FONT = Font(name=FONT, size=9, color="6B7C88")
BODY_FONT = Font(name=FONT, size=10)
SECTION_FONT = Font(name=FONT, size=11, bold=True, color="1F4E5A")
THIN = Side(style="thin", color="C9D4DA")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

METRIC_SHEETS = {
    "E-环境": "E",
    "S-社会": "S",
    "G-治理": "G",
    "行业特有": "H",
}
METRIC_HEADERS = [
    "编号", "指标名称", "单位", "口径说明", "优先级", "责任部门",
    "本期数值", "上期数值", "变化率", "数据来源", "证据文件", "填报状态", "备注", "关联规则",
]
METRIC_WIDTHS = [8, 24, 12, 42, 9, 12, 12, 12, 10, 14, 18, 12, 22, 16]


def read_v1_metric_catalog() -> dict[str, list[dict]]:
    """从 V1 模板读取指标清单（只读）。"""
    if not V1_TEMPLATE.exists():
        raise FileNotFoundError(f"缺少 V1 模板，无法复用指标清单: {V1_TEMPLATE}")
    wb = load_workbook(V1_TEMPLATE, data_only=False)
    catalog: dict[str, list[dict]] = {}
    for sheet in METRIC_SHEETS:
        if sheet not in wb.sheetnames:
            raise ValueError(f"V1 模板缺少工作表: {sheet}")
        ws = wb[sheet]
        rows: list[dict] = []
        for r in range(5, ws.max_row + 1):
            code = ws.cell(r, 1).value
            if not code:
                break
            rows.append({
                "code": str(code).strip(),
                "name": str(ws.cell(r, 2).value or "").strip(),
                "unit": str(ws.cell(r, 3).value or "").strip(),
                "basis": str(ws.cell(r, 4).value or "").strip(),
                "level": str(ws.cell(r, 5).value or "").strip(),
                "department": str(ws.cell(r, 6).value or "").strip(),
                "industry": str(ws.cell(r, 14).value or "").strip() if sheet == "行业特有" else "",
            })
        catalog[sheet] = rows
    return catalog


def metric_rule_map() -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for rule in load_ruleset().rules:
        for code in rule.required_fields:
            mapping.setdefault(code, []).append(rule.id)
    return mapping


def style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER
    ws.row_dimensions[row].height = 30


def set_widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def finish_sheet(ws, header_row: int, ncols: int) -> None:
    ws.freeze_panes = f"A{header_row + 1}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def add_status_validation(ws, col: str, first: int, last: int, options: list[str]) -> None:
    dv = DataValidation(
        type="list",
        formula1='"' + ",".join(options) + '"',
        allow_blank=True,
        showDropDown=False,
    )
    dv.error = "请从下拉列表中选择"
    dv.errorTitle = "取值不在允许范围"
    ws.add_data_validation(dv)
    dv.add(f"{col}{first}:{col}{last}")


def build_guide(wb: Workbook, ruleset) -> None:
    ws = wb.active
    ws.title = "使用说明"
    ws.sheet_view.showGridLines = False
    set_widths(ws, [6, 26, 96])

    lines = [
        ("非上市企业 ESG 数据采集表 · V2", TITLE_FONT),
        (f"规则版本：{ruleset.standard}（{ruleset.version}）｜采集表版本：V2.0", NOTE_FONT),
        ("", BODY_FONT),
        ("一 · 填写前必须确定的四项", SECTION_FONT),
        ("发行人类型", "主板 / GEM / 非上市。类型决定适用哪些披露规则以及披露责任，填错会导致整张检查表失效。"),
        ("财政年度起始年", "规则按财政年度生效。2025 年起范围一、范围二排放强制披露；2026 年起恒生综合大型股气候要求强制。"),
        ("组织边界", "明确按运营控制权法还是股权比例法，并写清纳入的子公司、门店、外包与租赁场所。"),
        ("基准年", "减排目标与同比的起点。一经确定，跨年度保持一致；变更需在备注说明。"),
        ("", BODY_FONT),
        ("二 · 各工作表用途", SECTION_FONT),
        ("基础信息", "企业主体、边界与报告范围，驱动适用性判定。"),
        ("E-环境 / S-社会 / G-治理 / 行业特有", "指标填报。优先级为「必填」的项会进入完整度计算。"),
        ("披露清单", "已按规则库预置条文。逐条选择状态；不适用与需解释必须写明理由。"),
        ("气候风险与机遇", "按物理风险、转型风险、机遇分类，填写时间范围、财务影响与应对措施。"),
        ("目标与行动", "减排与 ESG 目标、基准值、目标值与年度进展。"),
        ("碳活动数据", "只填用量与凭证，不填 tCO2e；排放量由核算引擎按因子计算。"),
        ("排放因子", "必须填写来源与版本。系统不内置默认因子，缺失时对应排放量不计算。"),
        ("证据索引", "每个关键数字对应一份可核验文件，供复核与追溯。"),
        ("", BODY_FONT),
        ("三 · 状态填写规则", SECTION_FONT),
        ("已填 / 已披露", "数值或内容已填写，且有证据。"),
        ("暂无数据", "本期确实无法取得。会计入缺失，需要整改计划。"),
        ("不适用", "经判断与本企业无关，必须在备注或解释列写明理由，不计入完整度分母。"),
        ("需解释", "适用于「不遵守就解释」条款。必须写明原因、整改措施、责任人与目标日期。"),
        ("", BODY_FONT),
        ("四 · 碳数据填写规则", SECTION_FONT),
        ("只填活动数据", "例如用电 kWh、管道燃气 Nm³、柴油 L、制冷剂 kg。不要自行折算成排放量。"),
        ("因子须可溯源", "填写因子来源（如香港机电工程署、生态环境部、IPCC）与版本年份。"),
        ("单位必须一致", "活动数据单位与因子分母单位须匹配，否则该行不参与计算并给出提示。"),
        ("", BODY_FONT),
        ("五 · 合规边界", SECTION_FONT),
        ("本表用途", "用于整理港交所披露底稿与管理数据，不构成审计、鉴证或合规结论。"),
        ("正式披露前", "须完成内部复核；对外披露的准确性由企业及其专业顾问负责。"),
    ]
    for r, item in enumerate(lines, start=1):
        if len(item) == 2 and isinstance(item[1], str):
            label, text = item
            ws.cell(r, 2, label).font = Font(name=FONT, size=10, bold=True)
            cell = ws.cell(r, 3, text)
            cell.font = BODY_FONT
            cell.alignment = WRAP
        else:
            text, font = item
            merged = ws.cell(r, 2, text)
            merged.font = font
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)


def build_profile(wb: Workbook) -> None:
    ws = wb.create_sheet("基础信息")
    set_widths(ws, [4, 24, 30, 52])
    ws.merge_cells("B2:D2")
    ws["B2"] = "基础信息"
    ws["B2"].font = TITLE_FONT
    ws.merge_cells("B3:D3")
    ws["B3"] = "带下拉的单元格请从列表选择；数值不要带千分位逗号与百分号。"
    ws["B3"].font = NOTE_FONT

    rows = [
        ("公司全称", "", "与营业执照一致"),
        ("所属行业", "", "如 餐饮 / 制造业 / 物流 / 零售 / 科技"),
        ("报告年度", "", "如 2025"),
        ("财政年度起始年", "", "如 2025；未填时按报告年度处理"),
        ("发行人类型", "非上市", "主板 / GEM / 非上市"),
        ("是否恒生综合大型股", "否", "是 / 否；影响 2026 年起气候披露是否强制"),
        ("基准年", "", "用于减排目标与同比"),
        ("组织边界", "运营控制权法", "运营控制权法 / 股权比例法"),
        ("纳入范围的实体", "", "子公司、门店、外包与租赁场所清单"),
        ("营业收入（万元）", "", "系统自动换算为百万元"),
        ("币种", "CNY", "CNY / HKD / USD"),
        ("员工总数（人）", "", "全职 + 兼职，不含劳务派遣"),
        ("主要产品／服务", "", "简述主营业务"),
        ("报告用途", "", "内部管理 / 供应链审核 / 投标 / 融资"),
        ("编制依据", "", "如 港交所附录C2 / GRI / 客户要求"),
        ("数据联系人", "", "姓名与联系方式"),
        ("主要利益相关方", "", "投资者、员工、客户、供应商、社区、监管等"),
        ("利益相关方沟通方式", "", "如年度报告、问卷、会议、投诉渠道，注明频次"),
        ("ESG 战略与愿景", "", "一句话描述可持续发展战略或愿景"),
    ]
    for i, (label, value, note) in enumerate(rows, start=4):
        ws.cell(i, 2, label).font = Font(name=FONT, size=10, bold=True)
        cell = ws.cell(i, 3, value)
        cell.font = BODY_FONT
        cell.border = BORDER
        cell.fill = PatternFill("solid", fgColor="EAF4FA")
        note_cell = ws.cell(i, 4, note)
        note_cell.font = NOTE_FONT
        note_cell.alignment = WRAP

    add_status_validation(ws, "C", 8, 8, ["主板", "GEM", "非上市"])
    add_status_validation(ws, "C", 9, 9, ["是", "否"])
    add_status_validation(ws, "C", 11, 11, ["运营控制权法", "股权比例法"])
    ws.freeze_panes = "A4"


def build_metric_sheets(wb: Workbook, catalog: dict[str, list[dict]], rule_map: dict[str, list[str]]) -> None:
    for sheet, category in METRIC_SHEETS.items():
        ws = wb.create_sheet(sheet)
        ncols = len(METRIC_HEADERS) + (1 if sheet == "行业特有" else 0)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        ws.cell(1, 1, f"{sheet}指标").font = TITLE_FONT
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
        ws.cell(2, 1, "无法取得时在「填报状态」选「暂无数据」；确认无关时选「不适用」并写明理由。").font = NOTE_FONT

        headers = METRIC_HEADERS + (["适用行业"] if sheet == "行业特有" else [])
        for c, h in enumerate(headers, start=1):
            ws.cell(4, c, h)
        style_header(ws, 4, ncols)
        set_widths(ws, METRIC_WIDTHS + ([16] if sheet == "行业特有" else []))

        for idx, item in enumerate(catalog[sheet]):
            r = 5 + idx
            ws.cell(r, 1, item["code"]).font = BODY_FONT
            ws.cell(r, 2, item["name"]).font = BODY_FONT
            ws.cell(r, 3, item["unit"]).font = BODY_FONT
            ws.cell(r, 4, item["basis"]).font = BODY_FONT
            ws.cell(r, 5, item["level"]).font = BODY_FONT
            ws.cell(r, 6, item["department"]).font = BODY_FONT
            ws.cell(r, 9, f'=IFERROR(IF(OR(G{r}="",H{r}=""),"",(G{r}-H{r})/H{r}),"")').font = BODY_FONT
            ws.cell(r, 14, "、".join(rule_map.get(item["code"], []))).font = BODY_FONT
            if sheet == "行业特有":
                ws.cell(r, 15, item["industry"]).font = BODY_FONT
            for c in range(1, ncols + 1):
                ws.cell(r, c).border = BORDER
                if c in (4, 13):
                    ws.cell(r, c).alignment = WRAP
        last = 5 + len(catalog[sheet]) - 1
        add_status_validation(ws, "L", 5, max(last, 20), ["已填", "暂无数据", "不适用"])
        finish_sheet(ws, 4, ncols)


def build_disclosure(wb: Workbook, ruleset) -> None:
    ws = wb.create_sheet("披露清单")
    headers = ["规则编号", "部分", "段落", "披露项", "披露状态", "报告位置", "不适用理由／解释", "责任人", "目标完成日期"]
    widths = [12, 8, 10, 30, 14, 22, 44, 10, 14]
    ws.merge_cells("A1:I1")
    ws["A1"] = "披露清单（按规则库预置，逐条填写状态）"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:I2")
    ws["A2"] = "状态选「不适用」或「需解释」时，必须在解释列写明理由；需解释还须填写责任人与目标日期。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, widths)

    for idx, rule in enumerate(ruleset.rules):
        r = 5 + idx
        ws.cell(r, 1, rule.id).font = BODY_FONT
        ws.cell(r, 2, rule.part).font = BODY_FONT
        ws.cell(r, 3, rule.paragraph).font = BODY_FONT
        ws.cell(r, 4, rule.title).font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
        ws.cell(r, 4).alignment = WRAP
        ws.cell(r, 7).alignment = WRAP

    last = 5 + len(ruleset.rules) - 1
    add_status_validation(ws, "E", 5, max(last, 20), ["已披露", "不适用", "需解释", "未完成"])
    finish_sheet(ws, 4, len(headers))


def build_climate(wb: Workbook) -> None:
    ws = wb.create_sheet("气候风险与机遇")
    headers = ["编号", "名称", "类型", "时间范围", "对业务与财务的影响", "应对措施", "关联规则"]
    ws.merge_cells("A1:G1")
    ws["A1"] = "气候相关风险与机遇"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:G2")
    ws["A2"] = "类型分物理风险、转型风险、机遇；时间范围填短期／中期／长期，并在基础信息或备注说明定义。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, [8, 30, 12, 12, 48, 40, 14])

    for idx in range(8):
        r = 5 + idx
        ws.cell(r, 1, f"C{idx + 1:02d}").font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
            ws.cell(r, c).alignment = WRAP
    add_status_validation(ws, "C", 5, 12, ["物理风险", "转型风险", "机遇"])
    add_status_validation(ws, "D", 5, 12, ["短期", "中期", "长期"])
    finish_sheet(ws, 4, len(headers))


def build_targets(wb: Workbook) -> None:
    ws = wb.create_sheet("目标与行动")
    headers = ["编号", "目标名称", "关联指标", "基准年", "基准值", "目标年", "目标值", "本期进展", "行动项", "负责人", "关联规则"]
    ws.merge_cells("A1:K1")
    ws["A1"] = "减排与 ESG 目标"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:K2")
    ws["A2"] = "目标可为绝对量或强度；本期进展写明与基准年相比的变化，以及与目标的距离。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, [8, 30, 10, 10, 12, 10, 12, 24, 36, 10, 14])

    for idx in range(6):
        r = 5 + idx
        ws.cell(r, 1, f"T{idx + 1:02d}").font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
            ws.cell(r, c).alignment = WRAP
    finish_sheet(ws, 4, len(headers))


def build_activities(wb: Workbook) -> None:
    ws = wb.create_sheet("碳活动数据")
    headers = ["编号", "场所／门店", "期间", "能源／活动类型", "活动数据", "单位", "凭证编号", "备注"]
    ws.merge_cells("A1:H1")
    ws["A1"] = "碳活动数据（只填用量，不填排放量）"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:H2")
    ws["A2"] = "每个场所按期间分行填写；单位与排放因子分母单位须一致；用电子表格按月汇总即可，不必逐张账单录入。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, [8, 24, 12, 20, 14, 12, 14, 28])

    for idx in range(24):
        r = 5 + idx
        ws.cell(r, 1, f"A{idx + 1:02d}").font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
    finish_sheet(ws, 4, len(headers))


def build_factors(wb: Workbook) -> None:
    ws = wb.create_sheet("排放因子")
    headers = ["能源／活动类型", "地区", "年份", "因子值", "单位", "来源", "版本", "备注"]
    ws.merge_cells("A1:H1")
    ws["A1"] = "排放因子（必须可溯源）"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:H2")
    ws["A2"] = "系统不内置默认因子。未填写来源与版本的因子不会参与核算，对应排放量只标记为缺失。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, [20, 14, 8, 12, 22, 36, 12, 28])

    presets = [
        ("外购电力", "kgCO2e/kWh"),
        ("管道燃气", "kgCO2e/Nm³"),
        ("柴油", "kgCO2e/L"),
        ("汽油", "kgCO2e/L"),
        ("制冷剂 R410A", "kgCO2e/kg"),
        ("制冷剂 R134a", "kgCO2e/kg"),
        ("航空燃油", "kgCO2e/L"),
    ]
    for idx in range(12):
        r = 5 + idx
        if idx < len(presets):
            ws.cell(r, 1, presets[idx][0]).font = BODY_FONT
            ws.cell(r, 5, presets[idx][1]).font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
            ws.cell(r, c).alignment = WRAP
    finish_sheet(ws, 4, len(headers))


def build_evidence(wb: Workbook, catalog: dict[str, list[dict]]) -> None:
    ws = wb.create_sheet("证据索引")
    headers = ["编号", "指标名称", "对应规则", "证据类型", "文件名称", "存放位置", "提供人", "提供日期", "核验状态"]
    ws.merge_cells("A1:I1")
    ws["A1"] = "证据索引"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:I2")
    ws["A2"] = "关键数字都应有一份可核验文件；没有证据的填报值在复核时应被标记。"
    ws["A2"].font = NOTE_FONT
    for c, h in enumerate(headers, start=1):
        ws.cell(4, c, h)
    style_header(ws, 4, len(headers))
    set_widths(ws, [10, 26, 14, 14, 30, 26, 10, 12, 12])

    rows = [item for sheet in METRIC_SHEETS for item in catalog[sheet]]
    for idx, item in enumerate(rows):
        r = 5 + idx
        ws.cell(r, 1, item["code"]).font = BODY_FONT
        ws.cell(r, 2, item["name"]).font = BODY_FONT
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).border = BORDER
    add_status_validation(ws, "I", 5, max(5 + len(rows) - 1, 20), ["已核验", "待核验", "缺失"])
    finish_sheet(ws, 4, len(headers))


def main() -> None:
    catalog = read_v1_metric_catalog()
    ruleset = load_ruleset()
    rule_map = metric_rule_map()

    wb = Workbook()
    build_guide(wb, ruleset)
    build_profile(wb)
    build_metric_sheets(wb, catalog, rule_map)
    build_disclosure(wb, ruleset)
    build_climate(wb)
    build_targets(wb)
    build_activities(wb)
    build_factors(wb)
    build_evidence(wb, catalog)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_FILE)
    total_metrics = sum(len(v) for v in catalog.values())
    print(f"已生成: {OUT_FILE}")
    print(f"工作表: {len(wb.sheetnames)} 个 | 指标: {total_metrics} 项 | 披露规则: {len(ruleset.rules)} 条")


if __name__ == "__main__":
    main()
