"""知识库 Metadata 受控词表。

LLM 可以理解“婚姻证明”等自然语言，但向量库 Metadata 必须使用稳定枚举。
本模块把模型输出归一到受控 domain_family，再展开成索引中允许的领域标签；
这样既保留 Pre-filter，也避免自由文本严格相等造成正确证据被误过滤。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


KnowledgeMaterialDomain = Literal[
    "IDENTITY",
    "HOUSEHOLD_FAMILY",
    "MARRIAGE_FAMILY",
    "PROPERTY_COLLATERAL",
    "TRANSACTION_PURPOSE",
    "AUTHORIZATION_DECLARATION",
    "BUSINESS_OPERATION",
    "INCOME_CREDIT",
    "HOUSING_POLICY",
]


DOMAIN_METADATA_VALUES: dict[KnowledgeMaterialDomain, tuple[str, ...]] = {
    "IDENTITY": ("身份与主体证明",),
    "HOUSEHOLD_FAMILY": (
        "家庭关系材料", "户籍与家庭关系", "户籍材料", "身份与户籍材料",
    ),
    "MARRIAGE_FAMILY": (
        "婚姻与家庭关系", "身份与婚姻材料", "家庭关系材料", "户籍与家庭关系",
    ),
    "PROPERTY_COLLATERAL": (
        "房产权属与抵押相关材料", "房产权属材料", "房产评估材料", "抵押人材料",
        "抵押物材料", "抵押物与授权材料",
    ),
    "TRANSACTION_PURPOSE": (
        "住房交易或建修材料", "购房交易材料", "贷款用途材料", "卖方材料",
    ),
    "AUTHORIZATION_DECLARATION": (
        "声明与授权", "授权与声明", "授权、声明与其他附件", "抵押授权材料", "抵押物与授权材料",
    ),
    "BUSINESS_OPERATION": (
        "经营主体与经营材料", "经营主体材料", "经营情况材料",
    ),
    "INCOME_CREDIT": ("收入证明材料", "经营情况材料"),
    "HOUSING_POLICY": ("异地缴存材料", "套数、征信与异地缴存材料"),
}


_DOMAIN_HINTS: tuple[tuple[tuple[str, ...], KnowledgeMaterialDomain], ...] = (
    (("婚姻", "结婚", "离婚", "婚证", "配偶"), "MARRIAGE_FAMILY"),
    (("户口", "户籍", "亲属", "子女", "家庭关系"), "HOUSEHOLD_FAMILY"),
    (("身份证", "身份材料", "身份证明", "身份与主体"), "IDENTITY"),
    (("房产", "不动产", "权属", "抵押物", "估价", "评估"), "PROPERTY_COLLATERAL"),
    (("购房", "买卖", "交易", "首付", "用途", "卖方"), "TRANSACTION_PURPOSE"),
    (("授权", "声明", "承诺", "同意抵押"), "AUTHORIZATION_DECLARATION"),
    (("经营", "营业执照", "经营主体"), "BUSINESS_OPERATION"),
    (("收入", "流水", "完税"), "INCOME_CREDIT"),
    (("异地缴存", "缴存证明", "征信", "住房套数"), "HOUSING_POLICY"),
)


@dataclass(frozen=True, slots=True)
class DomainResolution:
    """供检索和 Trace 共用的领域归一结果。"""

    family: KnowledgeMaterialDomain
    metadata_values: tuple[str, ...]
    source: str


def resolve_material_domain(
    *,
    domain_family: KnowledgeMaterialDomain | None,
    material_domain: str | None,
    material_type: str | None,
) -> DomainResolution | None:
    """把受控编码或自然语言别名解析成索引 Metadata 值。

    未识别的自由文本不会被直接用于严格过滤，否则模型一次措辞变化就会产生
    零召回；地区、产品、角色和日期过滤仍然照常执行。
    """

    if domain_family:
        return DomainResolution(
            family=domain_family,
            metadata_values=DOMAIN_METADATA_VALUES[domain_family],
            source="CONTROLLED_DOMAIN_FAMILY",
        )

    raw = " ".join(filter(None, (material_domain, material_type))).strip()
    if not raw:
        return None
    for hints, family in _DOMAIN_HINTS:
        if any(hint in raw for hint in hints):
            return DomainResolution(
                family=family,
                metadata_values=DOMAIN_METADATA_VALUES[family],
                source="NATURAL_LANGUAGE_ALIAS",
            )
    return None
