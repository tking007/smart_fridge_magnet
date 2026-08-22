"""
图片处理模块 — 压缩、模糊检测、方向修正、质量检查
"""
import os
import io
from typing import Tuple, Dict

try:
    from PIL import Image, ImageEnhance, ExifTags
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ==================== 配置 ====================

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_DIMENSION = 2048  # 最大边长
COMPRESSED_SIZE = 2 * 1024 * 1024  # 压缩目标 2MB
BLUR_THRESHOLD = 100  # 模糊检测阈值
MIN_BRIGHTNESS = 50  # 最低亮度


# ==================== 图片压缩 ====================

def compress_image(image_path: str, max_size_mb: float = 2, max_dimension: int = 2048) -> str:
    """
    压缩图片

    Args:
        image_path: 原始图片路径
        max_size_mb: 目标大小（MB）
        max_dimension: 最大边长

    Returns:
        str: 压缩后的图片路径
    """
    if not HAS_PIL:
        return image_path

    # with 只管原始文件句柄。fix_orientation / convert / resize 返回的都是新对象，
    # 不持有文件句柄，块外继续用是安全的；不 with 则句柄会一直挂到对象被 GC，
    # 长跑进程里每处理一张图漏一个 fd。
    with Image.open(image_path) as opened:
        img = fix_orientation(opened)
        # 转为 RGB（处理 RGBA/P 等格式）
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        # 缩放
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            # 未缩放也未转模式时 img 仍是 opened 本身，出块就被关掉。复制一份脱离句柄。
            img = img.copy() if img is opened else img

        # 压缩
        buffer = io.BytesIO()
        quality = 85
        max_bytes = max_size_mb * 1024 * 1024

        while quality >= 30:
            buffer.seek(0)
            buffer.truncate()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            if buffer.tell() <= max_bytes:
                break
            quality -= 5

    compressed_path = image_path + '.compressed.jpg'
    with open(compressed_path, 'wb') as f:
        f.write(buffer.getvalue())

    return compressed_path


def fix_orientation(img: Image.Image) -> Image.Image:
    """
    修正图片方向（根据 EXIF 信息）

    Args:
        img: PIL Image 对象

    Returns:
        Image.Image: 修正后的图片
    """
    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break

        exif = img._getexif()
        if exif and orientation in exif:
            if exif[orientation] == 3:
                img = img.rotate(180, expand=True)
            elif exif[orientation] == 6:
                img = img.rotate(270, expand=True)
            elif exif[orientation] == 8:
                img = img.rotate(90, expand=True)
    except (AttributeError, KeyError, IndexError):
        pass

    return img


# ==================== 模糊检测 ====================

def is_blurry(image_path: str, threshold: float = 100) -> bool:
    """
    检测图片是否模糊

    Args:
        image_path: 图片路径
        threshold: 模糊阈值（越小越模糊）

    Returns:
        bool: True 表示模糊
    """
    if HAS_CV2:
        return _is_blurry_cv2(image_path, threshold)
    elif HAS_PIL:
        return _is_blurry_pil(image_path, threshold)
    else:
        return False  # 无法检测，假设不模糊


def _is_blurry_cv2(image_path: str, threshold: float) -> bool:
    """使用 OpenCV 检测模糊"""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    laplacian_var = cv2.Laplacian(img, cv2.CV_64F).var()
    return laplacian_var < threshold


def _is_blurry_pil(image_path: str, threshold: float) -> bool:
    """使用 PIL 检测模糊（简化版）"""
    with Image.open(image_path) as opened:
        # 缩小图片加快处理。resize 返回新对象，不再持有文件句柄。
        gray = opened.convert('L')
        img = gray.resize((gray.width // 4, gray.height // 4))

    pixels = list(img.getdata())
    width, height = img.size

    edge_sum = 0
    count = 0
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            idx = y * width + x
            # 简单拉普拉斯算子
            gx = pixels[idx + 1] - pixels[idx - 1]
            gy = pixels[idx + width] - pixels[idx - width]
            edge_sum += abs(gx) + abs(gy)
            count += 1

    if count == 0:
        return False

    avg_edge = edge_sum / count
    return avg_edge < threshold / 10  # 调整阈值适配 PIL


# ==================== 亮度检测 ====================

def get_brightness(image_path: str) -> float:
    """
    获取图片平均亮度

    Args:
        image_path: 图片路径

    Returns:
        float: 亮度值 0-255
    """
    if not HAS_PIL:
        return 128  # 默认中等亮度

    with Image.open(image_path) as opened:
        # 缩小加快处理
        img = opened.convert('L').resize((100, 100))
        pixels = list(img.getdata())
    return sum(pixels) / len(pixels)


def is_too_dark(image_path: str, threshold: float = 50) -> bool:
    """
    检测图片是否太暗

    Args:
        image_path: 图片路径
        threshold: 亮度阈值

    Returns:
        bool: True 表示太暗
    """
    return get_brightness(image_path) < threshold


# ==================== 增强处理 ====================

def enhance_image(image_path: str) -> str:
    """
    增强图片质量（提亮、增强对比度）

    Args:
        image_path: 原始图片路径

    Returns:
        str: 增强后的图片路径
    """
    if not HAS_PIL:
        return image_path

    with Image.open(image_path) as opened:
        img = fix_orientation(opened)

        # 转为 RGB
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        elif img is opened:
            # 未做任何转换时 img 还是 opened 本身，出块会被关掉。
            img = img.copy()

    # 提亮
    brightness = get_brightness(image_path)
    if brightness < 80:
        enhancer = ImageEnhance.Brightness(img)
        factor = 1.0 + (80 - brightness) / 100
        img = enhancer.enhance(min(factor, 2.0))

    # 增强对比度
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)

    # 增强锐度
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.1)

    enhanced_path = image_path + '.enhanced.jpg'
    img.save(enhanced_path, 'JPEG', quality=90)

    return enhanced_path


# ==================== 质量检查 ====================

def check_image_quality(image_path: str) -> Dict:
    """
    全面检查图片质量

    Args:
        image_path: 图片路径

    Returns:
        {
            "valid": bool,
            "errors": [...],
            "warnings": [...],
            "info": {...}
        }
    """
    errors = []
    warnings = []
    info = {}

    # 1. 文件存在检查
    if not os.path.exists(image_path):
        return {"valid": False, "errors": ["文件不存在"], "warnings": [], "info": {}}

    # 2. 文件大小检查
    file_size = os.path.getsize(image_path)
    info['file_size'] = file_size
    info['file_size_mb'] = round(file_size / 1024 / 1024, 2)

    if file_size > MAX_FILE_SIZE:
        errors.append(f"文件太大 ({info['file_size_mb']}MB)，最大 {MAX_FILE_SIZE // 1024 // 1024}MB")
    elif file_size > MAX_FILE_SIZE * 0.8:
        warnings.append(f"文件较大 ({info['file_size_mb']}MB)，建议压缩")

    # 3. 图片格式检查
    # 只在 with 块内取需要的字段，块外一律用 info：把 Image 对象带出块会让文件句柄
    # 一直挂到 GC。
    try:
        with Image.open(image_path) as img:
            info['format'] = img.format
            info['mode'] = img.mode
            info['size'] = img.size
            info['width'] = img.width
            info['height'] = img.height
    except Exception as e:
        errors.append(f"无法打开图片: {str(e)}")
        return {"valid": False, "errors": errors, "warnings": warnings, "info": info}

    # 4. 图片尺寸检查
    if info['width'] < 100 or info['height'] < 100:
        errors.append("图片太小，至少 100x100 像素")
    elif info['width'] < 300 or info['height'] < 300:
        warnings.append("图片较小，可能影响识别效果")

    # 5. 模糊检测
    blurry = is_blurry(image_path)
    info['is_blurry'] = blurry
    if blurry:
        errors.append("照片模糊，请重新拍照")

    # 6. 亮度检测
    brightness = get_brightness(image_path)
    info['brightness'] = round(brightness, 1)
    if brightness < 30:
        errors.append("照片太暗，请开灯或使用闪光灯")
    elif brightness < 50:
        warnings.append("照片较暗，可能影响识别效果")

    # 7. 长宽比检查
    aspect_ratio = max(info['size']) / min(info['size'])
    info['aspect_ratio'] = round(aspect_ratio, 2)
    if aspect_ratio > 5:
        warnings.append("图片长宽比异常，建议重新拍摄")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "info": info
    }


# ==================== 预处理流水线 ====================

def preprocess_image(image_path: str, enhance: bool = True) -> Tuple[str, Dict]:
    """
    图片预处理流水线

    Args:
        image_path: 原始图片路径
        enhance: 是否增强处理

    Returns:
        (处理后的图片路径, 处理信息)
    """
    info = {
        "original_path": image_path,
        "steps": []
    }

    # 1. 质量检查
    quality = check_image_quality(image_path)
    info['quality_check'] = quality

    if not quality['valid']:
        return None, info

    # 2. 压缩（如果需要）
    file_size = os.path.getsize(image_path)
    if file_size > COMPRESSED_SIZE:
        compressed_path = compress_image(image_path)
        info['steps'].append('compress')
        info['compressed_size'] = os.path.getsize(compressed_path)
        current_path = compressed_path
    else:
        current_path = image_path

    # 3. 增强（如果太暗）
    if enhance and quality['info'].get('brightness', 128) < 80:
        enhanced_path = enhance_image(current_path)
        info['steps'].append('enhance')
        current_path = enhanced_path

    info['final_path'] = current_path
    info['final_size'] = os.path.getsize(current_path)

    return current_path, info


def cleanup_temp_files(original_path: str):
    """清理临时文件"""
    temp_suffixes = ['.compressed.jpg', '.enhanced.jpg']
    for suffix in temp_suffixes:
        temp_path = original_path + suffix
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python image_processor.py <图片路径>")
        sys.exit(1)

    image_path = sys.argv[1]

    print(f"检查图片: {image_path}")
    print()

    # 质量检查
    result = check_image_quality(image_path)

    print(f"有效: {result['valid']}")
    print(f"信息: {result['info']}")

    if result['errors']:
        print("错误:")
        for e in result['errors']:
            print(f"  ❌ {e}")

    if result['warnings']:
        print("警告:")
        for w in result['warnings']:
            print(f"  ⚠️ {w}")

    if result['valid']:
        print()
        print("预处理...")
        processed_path, info = preprocess_image(image_path)
        print(f"处理步骤: {info['steps']}")
        print(f"最终路径: {info['final_path']}")
        print(f"最终大小: {info['final_size'] // 1024}KB")
