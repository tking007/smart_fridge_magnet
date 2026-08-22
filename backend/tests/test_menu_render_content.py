from pathlib import Path
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import menu_render


def test_detail_lines_include_steps_and_nutrition():
    lines = menu_render.detail_lines_from_dish({
        'name': '番茄炒蛋',
        'ingredients': [
            {'name': '番茄', 'amount': '2个'},
            {'name': '鸡蛋', 'amount': '3个'},
        ],
        'cooking_time': '15分钟',
        'difficulty': '简单',
        'nutrition_highlight': '优质蛋白和维生素C',
        'steps': ['番茄切块，鸡蛋打散', '先炒鸡蛋，再加入番茄调味'],
    })

    assert lines[0] == '番茄炒蛋'
    assert any(line.startswith('食材：') for line in lines)
    assert '营养：优质蛋白和维生素C' in lines
    assert '1. 番茄切块，鸡蛋打散' in lines
    assert '2. 先炒鸡蛋，再加入番茄调味' in lines


def test_detail_text_wraps_to_available_width():
    draw = ImageDraw.Draw(Image.new('RGB', (300, 100), 'white'))
    font = menu_render._font(24)
    text = '食材：番茄2个、鸡蛋3个、香葱1根、生姜2片'
    wrapped = menu_render._wrap_text(draw, text, font, 160)

    assert len(wrapped) > 1
    assert ''.join(wrapped) == text
    assert all(menu_render._text_width(draw, line, font) <= 160 for line in wrapped)


def test_overview_capacity_keeps_every_meal_visible():
    dishes = [
        *(f'早餐|早餐菜{i}' for i in range(3)),
        *(f'午餐|午餐菜{i}' for i in range(3)),
        *(f'晚餐|晚餐菜{i}' for i in range(3)),
    ]

    selected = menu_render._select_overview_dishes(dishes, 5)

    assert selected == [
        '早餐|早餐菜0', '早餐|早餐菜1',
        '午餐|午餐菜0', '午餐|午餐菜1',
        '晚餐|晚餐菜0',
    ]


def test_detail_lines_tolerate_model_shape_drift():
    lines = menu_render.detail_lines_from_dish({
        'name': '油豆腐烧番茄',
        'ingredients': '不是数组',
        'steps': '小火焖至入味',
        'skip_ingredients': '辣椒',
    })

    assert '1. 小火焖至入味' in lines
    assert '去掉：辣椒' in lines
    assert not any(line.startswith('食材：') for line in lines)



def test_layout_aliases_map_old_keys_to_merged_layouts():
    """旧版式键必须仍能解析。

    库里存着用户在旧版本选过的偏好；解析不了就静默回落默认版式，用户看到的是
    「我明明选过大字版」的观感退化，而这种退化从日志里看不出来。
    """
    assert menu_render.get_layout('classic').key == 'columns'
    assert menu_render.get_layout('bold').key == 'list_big'
    assert menu_render.get_layout('minimal').key == 'list'
    assert menu_render.get_layout('zzz').key == menu_render.DEFAULT_LAYOUT
    # 别名不出现在给用户选的清单里，只用于解析。
    assert {c['key'] for c in menu_render.layout_choices()} == set(menu_render.LAYOUTS)


def test_layout_options_are_validated_and_change_rendering():
    defaults = menu_render.normalize_layout_options()
    assert defaults == menu_render.LAYOUT_OPTION_DEFAULTS
    with pytest.raises(ValueError):
        menu_render.normalize_layout_options({'separator': 'dots'}, strict=True)

    kwargs = {
        'title': '今日菜单',
        'dishes': ['早餐|燕麦粥', '午餐|番茄炒蛋', '晚餐|香菇鸡肉'],
        'layout': 'columns',
    }
    baseline = menu_render.render_overview(**kwargs)
    customized = menu_render.render_overview(
        **kwargs,
        layout_options={
            'accent': 'green', 'font_size': 'large',
            'density': 'compact', 'separator': 'rail',
        },
    )

    assert customized.size == (menu_render.WIDTH, menu_render.HEIGHT)
    assert customized.tobytes() != baseline.tobytes()


def test_columns_layout_dispatches_and_keeps_device_size():
    """分栏版式经 render_overview 分派，输出仍是设备尺寸。"""
    image = menu_render.render_overview(
        dishes=['早餐|燕麦粥', '午餐|番茄炒蛋', '晚餐|香菇焖饭'],
        expiring='牛奶 明天到期',
        layout='columns',
        meal_times={'早餐': '07:30'},
        details={'番茄炒蛋': '番茄 · 鸡蛋'},
    )

    assert image.size == (menu_render.WIDTH, menu_render.HEIGHT)


def test_columns_autofit_never_wraps_dish_names_that_fit_at_base_size():
    """自适应放大不得把原本一行的菜名折成两行。

    「燕麦牛/奶粥」这种断词每次看都要在脑子里接一次，比没用满屏幕更糟。
    """
    image = Image.new('RGB', (menu_render.WIDTH, menu_render.HEIGHT), 'white')
    draw = ImageDraw.Draw(image)
    columns = [('早餐', menu_render.YELLOW, ['燕麦牛奶粥'])]
    text_width = 200

    base_wraps = {'燕麦牛奶粥': len(menu_render._wrap_text(
        draw, '燕麦牛奶粥', menu_render._font(30), text_width))}
    # 给一个高度充裕但宽度不够的场景：只按高度判断会选中会折行的字号。
    assert not menu_render._columns_fit(
        draw, columns, {}, 60, 38, text_width, 9999, base_wraps)
    assert menu_render._columns_fit(
        draw, columns, {}, 30, 20, text_width, 9999, base_wraps)


def test_ingredient_summaries_drop_amounts_and_cap_count():
    summaries = menu_render.ingredient_summaries({
        'dishes': [{
            'name': '乱炖',
            'ingredients': [
                {'name': '牛腩', 'amount': '300g'},
                {'name': '土豆', 'amount': '2个'},
                {'name': '胡萝卜', 'amount': '1根'},
                {'name': '洋葱', 'amount': '半个'},
                {'name': '番茄', 'amount': '1个'},
            ],
        }],
    }, limit=4)

    assert summaries['乱炖'] == '牛腩 · 土豆 · 胡萝卜 · 洋葱'
    assert '300g' not in summaries['乱炖']
