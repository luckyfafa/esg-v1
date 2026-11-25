"""前置章节：封面、关于本报告、管理层致辞、第一章 关于本公司。"""
from __future__ import annotations

from .facts import Facts
from .industries import IndustryPack
from .text import join_sentences, table


def cover_data(f: Facts, pack: IndustryPack) -> dict:
    p = f.profile
    return {
        "company": f.company,
        "title": "环境、社会及管治报告",
        "title_en": "ENVIRONMENTAL, SOCIAL AND GOVERNANCE REPORT",
        "year": str(p.report_year),
        "industry": pack.label if pack.key != "unknown" else f.industry_label,
        "basis": p.reporting_basis or "企业 ESG 数据采集表",
        "boundary": p.boundary or "未填写",
        "tag": "润色版 · 依据企业填报数据整理",
    }


def about_report(f: Facts, pack: IndustryPack, source_name: str) -> str:
    p = f.profile
    md = ["## 关于本报告\n"]
    md.append(
        f"本报告为 **{f.company}**（以下简称「本公司」）{p.report_year} 年度环境、社会及管治报告，"
        f"按{pack.label}的披露重点组织章节。报告以本公司填报的 ESG 数据采集表为唯一数据来源，"
        "不引用未经核实的第三方信息，也不对缺失数据作估算或外推。\n"
    )
    md.append(
        "**报告定位。** 本报告由两层内容构成：一层是可直接核查的量化数据，包括排放、能源、"
        "水资源、废弃物、用工、安全与治理指标；另一层是围绕这些数据的文字说明，用于交代口径、"
        "解释变化原因、指出差距与改进方向。两层内容一一对应，任何结论都能回溯到具体指标。"
        "本报告适用于客户供应链审核、政府资助申请与内部管理复盘三类场景；"
        "不同场景的关注重点不同，报告已在相应章节标注证据来源。\n"
    )

    bits = [f"组织边界采用 **{p.boundary or '未填写'}**"]
    if p.boundary_entities:
        bits.append(f"覆盖 **{p.boundary_entities}**")
    if p.revenue_million:
        bits.append(f"本期营业收入 **{f.fmt(p.revenue_million)} 百万元 {p.currency}**")
    if p.employees:
        bits.append(f"员工总数 **{f.fmt(p.employees)} 人**")
    if p.baseline_year:
        bits.append(f"基准年为 **{p.baseline_year}**")
    md.append(
        "**报告范围。** " + "，".join(bits) + f"。报告期为 {p.report_year} 年度（按企业填报口径）。"
        "组织边界与基准年一经确定，跨年度应保持一致；若因并购、剥离或核算方法调整而变化，"
        "需要在下一期报告中说明原因、影响金额与可比数据的重述方式。\n"
    )
    md.append(
        f"**编制依据。** 报告结构参照香港联合交易所《环境、社会及管治报告守则》附录 C2 的一般披露、"
        "环境、社会与气候章节，并对照 IFRS S2 的治理、策略、风险管理、指标与目标四支柱组织气候内容；"
        f"行业特定的监管要求与自愿性标准见附录 D。本公司实际采用的编制依据为："
        f"{p.reporting_basis or '企业 ESG 数据采集表'}。\n"
    )
    md.append(
        "**编制流程。** 报告按以下步骤形成：\n"
        "1. 由各责任部门按采集表口径填报原始数据，并保留电费单、水费单、转移联单、"
        "考勤与培训记录等凭证；\n"
        "2. 对填报值做完整性与逻辑校验，识别缺项、异常值与单位错误；\n"
        "3. 计算比率、强度与合计指标，并与行业公开披露的分位水平对标；\n"
        "4. 按章节组织文字说明，逐项引用已核实的数据；\n"
        "5. 由管理层复核后定稿，对外披露前再次确认口径与边界。\n"
    )
    md.append(
        f"**数据质量控制。** 本期必填项完整度为 **{f.pct(f.completeness_pct)}**。"
        + (f"仍有 {f.fmt(len(f.analysis.missing_required))} 项必填指标未取得数据，已在附录 B 逐项列示。"
           if f.analysis.missing_required else "必填项无缺失。")
        + "比率类指标由填报的原始值直接计算，计算式见采集表说明列；"
        "涉及估算的（如范围三部分类别）已在对应表格中标注方法与假设，不与实测值混列。\n"
    )
    md.append(
        "**报告编制原则。** 报告在编制中遵循四项原则。**重要性**：以双重重要性评估结果决定披露深度，"
        "对影响与财务两个维度均重要的议题展开说明，对暂不重要的议题只作简要列示。**量化**：优先使用可计量、"
        "可复核的数据，定性描述用于解释数据而非替代数据。**平衡**：既披露进展，也披露差距与未完成事项，"
        "不因对外展示需要而隐去不利信息。**一致性**：口径、边界与计算方法跨年度保持一致，"
        "发生变更时同步说明原因与影响，必要时重述可比数据。\n"
    )
    md.append(
        "**数据与文字的对应关系。** 报告中的每一处量化表述都可以在第九章的关键绩效表或对应章节的表格中找到原始值，"
        "比率与强度指标同时给出分子与分母的口径说明。文字部分承担三项功能：交代数据的统计边界与计算方法、"
        "解释数据变化的管理原因、指出仍然缺失的信息及其补齐路径。凡属行业通行做法或监管要求的一般性说明，"
        "均以「行业通行」「通常」等措辞与本公司实际执行情况相区分，避免把行业惯例误读为本公司已实施的动作。\n"
    )
    md.append(
        "**与底稿的关系。** 本报告以同名的「底稿」文件为数据基础。底稿保留采集表的原始填报值、完整度诊断、"
        "行业对标结果与改进建议，供内部核对与客户审核时追溯；润色版在此基础上重组章节、补充叙述与版式，"
        "不新增、不修改任何原始数值。两者如有表述差异，以底稿中的填报值为准。\n"
    )
    md.append(
        "**审阅与使用限制。** 本报告由系统依据填报数据整理生成，**未经第三方审阅或鉴证**，"
        "不构成审计意见、合规结论、评级结果或投资建议。报告中的行业做法与监管要求为一般性说明，"
        "不表示本公司已全部实施；对外披露或提交客户前，应由本公司管理层复核，"
        "并按适用准则补充定性说明、证据索引与必要的法律意见。\n"
    )
    md.append(
        "**意见反馈。** 本公司欢迎股东、客户、员工、供应商、社区代表与监管机构就本报告的范围、数据与结论提出意见。"
        "反馈渠道与联系人信息由本公司另行公布；收到的意见将作为下一期议题评估与报告改进的输入。\n"
    )
    return "\n".join(md)


def management_message(f: Facts, pack: IndustryPack) -> str:
    p = f.profile
    counts = f.position_counts
    lines = ["## 管理层致辞\n"]
    lines.append(
        f"{p.report_year} 年，本公司在{pack.label}的经营环境中推进环境、社会及管治工作。"
        + (f"主营业务为{p.main_business}。" if p.main_business else "")
        + "本报告把本期可核实的数据完整呈现，既列示进展，也如实说明差距，"
        "不以形容词替代数字，也不对未采集的事项作出承诺。\n"
    )
    if pack.climate_notes:
        lines.append("**行业背景。** " + pack.climate_notes[0] + "\n")
    lines.append(
        f"**外部要求的变化。** 过去几年，{pack.label}面临的外部要求呈现三个方向的变化："
        "一是披露从自愿走向规范，监管机构对气候、用工与供应链信息的颗粒度要求持续提高；"
        "二是客户把 ESG 表现前置为准入条件，问卷、审核与产品碳足迹问询成为常规动作；"
        "三是融资端开始把可持续表现与资金成本挂钩，银行与投资人对数据可核查性的要求高于对口号的要求。"
        "这三重压力共同指向同一件事：数据必须能够被追溯和复核。\n"
    )

    good: list[str] = []
    if f.ops_total is not None:
        good.append(f"范围一与范围二（市场法）合计排放 **{f.fmt(f.ops_total)} tCO2e**")
    if f.val("E07") is not None:
        good.append(f"清洁能源占比 **{f.pct(f.val('E07'))}**")
    if f.val("S12") == 0:
        good.append("报告期内**未发生因工死亡事故**")
    if f.val("S15") is not None:
        good.append(f"人均培训 **{f.fmt(f.val('S15'))} 小时**")
    if f.val("G10") == 0:
        good.append("**未发生腐败与贿赂事件**")
    if f.val("G13") is not None:
        good.append(f"供应商行为准则覆盖率 **{f.pct(f.val('G13'))}**")
    if good:
        lines.append(
            "**本期主要数据。** " + "；".join(good) + "。"
            "上述数据均由填报的原始活动数据与排放因子计算得出，可在第九章的关键绩效表中逐项复核；"
            "范围三与比率类指标的估算方法已在对应位置标注。\n"
        )

    if f.behind_top:
        items = "；".join(
            f"{g.name}（本期 {f.fmt(g.client_value)} {g.unit}，行业中位 {f.fmt(g.median)} {g.unit}，"
            f"差异 {f.signed_pct(g.gap_pct)}）"
            for g in f.behind_top
        )
        lines.append(
            f"**需要正视的差距。** 在与同行业公开披露数据的对标中，本公司有 {f.fmt(counts['落后'])} 项指标"
            f"处于落后位置，主要包括{items}。差距的形成既有资源投入的客观限制，也反映管理颗粒度仍需细化。"
            "第七章列示了分阶段的改进路线图，各项动作均对应到责任部门与验证指标。\n"
        )
    else:
        lines.append(
            "**关于差距。** 本期对标未发现明显落后项，但样本、口径与年度差异仍会影响可比性，"
            "本公司将继续以年度为单位跟踪关键指标，并在口径变化时同步重述。\n"
        )

    if f.val("S07") is not None or f.val("S01") is not None:
        staff = []
        if f.val("S01") is not None:
            staff.append(f"员工总数 {f.fmt(f.val('S01'))} 人")
        if f.val("S07") is not None:
            staff.append(f"流失率 {f.pct(f.val('S07'))}")
        if f.val("S03") is not None:
            staff.append(f"女性员工占比 {f.pct(f.val('S03'))}")
        lines.append("**关于员工。** 本期" + "，".join(staff) + "。"
                     "员工是服务与交付的直接承载者，本公司把培训学时、安全培训覆盖率与流失率放在同一张表上跟踪，"
                     "以避免只关注招聘数量而忽略稳定性。\n")

    g16_note = (f.metric("G16").note if f.metric("G16") else "") or ""
    if f.text("G16") == "是" and g16_note:
        lines.append(
            f"**下一步。** 本公司已设定减排目标：{g16_note}。"
            "下一报告期将把目标分解为年度任务，按季度复核进度；同时补齐本期尚未采集的指标，"
            "特别是范围三与行业特有指标，以提升披露的完整性。\n"
        )
    else:
        lines.append(
            "**下一步。** 本公司将优先补齐排放强度、能源结构、供应链评估与行业特有指标等基础数据，"
            "在此基础上设定可量化的年度目标。在数据尚不完整时，本公司不作出无法验证的公开承诺。\n"
        )
    lines.append(
        "ESG 管理的价值在于把可核实的数据转化为可执行的动作。本报告欢迎股东、客户、员工、"
        "供应商与监管机构提出意见，帮助本公司持续改进。\n"
    )
    lines.append(
        "**关于本报告的可核查性。** 本报告的所有量化结论都能回溯到采集表的填报值或由其直接计算的比率，"
        "不使用行业均值填补空缺，也不以「预计」「约」等措辞给出未经核算的数量。"
        "这一取舍会让部分章节看起来比同行业报告简略，但它保证了每一条数字都可以在客户审核或监管问询时被验证。"
        "下一阶段的工作重点，是在保持可核查性的前提下逐步扩充数据面，而不是先写出漂亮的结论再回头找依据。\n"
    )
    return "\n".join(lines)


def chapter_company(f: Facts, pack: IndustryPack) -> str:
    p = f.profile
    md = ["## 第一章 关于本公司\n", "### 1.1 业务与价值链\n"]
    biz = f"本公司主要从事{p.main_business}，所属行业为{pack.label}。" if p.main_business \
        else f"本公司所属行业为{pack.label}，主要业务未在采集表中填写。"
    if p.revenue_million:
        biz += f"报告期内营业收入为 **{f.fmt(p.revenue_million)} 百万元 {p.currency}**。"
    else:
        biz += "报告期内营业收入未填报。"
    if p.employees:
        biz += f"截至报告期末，纳入统计的员工总数为 **{f.fmt(p.employees)} 人**。"
    else:
        biz += "员工总数本期未填报。"
    md.append(biz + "\n")
    md.append(
        "ESG 风险与机会沿价值链分布，而非集中在单一环节。下表按"
        f"{pack.label}的典型价值链拆解各环节的主要 ESG 触点，"
        "用于说明本报告为何选取相应议题与指标。\n"
    )
    rows = [[stage, touch] for stage, touch in pack.value_chain]
    md.append(table(["价值链环节", "主要 ESG 触点"], rows))

    md.append("### 1.2 报告期关键数据\n")
    rows = [
        ["营业收入", f"{f.fmt(p.revenue_million)} 百万元" if p.revenue_million else "未填报", p.currency or "—"],
        ["员工总数", f"{f.fmt(p.employees)} 人" if p.employees else "未填报", "人"],
        ["组织边界", p.boundary or "未填写", "—"],
        ["纳入范围实体", p.boundary_entities or "未填写", "—"],
        ["基准年", str(p.baseline_year) if p.baseline_year else "未设定", "年"],
        ["报告用途", p.report_purpose or "未填写", "—"],
        ["编制依据", p.reporting_basis or "未填写", "—"],
        ["必填项完整度", f.pct(f.completeness_pct), "%"],
    ]
    md.append(table(["项目", "本期内容", "单位 / 说明"], rows))

    md.append("### 1.3 报告期 ESG 事项概览\n")
    items: list[str] = []
    if f.ops_total is not None:
        items.append(f"运营排放（范围一 + 范围二市场法）合计 {f.fmt(f.ops_total)} tCO2e")
    if f.val("E01") is not None:
        items.append(f"综合能源消耗 {f.fmt(f.val('E01'))} MWh")
    if f.val("E07") is not None:
        items.append(f"清洁能源占比 {f.pct(f.val('E07'))}")
    if f.val("E16") is not None:
        items.append(f"总取水量 {f.fmt(f.val('E16'))} m³")
    if f.val("E21") is not None:
        items.append(f"废弃物回收利用率 {f.pct(f.val('E21'))}")
    if f.val("S01") is not None:
        items.append(f"员工 {f.fmt(f.val('S01'))} 人")
    if f.val("S12") is not None:
        items.append("未发生因工死亡事故" if f.val("S12") == 0 else f"因工死亡 {f.fmt(f.val('S12'))} 人")
    if f.val("G10") is not None:
        items.append("未发生腐败与贿赂事件" if f.val("G10") == 0 else f"腐败与贿赂事件 {f.fmt(f.val('G10'))} 起")
    gov = f.gov_yes_ratio
    if gov:
        items.append(f"港府 ESG 问卷肯定回答 {f.fmt(gov[0])}/{f.fmt(gov[1])} 条")
    if items:
        md.append("\n".join(f"- {x}。" for x in items) + "\n")

    md.append("### 1.4 报告边界与可比性说明\n")
    md.append(
        "组织边界的设定方式会直接影响排放与用工数据的绝对值。"
        "采用运营控制权法时，仅纳入本公司实际运营的实体；采用股权比例法时，按持股比例折算。"
        "本报告采用 " + (p.boundary or "未填写") + "，跨年度比较时应先确认边界未发生变化。\n"
    )
    md.append(
        "数据可比性还受三方面影响：一是排放因子的来源与年份，市场法与地区法的结果可能显著不同；"
        "二是业务量波动，绝对值的升降未必代表效率变化，需结合强度指标判断；"
        "三是统计口径，例如外包人员的工时是否计入安全统计、供应商的排放是否纳入范围三。"
        "上述差异已在相关章节逐项说明。\n"
    )

    md.append("### 1.5 ESG 影响的传导路径\n")
    md.append(
        f"从价值链看，{pack.label}的 ESG 影响并非平均分布，而是沿少数关键环节集中传导。"
        "第一层是资源投入环节：能源、水与原材料的使用量决定范围一、范围二排放与水耗强度，"
        "这部分数据可从电费单、水费单与采购台账直接取得，可核查性最高。"
        "第二层是生产或服务交付环节：工艺参数、设备效率与污染治理设施决定单位产出的环境负荷，"
        "也是环保合规风险的集中点。第三层是上下游环节：采购、运输、外包与产品使用构成范围三排放的主要来源，"
        "其数据依赖供应商配合，可得性最低，因此本报告对这部分只披露已采集的类别，不作推算。\n"
    )
    md.append(
        "社会维度的影响同样沿环节传导。直接用工的合同、社保、工时与安全数据由本公司掌握，"
        "披露相对完整；外包、劳务派遣与供应链用工的数据需要通过合同条款与审核获取，"
        "覆盖率的提升本身就是管理改进的度量。客户责任方面，投诉、召回与数据事件的记录集中反映交付质量，"
        "其影响具有不可逆性，因而在议题评分中被赋予较高的不可补救性权重。\n"
    )
    md.append(
        "治理维度的影响则体现在决策与监督机制上：董事会是否把 ESG 纳入议程、是否设有归口部门与考核机制，"
        "决定了上述环境与社会数据能否被持续采集。本报告因此把治理章节置于环境与社会章节之前，"
        "用意在于说明：数据的完整性首先是一个治理问题，其次才是技术问题。\n"
    )
    md.append("### 1.6 报告期内的主要变化\n")
    md.append(
        "与上一报告期相比，本期报告有三方面的变化值得说明。**其一，披露结构**："
        "本报告按双重重要性评估结果重组章节，把评估方法、评分结果与议题索引单独成章，"
        "使披露重点由「按模板填写」转为「按重要性组织」。**其二，数据范围**："
        "本期在环境、社会与治理三组核心指标之外，增加了行业特有指标，"
        "未采集项在第九章逐项标注，不以估算值填充。**其三，说明深度**："
        "对每项量化结果补充了口径、方法与限制条件，读者可以据此判断数据的适用范围。\n"
    )
    md.append(
        "上述变化会影响跨年度可比性。结构变化不影响数值本身，但章节位置调整后，"
        "同一指标可能出现在不同章节；数据范围扩大则可能使某些指标的总量上升，"
        "这属于统计口径变化而非绩效恶化。建议读者在比较两个年度时，先确认指标定义与边界是否一致。\n"
    )
    return "\n".join(md)
