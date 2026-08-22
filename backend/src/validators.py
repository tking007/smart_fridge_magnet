"""
输入校验模块 — 统一的参数校验
支持：类型校验、范围校验、格式校验、自定义校验
"""
import re
from datetime import datetime
from typing import Any, List, Optional, Dict

from .errors import (
    BadRequestError, InvalidCategoryError, InvalidQuantityError,
    InvalidDateError, ErrorCode
)
from . import database as db
from . import inventory_units
from .inventory_units import normalize_inventory_amount


# ==================== 预设值 ====================

# 预设分类
VALID_CATEGORIES = db.DEFAULT_CATEGORIES

# 预设单位（归一后的取值：件数单位 + 克重 + 容量）
VALID_UNITS = list(inventory_units.COUNT_UNITS) + ['g', 'ml']

# 预设置信度
VALID_CONFIDENCE = ['高', '中', '低']

# 预设动作
VALID_ACTIONS = ['add', 'deduct', 'update', 'delete']


# ==================== 基础校验 ====================

def validate_required(value: Any, field_name: str) -> Any:
    """
    校验必填字段

    Args:
        value: 字段值
        field_name: 字段名

    Returns:
        校验后的值

    Raises:
        BadRequestError: 字段为空
    """
    if value is None or (isinstance(value, str) and value.strip() == ''):
        raise BadRequestError(
            code=ErrorCode.MISSING_PARAMETER,
            message=f"缺少必要参数: {field_name}",
            details={'field': field_name}
        )
    return value


def validate_string(value: Any, field_name: str, min_length: int = 0, max_length: int = None) -> str:
    """
    校验字符串

    Args:
        value: 字段值
        field_name: 字段名
        min_length: 最小长度
        max_length: 最大长度

    Returns:
        校验后的字符串

    Raises:
        BadRequestError: 校验失败
    """
    if not isinstance(value, str):
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 必须是字符串",
            details={'field': field_name, 'type': type(value).__name__}
        )

    value = value.strip()

    if len(value) < min_length:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 长度不能小于 {min_length}",
            details={'field': field_name, 'min_length': min_length, 'actual': len(value)}
        )

    if max_length and len(value) > max_length:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 长度不能超过 {max_length}",
            details={'field': field_name, 'max_length': max_length, 'actual': len(value)}
        )

    return value


def validate_number(value: Any, field_name: str, min_value: float = None, max_value: float = None) -> float:
    """
    校验数字

    Args:
        value: 字段值
        field_name: 字段名
        min_value: 最小值
        max_value: 最大值

    Returns:
        校验后的数字

    Raises:
        BadRequestError: 校验失败
    """
    try:
        if isinstance(value, str):
            value = float(value)
        elif not isinstance(value, (int, float)):
            raise ValueError()
    except (ValueError, TypeError):
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 必须是数字",
            details={'field': field_name, 'value': value}
        )

    if min_value is not None and value < min_value:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 不能小于 {min_value}",
            details={'field': field_name, 'min_value': min_value, 'actual': value}
        )

    if max_value is not None and value > max_value:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 不能超过 {max_value}",
            details={'field': field_name, 'max_value': max_value, 'actual': value}
        )

    return float(value)


def validate_integer(value: Any, field_name: str, min_value: int = None, max_value: int = None) -> int:
    """
    校验整数

    Args:
        value: 字段值
        field_name: 字段名
        min_value: 最小值
        max_value: 最大值

    Returns:
        校验后的整数

    Raises:
        BadRequestError: 校验失败
    """
    try:
        if isinstance(value, str):
            value = int(value)
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        elif not isinstance(value, int):
            raise ValueError()
    except (ValueError, TypeError):
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 必须是整数",
            details={'field': field_name, 'value': value}
        )

    if min_value is not None and value < min_value:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 不能小于 {min_value}",
            details={'field': field_name, 'min_value': min_value, 'actual': value}
        )

    if max_value is not None and value > max_value:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 不能超过 {max_value}",
            details={'field': field_name, 'max_value': max_value, 'actual': value}
        )

    return value


def validate_enum(value: Any, field_name: str, valid_values: list) -> Any:
    """
    校验枚举值

    Args:
        value: 字段值
        field_name: 字段名
        valid_values: 有效值列表

    Returns:
        校验后的值

    Raises:
        BadRequestError: 校验失败
    """
    if value not in valid_values:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"{field_name} 无效: {value}",
            details={'field': field_name, 'value': value, 'valid_values': valid_values}
        )
    return value


# ==================== 业务校验 ====================

def validate_category(category: str) -> str:
    """
    校验分类

    Args:
        category: 分类名称

    Returns:
        校验后的分类

    Raises:
        InvalidCategoryError: 分类无效
    """
    category = db.normalize_category(category)
    if category not in VALID_CATEGORIES:
        raise InvalidCategoryError(category, VALID_CATEGORIES)
    return category


def validate_quantity(quantity: Any) -> float:
    """
    校验数量

    Args:
        quantity: 数量

    Returns:
        校验后的数量

    Raises:
        InvalidQuantityError: 数量无效
    """
    try:
        if isinstance(quantity, str):
            # 尝试解析范围（如 "3-5"）
            if '-' in quantity:
                parts = quantity.split('-')
                if len(parts) == 2:
                    quantity = (float(parts[0]) + float(parts[1])) / 2
                else:
                    quantity = float(quantity)
            else:
                quantity = float(quantity)
        elif not isinstance(quantity, (int, float)):
            raise ValueError()
    except (ValueError, TypeError):
        raise InvalidQuantityError(quantity)

    if quantity <= 0:
        raise InvalidQuantityError(quantity)

    return float(quantity)


def validate_unit(unit: str) -> str:
    """把单位别名归一为预设取值；无法识别时原样保留自定义单位。

    别名映射交给 inventory_units.canonical_unit_name()，避免在此维护一份大小写变体清单。
    """
    return inventory_units.canonical_unit_name(unit) or str(unit or '').strip()


def validate_date(date_str: Any, field_name: str = 'date') -> Optional[str]:
    """
    校验日期

    Args:
        date_str: 日期字符串
        field_name: 字段名

    Returns:
        格式化后的日期 (YYYY-MM-DD) 或 None

    Raises:
        InvalidDateError: 日期无效
    """
    if date_str is None or date_str == '':
        return None

    if not isinstance(date_str, str):
        raise InvalidDateError(str(date_str))

    # 已经是标准格式
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return date_str
        except ValueError:
            raise InvalidDateError(date_str)

    # 尝试其他格式
    formats = [
        ('%Y/%m/%d', 'YYYY/MM/DD'),
        ('%Y.%m.%d', 'YYYY.MM.DD'),
        ('%Y年%m月%d日', 'YYYY年MM月DD日'),
        ('%Y%m%d', 'YYYYMMDD'),
    ]

    for fmt, _ in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    raise InvalidDateError(date_str)


def validate_confidence(confidence: str) -> str:
    """
    校验置信度

    Args:
        confidence: 置信度

    Returns:
        校验后的置信度

    Raises:
        BadRequestError: 置信度无效
    """
    if confidence not in VALID_CONFIDENCE:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"置信度无效: {confidence}",
            details={'confidence': confidence, 'valid_values': VALID_CONFIDENCE}
        )
    return confidence


def validate_date_logic(production_date: Optional[str], expiry_date: Optional[str]) -> tuple:
    """
    校验日期逻辑

    Args:
        production_date: 生产日期
        expiry_date: 保质期

    Returns:
        (production_date, expiry_date)

    Raises:
        BadRequestError: 日期逻辑错误
    """
    if production_date and expiry_date:
        if production_date > expiry_date:
            raise BadRequestError(
                code=ErrorCode.INVALID_DATE,
                message="生产日期不能晚于保质期",
                details={
                    'production_date': production_date,
                    'expiry_date': expiry_date
                }
            )
    return production_date, expiry_date


def validate_file_size(size_bytes: int, max_mb: float = 10) -> int:
    """
    校验文件大小

    Args:
        size_bytes: 文件大小（字节）
        max_mb: 最大大小（MB）

    Returns:
        文件大小

    Raises:
        BadRequestError: 文件太大
    """
    max_bytes = max_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise BadRequestError(
            code=ErrorCode.RECOGNITION_IMAGE_TOO_LARGE,
            message=f"文件太大 ({size_bytes / 1024 / 1024:.1f}MB)，最大 {max_mb:.1f}MB",
            details={'size_mb': size_bytes / 1024 / 1024, 'max_mb': max_mb}
        )
    return size_bytes


def validate_image_type(content_type: str) -> str:
    """
    校验图片类型

    Args:
        content_type: 内容类型

    Returns:
        校验后的内容类型

    Raises:
        BadRequestError: 类型不支持
    """
    valid_types = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if content_type not in valid_types:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"不支持的图片类型: {content_type}",
            details={'content_type': content_type, 'valid_types': valid_types}
        )
    return content_type


# ==================== 复合校验 ====================

def validate_inventory_item(data: Dict) -> Dict:
    """
    校验库存项数据

    Args:
        data: 库存项数据

    Returns:
        校验后的数据

    Raises:
        BadRequestError: 校验失败
    """
    result = {}

    # 必填字段
    result['name'] = validate_string(data.get('name'), 'name', min_length=1, max_length=50)
    result['category'] = validate_category(data.get('category'))
    # 归一化后 quantity 一定是 float、unit 一定是预设取值，后续只需做范围校验。
    try:
        amount = normalize_inventory_amount(
            result['name'], data.get('quantity', 0), data.get('unit', '个'),
            data.get('weight_grams'), data.get('confidence'),
        )
    except (TypeError, ValueError) as exc:
        invalid = data.get('weight_grams')
        if invalid in (None, ''):
            invalid = data.get('quantity')
        raise InvalidQuantityError(invalid) from exc
    result['weight_grams'] = (
        validate_quantity(amount['weight_grams']) if amount['weight_grams'] is not None else None
    )
    result['quantity'] = (
        0.0 if amount['quantity'] == 0 and result['weight_grams']
        else validate_quantity(amount['quantity'])
    )
    if result['quantity'] <= 0 and not result['weight_grams']:
        raise InvalidQuantityError(amount['quantity'])

    # 可选字段
    result['unit'] = validate_unit(amount['unit'])

    if 'production_date' in data:
        result['production_date'] = validate_date(data['production_date'], 'production_date')
    else:
        result['production_date'] = None

    if 'expiry_date' in data:
        result['expiry_date'] = validate_date(data['expiry_date'], 'expiry_date')
    else:
        result['expiry_date'] = None

    result['shelf_life_days'] = data.get('shelf_life_days')

    # 日期逻辑校验
    try:
        (result['production_date'], result['shelf_life_days'],
         result['expiry_date']) = db.resolve_shelf_life(
            result['production_date'], result['shelf_life_days'], result['expiry_date']
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc))

    result['confidence'] = (
        validate_confidence(amount['confidence']) if amount['confidence'] is not None else None
    )

    return result


def validate_batch_items(items: List[Dict]) -> List[Dict]:
    """
    校验批量库存项

    Args:
        items: 库存项列表

    Returns:
        校验后的列表

    Raises:
        BadRequestError: 校验失败
    """
    if not isinstance(items, list):
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message="items 必须是数组",
            details={'type': type(items).__name__}
        )

    if len(items) == 0:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message="items 不能为空",
        )

    if len(items) > 100:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message="单次最多添加 100 项",
            details={'count': len(items), 'max': 100}
        )

    return [validate_inventory_item(item) for item in items]


def validate_deduct_items(items: List[Dict]) -> List[Dict]:
    """
    校验扣减项

    Args:
        items: 扣减项列表

    Returns:
        校验后的列表

    Raises:
        BadRequestError: 校验失败
    """
    if not isinstance(items, list):
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message="items 必须是数组",
            details={'type': type(items).__name__}
        )

    if len(items) == 0:
        raise BadRequestError(
            code=ErrorCode.INVALID_PARAMETER,
            message="items 不能为空",
        )

    result = []
    for i, item in enumerate(items):
        if 'name' not in item:
            raise BadRequestError(
                code=ErrorCode.MISSING_PARAMETER,
                message=f"items[{i}] 缺少 name 字段",
            )
        has_quantity = item.get('quantity') not in (None, '')
        has_weight = item.get('weight_grams') not in (None, '')
        if not has_quantity and not has_weight:
            raise BadRequestError(
                code=ErrorCode.MISSING_PARAMETER,
                message=f"items[{i}] 必须提供 quantity 或 weight_grams",
            )
        if has_quantity and has_weight:
            raise BadRequestError(
                code=ErrorCode.INVALID_PARAMETER,
                message=f"items[{i}] 只能扣减 quantity 或 weight_grams 其中一个",
            )

        validated = {
            'name': validate_string(item['name'], f'items[{i}].name', min_length=1, max_length=50),
        }
        amount_key = 'weight_grams' if has_weight else 'quantity'
        validated[amount_key] = validate_quantity(item[amount_key])
        result.append(validated)

    return result


# ==================== 测试 ====================

if __name__ == '__main__':
    print("Validator Module Test")
    print("=" * 50)

    # 测试成功案例
    print("\nSuccess cases:")
    try:
        print(f"  Category: {validate_category('蔬菜')}")
        print(f"  Quantity: {validate_quantity(5)}")
        print(f"  Quantity: {validate_quantity('3-5')}")
        print(f"  Date: {validate_date('2026-08-20')}")
        print(f"  Date: {validate_date('2026/08/20')}")
        print("  ✅ All passed")
    except Exception as e:
        print(f"  ❌ Failed: {e}")

    # 测试失败案例
    print("\nFailure cases:")
    test_cases = [
        (lambda: validate_category('无效'), "Invalid category"),
        (lambda: validate_quantity(0), "Zero quantity"),
        (lambda: validate_quantity(-1), "Negative quantity"),
        (lambda: validate_date('invalid'), "Invalid date"),
        (lambda: validate_date_logic('2026-08-20', '2026-07-20'), "Date logic error"),
    ]

    for func, desc in test_cases:
        try:
            func()
            print(f"  ❌ {desc}: Should have failed")
        except Exception as e:
            print(f"  ✅ {desc}: {str(e)[:50]}")

    print("\nDone!")
