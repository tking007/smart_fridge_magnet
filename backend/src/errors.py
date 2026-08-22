"""
错误处理模块 — 统一的错误定义和处理
支持：自定义异常、错误码、错误响应格式化
"""
from typing import Dict, Any


# ==================== 错误码定义 ====================

class ErrorCode:
    """错误码常量"""

    # 通用错误 (1xxx)
    UNKNOWN_ERROR = 1000
    INVALID_REQUEST = 1001
    MISSING_PARAMETER = 1002
    INVALID_PARAMETER = 1003
    NOT_FOUND = 1004
    CONFLICT = 1005
    RATE_LIMITED = 1006
    INTERNAL_ERROR = 1007

    # 认证错误 (2xxx)
    UNAUTHORIZED = 2001
    FORBIDDEN = 2002
    TOKEN_EXPIRED = 2003

    # 业务错误 (3xxx)
    INVENTORY_NOT_FOUND = 3001
    INVENTORY_INSUFFICIENT = 3002
    INVENTORY_VERSION_CONFLICT = 3003
    INVENTORY_INVALID_CATEGORY = 3004
    INVENTORY_INVALID_QUANTITY = 3005
    INVENTORY_INVALID_DATE = 3006

    # 识别错误 (4xxx)
    RECOGNITION_FAILED = 4001
    RECOGNITION_NO_ITEMS = 4002
    RECOGNITION_IMAGE_TOO_LARGE = 4003
    RECOGNITION_IMAGE_BLURRY = 4004
    RECOGNITION_IMAGE_DARK = 4005
    RECOGNITION_TIMEOUT = 4006

    # 推荐错误 (5xxx)
    RECOMMEND_FAILED = 5001
    RECOMMEND_NO_INVENTORY = 5002
    RECOMMEND_NO_PROFILE = 5003

    # 画像错误 (6xxx)
    PROFILE_PARSE_FAILED = 6001
    PROFILE_MEMBER_EXISTS = 6002
    PROFILE_MEMBER_NOT_FOUND = 6003


# 错误消息映射
ERROR_MESSAGES = {
    ErrorCode.UNKNOWN_ERROR: "未知错误",
    ErrorCode.INVALID_REQUEST: "无效的请求",
    ErrorCode.MISSING_PARAMETER: "缺少必要参数",
    ErrorCode.INVALID_PARAMETER: "参数无效",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.CONFLICT: "资源冲突",
    ErrorCode.RATE_LIMITED: "请求过于频繁",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",

    ErrorCode.UNAUTHORIZED: "未授权",
    ErrorCode.FORBIDDEN: "禁止访问",
    ErrorCode.TOKEN_EXPIRED: "令牌已过期",

    ErrorCode.INVENTORY_NOT_FOUND: "库存项不存在",
    ErrorCode.INVENTORY_INSUFFICIENT: "库存不足",
    ErrorCode.INVENTORY_VERSION_CONFLICT: "版本冲突，请刷新后重试",
    ErrorCode.INVENTORY_INVALID_CATEGORY: "无效的分类",
    ErrorCode.INVENTORY_INVALID_QUANTITY: "无效的数量",
    ErrorCode.INVENTORY_INVALID_DATE: "无效的日期",

    ErrorCode.RECOGNITION_FAILED: "识别失败",
    ErrorCode.RECOGNITION_NO_ITEMS: "未识别到食材",
    ErrorCode.RECOGNITION_IMAGE_TOO_LARGE: "图片太大",
    ErrorCode.RECOGNITION_IMAGE_BLURRY: "图片模糊",
    ErrorCode.RECOGNITION_IMAGE_DARK: "图片太暗",
    ErrorCode.RECOGNITION_TIMEOUT: "识别超时",

    ErrorCode.RECOMMEND_FAILED: "推荐失败",
    ErrorCode.RECOMMEND_NO_INVENTORY: "库存为空，无法推荐",
    ErrorCode.RECOMMEND_NO_PROFILE: "未设置家庭画像",

    ErrorCode.PROFILE_PARSE_FAILED: "画像解析失败",
    ErrorCode.PROFILE_MEMBER_EXISTS: "成员已存在",
    ErrorCode.PROFILE_MEMBER_NOT_FOUND: "成员不存在",
}


# ==================== 自定义异常 ====================

class AppError(Exception):
    """应用基础异常"""

    def __init__(
        self,
        code: int = ErrorCode.UNKNOWN_ERROR,
        message: str = None,
        details: Any = None,
        status_code: int = 500
    ):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "未知错误")
        self.details = details
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {
            'error': self.message,
            'code': self.code
        }
        if self.details:
            result['details'] = self.details
        return result


class BadRequestError(AppError):
    """请求错误 (400)"""

    def __init__(self, code: int = ErrorCode.INVALID_REQUEST, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=400)


class NotFoundError(AppError):
    """资源不存在 (404)"""

    def __init__(self, code: int = ErrorCode.NOT_FOUND, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=404)


class ConflictError(AppError):
    """资源冲突 (409)"""

    def __init__(self, code: int = ErrorCode.CONFLICT, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=409)


class InternalError(AppError):
    """服务器内部错误 (500)"""

    def __init__(self, code: int = ErrorCode.INTERNAL_ERROR, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=500)


# ==================== 业务异常 ====================

class InventoryError(AppError):
    """库存错误基类"""

    def __init__(self, code: int, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=400)


class InventoryNotFoundError(NotFoundError):
    """库存项不存在"""

    def __init__(self, item_id: int = None):
        details = {'item_id': item_id} if item_id else None
        super().__init__(
            code=ErrorCode.INVENTORY_NOT_FOUND,
            message=f"库存项 #{item_id} 不存在" if item_id else "库存项不存在",
            details=details
        )


class InventoryInsufficientError(InventoryError):
    """库存不足"""

    def __init__(self, item_name: str, requested: float, available: float):
        super().__init__(
            code=ErrorCode.INVENTORY_INSUFFICIENT,
            message=f"{item_name} 库存不足：需要 {requested}，实际 {available}",
            details={
                'item_name': item_name,
                'requested': requested,
                'available': available
            }
        )


class InventoryVersionConflictError(ConflictError):
    """版本冲突"""

    def __init__(self, item_id: int, current_version: int, provided_version: int):
        super().__init__(
            code=ErrorCode.INVENTORY_VERSION_CONFLICT,
            message=f"版本冲突：当前版本 {current_version}，传入版本 {provided_version}",
            details={
                'item_id': item_id,
                'current_version': current_version,
                'provided_version': provided_version
            }
        )


class InvalidCategoryError(BadRequestError):
    """无效分类"""

    def __init__(self, category: str, valid_categories: list = None):
        super().__init__(
            code=ErrorCode.INVENTORY_INVALID_CATEGORY,
            message=f"无效的分类: {category}",
            details={
                'category': category,
                'valid_categories': valid_categories
            }
        )


class InvalidQuantityError(BadRequestError):
    """无效数量"""

    def __init__(self, quantity: float):
        super().__init__(
            code=ErrorCode.INVENTORY_INVALID_QUANTITY,
            message=f"无效的数量: {quantity}，必须大于0",
            details={'quantity': quantity}
        )


class InvalidDateError(BadRequestError):
    """无效日期"""

    def __init__(self, date_str: str):
        super().__init__(
            code=ErrorCode.INVENTORY_INVALID_DATE,
            message=f"无效的日期: {date_str}",
            details={'date': date_str}
        )


class RecognitionError(AppError):
    """识别错误基类"""

    def __init__(self, code: int, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=400)


class ImageTooLargeError(RecognitionError):
    """图片太大"""

    def __init__(self, size_mb: float, max_mb: float):
        super().__init__(
            code=ErrorCode.RECOGNITION_IMAGE_TOO_LARGE,
            message=f"图片太大 ({size_mb:.1f}MB)，最大 {max_mb:.1f}MB",
            details={'size_mb': size_mb, 'max_mb': max_mb}
        )


class ImageBlurryError(RecognitionError):
    """图片模糊"""

    def __init__(self):
        super().__init__(
            code=ErrorCode.RECOGNITION_IMAGE_BLURRY,
            message="图片模糊，请重新拍照",
            details={'retry': True}
        )


class ImageTooDarkError(RecognitionError):
    """图片太暗"""

    def __init__(self, brightness: float):
        super().__init__(
            code=ErrorCode.RECOGNITION_IMAGE_DARK,
            message="图片太暗，请开灯或使用闪光灯",
            details={'brightness': brightness, 'retry': True}
        )


class RecognitionTimeoutError(RecognitionError):
    """识别超时"""

    def __init__(self):
        super().__init__(
            code=ErrorCode.RECOGNITION_TIMEOUT,
            message="识别超时，请重试",
            status_code=408,
            details={'retry': True}
        )


class RecommendError(AppError):
    """推荐错误基类"""

    def __init__(self, code: int, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=400)


class NoInventoryError(RecommendError):
    """库存为空"""

    def __init__(self):
        super().__init__(
            code=ErrorCode.RECOMMEND_NO_INVENTORY,
            message="冰箱是空的，无法推荐菜谱"
        )


class ProfileError(AppError):
    """画像错误基类"""

    def __init__(self, code: int, message: str = None, details: Any = None):
        super().__init__(code=code, message=message, details=details, status_code=400)


class ProfileParseError(ProfileError):
    """画像解析失败"""

    def __init__(self, raw_input: str):
        super().__init__(
            code=ErrorCode.PROFILE_PARSE_FAILED,
            message="无法理解您的描述，请重新描述",
            details={'raw_input': raw_input}
        )


# ==================== 错误处理函数 ====================

def format_error_response(error: Exception) -> Dict:
    """
    格式化错误响应

    Args:
        error: 异常对象

    Returns:
        dict: 错误响应
    """
    if isinstance(error, AppError):
        return error.to_dict()

    # 未知错误
    return {
        'error': str(error) or '服务器内部错误',
        'code': ErrorCode.UNKNOWN_ERROR
    }


def get_status_code(error: Exception) -> int:
    """
    获取 HTTP 状态码

    Args:
        error: 异常对象

    Returns:
        int: HTTP 状态码
    """
    if isinstance(error, AppError):
        return error.status_code
    return 500


# ==================== 测试 ====================

if __name__ == '__main__':
    print("Error Module Test")
    print("=" * 50)

    # 测试各种错误
    errors = [
        InventoryNotFoundError(1),
        InventoryInsufficientError("鸡蛋", 5, 3),
        InventoryVersionConflictError(1, 2, 1),
        InvalidCategoryError("无效分类", ["蔬菜", "水果"]),
        InvalidQuantityError(0),
        InvalidDateError("2026/13/01"),
        ImageTooLargeError(15.5, 10),
        ImageBlurryError(),
        ImageTooDarkError(25),
        NoInventoryError(),
        ProfileParseError("一些无法理解的文字"),
    ]

    for error in errors:
        print(f"\n{error.__class__.__name__}:")
        print(f"  Code: {error.code}")
        print(f"  Message: {error.message}")
        print(f"  Status: {error.status_code}")
        print(f"  Response: {format_error_response(error)}")

    print("\nDone!")
