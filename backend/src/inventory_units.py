"""库存计量单位的规范化。"""

from __future__ import annotations


WATER_NAMES = ('纯净水', '矿泉水', '饮用水')

#: 重量单位到克的换算系数。
WEIGHT_UNIT_GRAMS = {'g': 1.0, '克': 1.0, 'kg': 1000.0, '千克': 1000.0, '公斤': 1000.0}

#: 容量单位到毫升的换算系数。
VOLUME_UNIT_ML = {'ml': 1.0, '毫升': 1.0, 'l': 1000.0, '升': 1000.0}

#: 件数单位。大小写与中英文别名归一后与之比对。
COUNT_UNITS = (
    '个', '盒', '袋', '瓶', '包', '斤', '块', '条', '根', '颗', '把', '碗', '盘', '份',
)


def canonical_unit_name(unit: str) -> str | None:
    """把单位别名归一为 ``g`` / ``ml`` / 件数单位原文；无法识别返回 None。"""
    value = str(unit or '').strip()
    lower = value.lower()
    if lower in WEIGHT_UNIT_GRAMS:
        return 'g'
    if lower in VOLUME_UNIT_ML:
        return 'ml'
    return value if value in COUNT_UNITS else None


def normalize_inventory_amount(
    name: str,
    quantity=0,
    unit: str = '个',
    weight_grams=None,
    confidence: str | None = None,
    *,
    correct_recognized_water: bool = False,
) -> dict:
    """把重量和容量别名收敛为克重或 ``quantity + ml``。

    重量单位换算后与调用方给的 ``weight_grams`` 取较大值，不丢弃任何一个：
    `2kg` 配 `weight_grams=300` 若直接采信后者会把 2000g 记成 300g。
    容量单位下丢弃克重是有意的：包装净含量比模糊估重可靠（见 CHANGELOG v0.19.0）。
    """
    quantity = float(quantity or 0)
    weight = float(weight_grams) if weight_grams not in (None, '') else None
    canonical_unit = str(unit or '个').strip()
    lower = canonical_unit.lower()

    if lower in WEIGHT_UNIT_GRAMS:
        converted = quantity * WEIGHT_UNIT_GRAMS[lower]
        weight = converted if weight is None else max(weight, converted)
        quantity, canonical_unit = 0.0, '个'
    elif lower in VOLUME_UNIT_ML:
        quantity *= VOLUME_UNIT_ML[lower]
        canonical_unit = 'ml'
        weight = None

    corrected = False
    if (
        correct_recognized_water
        and weight is not None
        and quantity <= 0
        and any(marker in str(name or '') for marker in WATER_NAMES)
    ):
        quantity, canonical_unit, weight = weight, 'ml', None
        confidence = '低' if confidence == '低' else '中'
        corrected = True

    return {
        'quantity': quantity,
        'unit': canonical_unit,
        'weight_grams': weight,
        'confidence': confidence,
        'corrected_water_unit': corrected,
    }
