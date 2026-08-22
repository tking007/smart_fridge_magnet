"""`.film` 编码器 —— 把 RGB 位图转成智能冰箱贴屏幕可接受的字节文件。

后端是 `.film` 的唯一生产实现（另一处是网页调试工具的 JS，仅用于交叉验证），两者必须
**逐字节一致**：固件 `film_validate.c` 有 7 类拒收判定，而拒收的表现是**保留旧画面**，
真机上看起来就是「上传成功、设备毫无反应」，只能接串口定位。

跨端契约（尺寸、字节数与调色板见 docs/contracts/README.md 与 film_validate.h）：

    偏移        内容
    [0:4]       209088   像素数据长度，小端 u32 —— 注意不是文件总长
    [4:6]       792      画布宽，小端 u16
    [6:8]       528      画布高，小端 u16
    [8]         6        颜色数
    [9:16]      0        保留
    [16:22]     00 FF FC E0 03 1C   调色板：固件 color_lut 认得的【真实设备色值】
    [22:32]     0        保留
    [32:]       209088 字节像素，每像素 4 bit，值为【调色板索引 0..5】

最容易写错的是最后两行的组合：**文件头写真实色值、像素写索引**。二者写反或混用，要么被
`FILM_ERR_COLOR_TABLE` / `FILM_ERR_PALETTE_INDEX` 拒收，要么整屏错色 —— 固件取色是
`color_lut[colorTable[nibble]]` 的两级间接（hal_epd.c）。

本模块只依赖标准库（不依赖 Pillow），可单独 import 做交叉验证与属性测试，
「与 JS 实现比对 SHA-256」这类验证也就不受图像库版本影响。
"""
import hashlib
import math

# ---------------------------------------------------------------- 契约常量
FILM_WIDTH = 792
FILM_HEIGHT = 528
FILM_HEADER_SIZE = 32
FILM_PIXEL_BYTES = 209088          # FILM_WIDTH * FILM_HEIGHT // 2（4bit/px）
FILM_TOTAL_SIZE = 209120           # 32 + 209088
FILM_COLOR_COUNT = 6
FILM_COLOR_TABLE_OFFSET = 0x10

# 调色板。索引即像素半字节的取值；device_code 是写入文件头 colorTable 的真实设备色值。
# 绿色刻意用 (41, 204, 20) 而不是 (0, 255, 0)：这是三端一致的既有取值，量化结果依赖它，
# 改成纯绿会让同一张输入在后端与调试工具/安卓上产出不同的 .film。
PALETTE = (
    ("黑", (0, 0, 0), 0x00),
    ("白", (255, 255, 255), 0xFF),
    ("黄", (255, 255, 0), 0xFC),
    ("红", (255, 0, 0), 0xE0),
    ("蓝", (0, 0, 255), 0x03),
    ("绿", (41, 204, 20), 0x1C),
)
PALETTE_RGB = tuple(entry[1] for entry in PALETTE)
DEVICE_COLOR_CODES = bytes(entry[2] for entry in PALETTE)

# 量化阈值。与 devtool 的 JS 实现完全相同，改动任一处都会破坏逐字节一致性。
_LOW_SATURATION = 0.12      # 饱和度低于此值直接判黑白，不参与彩色比较
_SAT_WEIGHT = 120.0         # 彩色距离里饱和度差的权重
_LUM_WEIGHT = 80.0          # 彩色距离里亮度差的权重
_NEUTRAL_PREFERENCE = 0.45  # 中性色（黑/白）Lab 距离明显更近时改判中性


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple:
    """RGB -> HSL。h ∈ [0,360)，s / l ∈ [0,1]。与 devtool rgbToHsl 逐行等价。"""
    rn, gn, bn = r / 255, g / 255, b / 255
    high, low = max(rn, gn, bn), min(rn, gn, bn)
    lightness = (high + low) / 2
    if high == low:
        return 0.0, 0.0, lightness

    delta = high - low
    saturation = delta / (2 - high - low) if lightness > 0.5 else delta / (high + low)
    if high == rn:
        hue = ((gn - bn) / delta + (6 if gn < bn else 0)) / 6
    elif high == gn:
        hue = ((bn - rn) / delta + 2) / 6
    else:
        hue = ((rn - gn) / delta + 4) / 6
    return hue * 360, saturation, lightness


def _rgb_to_lab(r: int, g: int, b: int) -> tuple:
    """sRGB -> CIE Lab（D65）。与 devtool rgbToLab 逐行等价。

    立方根用 math.cbrt（而非 x ** (1/3)）以对齐 JS 的 Math.cbrt —— 两者都是专用实现，
    误差比幂运算更小。这一点关系到「与 JS 输出逐字节相同」这条验收：
    量化的中性色回退是一次浮点比较，在极接近的边界上，最后几位的差异足以让判定翻转。
    """
    values = []
    for channel in (r / 255, g / 255, b / 255):
        linear = (
            math.pow((channel + 0.055) / 1.055, 2.4)
            if channel > 0.04045
            else channel / 12.92
        )
        values.append(linear * 100)
    rl, gl, bl = values

    x = (rl * 0.4124 + gl * 0.3576 + bl * 0.1805) / 95.047
    y = (rl * 0.2126 + gl * 0.7152 + bl * 0.0722) / 100
    z = (rl * 0.0193 + gl * 0.1192 + bl * 0.9505) / 108.883

    def _f(t: float) -> float:
        return math.cbrt(t) if t > 0.008856 else (7.787 * t) + 16 / 116

    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab_distance(a: tuple, b: tuple) -> float:
    dl, da, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dl * dl + da * da + db * db)


_PALETTE_HSL = tuple(_rgb_to_hsl(*rgb) for rgb in PALETTE_RGB)
_PALETTE_LAB = tuple(_rgb_to_lab(*rgb) for rgb in PALETTE_RGB)
_LAB_BLACK = _PALETTE_LAB[0]
_LAB_WHITE = _PALETTE_LAB[1]


def rgb_to_index(r: int, g: int, b: int) -> int:
    """求最接近的调色板【索引 0..5】。

    返回索引而不是颜色：像素半字节要存的就是索引，返回颜色再反查既多一次线性搜索，
    也给「误把设备色值当索引写进像素」留了口子。

    算法与 devtool findClosestIndex 完全相同：
      1. 饱和度 < 0.12 视为中性，按亮度取白(1)或黑(0)；
      2. 否则只在四个彩色项(2..5)里比 `色相差 + 饱和差*120 + 亮度差*80`，色相差取环形最小值；
      3. 再用 Lab 距离做一次中性回退：黑/白明显更近（< 所选色距离的 0.45 倍）时改判中性。
    第 3 步是必要的：单纯按 HSL 比较会把深灰、米白之类判成彩色，电子纸上非常刺眼。

    **不做抖动（dither）**：抖动不会被固件拒收，但会让同一输入在两端产出不同字节，
    破坏「调试工具预览 == 设备显示」这个排障前提。
    """
    hue, saturation, lightness = _rgb_to_hsl(r, g, b)

    if saturation < _LOW_SATURATION:
        return 1 if lightness > 0.5 else 0

    best_index = 0
    best_distance = float("inf")
    for index in range(2, len(PALETTE)):
        p_hue, p_sat, p_lum = _PALETTE_HSL[index]
        hue_diff = abs(hue - p_hue)
        if hue_diff > 180:
            hue_diff = 360 - hue_diff
        distance = (
            hue_diff
            + abs(saturation - p_sat) * _SAT_WEIGHT
            + abs(lightness - p_lum) * _LUM_WEIGHT
        )
        if distance < best_distance:
            best_distance = distance
            best_index = index

    lab_input = _rgb_to_lab(r, g, b)
    distance_black = _lab_distance(lab_input, _LAB_BLACK)
    distance_white = _lab_distance(lab_input, _LAB_WHITE)
    distance_neutral = min(distance_black, distance_white)
    neutral_index = 0 if distance_black < distance_white else 1
    distance_chosen = _lab_distance(lab_input, _PALETTE_LAB[best_index])

    if distance_neutral < distance_chosen * _NEUTRAL_PREFERENCE:
        return neutral_index
    return best_index


def quantize_rgb(rgb_bytes: bytes) -> bytearray:
    """把扁平 RGB 字节流量化成逐像素调色板索引。

    @param rgb_bytes 长度必须是 FILM_WIDTH * FILM_HEIGHT * 3（row-major，RGB 三通道）
    @return 长度 FILM_WIDTH * FILM_HEIGHT 的索引数组，每项 0..5

    性能取舍：用一个**按精确 RGB 值缓存**的字典，而不是把 RGB 降精度做查找表。降精度
    （如 5-bit/通道的 32768 项 LUT）会让结果与 JS 的全精度计算在边界颜色上出现差异，
    违背「逐字节一致」这条硬约束，也就无法用交叉验证 SHA-256 来证明编码正确。

    精确缓存两头都占：主要输入是后端渲染的菜单图，只用调色板里的 6 个精确色加上文字
    抗锯齿的少量中间色，去重后通常只有几百种取值；喂进照片时也只是退化为逐像素全精度
    计算，结果依旧正确。缓存是函数内局部的，用完即释放。
    """
    expected = FILM_WIDTH * FILM_HEIGHT * 3
    if len(rgb_bytes) != expected:
        raise ValueError(
            f"RGB 数据长度错误: {len(rgb_bytes)} != {expected} "
            f"（应为 {FILM_WIDTH}x{FILM_HEIGHT} 的 RGB 三通道）"
        )

    indices = bytearray(FILM_WIDTH * FILM_HEIGHT)
    cache: dict = {}
    for pixel in range(FILM_WIDTH * FILM_HEIGHT):
        offset = pixel * 3
        key = rgb_bytes[offset:offset + 3]
        index = cache.get(key)
        if index is None:
            index = rgb_to_index(key[0], key[1], key[2])
            cache[key] = index
        indices[pixel] = index
    return indices


def build_header() -> bytes:
    """构造 32 字节文件头。"""
    header = bytearray(FILM_HEADER_SIZE)
    header[0:4] = FILM_PIXEL_BYTES.to_bytes(4, "little")
    header[4:6] = FILM_WIDTH.to_bytes(2, "little")
    header[6:8] = FILM_HEIGHT.to_bytes(2, "little")
    header[8] = FILM_COLOR_COUNT
    # 调色板写【真实设备色值】。写成索引 0..5 会被固件 FILM_ERR_COLOR_TABLE 拒收。
    header[FILM_COLOR_TABLE_OFFSET:FILM_COLOR_TABLE_OFFSET + FILM_COLOR_COUNT] = \
        DEVICE_COLOR_CODES
    return bytes(header)


def build_film(indices: bytes) -> bytes:
    """把调色板索引数组打包成完整 `.film`（头 + 像素）。

    排布必须是 row-major `lin = y * 792 + x`，且**偶数 lin 占高 4 位**、奇数占低 4 位。
    固件按 `pixelData[i * 396 + j]` 逐行读取、先取高半字节（hal_epd.c），
    三端写入侧与之严格对应；高低位写反会让画面按 2 像素为单位左右互换。
    """
    if len(indices) != FILM_WIDTH * FILM_HEIGHT:
        raise ValueError(
            f"索引数组长度错误: {len(indices)} != {FILM_WIDTH * FILM_HEIGHT}"
        )

    pixels = bytearray(FILM_PIXEL_BYTES)
    for lin in range(0, FILM_WIDTH * FILM_HEIGHT, 2):
        high = indices[lin] & 0x0F
        low = indices[lin + 1] & 0x0F
        pixels[lin >> 1] = (high << 4) | low

    film = build_header() + bytes(pixels)
    # 断言而非静默返回：长度不对说明上面的常量或打包逻辑被改坏了，
    # 与其让设备去拒收（表现为「上传成功但屏幕不动」），不如在这里直接暴露。
    if len(film) != FILM_TOTAL_SIZE:
        raise AssertionError(f"生成的 .film 长度异常: {len(film)} != {FILM_TOTAL_SIZE}")
    return film


def encode_rgb_image(rgb_bytes: bytes) -> bytes:
    """扁平 RGB 字节流 -> 完整 `.film`。

    调用方（menu_render / API 端点）负责把画布准备成 792x528，例如 Pillow 侧用
    `image.convert("RGB").tobytes()`。本模块刻意不 import Pillow，以便单独做
    交叉验证与属性测试。
    """
    return build_film(quantize_rgb(rgb_bytes))


def sha256_hex(film: bytes) -> str:
    """`.film` 的 SHA-256（小写 hex）—— manifest 里的 sha256 字段就是这个格式。"""
    return hashlib.sha256(film).hexdigest()


#: 半字节拆分查表。`bytes.translate` 要求 256 字节的映射表，索引即原字节值。
_NIBBLE_HIGH = bytes((value >> 4) & 0x0F for value in range(256))
_NIBBLE_LOW = bytes(value & 0x0F for value in range(256))


def palette_bytes() -> bytes:
    """256 项 RGB 调色板，供 Pillow 的 `Image.putpalette()` 用。

    调色板外的索引（.film 里只会出现 0..5，但字节里能表示到 15）全部填白，与
    [index_to_rgb] 的越界回落一致 —— 否则同一个坏文件在"直接出 P 图"和"先转 RGB"两条
    路径上会显示成不同颜色。
    """
    table = bytearray()
    for rgb in PALETTE_RGB:
        table += bytes(rgb)
    fallback = bytes(PALETTE_RGB[1])
    table += fallback * (256 - len(PALETTE_RGB))
    return bytes(table)


def index_to_rgb(index: int) -> tuple:
    """索引 -> 预览用 RGB。越界回落为白，与固件/安卓的解码侧行为一致。"""
    if 0 <= index < len(PALETTE_RGB):
        return PALETTE_RGB[index]
    return PALETTE_RGB[1]


def film_to_indices(film: bytes) -> bytearray:
    """完整 `.film` -> 调色板索引数组（[build_film] 的逆运算）。

    用于在 App 上看到设备屏幕上的那一张：BLE 协议里**没有**「读取设备上 .film 内容」的
    命令，而后端手里有每个版本的 .film 原文件。

    严格校验长度与头部，不做任何容错：一个长度不对的 .film 反解出来是一张错位的花屏图，
    而排障工具给出错误画面会把人引向渲染和固件，问题却在这个文件本身。
    """
    if len(film) != FILM_TOTAL_SIZE:
        raise ValueError(
            f".film 长度错误: {len(film)} != {FILM_TOTAL_SIZE}"
        )

    width = int.from_bytes(film[4:6], "little")
    height = int.from_bytes(film[6:8], "little")
    if width != FILM_WIDTH or height != FILM_HEIGHT:
        raise ValueError(
            f".film 头部尺寸错误: {width}x{height} != {FILM_WIDTH}x{FILM_HEIGHT}"
        )

    pixels = film[FILM_HEADER_SIZE:]
    indices = bytearray(FILM_WIDTH * FILM_HEIGHT)
    # 拆半字节用 bytes.translate + 切片赋值，全部在 C 层完成：逐字节 Python 循环要遍历
    # 41 万像素（实测 41 ms/张），而预览端点在 --workers 1 的进程里跑，那段时间设备拉
    # manifest 只能排队。语义与逐字节版本完全一致（输出逐字节比对过）。
    #
    # 高 4 位是偶数 lin、低 4 位是奇数 lin，与 build_film 严格对应。
    # 反了会让画面按 2 像素为单位左右互换 —— 那种错在缩略图上几乎看不出来。
    indices[0::2] = pixels.translate(_NIBBLE_HIGH)
    indices[1::2] = pixels.translate(_NIBBLE_LOW)
    return indices


def film_to_rgb(film: bytes) -> bytes:
    """完整 `.film` -> 扁平 RGB 字节流（792x528x3），供 Pillow 直接构图。

    颜色走 [index_to_rgb]，与安卓端 FilmDecoder 用同一张调色板 —— 两边各存一份 RGB
    的话，同一个文件在 App 上与后端预览里会显示成不同的颜色，而两边看着都"正常"。
    """
    indices = film_to_indices(film)
    palette = palette_bytes()
    # 三个通道各自用 translate 展开，再按 3 字节步长交错写回：同样是把 41 万次 Python
    # 循环换成 C 层批量操作。逐通道查表表用 palette 的对应字节。
    out = bytearray(len(indices) * 3)
    indices_bytes = bytes(indices)
    for channel in range(3):
        table = bytes(palette[value * 3 + channel] for value in range(256))
        out[channel::3] = indices_bytes.translate(table)
    return bytes(out)
