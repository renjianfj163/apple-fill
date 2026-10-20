"""Product field dictionary.

Policy lives here, not in model output. Literal fields can only be copied from
a source quote. Derivable fields may later be marked derived by the model, and
still need a person to confirm them on the page.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScalarField:
    key: str
    labels: tuple[str, ...]
    derivable: bool = False


@dataclass(frozen=True)
class SpecField:
    key: str
    label: str
    labels: tuple[str, ...]


SCALAR_FIELDS: tuple[ScalarField, ...] = (
    ScalarField("productName", ("产品名称", "产品名")),
    ScalarField("productNameEn", ("英文名称", "产品英文名称", "Product Name"), derivable=True),
    ScalarField("brand", ("品牌", "商标")),
    ScalarField("modelNo", ("型号", "产品型号", "规格型号")),
    ScalarField("category", ("产品类别", "产品分类", "类别"), derivable=True),
    ScalarField("manufacturerName", ("制造商", "生产者", "生产企业")),
    ScalarField("manufacturerAddress", ("制造商地址", "生产地址", "生产企业地址")),
    ScalarField("contactName", ("联系人",)),
    ScalarField("contactPhone", ("联系电话", "联系手机")),
    ScalarField("contactEmail", ("联系邮箱", "邮箱")),
    ScalarField("description", ("产品描述", "产品简介"), derivable=True),
)

SPEC_FIELDS: tuple[SpecField, ...] = (
    SpecField("ratedVoltage", "额定电压", ("额定电压",)),
    SpecField("ratedPower", "额定功率", ("额定功率",)),
    SpecField("standardNo", "执行标准", ("执行标准", "产品标准")),
)

SCALAR_KEYS: tuple[str, ...] = tuple(field.key for field in SCALAR_FIELDS)
DERIVABLE_KEYS: frozenset[str] = frozenset(field.key for field in SCALAR_FIELDS if field.derivable)

PROMPT_VERSION = "extract-1.0.0"
SCHEMA_VERSION = "1.0.0"
