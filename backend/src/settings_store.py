"""可在 App 里改的运行时设置 — **DB 优先、env 兜底**。

不放进 config.py：那个模块只依赖标准库与环境变量，因为它是启动期 fail-closed 校验
（`validate_runtime()`）的基础，必须在数据库、字体、渲染栈都还不确定可用时就能跑完。
让它读数据库会带来循环导入（`database` 在模块级 `from . import config`），也会让
「配置是否合法」反过来依赖被校验的东西 —— 数据库路径配错时报的就成了 sqlite
OperationalError，而不是那句写清楚「请配置 SFM_API_TOKEN」的 RuntimeError。

可配（App 或运维能改）：`daily_publish_time`、`auto_publish`、`lead_minutes`。

不可配（维持读 env）：`auto_publish_devices` 设备白名单属于运维范围 —— 一台设备的
device_id 出现在白名单里意味着「这块屏归这套后端管」，App 用户改它会误伤同一后端下的
别的设备，而表现是那台设备再也不换图。

坏值一律回落、绝不抛异常：读取路径上有一半调用方是定时任务，没有人在等答复。一个坏配置
值把整条自动下发拖停的代价远大于按默认值继续跑，所以只 `logger.warning` 并回落 env。
"""
import sqlite3
from typing import Optional

from . import config
from .logger import get_logger

logger = get_logger('settings_store')

# 设置键。用完整语义命名而不是 'lead' 之类缩写：这张表会被人直接 sqlite3 查，
# 键名就是它唯一的文档。
KEY_LEAD_MINUTES = 'auto_publish_lead_minutes'
KEY_AUTO_PUBLISH = 'auto_publish_enabled'
KEY_DAILY_PUBLISH_TIME = 'daily_publish_time'

# 调度器检查每日发布窗口的节奏（分钟）。`scheduler._add_display_publish_task()` 的
# IntervalTrigger 直接引用本常量；发布截止余量和 `lead_minutes` 下界也由它推导。
#
# ── 为什么这个常量落在 settings_store，而不是 scheduler ──
#
# 依赖方向只有这一个不成环：`scheduler` 模块级已经 `from . import settings_store`
# （并顺带拉起 agent / database / device_display / menu_render）。反过来让
# settings_store 去 import scheduler 会直接绕成环；即便用函数内延迟导入绕开报错，
# 也等于把那条重依赖链塞进「读一个设置值」的路径上 —— app.py 为了回一个
# GET /api/meal-schedule 就得先把调度器那套东西 import 完。
# settings_store 只依赖 config + logger，是这条链的下游，常量放这里谁都能引用。
DISPLAY_PUBLISH_TICK_MINUTES = 5

# 调度任务最多晚一整个 tick 才执行，三餐模型生成也需要时间；发布截止点比设备唤醒再早
# 两个 tick，给调度抖动和一次生成重试留出最小余量。
PUBLISH_READY_MARGIN_MINUTES = DISPLAY_PUBLISH_TICK_MINUTES * 2

# `lead_minutes` 的合法域。客户端按响应中的 min/max 渲染控件。
LEAD_MINUTES_MIN = DISPLAY_PUBLISH_TICK_MINUTES
LEAD_MINUTES_MAX = 240

# 布尔值的落库形式与接受形式。存 '1'/'0'，读时也认 true/false 这类写法 ——
# 库里的值可能是人用 sqlite3 手改的，只认 '1' 会把 'true' 当成坏值白白回落。
_TRUE_WORDS = ('1', 'true', 'yes', 'on')
_FALSE_WORDS = ('0', 'false', 'no', 'off')


def _get_connection():
    """延迟导入 database，避免与它的模块级 `from . import config` 绕成环。"""
    from .database import get_connection
    return get_connection()


def init_settings_table() -> None:
    """建 `app_settings` 表。

    ── 为什么在本模块末尾自建，而不是加到 `database.init_db()` ──

    `init_db()` 是**显式调用**的：`app.py` 在 import 时调一次，脚本与测试各自再调。
    也就是说凡是不经过 app 的入口（`scheduler.py` 被 CLI 直接拉起、
    `scripts/` 下的工具、只 import 某个模块的自检）都可能在表还不存在时就读设置。
    那条路上的失败是静默的：读不到设置就回落 env，于是「App 里显示 45 分钟、
    调度器仍按 30 分钟发图」，两边都不报错。

    `device_display.py` 末尾的 `init_device_tables()` 已经是这个做法，理由相同 ——
    只要 import 到了这个模块，它依赖的表就一定在。这里沿用，不再引入第二种约定。
    """
    conn = _get_connection()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    finally:
        conn.close()


def _read_raw(key: str) -> Optional[str]:
    """读原始字符串；没有该键、或库不可用时返回 None（不抛异常）。"""
    try:
        conn = _get_connection()
    except sqlite3.Error as exc:
        logger.warning('设置读取失败（无法连接数据库），回落环境变量: key=%s %s', key, exc)
        return None

    try:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?', (key,)
        ).fetchone()
    except sqlite3.Error as exc:
        # 表不存在（库文件被删后重建）、库被锁死等等。这条路上没有调用方在等答复，
        # 回落 env 让自动下发继续跑，比抛异常把整条链路停掉划算。
        logger.warning('设置读取失败，回落环境变量: key=%s %s', key, exc)
        return None
    finally:
        conn.close()

    if row is None:
        return None
    return row[0]


def get_int(key: str, fallback: int) -> int:
    """读整数设置；无值或值损坏时回落 fallback（损坏会 warning）。"""
    raw = _read_raw(key)
    if raw is None:
        return fallback
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning('设置 %s 的值不是整数（%r），回落 %s', key, raw, fallback)
        return fallback


def get_bool(key: str, fallback: bool) -> bool:
    """读布尔设置；无值或值损坏时回落 fallback（损坏会 warning）。"""
    raw = _read_raw(key)
    if raw is None:
        return fallback
    lowered = str(raw).strip().lower()
    if lowered in _TRUE_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False
    logger.warning('设置 %s 的值不是布尔（%r），回落 %s', key, raw, fallback)
    return fallback


def set_value(key: str, value) -> None:
    """写入设置。布尔存成 '1'/'0'，其余 `str()` 后落库。

    先 `init_settings_table()` 再写：写入是低频动作（用户在 App 里改设置），
    多一条 `CREATE TABLE IF NOT EXISTS` 的代价可以忽略，换来的是「库文件被换掉后
    第一次写不会失败」。
    """
    if isinstance(value, bool):
        stored = '1' if value else '0'
    else:
        stored = str(value)

    init_settings_table()
    conn = _get_connection()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO app_settings (key, value, updated_at)'
            ' VALUES (?, ?, CURRENT_TIMESTAMP)',
            (key, stored)
        )
        conn.commit()
    finally:
        conn.close()
    logger.info('设置已更新: %s = %s', key, stored)


def lead_minutes_in_range(value: int) -> bool:
    """`lead_minutes` 是否在合法域内。接口层与本模块共用同一份判断，避免两处漂移。"""
    return LEAD_MINUTES_MIN <= value <= LEAD_MINUTES_MAX


def _env_lead_minutes() -> int:
    """`lead_minutes` 的 env 兜底值，并保证符合对外声明的范围。"""
    value = config.auto_publish_lead_minutes()
    if value < LEAD_MINUTES_MIN:
        logger.warning(
            'SFM_AUTO_PUBLISH_LEAD_MIN=%s 小于最小值 %s，本次按 %s 分钟使用',
            value, DISPLAY_PUBLISH_TICK_MINUTES, LEAD_MINUTES_MIN)
        return LEAD_MINUTES_MIN
    return value


def publish_lead_minutes() -> int:
    """读取设备饭点前取图的提前量。"""
    fallback = _env_lead_minutes()

    raw = _read_raw(KEY_LEAD_MINUTES)
    if raw is None:
        return fallback

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning('设置 %s 的值不是整数（%r），回落环境变量 %s 分钟',
                       KEY_LEAD_MINUTES, raw, fallback)
        return fallback

    if not lead_minutes_in_range(value):
        logger.warning(
            '设置 %s 的值 %s 不在 [%s, %s] 内，本次回落环境变量 '
            'SFM_AUTO_PUBLISH_LEAD_MIN=%s 分钟',
            KEY_LEAD_MINUTES, value, LEAD_MINUTES_MIN, LEAD_MINUTES_MAX,
            fallback)
        return fallback

    return value


def auto_publish_enabled() -> bool:
    """是否自动发布 —— DB 有值用它，否则回落 `SFM_AUTO_PUBLISH`。"""
    return get_bool(KEY_AUTO_PUBLISH, config.auto_publish_enabled())


def _configured_daily_publish_time() -> str:
    """读取旧客户端或运维配置的每日发布时间。"""
    raw = (_read_raw(KEY_DAILY_PUBLISH_TIME) or config.daily_publish_time()).strip()
    try:
        hour, minute = map(int, raw.split(':'))
    except (TypeError, ValueError):
        logger.warning('设置 %s 的值不是 HH:MM（%r），回落 06:30', KEY_DAILY_PUBLISH_TIME, raw)
        return '06:30'
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logger.warning('设置 %s 的时间越界（%r），回落 06:30', KEY_DAILY_PUBLISH_TIME, raw)
        return '06:30'
    return f'{hour:02d}:{minute:02d}'


def _wake_publish_deadline() -> Optional[str]:
    """按第一餐作息算出必须完成发布的最晚时间；时间表不可用时返回 None。"""
    try:
        from .database import get_meal_schedule
        rows = get_meal_schedule()
    except sqlite3.Error as exc:
        logger.warning('读取用餐时间失败，沿用配置的每日发布时间: %s', exc)
        return None

    by_key = {str(row.get('meal_type') or '').strip().lower(): row for row in rows}
    first = next((by_key[key] for key in ('breakfast', 'lunch', 'dinner')
                  if key in by_key), None)
    if first is None:
        return None
    try:
        hour, minute = map(int, str(first.get('meal_time') or '').split(':'))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    deadline = (hour * 60 + minute - publish_lead_minutes()
                - PUBLISH_READY_MARGIN_MINUTES) % (24 * 60)
    return f'{deadline // 60:02d}:{deadline % 60:02d}'


def daily_publish_time() -> str:
    """实际每日发布时间，不晚于第一餐的设备取图截止点。"""
    configured = _configured_daily_publish_time()
    deadline = _wake_publish_deadline()
    if deadline is None:
        return configured
    return min(configured, deadline)


# 初始化时创建表（理由见 init_settings_table 的 docstring）
init_settings_table()
