"""把 V1 底稿的分析结果整理为润色版所需的「事实层」。

原则：
1. 只使用采集表填报值，以及由填报值直接计算出的比率 / 强度 / 合计；
2. 所有对外出现的数字统一经 Facts.fmt / pct / delta 输出，并登记到 allowed_numbers，
   供 verify 模块做一致性校验，确保润色版不引入底稿之外的数字；
3. 缺失一律返回 None，由叙述层如实写「未采集」，不做估算或外推。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.client_data import resolve_value
from app.schemas import ClientAnalysis, ClientMetric

# 各章正文引用的指标分组（与底稿保持一致，避免同一指标两套口径）
E_CORE = [
    ("E01", "综合能源消耗", "MWh"),
    ("E06", "清洁能源使用量", "MWh"),
    ("E07", "清洁能源占比", "%"),
    ("E08", "范围一温室气体排放", "tCO2e"),
    ("E09", "范围二排放（市场法）", "tCO2e"),
    ("E10", "范围二排放（地区法）", "tCO2e"),
    ("E15", "排放强度", "tCO2e/百万元营收"),
    ("E16", "总取水量", "m³"),
    ("E17", "总排水量", "m³"),
    ("E18", "COD 排放量", "吨"),
    ("E19", "一般废弃物", "吨"),
    ("E20", "危险废弃物", "吨"),
    ("E21", "废弃物回收利用率", "%"),
    ("E22", "环保投入", "万元"),
    ("E23", "环保行政处罚", "件"),
]

E_SCOPE3 = [
    ("E11", "类别 1 采购商品与服务"),
    ("E12", "类别 4 上游运输与配送"),
    ("E13", "类别 6 商务差旅"),
    ("E14", "类别 7 员工通勤"),
]

S_CORE = [
    ("S01", "员工总数", "人"),
    ("S02", "女性员工人数", "人"),
    ("S03", "女性员工占比", "%"),
    ("S04", "劳务派遣 / 外包人数", "人"),
    ("S05", "新入职人数", "人"),
    ("S06", "离职人数", "人"),
    ("S07", "员工流失率", "%"),
    ("S08", "女性管理者人数", "人"),
    ("S09", "女性管理者占比", "%"),
    ("S10", "社保覆盖率", "%"),
    ("S11", "劳动合同签订率", "%"),
    ("S12", "工伤死亡人数", "人"),
    ("S13", "损工事故起数", "起"),
    ("S14", "百万工时损工事故率", "LTIR"),
    ("S15", "人均培训时长", "小时"),
    ("S16", "安全培训覆盖率", "%"),
    ("S17", "劳资纠纷件数", "件"),
    ("S18", "供应商总数", "家"),
    ("S19", "已开展 ESG 评估的供应商数", "家"),
    ("S19_COV", "供应商 ESG 评估覆盖率", "%"),
    ("S20", "本地采购比例", "%"),
    ("S21", "客户投诉件数", "件"),
    ("S22", "产品召回次数", "次"),
    ("S23", "数据安全与隐私事件", "件"),
    ("S24", "公益捐赠金额", "万元"),
]

G_CORE = [
    ("G01", "董事会人数", "人"),
    ("G02", "独立董事人数", "人"),
    ("G03", "独立董事占比", "%"),
    ("G04", "女性董事人数", "人"),
    ("G05", "董事会会议次数", "次"),
    ("G09", "反腐败培训覆盖率", "%"),
    ("G10", "腐败与贿赂事件", "件"),
    ("G11", "举报渠道", "有 / 无"),
    ("G12", "受理举报件数", "件"),
    ("G13", "供应商行为准则覆盖率", "%"),
    ("G14", "ESG 制度文件", "份"),
]

# 港府 ESG 问卷覆盖到的治理条目（G20 起为是/否题）
GOV_QUESTION_MIN = 20

# 章节号：目录与正文的小节编号，登记为允许数字，避免被误判为「新数字」
SECTION_NUMBERS = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "1.1", "1.2", "1.3", "1.4", "1.5", "1.6",
    "2.1", "2.2", "2.3", "2.4", "2.5", "2.6",
    "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7",
    "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8",
    "5.1", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9",
    "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8",
    "7.1", "7.2", "7.3", "7.4", "7.5",
    "8.1", "8.2", "8.3", "8.4", "8.5",
    "9.1", "9.2", "9.3", "9.4", "9.5", "9.6",
    "B.1", "B.2", "B.3", "B.4", "B.5",
]


def _clean_num(s: str) -> str:
    s = s.replace(",", "").replace("，", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


@dataclass
class KpiRow:
    code: str
    name: str
    unit: str
    value: Optional[float]
    prev: Optional[float]
    status: str
    note: str = ""


@dataclass
class Facts:
    """润色版事实表：所有叙述都从这里取数。"""

    analysis: ClientAnalysis
    allowed_numbers: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        p = self.analysis.client.profile
        self.report_year = p.report_year
        self.baseline_year = p.baseline_year
        self._register(str(p.report_year))
        if p.baseline_year:
            self._register(str(p.baseline_year))
        for n in SECTION_NUMBERS:
            self._register(n)
        # 行业包与附录中的固定表述（管理体系标准号、范围三类别编号）
        for n in ("15", "14001", "45001", "27001", "27701", "9001"):
            self._register(n)
        # 行业对标表头的 P75 为固定表述
        self._register("75")
        # 表格中的文字（目标年份、编制依据、备注）属于填报原文，其中的数字同样可核验
        for text in (
            p.reporting_basis, p.report_purpose, p.main_business, p.boundary,
            p.boundary_entities, p.stakeholders, p.stakeholder_communication,
        ):
            self._register_text(text or "")
        for m in self.analysis.client.metrics:
            self._register_text(str(m.value or ""))
            self._register_text(m.note or "")
        for note in (self.analysis.data_quality_notes or []):
            self._register_text(note)
        for item in (self.analysis.missing_required or []):
            self._register_text(item)

    def _register_text(self, text: str) -> None:
        for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
            token = _clean_num(m)
            if token:
                self.allowed_numbers.add(token)

    # ---------- 数字登记与格式化 ----------
    def _register(self, s: str) -> None:
        if s:
            self.allowed_numbers.add(s)

    def fmt(self, v: Optional[float | int]) -> str:
        """与底稿一致的数值格式：千分位 / 1 位小数 / 2 位小数。"""
        if v is None:
            return "未采集"
        if isinstance(v, str):
            return v
        if v == int(v) and abs(v) >= 100:
            out = f"{v:,.0f}"
        elif abs(v) >= 1000:
            out = f"{v:,.0f}"
        elif abs(v) >= 10:
            out = f"{v:,.1f}".rstrip("0").rstrip(".")
        else:
            out = f"{v:.2f}".rstrip("0").rstrip(".")
        self._register(_clean_num(out))
        return out

    def pct(self, v: Optional[float]) -> str:
        if v is None:
            return "未采集"
        out = f"{v:.1f}%"
        self._register(f"{v:.1f}")
        return out

    def signed_pct(self, v: Optional[float]) -> str:
        if v is None:
            return "—"
        out = f"{v:+.1f}%"
        self._register(f"{abs(v):.1f}")
        return out

    def delta(self, cur: Optional[float], prev: Optional[float]) -> Optional[float]:
        """同比变化百分比；任一期缺失或上期为 0 时返回 None。"""
        if cur is None or prev is None or prev == 0:
            return None
        return round((cur - prev) / abs(prev) * 100, 1)

    # ---------- 取数 ----------
    def metric(self, code: str) -> Optional[ClientMetric]:
        return self.analysis.client.metric(code)

    def val(self, code: str) -> Optional[float]:
        v = resolve_value(self.analysis.client, code, self.analysis.derived or {})
        return float(v) if isinstance(v, (int, float)) else None

    def text(self, code: str) -> str:
        m = self.metric(code)
        if m is None or m.value is None:
            return ""
        return str(m.value).strip()

    def prev(self, code: str) -> Optional[float]:
        m = self.metric(code)
        if m is not None and isinstance(m.prev_value, (int, float)):
            return float(m.prev_value)
        return None

    def status(self, code: str) -> str:
        m = self.metric(code)
        if m is None:
            return "未定义"
        if m.status == "不适用":
            return "不适用"
        if m.value is None:
            return "暂无数据"
        return m.status or "已填"

    def kpi(self, spec: list[tuple[str, str, str]]) -> list[KpiRow]:
        rows: list[KpiRow] = []
        for item in spec:
            code, name, unit = item[0], item[1], item[2]
            m = self.metric(code)
            value = self.val(code)
            status = self.status(code)
            if m is None and value is not None:
                status = "计算值"
            rows.append(
                KpiRow(
                    code=code,
                    name=name,
                    unit=(m.unit if m and m.unit else unit),
                    value=value,
                    prev=self.prev(code),
                    status=status,
                    note=(m.note if m else "") or "",
                )
            )
        return rows

    # ---------- 汇总事实 ----------
    @property
    def profile(self):
        return self.analysis.client.profile

    @property
    def company(self) -> str:
        return self.profile.company_name

    @property
    def industry_label(self) -> str:
        return self.profile.industry or self.analysis.industry_key or "未填写行业"

    @property
    def gaps(self):
        return self.analysis.gaps

    @property
    def completeness_pct(self) -> float:
        return round(self.analysis.completeness * 100, 1)

    @property
    def ops_total(self) -> Optional[float]:
        s1, s2 = self.val("E08"), self.val("E09")
        if s1 is None or s2 is None:
            return None
        total = round(s1 + s2, 2)
        self.fmt(total)
        return total

    @property
    def scope3_rows(self) -> list[tuple[str, Optional[float], str]]:
        out = []
        for code, label in E_SCOPE3:
            m = self.metric(code)
            v = m.value if (m and isinstance(m.value, (int, float))) else None
            out.append((label, float(v) if v is not None else None, (m.note if m else "") or ""))
        return out

    @property
    def scope3_total(self) -> Optional[float]:
        vals = [v for _, v, _ in self.scope3_rows if v is not None]
        if not vals:
            return None
        total = round(sum(vals), 2)
        self.fmt(total)
        return total

    @property
    def gov_question_rows(self) -> list[ClientMetric]:
        return [
            m for m in self.analysis.client.metrics
            if m.code.startswith("G")
            and m.code[1:].isdigit()
            and int(m.code[1:]) >= GOV_QUESTION_MIN
            and m.value in ("是", "否")
        ]

    @property
    def gov_yes_ratio(self) -> Optional[tuple[int, int, float]]:
        rows = self.gov_question_rows
        if not rows:
            return None
        yes = sum(1 for m in rows if m.value == "是")
        total = len(rows)
        pct = round(yes / total * 100, 1)
        self.fmt(yes)
        self.fmt(total)
        self._register(f"{pct:.1f}")
        return yes, total, pct

    @property
    def position_counts(self) -> dict[str, int]:
        counts = {"领先": 0, "接近": 0, "落后": 0}
        for g in self.gaps:
            counts[g.position] = counts.get(g.position, 0) + 1
        for v in counts.values():
            self.fmt(v)
        return counts

    @property
    def behind_top(self) -> list:
        behind = [g for g in self.gaps if g.position == "落后" and g.gap_pct is not None]
        return sorted(behind, key=lambda g: abs(g.gap_pct or 0), reverse=True)[:3]

    @property
    def ahead_list(self) -> list:
        return [g for g in self.gaps if g.position == "领先"]

    @property
    def roadmap_groups(self) -> tuple[list, list, list]:
        """与底稿 9.1–9.3 相同的分档逻辑。"""
        quick, mid, long = [], [], []
        for g in self.gaps:
            if not g.suggestion:
                continue
            row = (g.name, g.position, g.suggestion)
            if g.code in ("G09", "S07", "S14") and g.position != "领先":
                quick.append(row)
            elif g.code in ("E07", "E21", "S09", "S19_COV") and g.position != "领先":
                mid.append(row)
            else:
                long.append(row)
        return quick, mid, long

    @property
    def baseline_source(self) -> str:
        return next((b.source for b in self.analysis.bands if b.source), "行业公开披露分位统计")
