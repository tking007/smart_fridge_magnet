"""餐次的唯一口径：把各种写法归一成 breakfast/lunch/dinner，并给出中文标签。

同一顿饭在本项目里有三套写法，各有必须存在的理由：数据库 meal_schedule.meal_type 与
agent.py 写英文键；渲染版式 menu_render.MEAL_STYLES 只认英文键（顶栏颜色、目标文件名都
挂在它上面）；送进模型 prompt 与 App 上显示的必须是中文。

归一逻辑有两份就必然漂移，而漂移的表现极隐蔽（比较不成立时去重失效 → 同一顿饭被推荐
好几次；或设备上的餐段颜色跟标题对不上），从任何一处代码看都像是对的。
所以这里是唯一定义，调度器与接口层都从这里取。

本模块刻意不在顶部 import menu_render：那会连带拉起 PIL 与字体栈，而餐次归一处在纯数据
路径上（调度器判窗口、接口校验参数、CLI 脚本），没有渲染环境时也必须能用。
中文标签依赖 menu_render.MEAL_STYLES，故只在 meal_label() 内部延迟导入。
"""
from typing import List, Optional

# 三餐的规范顺序。对外输出（时间表、总览汇总）一律按这个顺序，而不是按数据库主键 ——
# 主键顺序取决于插入历史，与用户看到的「早中晚」自然序不一致时会被当成 bug。
MEAL_KEYS = ('breakfast', 'lunch', 'dinner')

# 各种写法 -> 规范键。键一律用小写，查表前会把入参 lower()。
MEAL_ALIASES = {
    'breakfast': 'breakfast', '早餐': 'breakfast', '早饭': 'breakfast',
    'lunch': 'lunch', '午餐': 'lunch', '午饭': 'lunch', '中餐': 'lunch',
    'dinner': 'dinner', '晚餐': 'dinner', '晚饭': 'dinner',
}


def meal_key(raw) -> Optional[str]:
    """把任意写法的餐次归一成 breakfast/lunch/dinner。

    认不出来的返回 None（例如「下午茶」「夜宵」）：这些餐次没有对应的画面版式，
    不该硬塞进三色顶栏里。调用方据此决定跳过（调度器）还是报 400（接口层）。
    """
    if not raw:
        return None
    return MEAL_ALIASES.get(str(raw).strip().lower())


def meal_label(key: str) -> str:
    """规范键 -> 中文标签（早餐 / 午餐 / 晚餐）。

    标签取自 menu_render.MEAL_STYLES，而不是在这里另抄一份：那张表同时定义了餐段的
    顶栏颜色与目标文件名，标签跟它放在一起才不会出现「标题写午餐、顶栏是晚餐蓝」。

    延迟导入是为了让纯数据路径不依赖 PIL：见模块 docstring。
    认不出的键原样返回，方便日志里保留现场值。
    """
    from .menu_render import MEAL_STYLES
    style = MEAL_STYLES.get(key)
    return style[0] if style else key


def meal_keys_ordered() -> List[str]:
    """三餐规范键的列表副本，供调用方遍历时不必担心改到常量。"""
    return list(MEAL_KEYS)
