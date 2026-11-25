"""ESG 报告润色层（V2）。

职责：把 V1 生成的「诊断底稿」重组为港股上市公司风格的 ESG 报告（润色版），
同时保留底稿原文。V1 代码只读复用，不做任何修改。

样本与写作规则见 data/hkex_reports/_index.json；行业差异见 industries.py。
"""
from .builder import PolishResult, polish_bytes, polish_excel

__all__ = ["PolishResult", "polish_bytes", "polish_excel"]
