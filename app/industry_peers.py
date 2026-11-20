"""港股行业标杆库：按行业映射代表性已上市公司。

用于非上市公司的 ESG 报告生成：选同行业标杆的 ESG 报告作为模板与基准参照。
代码 + Yahoo ticker + 英文名（用于 NZDPU 查询）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PeerCompany:
    stock_code: str         # 港交所代码 5 位
    ticker: str             # Yahoo ticker
    name: str               # 英文全称（用于 NZDPU）
    name_cn: str            # 中文简称
    notes: str = ""         # 备注（披露质量、特殊口径）


# 行业 → 该行业的代表性港股标杆公司列表（按市值/披露质量排序）
PEER_LIBRARY: dict[str, list[PeerCompany]] = {
    "banking": [
        PeerCompany("00005", "0005.HK", "HSBC Holdings", "汇丰控股",
                    "全球化运营，ESG 披露按 TCFD + ISSB S2"),
        PeerCompany("01398", "1398.HK", "Industrial and Commercial Bank of China", "工商银行",
                    "国内最大商业银行，按人行绿色金融指引披露"),
        PeerCompany("03988", "3988.HK", "Bank of China", "中国银行",
                    "境内外双披露，绿色信贷数据较全"),
    ],
    "insurance": [
        PeerCompany("01299", "1299.HK", "AIA Group", "友邦保险",
                    "泛亚保险龙头，TCFD 报告典范"),
        PeerCompany("02318", "2318.HK", "Ping An Insurance", "中国平安",
                    "气候风险评估框架完整"),
    ],
    "real_estate": [
        PeerCompany("00001", "0001.HK", "CK Asset Holdings", "长江实业",
                    "地产+基建综合"),
        PeerCompany("00016", "0016.HK", "Sun Hung Kai Properties", "新鸿基地产",
                    "香港本地地产龙头，绿色建筑披露详尽"),
        PeerCompany("00688", "0688.HK", "China Overseas Land", "中国海外发展",
                    "内地房企 ESG 标杆"),
    ],
    "technology": [
        PeerCompany("00700", "0700.HK", "Tencent", "腾讯",
                    "互联网龙头，碳中和目标清晰"),
        PeerCompany("09988", "9988.HK", "Alibaba Group", "阿里巴巴",
                    "云业务范围 1/2/3 完整披露"),
        PeerCompany("03690", "3690.HK", "Meituan", "美团",
                    "本地生活平台，外卖配送排放口径特殊"),
    ],
    "utilities": [
        PeerCompany("00002", "0002.HK", "CLP Holdings", "中电控股",
                    "香港电力双寡头之一，发电排放是核心"),
        PeerCompany("00006", "0006.HK", "Power Assets Holdings", "电能实业",
                    "港灯母公司"),
        PeerCompany("00836", "0836.HK", "China Resources Power", "华润电力",
                    "新能源转型披露标杆"),
    ],
    "energy": [
        PeerCompany("00883", "0883.HK", "CNOOC", "中国海洋石油",
                    "上游油气，甲烷排放披露标杆"),
        PeerCompany("00386", "0386.HK", "Sinopec", "中国石化",
                    "下游炼化+加油站范围 3 完整"),
    ],
    "retail": [
        PeerCompany("06862", "6862.HK", "Haidilao International", "海底捞",
                    "餐饮连锁，食材采购范围 3 标杆"),
        PeerCompany("09633", "9633.HK", "Nongfu Spring", "农夫山泉",
                    "包装饮用水，水资源披露标杆"),
    ],
    "logistics": [
        PeerCompany("02628", "2628.HK", "China COSCO Shipping", "中远海控",
                    "航运排放标杆"),
        PeerCompany("06060", "6060.HK", "ZTO Express", "中通快递",
                    "快递配送车队排放"),
    ],
    "healthcare": [
        PeerCompany("01093", "1093.HK", "CSPC Pharmaceutical", "石药集团",
                    "创新药企 ESG 标杆"),
        PeerCompany("06160", "6160.HK", "Beijing-Shanghai High Speed Railway", "京沪高铁",
                    "公共交通减碳披露"),
    ],
}


# 行业中文名映射（输入支持中文行业）
INDUSTRY_ALIASES: dict[str, str] = {
    "银行": "banking", "bank": "banking", "banking": "banking",
    "金融": "banking", "financial": "banking",
    "保险": "insurance", "insurance": "insurance",
    "地产": "real_estate", "房地产": "real_estate",
    "real_estate": "real_estate", "realestate": "real_estate",
    "科技": "technology", "互联网": "technology",
    "technology": "technology", "tech": "technology",
    "公用事业": "utilities", "电力": "utilities",
    "utilities": "utilities", "utility": "utilities",
    "能源": "energy", "油气": "energy", "石油": "energy",
    "energy": "energy",
    "零售": "retail", "餐饮": "retail", "retail": "retail",
    "物流": "logistics", "航运": "logistics", "logistics": "logistics",
    "医疗": "healthcare", "医药": "healthcare", "healthcare": "healthcare",
}


def resolve_industry(raw: str) -> str | None:
    """用户输入的行业字符串 → 标准行业 key。不匹配返回 None。"""
    key = raw.strip().lower()
    if key in PEER_LIBRARY:
        return key
    if key in INDUSTRY_ALIASES:
        return INDUSTRY_ALIASES[key]
    # 模糊匹配：包含关键词
    for alias, std in INDUSTRY_ALIASES.items():
        if alias in raw.lower() or alias in key:
            return std
    return None


def get_peers(industry: str) -> list[PeerCompany]:
    """获取某行业的标杆公司列表。"""
    std = resolve_industry(industry)
    if not std:
        return []
    return PEER_LIBRARY.get(std, [])


def list_industries() -> list[str]:
    """列出所有支持的行业 key。"""
    return list(PEER_LIBRARY.keys())
