"""把菜单数据渲染成 792x528 画布，交给 film_encoder 生成 `.film`。

本文件是菜单渲染的唯一实现（见 docs/contracts/README.md）。版式沿用网页调试工具里验证过
可读性的那套排版：大字号、少信息量、强对比色块 —— 电子纸是远看的，塞满小字反而不可读。

只用调色板里那 6 个精确 RGB 作画：画布上出现调色板外的颜色时，量化会把它归到最近的一色
（例如浅灰归成白、深灰归成黑），排版效果就失控了。文字抗锯齿仍会产生中间色，那部分交给
量化处理即可。

同一份排版逻辑服务多种看屏场景，差异全部收进 LayoutSpec 参数表，渲染函数只有一条路径。

坐标系与 canvas 对齐的两个要点：
  - canvas 的 textBaseline='middle' 对应 PIL 的 anchor 垂直分量 'm'；
  - canvas 的 textAlign='left'/'right' 对应 anchor 水平分量 'l'/'r'。
不显式给 anchor 的话 PIL 默认按 'la'（左上），所有文字会整体下移半行高。
"""
import copy

from PIL import Image, ImageDraw, ImageFont

from . import config
from . import film_encoder as fe
from .logger import get_logger

logger = get_logger(__name__)

# 画布尺寸恒等于设备屏幕，避免任何缩放（缩放会引入调色板外的插值色）
WIDTH = fe.FILM_WIDTH
HEIGHT = fe.FILM_HEIGHT

# 版式输出发生变化时递增；调度器把它纳入发布幂等键，避免升级后继续复用旧 .film。
RENDER_REVISION = 5

# 调色板索引别名，画笔颜色一律从这里取
BLACK, WHITE, YELLOW, RED, BLUE, GREEN = range(6)
RGB = fe.PALETTE_RGB

# 底部临期提醒条的高度。与字号无关：这条是「有没有」的开关，不参与版式差异，
# 所以留在模块级而不是塞进 LayoutSpec。
EXPIRING_BAR_HEIGHT = 74

# 分类色块配色（与 devtool catColor 一致）
CATEGORY_COLORS = {
    '早餐': YELLOW, '午餐': GREEN, '晚餐': BLUE,
    '荤菜': RED, '素菜': GREEN, '汤品': BLUE, '主食': YELLOW,
}

# 餐段 -> (顶部条颜色, 目标文件名)。文件名的数字前缀决定设备按键轮播次序，
# 餐段关键字决定固件按时段自动显示哪一张（service_film.c）。
MEAL_STYLES = {
    'breakfast': ('早餐', YELLOW, '2_breakfast.film'),
    'lunch': ('午餐', GREEN, '3_lunch.film'),
    'dinner': ('晚餐', BLUE, '4_dinner.film'),
}
OVERVIEW_FILENAME = '1_today.film'

# 一套完整画面的槽位 -> 文件名。设备按文件名决定「什么时候显示哪一张」
# （service_film.c 用 strstr 匹配 today/breakfast/lunch/dinner，数字前缀只决定按键轮播次序），
# 所以文件名属于跨端契约，不能随意改。
SLOT_FILENAMES = {
    'today': OVERVIEW_FILENAME,
    'breakfast': MEAL_STYLES['breakfast'][2],
    'lunch': MEAL_STYLES['lunch'][2],
    'dinner': MEAL_STYLES['dinner'][2],
}


class LayoutSpec:
    """一套版式的全部可调参数。

    为什么是数据而不是多份渲染函数：版式的结构是同一个 —— 标题区 + 列表区 +
    可选的底部提醒区，彼此的差别只在字号、要不要画色块、以及横竖方向。如果各写一份
    渲染函数，修一个排版 bug（比如行高算式把最后一行挤出画布）就得逐套修改，漏一处
    就在真机上表现成「某个版式偶尔缺一行」，而这种问题从代码上看不出来。把差异收进
    参数表，渲染逻辑只有一条路径，所有版式必然同时受益。

    portrait 为什么先在 528x792 上排版、最后旋转 90 度输出 792x528：屏幕物理上就是
    横的，固件收到的是裸像素流，没有任何旋转或方向标记的能力。竖版只能是「按竖着的
    尺寸排好版，再把结果转成屏幕的横向像素」，这一步必须在渲染期做完。
    """

    def __init__(self, key, label, description,
                 portrait=False, header_bar=True, chips=True, columns=False, big=False,
                 title_size=48, date_size=28, dish_size=30, category_size=24,
                 expiring_size=28, meal_title_size=44, meal_hint_size=26,
                 dish_name_size=40, detail_size=30, detail_row_height=52,
                 row_min=46, row_max=64, margin_x=28, header_height=92,
                 header_color=RED, ingredient_color=YELLOW, accent_rail=False):
        self.key = key
        self.label = label
        self.description = description
        self.portrait = portrait
        self.header_bar = header_bar
        self.chips = chips
        # columns=True 走 render_overview_columns 那条独立排版路径；下面那些字号参数
        # 对它无效（它有自己一套）。放在同一张表里只是为了让「版式」对外仍是一个键。
        self.columns = columns
        self.big = big
        self.title_size = title_size
        self.date_size = date_size
        self.dish_size = dish_size
        self.category_size = category_size
        self.expiring_size = expiring_size
        self.meal_title_size = meal_title_size
        self.meal_hint_size = meal_hint_size
        self.dish_name_size = dish_name_size
        self.detail_size = detail_size
        self.detail_row_height = detail_row_height
        self.row_min = row_min
        self.row_max = row_max
        self.margin_x = margin_x
        self.header_height = header_height
        self.header_color = header_color
        self.ingredient_color = ingredient_color
        self.accent_rail = accent_rail

    @property
    def canvas_size(self) -> tuple:
        """排版画布的尺寸。竖版把长边放在纵向，旋转由 _finalize 收尾。"""
        return (HEIGHT, WIDTH) if self.portrait else (WIDTH, HEIGHT)


LAYOUTS = {
    'columns': LayoutSpec(
        'columns', '三餐分栏', '三餐各占一栏、栏头彩色色块，菜名下带用料；一屏看全（默认）',
        columns=True,
    ),
    'columns_big': LayoutSpec(
        'columns_big', '三餐分栏 · 大字', '同上但字号更大，三五米外也能看清；每栏少放一道',
        columns=True, big=True,
    ),
    'list': LayoutSpec(
        'list', '单列列表', '三餐混排成一列、每行一个餐次色标；菜特别多时比分栏能塞更多行',
    ),
    'list_big': LayoutSpec(
        'list_big', '单列列表 · 大字', '单列排版、字号加大约 40%；相应少显示几道菜',
        title_size=56, date_size=32, dish_size=42, category_size=28,
        expiring_size=32, dish_name_size=52, detail_size=36,
        detail_row_height=62, row_min=64, row_max=88, header_height=104,
    ),
    'portrait': LayoutSpec(
        'portrait', '竖版长列表', '把设备竖起来看，一屏能排更多菜；适合贴在冰箱侧面',
        portrait=True,
        title_size=42, date_size=24, dish_size=32, category_size=22,
        dish_name_size=38, detail_size=28, detail_row_height=46,
        row_min=42, row_max=56, header_height=84,
    ),
}

# 旧版式键 -> 新键。九套里有五套的差别只是字号与标题条颜色，用户在设置页看到九个
# 分不出区别的选项并不是选择自由，只是噪声。别名保留是因为库里存着用户选过的值，
# 直接删键会让那些设备静默回落到默认版式 —— 回落本身没错，但「我明明选过大字版」
# 这件事会变成一次无法解释的观感退化。
LAYOUT_ALIASES = {
    'classic': 'columns',
    'colorful': 'columns',
    'fresh': 'columns',
    'banner': 'columns_big',
    'bold': 'list_big',
    'minimal': 'list',
    'compact': 'list',
    'card': 'list',
}

DEFAULT_LAYOUT = 'columns'

LAYOUT_OPTION_DEFAULTS = {
    'accent': 'meal',
    'font_size': 'normal',
    'density': 'comfortable',
    'separator': 'block',
}
LAYOUT_OPTION_CHOICES = {
    'accent': [('meal', '餐次配色'), ('red', '红'), ('green', '绿'), ('blue', '蓝')],
    'font_size': [('normal', '标准字'), ('large', '大字')],
    'density': [('comfortable', '舒展'), ('compact', '紧凑')],
    'separator': [('block', '色块'), ('rail', '色带'), ('none', '留白')],
}
_ACCENT_COLORS = {'red': RED, 'green': GREEN, 'blue': BLUE}


def get_layout(key) -> LayoutSpec:
    """取版式，取不到就回落默认版式。

    这里刻意不抛异常：版式只是展示偏好，取值来自数据库里可能被手工改过的字段，
    为一个拼错的偏好值让整条下发链路失败并不成比例 —— 回落到默认版式仍然能给出
    一张可读的画面，屏幕不会因此空着。入参校验属于接口层的职责，那里该报 400，
    因为那种场合错的是调用方，需要立刻被告知。
    """
    normalized = (key or '').strip().lower()
    normalized = LAYOUT_ALIASES.get(normalized, normalized)
    return LAYOUTS.get(normalized, LAYOUTS[DEFAULT_LAYOUT])


def normalize_layout_options(raw=None, strict: bool = False) -> dict:
    """校验并补齐版式自定义参数。strict=True 时非法值直接报错。"""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for name, default in LAYOUT_OPTION_DEFAULTS.items():
        value = str(raw.get(name, default)).strip().lower()
        allowed = {key for key, _label in LAYOUT_OPTION_CHOICES[name]}
        if value not in allowed:
            if strict:
                raise ValueError(f'{name}={value!r} 不合法（可选 {" / ".join(sorted(allowed))}）')
            value = default
        result[name] = value
    return result


def layout_option_choices() -> dict:
    """供客户端直接渲染自定义控件的合法值与标签。"""
    return {
        name: [{'key': key, 'label': label} for key, label in choices]
        for name, choices in LAYOUT_OPTION_CHOICES.items()
    }


def _customized_spec(layout, options=None) -> tuple[LayoutSpec, dict]:
    spec = copy.copy(layout if isinstance(layout, LayoutSpec) else get_layout(layout))
    normalized = normalize_layout_options(options)

    accent = _ACCENT_COLORS.get(normalized['accent'])
    if accent is not None:
        spec.header_color = accent

    if normalized['font_size'] == 'large':
        for field in ('title_size', 'date_size', 'dish_size', 'category_size',
                      'expiring_size', 'meal_title_size', 'meal_hint_size',
                      'dish_name_size', 'detail_size'):
            setattr(spec, field, max(1, int(getattr(spec, field) * 1.15)))

    if normalized['density'] == 'compact':
        for field in ('row_min', 'row_max', 'detail_row_height'):
            setattr(spec, field, max(1, int(getattr(spec, field) * 0.84)))

    separator = normalized['separator']
    spec.chips = separator == 'block'
    spec.accent_rail = separator == 'rail'
    return spec, normalized


def layout_choices() -> list:
    """可选版式清单，供接口把选项直接交给前端展示。"""
    return [
        {
            'key': spec.key,
            'label': spec.label,
            'description': spec.description,
            'orientation': 'portrait' if spec.portrait else 'landscape',
        }
        for spec in LAYOUTS.values()
    ]


_font_cache: dict = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """按字号取字体。

    wqy-zenhei.ttc 里没有独立的 Bold 字面，canvas 的 'bold' 在这里无法直接对应。
    与其伪造加粗（PIL 的 stroke_width 会让小字号发糊），不如统一用常规字重 ——
    电子纸上真正决定可读性的是字号与色块对比，而不是字重。bold 参数保留是为了
    让调用处的意图与 devtool 版式一一对应，便于日后换成带 Bold 的字体。
    """
    key = (size, bold)
    cached = _font_cache.get(key)
    if cached is None:
        cached = ImageFont.truetype(config.require_font_path(), size)
        _font_cache[key] = cached
    return cached


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """文本像素宽度，对应 canvas 的 measureText().width。"""
    return int(draw.textlength(text, font=font))


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    """按真实字体宽度逐字换行，适配以中文为主的设备正文。"""
    result, current = [], ''
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            result.append(current)
            current = char
        else:
            current = candidate
    if current:
        result.append(current)
    return result or ['']


def _select_overview_dishes(dishes: list, max_rows: int) -> list:
    """版式容量不足时按分类均衡取行，并保持原始展示顺序。"""
    if len(dishes) <= max_rows:
        return dishes

    groups = {}
    for index, line in enumerate(dishes):
        category = line.split('|', 1)[0].strip() if '|' in line else ''
        groups.setdefault(category, []).append(index)

    selected = {indices[0] for indices in list(groups.values())[:max_rows]}
    depth = 1
    while len(selected) < max_rows:
        added = False
        for indices in groups.values():
            if depth < len(indices):
                selected.add(indices[depth])
                added = True
                if len(selected) == max_rows:
                    break
        if not added:
            break
        depth += 1
    return [line for index, line in enumerate(dishes) if index in selected]


def _new_canvas(spec: LayoutSpec = None) -> tuple:
    """按版式建白底画布。竖版此时是 528x792，尚未旋转。"""
    size = (spec or LAYOUTS[DEFAULT_LAYOUT]).canvas_size
    image = Image.new('RGB', size, RGB[WHITE])
    return image, ImageDraw.Draw(image)


def _finalize(image: Image.Image, spec: LayoutSpec) -> Image.Image:
    """把排版结果转成屏幕的横向像素。

    旋转是逆时针 90 度，于是内容的顶部落在设备的左侧边缘 —— 人要读的时候把设备
    顺时针转 90 度（左边缘朝上）即可正着看。选这个方向是因为设备挂在冰箱上时
    磁吸面固定，顺时针那一下更顺手。

    这里守住输出尺寸：旋转方向或画布尺寸一旦写错，错误会一路传到固件，
    表现成满屏错位条纹，而那时已经很难回溯到渲染这一步。
    """
    result = image.rotate(90, expand=True) if spec.portrait else image
    if result.size != (WIDTH, HEIGHT):
        raise ValueError(f'渲染结果必须是 {WIDTH}x{HEIGHT}，当前 {result.size}')
    return result


def _fill_rect(draw, x0: int, y0: int, x1: int, y1: int, color_index: int) -> None:
    draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=RGB[color_index])


def render_overview(
    title: str = '今日菜单',
    date_text: str = '',
    dishes: list = None,
    expiring: str = '',
    layout=None,
    meal_times: dict = None,
    details: dict = None,
    layout_options: dict = None,
) -> Image.Image:
    """渲染「今日总览」。

    @param dishes 每项可写成 "分类|菜名"（如 "午餐|红烧肉"），分类会被渲染成彩色小色块；
                  不带分隔符则整行当菜名。
    @param layout 版式键（见 LAYOUTS）或 LayoutSpec；缺省即经典横版。
    """
    dishes = dishes or []
    spec, options = _customized_spec(layout, layout_options)
    if spec.columns:
        # 分栏是另一条排版路径。在这里分派而不是让调用方自己选函数：版式对外始终是
        # 一个键，四处调用点（调度器、预览、下发、devtool）都不必知道有两条路径。
        return render_overview_columns(
            title=title, date_text=date_text, dishes=dishes, expiring=expiring,
            meal_times=meal_times, details=details, big=spec.big, options=options,
        )
    image, draw = _new_canvas(spec)
    w, h = spec.canvas_size

    # 标题基线比 header 的几何中心低 2px：汉字视觉重心偏上，放正中反而显得贴着上沿。
    header_mid = spec.header_height // 2 + 2

    if spec.header_bar:
        _fill_rect(draw, 0, 0, w, spec.header_height, spec.header_color)
        header_text_color = BLACK if spec.header_color in (YELLOW, GREEN) else WHITE
    else:
        # 无色块版式用一条红线代替整条色块：仍然把标题区和菜品区分开，
        # 但红色墨水面积从整条降到一根线 —— 电子纸刷一次大面积彩色明显更慢。
        _fill_rect(draw, spec.margin_x, spec.header_height - 6,
                   w - spec.margin_x, spec.header_height, spec.header_color)
        header_text_color = BLACK

    draw.text((spec.margin_x, header_mid), title.strip() or '今日菜单',
              font=_font(spec.title_size, True), fill=RGB[header_text_color], anchor='lm')
    if date_text:
        draw.text((w - spec.margin_x, header_mid), date_text,
                  font=_font(spec.date_size), fill=RGB[header_text_color], anchor='rm')

    # 底部临期黄条（有内容才占位，否则把空间让给菜品行）
    bottom_bar = EXPIRING_BAR_HEIGHT if expiring else 0
    area_top = spec.header_height + 20
    area_bottom = h - bottom_bar - 12

    # 大字版的容量可能小于三餐菜品总数。按餐次均衡取行，避免顺序截断后晚餐完全消失。
    available_height = area_bottom - area_top
    max_rows = max(1, int(available_height / spec.row_min + 0.5))
    visible_dishes = _select_overview_dishes(dishes, max_rows)

    # 行高自适应：菜品少时不要拉得太散，多时也不要挤到重叠。与 devtool 同一算式。
    if visible_dishes:
        row_height = min(spec.row_max, max(spec.row_min, available_height / len(visible_dishes)))
    else:
        row_height = spec.row_max

    y = area_top + row_height / 2
    for line in visible_dishes:
        category, name = '', line.strip()
        if '|' in line:
            category, name = (part.strip() for part in line.split('|', 1))

        x = spec.margin_x
        if spec.accent_rail:
            rail_color = CATEGORY_COLORS.get(category, RED)
            rail_half = max(12, spec.dish_size // 2)
            _fill_rect(draw, x, int(y - rail_half), x + 8, int(y + rail_half), rail_color)
            x += 20
        if category and spec.chips:
            category_font = _font(spec.category_size, True)
            block_width = _text_width(draw, category, category_font) + 24
            block_color = CATEGORY_COLORS.get(category, BLUE)
            chip_half = spec.category_size - 2
            _fill_rect(draw, x, int(y - chip_half), x + block_width, int(y + chip_half),
                       block_color)
            # 黄底配黑字：黄色亮度高，白字在电子纸上几乎看不见
            label_color = BLACK if block_color == YELLOW else WHITE
            draw.text((x + 12, y), category, font=category_font,
                      fill=RGB[label_color], anchor='lm')
            x += block_width + 16
            text = name
        elif not spec.chips:
            # 不画色块时分类靠文字承载，不能把它丢掉：只剩菜名的话「早餐/午餐/晚餐」
            # 这层归属就消失了，一屏菜名连在一起没法快速找到当前这一餐。
            text = f'· {category} · {name}' if category else f'· {name}'
        else:
            text = name

        draw.text((x, y), text, font=_font(spec.dish_size), fill=RGB[BLACK], anchor='lm')
        y += row_height

    if expiring:
        _fill_rect(draw, 0, h - bottom_bar, w, h, YELLOW)
        draw.text((spec.margin_x, h - bottom_bar / 2), '临期提醒：' + expiring,
                  font=_font(spec.expiring_size, True), fill=RGB[BLACK], anchor='lm')

    return _finalize(image, spec)



# ─── 三餐分栏 ───

# 三餐的身份色。让六色去承担**结构**而不是装饰：一眼分出三餐靠的是栏头实心色块，
# 而不是行内那个小色标 —— 色标只有二十来像素宽，站远一点就消失了。
MEAL_COLUMN_COLORS = (('早餐', YELLOW), ('午餐', GREEN), ('晚餐', BLUE))

# 红色**只**留给警示（临期 / 缺料），且面积压到一个圆点：满屏都是红的时候红色就失去
# 警示含义，而电子纸刷大面积彩色也明显更慢。
ALERT_DOT_RADIUS = 9


def _column_dishes(dishes: list) -> tuple:
    """按餐次归组，返回 (分组, 无归属的菜)。不带分类前缀的行不丢，单独交回调用方。"""
    groups = {label: [] for label, _ in MEAL_COLUMN_COLORS}
    extra = []
    for line in dishes or []:
        if '|' in line:
            category, name = (part.strip() for part in line.split('|', 1))
        else:
            category, name = '', line.strip()
        if not name:
            continue
        if category in groups:
            groups[category].append(name)
        else:
            extra.append(name)
    return groups, extra


def ingredient_summaries(plan: dict, limit: int = 4) -> dict:
    """从 plan 里取「菜名 -> 用料摘要」，供分栏版填在菜名下面。

    只取前 [limit] 样并去掉用量：站在冰箱前要判断的是「东西齐不齐」，不是「放几克」，
    而一整行带用量的用料会挤掉下一道菜。基础调料不特意剔除 —— 那是配菜阶段的口径
    （见 agent.PANTRY_*），展示阶段再抄一份判定必然与它漂移。
    """
    out = {}
    for dish in (plan or {}).get('dishes', []):
        name = (dish.get('name') or '').strip()
        if not name:
            continue
        parts = []
        for item in dish.get('ingredients') or []:
            label = (item.get('name') or '').strip()
            if label and label not in parts:
                parts.append(label)
            if len(parts) >= limit:
                break
        if parts:
            out[name] = ' · '.join(parts)
    return out


def _column_block_height(draw, names: list, details: dict,
                        dish_size: int, detail_size: int, text_width: int) -> int:
    """按给定字号量出一栏内容的总高度。给 [render_overview_columns] 选字号用。

    必须真的换行一遍再量：中文菜名在窄栏里放大一档就可能从一行变两行，
    只按字数估算会挑出一个「算得下、画出来溢出」的字号。
    """
    dish_font = _font(dish_size)
    detail_font = _font(detail_size)
    dish_line = int(dish_size * 1.28)
    detail_line = int(detail_size * 1.30)
    total = 0
    for name in names:
        total += len(_wrap_text(draw, name, dish_font, text_width)) * dish_line
        summary = details.get(name) or ''
        if summary:
            total += len(_wrap_text(draw, summary, detail_font, text_width)[:2]) * detail_line
        total += 6
    return total


def _columns_fit(draw, columns: list, details: dict, dish_size: int,
                 detail_size: int, text_width: int, body_height: int,
                 base_wraps: dict = None) -> bool:
    """三栏里最高的那栏能否放进 body_height，且放大后菜名没有多折行。

    只看高度会挑出一个「算得下但把菜名折成两行」的字号，而「燕麦牛/奶粥」这种断词
    比留白更难读 —— 留白只是没用满屏幕，断词是每次看都要在脑子里接一次。
    """
    for _label, _color, names in columns:
        if _column_block_height(draw, names, details, dish_size, detail_size,
                                text_width) > body_height:
            return False
        if base_wraps is None:
            continue
        font = _font(dish_size)
        for name in names:
            if len(_wrap_text(draw, name, font, text_width)) > base_wraps.get(name, 1):
                return False
    return True


def render_overview_columns(
    title: str = '今日菜单',
    date_text: str = '',
    dishes: list = None,
    expiring: str = '',
    meal_times: dict = None,
    details: dict = None,
    big: bool = False,
    options: dict = None,
) -> Image.Image:
    """渲染「今日总览 · 三餐分栏」。

    与 [render_overview] 的单列平铺是**两条不同的排版路径**，不是同一套参数的两组取值：
    分栏要按列分配横向空间、每列独立换行，行高算式与单列版没有共用部分。

    分栏的收益：三餐是这块屏幕上唯一真正的结构，同样高度能放下三餐各 3–4 道
    （单列版放 5 道就满了），字号还能大一档。

    @param meal_times 餐次 -> 时间文本（如 {'早餐': '07:30'}），栏头会带上它。
    @param details    菜名 -> 用料摘要（见 [ingredient_summaries]）。给了就排在菜名下面。
    @param big        大字模式：字号整体放大，相应少显示几道。
    """
    dishes = dishes or []
    meal_times = meal_times or {}
    details = details or {}
    options = normalize_layout_options(options)
    image = Image.new('RGB', (WIDTH, HEIGHT), RGB[WHITE])
    draw = ImageDraw.Draw(image)

    font_scale = 1.15 if options['font_size'] == 'large' else 1.0
    density_scale = 0.88 if options['density'] == 'compact' else 1.0
    title_size = int((44 if not big else 52) * font_scale)
    date_size = int((25 if not big else 29) * font_scale)
    head_size = int((27 if not big else 33) * font_scale)
    time_size = int((21 if not big else 25) * font_scale)
    alert_size = int((25 if not big else 29) * font_scale)
    base_dish_size = int((30 if not big else 38) * font_scale * density_scale)

    margin = 24
    # 标题区不铺色块：黑字白底最清楚，也把彩色额度留给下面真正需要区分的三栏。
    draw.text((margin, 40), title.strip() or '今日菜单',
              font=_font(title_size, True), fill=RGB[BLACK], anchor='lm')
    if date_text:
        draw.text((WIDTH - margin, 42), date_text,
                  font=_font(date_size), fill=RGB[BLACK], anchor='rm')
    _fill_rect(draw, margin, 70, WIDTH - margin, 73, BLACK)

    groups, extra = _column_dishes(dishes)
    accent = _ACCENT_COLORS.get(options['accent'])
    columns = [(label, accent if accent is not None else color, groups.get(label, []))
               for label, color in MEAL_COLUMN_COLORS]

    alert_height = 58 if expiring else 0
    area_top = 86
    area_bottom = HEIGHT - alert_height - 14

    gap = 8 if options['density'] == 'compact' else 12
    col_width = (WIDTH - margin * 2 - gap * (len(columns) - 1)) // len(columns)
    head_height = head_size + 16
    rail_width = 5
    text_pad = rail_width + 10
    max_text_width = col_width - text_pad - 6

    # 字号按可用高度自适应。三餐分栏后每栏通常只有 2–4 道，固定字号会空掉半屏；
    # 而这块屏幕是贴在冰箱上隔几米看的，空白不如字大。取「最高那栏刚好放得下」的
    # 字号，所有栏用同一个 —— 各栏字号不同会读成「这一餐更重要」。
    body_top = area_top + head_height + 14
    body_height = area_bottom - body_top
    # 基准字号下每道菜各占几行。放大只在不增加折行的前提下进行。
    base_font = _font(base_dish_size)
    base_wraps = {name: len(_wrap_text(draw, name, base_font, max_text_width))
                  for _label, _color, names in columns for name in names}
    dish_size, detail_size = base_dish_size, max(19, int(base_dish_size * 0.68))
    for scale in (1.9, 1.75, 1.6, 1.45, 1.3, 1.15, 1.0):
        trial_dish = int(base_dish_size * scale)
        trial_detail = max(18, int(trial_dish * 0.62))
        if _columns_fit(draw, columns, details, trial_dish, trial_detail,
                        max_text_width, body_height, base_wraps):
            dish_size, detail_size = trial_dish, trial_detail
            break

    # 字号顶到「不折行的上限」后可能仍有余高（每餐只一道菜时尤其明显）。把三栏当成
    # 一整块垂直居中：同样的留白，居中读作刻意的版式，顶部对齐读作内容没加载完。
    # 三栏共用一个偏移而不是各自居中 —— 各自居中会让三栏首行错落成阶梯。
    tallest = max((_column_block_height(draw, names, details, dish_size, detail_size,
                                        max_text_width)
                   for _label, _color, names in columns), default=0)
    body_top += max(0, (body_height - tallest) // 2)

    for index, (label, color, names) in enumerate(columns):
        x0 = margin + index * (col_width + gap)
        x1 = x0 + col_width

        separator = options['separator']
        if separator == 'block':
            _fill_rect(draw, x0, area_top, x1, area_top + head_height, color)
        elif separator == 'rail':
            _fill_rect(draw, x0, area_top + head_height - 4, x1,
                       area_top + head_height, color)
        head_fg = (BLACK if color in (YELLOW, GREEN) else WHITE) \
            if separator == 'block' else BLACK
        hy = area_top + head_height // 2 + 1
        draw.text((x0 + 10, hy), label, font=_font(head_size, True),
                  fill=RGB[head_fg], anchor='lm')
        time_text = str(meal_times.get(label) or '').strip()
        if time_text:
            draw.text((x1 - 10, hy), time_text, font=_font(time_size),
                      fill=RGB[head_fg], anchor='rm')

        dish_font = _font(dish_size)
        detail_font = _font(detail_size)
        dish_line = int(dish_size * 1.28)
        detail_line = int(detail_size * 1.30)

        y = body_top
        content_start = y
        for name in names:
            block = _wrap_text(draw, name, dish_font, max_text_width)
            summary = details.get(name) or ''
            summary_lines = (_wrap_text(draw, summary, detail_font, max_text_width)[:2]
                             if summary else [])
            need = len(block) * dish_line + len(summary_lines) * detail_line + 6
            if y + need > area_bottom:
                break
            for piece in block:
                draw.text((x0 + text_pad, y), piece, font=dish_font,
                          fill=RGB[BLACK], anchor='lm')
                y += dish_line
            for piece in summary_lines:
                draw.text((x0 + text_pad, y), piece, font=detail_font,
                          fill=RGB[BLACK], anchor='lm')
                y += detail_line
            y += 6

        if not names:
            draw.text((x0 + text_pad, y), '—', font=dish_font,
                      fill=RGB[BLACK], anchor='lm')
            y += dish_line

        # 色带贴着内容画，上端跟随首行而不是钉在栏头下沿：菜少时内容被居中，钉在栏头
        # 会拖出一条穿过空白的长腿，读成一个空心的「L」。下端也只到内容底部，不贯通到
        # 画布底边 —— 贯通会让它读成一根装饰竖线，而它要表达的是「这一段属于这一餐」。
        rail_top = max(area_top + head_height, content_start - dish_line // 2)
        rail_bottom = min(area_bottom, max(y - 6, content_start + dish_line))
        if separator != 'none':
            _fill_rect(draw, x0, rail_top, x0 + rail_width, rail_bottom, color)

    if extra:
        logger.info('分栏版有 %d 道菜没有餐次归属，未排入三栏：%s',
                    len(extra), '、'.join(extra))

    if expiring:
        cy = HEIGHT - alert_height // 2
        draw.ellipse([margin, cy - ALERT_DOT_RADIUS,
                      margin + ALERT_DOT_RADIUS * 2, cy + ALERT_DOT_RADIUS],
                     fill=RGB[RED])
        draw.text((margin + ALERT_DOT_RADIUS * 2 + 12, cy), expiring,
                  font=_font(alert_size, True), fill=RGB[BLACK], anchor='lm')

    if image.size != (WIDTH, HEIGHT):
        raise ValueError(f'渲染结果必须是 {WIDTH}x{HEIGHT}，当前 {image.size}')
    return image


def render_meal_detail(meal_label: str, header_color: int, lines: list,
                       layout=None, layout_options: dict = None) -> Image.Image:
    """渲染某一餐的「详细做法」页。

    第一行当菜名（大字号），其余逐行排；以「食材」开头的行加黄色底衬，便于备餐时
    一眼定位。这个约定与 devtool 一致。

    @param layout 版式键（见 LAYOUTS）或 LayoutSpec；缺省即经典横版。
    """
    lines = [line.strip() for line in (lines or []) if line.strip()]
    dish_name = lines[0] if lines else meal_label
    body = lines[1:]

    spec, options = _customized_spec(layout, layout_options)
    header_color = _ACCENT_COLORS.get(options['accent'], header_color)
    image, draw = _new_canvas(spec)
    w, h = spec.canvas_size
    header_mid = spec.header_height // 2 + 2

    if spec.header_bar:
        _fill_rect(draw, 0, 0, w, spec.header_height, header_color)
        header_text_color = BLACK if header_color == YELLOW else WHITE
    else:
        # 详情页即使在无色块版式下也保留餐段色，只是收成一根线：这是全页唯一的
        # 彩色元素，人一眼就能确认「现在看的是哪一餐」，而不必去读标题文字。
        _fill_rect(draw, spec.margin_x, spec.header_height - 6,
                   w - spec.margin_x, spec.header_height, header_color)
        header_text_color = BLACK

    draw.text((spec.margin_x, header_mid), meal_label,
              font=_font(spec.meal_title_size, True),
              fill=RGB[header_text_color], anchor='lm')
    draw.text((w - spec.margin_x, header_mid), '详细做法',
              font=_font(spec.meal_hint_size),
              fill=RGB[header_text_color], anchor='rm')

    # 菜名与正文首行的位置跟着字号走：大字版式下写死像素间距会让菜名贴住 header。
    name_y = spec.header_height + spec.dish_name_size + 8
    draw.text((spec.margin_x, name_y), dish_name, font=_font(spec.dish_name_size, True),
              fill=RGB[BLACK], anchor='lm')

    y = name_y + spec.dish_name_size + 25
    truncated = False
    for line in body:
        is_ingredient = line.startswith('食材')
        font = _font(spec.expiring_size, True) if is_ingredient else _font(spec.detail_size)
        for wrapped in _wrap_text(draw, line, font, w - spec.margin_x * 2):
            if y > h - spec.detail_row_height // 2 - 12:
                truncated = True
                break
            if is_ingredient:
                row_half = spec.detail_row_height // 2
                if options['separator'] == 'block' and spec.header_bar:
                    _fill_rect(draw, 0, y - row_half, w, y + row_half,
                               spec.ingredient_color)
                elif options['separator'] != 'none':
                    _fill_rect(draw, spec.margin_x, y + row_half - 3,
                               w - spec.margin_x, y + row_half, spec.ingredient_color)
                text_color = (BLACK if spec.ingredient_color in (YELLOW, GREEN) else WHITE) \
                    if options['separator'] == 'block' else BLACK
            else:
                text_color = BLACK
            draw.text((spec.margin_x, y), wrapped, font=font,
                      fill=RGB[text_color], anchor='lm')
            y += spec.detail_row_height
        if truncated:
            logger.warning('详情行数超出画布，已截断')
            break

    return _finalize(image, spec)


def image_to_film(image: Image.Image) -> bytes:
    """PIL 图像 -> `.film` 字节。

    尺寸不符时拒绝而不是自动缩放：缩放会插值出调色板外的颜色，让排版效果偏离预期，
    而调用方传错尺寸本身就是 bug，静默纠正只会掩盖它。
    """
    if image.size != (WIDTH, HEIGHT):
        raise ValueError(f'画布尺寸必须是 {WIDTH}x{HEIGHT}，当前 {image.size}')
    return fe.encode_rgb_image(image.convert('RGB').tobytes())


def render_overview_film(layout=None, layout_options=None, **kwargs) -> bytes:
    """渲染总览并直接返回 `.film` 字节。不传 layout 即经典横版。"""
    return image_to_film(render_overview(
        layout=layout, layout_options=layout_options, **kwargs))


def render_meal_detail_film(meal: str, lines: list, layout=None,
                            layout_options=None) -> bytes:
    """按餐段（breakfast/lunch/dinner）渲染详情并返回 `.film` 字节。"""
    if meal not in MEAL_STYLES:
        raise ValueError(f'未知餐段 {meal}，可选 {sorted(MEAL_STYLES)}')
    label, color, _filename = MEAL_STYLES[meal]
    return image_to_film(render_meal_detail(
        label, color, lines, layout=layout, layout_options=layout_options))


def dishes_from_plan(plan: dict) -> list:
    """把 agent.py 产出的 plan（plan_a / plan_b）转成总览用的菜品行。

    plan 结构见 agent.py::build_enhanced_recipe_prompt 里约定的 JSON：
    dishes[{name, ingredients[{name, amount}], cooking_time, difficulty, ...}]
    """
    lines = []
    for dish in (plan or {}).get('dishes', []):
        name = (dish.get('name') or '').strip()
        if not name:
            continue
        cooking_time = (dish.get('cooking_time') or '').strip()
        lines.append(f"{name}（{cooking_time}）" if cooking_time else name)
    return lines


def detail_lines_from_dish(dish: dict) -> list:
    """把单道菜转成详情页的食材、营养、时间和编号步骤。"""
    lines = [(dish.get('name') or '').strip() or '菜品']

    ingredients = dish.get('ingredients') or []
    if not isinstance(ingredients, list):
        ingredients = []
    if ingredients:
        parts = []
        for item in ingredients:
            if not isinstance(item, dict):
                continue
            item_name = (item.get('name') or '').strip()
            amount = (item.get('amount') or '').strip()
            if item_name:
                parts.append(f'{item_name}{amount}' if amount else item_name)
        if parts:
            lines.append('食材：' + '、'.join(parts))

    meta = []
    if dish.get('cooking_time'):
        meta.append(f"用时 {dish['cooking_time']}")
    if dish.get('difficulty'):
        meta.append(f"难度 {dish['difficulty']}")
    if meta:
        lines.append(' · '.join(meta))

    nutrition = str(dish.get('nutrition_highlight') or '').strip()
    if nutrition:
        lines.append(f'营养：{nutrition}')

    steps = dish.get('steps') or []
    if isinstance(steps, str):
        steps = [steps]
    elif not isinstance(steps, list):
        steps = []
    for index, step in enumerate(steps[:5], start=1):
        step = str(step or '').strip()
        if step:
            lines.append(f'{index}. {step}')

    if dish.get('skip_ingredients'):
        skip_ingredients = dish['skip_ingredients']
        if isinstance(skip_ingredients, str):
            skip_ingredients = [skip_ingredients]
        elif not isinstance(skip_ingredients, list):
            skip_ingredients = []
        skipped = '、'.join(str(x) for x in skip_ingredients if x)
        if skipped:
            lines.append(f'去掉：{skipped}')

    return lines
