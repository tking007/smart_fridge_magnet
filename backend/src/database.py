"""
数据库模块 — SQLite 操作
"""
import sqlite3
import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, List

from . import config
from .inventory_units import normalize_inventory_amount
from .logger import get_logger

logger = get_logger('database')

CATEGORY_DEFINITIONS = (
    ('蔬菜', '🥬', 1), ('水果', '🍎', 2), ('肉类', '🥩', 3),
    ('海鲜', '🐟', 4), ('蛋类', '🥚', 5), ('乳制品', '🥛', 6),
    ('豆制品', '🫘', 7), ('主食', '🍚', 8), ('调味品', '🧂', 9),
    ('饮料', '🥤', 10), ('零食', '🍪', 11), ('冷冻食品', '🧊', 12),
    ('其他', '📦', 99),
)
DEFAULT_CATEGORIES = [item[0] for item in CATEGORY_DEFINITIONS]
CATEGORY_ALIASES = {
    '水产': '海鲜', '奶制品': '乳制品', '蛋奶': '蛋类',
    '谷物': '主食', '米面': '主食', '调料': '调味品', '冷冻': '冷冻食品',
}
DEFAULT_SHELF_LIFE_DAYS = {
    '蔬菜': 5, '水果': 14, '肉类': 3, '海鲜': 2, '蛋类': 30,
    '乳制品': 7, '豆制品': 5, '主食': 180, '调味品': 365,
    '饮料': 90, '零食': 90, '冷冻食品': 180, '其他': 14,
}


def normalize_category(category: str) -> str:
    """归一化已知同义词；未知分类仍由 validate_category 拒绝。"""
    value = str(category or '').strip()
    return CATEGORY_ALIASES.get(value, value)


def correct_known_category(name: str, category: str) -> str:
    """只纠正已知历史误分类，不重猜用户明确选择的其他分类。"""
    category = validate_category(category)
    if category == '调味品' and '油豆腐' in str(name or ''):
        return '豆制品'
    return category


def resolve_shelf_life(production_date=None, shelf_life_days=None, expiry_date=None):
    """生产日期、保存天数、到期日任意两个推导第三个。"""
    production_date = validate_date(production_date)
    expiry_date = validate_date(expiry_date)

    days = None
    if shelf_life_days not in (None, ''):
        try:
            days = int(shelf_life_days)
        except (TypeError, ValueError):
            raise ValueError('保质期天数必须是整数')
        if days < 1 or days > 3650:
            raise ValueError('保质期天数必须在 1 到 3650 之间')

    production = datetime.strptime(production_date, '%Y-%m-%d').date() if production_date else None
    expiry = datetime.strptime(expiry_date, '%Y-%m-%d').date() if expiry_date else None
    if production and expiry:
        inferred = (expiry - production).days
        if inferred < 1:
            raise ValueError('生产日期必须早于到期日')
        if days is None:
            days = inferred
        elif inferred != days:
            raise ValueError('生产日期、保质期天数与到期日不一致')
    elif production and days is not None:
        expiry_date = (production + timedelta(days=days)).strftime('%Y-%m-%d')
    elif expiry and days is not None:
        production_date = (expiry - timedelta(days=days)).strftime('%Y-%m-%d')

    return production_date, days, expiry_date

# 数据库路径来自 SFM_DATABASE_PATH（未配置时为 backend/data/fridge.db）。
# 用可配置路径而非硬编码，测试与容器部署才能把数据落到各自的位置。
#
# ── 为什么是函数而不是模块级常量 ──
# 这里原本是 `DB_PATH = config.database_path()`，在**模块导入的那一刻**就把路径定死了。
# 生产上无所谓（环境变量在进程启动前就设好），但它让**测试无法相互隔离**：
# 每个测试模块都在自己的 tmpdir 上跑，可 pytest 是先把所有模块 import 完再执行用例的，
# 于是第一个 import `database` 的模块就替整个进程钉死了库路径，后面所有模块都在往
# 同一个库里读写 —— 表现为「A 模块建的设备出现在 B 模块的断言里」，
# 而且换个文件名就可能好（收集顺序按字母序），极难定位。
#
# 现读环境变量之后，改 `SFM_DATABASE_PATH` 立刻生效，测试隔离与生产行为都正确。
def database_path() -> str:
    """当前生效的 SQLite 文件路径（每次调用现读配置）。"""
    return config.database_path()


#: 抢不到写锁时最多等多久（秒）。默认的 5 秒对本项目偏短：设备每次拉 manifest 都要写一行
#: 同步日志，而写方还有 8 个 gunicorn 线程与 4 个调度 job，其中几处用了 `BEGIN IMMEDIATE`。
_BUSY_TIMEOUT_SEC = 15.0


def get_connection():
    """获取数据库连接。

    开 WAL：默认的 `journal_mode=delete` 下读写互斥，而本项目的形态正是「设备高频读 +
    App/调度偶发写」—— 一次写就会把并发的读全挡住，表现为偶发 `database is locked` 的 500，
    设备侧则是同步偶发失败。WAL 让读不阻塞写、写不阻塞读。

    WAL 需要与库文件同目录可写（会生成 `-wal` / `-shm`），entrypoint 已在校验 `/data` 可写。
    """
    db_path = database_path()
    # SFM_DATABASE_PATH 可能是不带目录的纯文件名，此时 dirname 为空，makedirs('') 会报错。
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(f'PRAGMA busy_timeout={int(_BUSY_TIMEOUT_SEC * 1000)}')
    # NORMAL 而非 FULL：WAL 下 NORMAL 只在 checkpoint 时 fsync，断电最坏丢**最近的事务**，
    # 而这里的数据（库存、菜单、同步日志）都可由用户或设备重做，不值得每次提交都等一次 fsync。
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 预设分类表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '📦',
            sort_order INTEGER DEFAULT 0
        )
    ''')

    # 家庭画像表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS family_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_name TEXT NOT NULL,
            height REAL,
            weight REAL,
            age INTEGER,
            allergies TEXT DEFAULT '[]',
            dislikes_main TEXT DEFAULT '[]',
            dislikes_ingredient TEXT DEFAULT '[]',
            dislikes_taste TEXT DEFAULT '[]',
            health_notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 冰箱库存表（带乐观锁）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fridge_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT DEFAULT '个',
            weight_grams REAL,
            production_date TEXT,
            shelf_life_days INTEGER,
            expiry_date TEXT,
            confidence TEXT,
            photo_path TEXT,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 索引优化
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_inventory_category
        ON fridge_inventory(category)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_inventory_expiry
        ON fridge_inventory(expiry_date)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_inventory_name_qty
        ON fridge_inventory(name, quantity)
    ''')

    # 用餐计划表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS meal_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_type TEXT NOT NULL,
            meal_time TEXT NOT NULL,
            people_count INTEGER NOT NULL,
            plan_a TEXT,
            plan_b TEXT,
            selected_plan TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_plan_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            dirty INTEGER NOT NULL DEFAULT 1,
            inventory_revision INTEGER NOT NULL DEFAULT 0,
            progress_date TEXT,
            progress_revision INTEGER,
            completed_meals TEXT NOT NULL DEFAULT '[]',
            last_generated_date TEXT
        )
    ''')
    cursor.execute('INSERT OR IGNORE INTO daily_plan_state (id, dirty) VALUES (1, 1)')

    # 对话历史表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 营养成分表（中国食物成分表）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nutrition_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            food_code TEXT,
            food_name TEXT NOT NULL,
            category TEXT,
            edible REAL,
            water REAL,
            energy_kcal REAL,
            energy_kj REAL,
            protein REAL,
            fat REAL,
            cho REAL,
            dietary_fiber REAL,
            cholesterol REAL,
            vitamin_a REAL,
            vitamin_c REAL,
            ca REAL,
            fe REAL,
            zn REAL,
            UNIQUE(food_name)
        )
    ''')

    # 用餐时间配置表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS meal_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_type TEXT NOT NULL UNIQUE,
            meal_time TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    ''')

    # 条码 -> 商品 的缓存表。
    #
    # 查询走 Open Food Facts（见 barcode_lookup.py），一次网络往返几秒，而同一个家庭反复买
    # 的就是那几十样东西，第二次扫同一瓶牛奶应该是瞬时的。
    #
    # resolved=0 表示「查过了但商品库里没有」，同样要存：不存的话每次扫到那个生僻商品都要
    # 重新等一次网络往返，得到同样的「不知道」。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS barcode_cache (
            barcode TEXT PRIMARY KEY,
            name TEXT,
            category TEXT,
            shelf_life_days INTEGER,
            resolved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 幂等迁移：已有部署只补列；克/g 旧数据迁到唯一的重量事实字段。
    inventory_columns = {row[1] for row in cursor.execute('PRAGMA table_info(fridge_inventory)')}
    if 'weight_grams' not in inventory_columns:
        cursor.execute('ALTER TABLE fridge_inventory ADD COLUMN weight_grams REAL')
    if 'shelf_life_days' not in inventory_columns:
        cursor.execute('ALTER TABLE fridge_inventory ADD COLUMN shelf_life_days INTEGER')
    cursor.execute('''
        UPDATE fridge_inventory
        SET weight_grams = COALESCE(weight_grams, quantity), quantity = 0, unit = '个'
        WHERE lower(trim(unit)) IN ('g', '克')
          AND (quantity > 0 OR COALESCE(weight_grams, 0) > 0)
    ''')

    state_columns = {row[1] for row in cursor.execute('PRAGMA table_info(daily_plan_state)')}
    if 'inventory_revision' not in state_columns:
        cursor.execute(
            'ALTER TABLE daily_plan_state ADD COLUMN inventory_revision INTEGER NOT NULL DEFAULT 0'
        )
    if 'progress_date' not in state_columns:
        cursor.execute('ALTER TABLE daily_plan_state ADD COLUMN progress_date TEXT')
    if 'progress_revision' not in state_columns:
        cursor.execute('ALTER TABLE daily_plan_state ADD COLUMN progress_revision INTEGER')
    if 'completed_meals' not in state_columns:
        cursor.execute(
            "ALTER TABLE daily_plan_state ADD COLUMN completed_meals TEXT NOT NULL DEFAULT '[]'"
        )

    barcode_columns = {row[1] for row in cursor.execute('PRAGMA table_info(barcode_cache)')}
    if 'shelf_life_days' not in barcode_columns:
        cursor.execute('ALTER TABLE barcode_cache ADD COLUMN shelf_life_days INTEGER')

    # 13 类是唯一事实来源；旧库缺类时补齐，显示元数据变化时同步更新。
    cursor.executemany('''
        INSERT INTO inventory_categories (name, icon, sort_order) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET icon=excluded.icon, sort_order=excluded.sort_order
    ''', CATEGORY_DEFINITIONS)
    for alias, canonical in CATEGORY_ALIASES.items():
        cursor.execute('UPDATE fridge_inventory SET category = ? WHERE category = ?', (canonical, alias))
    cursor.execute("""
        UPDATE fridge_inventory SET category = '豆制品'
        WHERE category = '调味品' AND name LIKE '%油豆腐%'
    """)
    if cursor.rowcount:
        cursor.execute('''
            UPDATE daily_plan_state
            SET dirty = 1, inventory_revision = inventory_revision + 1 WHERE id = 1
        ''')
    cursor.execute('''
        UPDATE fridge_inventory SET category = '其他'
        WHERE category NOT IN (SELECT name FROM inventory_categories)
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS fridge_inventory_category_insert
        BEFORE INSERT ON fridge_inventory
        WHEN NOT EXISTS (SELECT 1 FROM inventory_categories WHERE name = NEW.category)
        BEGIN SELECT RAISE(ABORT, 'invalid inventory category'); END
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS fridge_inventory_category_update
        BEFORE UPDATE OF category ON fridge_inventory
        WHEN NOT EXISTS (SELECT 1 FROM inventory_categories WHERE name = NEW.category)
        BEGIN SELECT RAISE(ABORT, 'invalid inventory category'); END
    ''')

    # 初始化默认用餐时间（如果为空）。
    #
    # 必须预置：所有定时任务都以 `for meal in get_meal_schedule()` 开头，空表意味着餐前
    # 推荐**一次都不会触发**，且日志里没有任何异常（表现为「调度器已启动」但什么都不发生）。
    #
    # 键名用 breakfast/lunch/dinner 而不是中文：agent.py 解析家庭画像写入时用的就是这三个
    # 英文键，而 meal_type 上有 UNIQUE 约束 —— 两种写法并存会变成两行独立记录，
    # 同一顿饭被推荐两次。
    cursor.execute('SELECT COUNT(*) FROM meal_schedule')
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            'INSERT INTO meal_schedule (meal_type, meal_time) VALUES (?, ?)',
            [('breakfast', '07:30'), ('lunch', '12:00'), ('dinner', '18:30')]
        )

    # ── 调度任务历史 ──
    #
    # 落库而不是只留在内存里：容器一重启历史就会归零，而它正是排查「昨天那次自动下发到底
    # 发生了没有」的唯一线索。
    #
    # created_at 存**本地时间** ISO 串，与 `_has_recent_task` 里拿 `datetime.now()` 比较的
    # 口径一致。刻意不用 `CURRENT_TIMESTAMP`（那是 UTC），否则「最近 N 分钟内是否做过」
    # 会整体偏一个时区。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduler_task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            description TEXT,
            success INTEGER NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_created
        ON scheduler_task_history (created_at DESC)
    ''')

    conn.commit()
    conn.close()


# ==================== 调度任务历史 ====================

#: 库里最多保留多少条任务历史。排查「昨天为什么没下发」需要跨天，1000 条约覆盖三四天
#: （每 5 分钟一跳的下发检查占大头）。
TASK_HISTORY_KEEP = 1000


def add_task_record(task_id: str, description: str, success: bool,
                    error: str = None, timestamp: str = None) -> Dict:
    """记一条调度任务执行结果，返回落库的那条记录。

    **返回记录**是刻意的：手动触发下发的调用方需要知道「这一次」的结果。用 `len(history)`
    做游标再切片不可靠 —— 那份历史被 4 个调度 job、8 个请求线程与设备按键线程共写。
    """
    record = {
        'task_id': task_id,
        'description': description,
        'success': bool(success),
        'error': error,
        'timestamp': timestamp or datetime.now().isoformat(),
    }
    conn = get_connection()
    try:
        conn.execute(
            'INSERT INTO scheduler_task_history '
            '(task_id, description, success, error, created_at) VALUES (?, ?, ?, ?, ?)',
            (record['task_id'], record['description'], int(record['success']),
             record['error'], record['timestamp'])
        )
        # 顺手裁剪，不另设清理任务：这张表只有这一个写入方，写的时候修剪最省事。
        conn.execute(
            'DELETE FROM scheduler_task_history WHERE id <= '
            '(SELECT MAX(id) - ? FROM scheduler_task_history)',
            (TASK_HISTORY_KEEP,)
        )
        conn.commit()
    finally:
        # `with sqlite3.Connection` 只管事务、**不关连接** —— 用它会漏连接。
        conn.close()
    return record


def get_task_records(limit: int = 100) -> List[Dict]:
    """取最近 `limit` 条任务历史，**按时间正序**（最新在尾）。

    正序是既有语义：调用方用 `history[-1]` 拿最新一条。用 `id DESC` 取最近 N 条再反转，
    而不是 `id ASC` 直接取 —— 后者在表里攒了上千条时会返回最旧的那一批。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            'SELECT task_id, description, success, error, created_at '
            'FROM scheduler_task_history ORDER BY id DESC LIMIT ?',
            (max(1, int(limit)),)
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            'task_id': r['task_id'],
            'description': r['description'],
            'success': bool(r['success']),
            'error': r['error'],
            'timestamp': r['created_at'],
        }
        for r in reversed(rows)
    ]


def count_task_records() -> int:
    conn = get_connection()
    try:
        return conn.execute('SELECT COUNT(*) FROM scheduler_task_history').fetchone()[0]
    finally:
        conn.close()


def has_recent_task_record(task_id: str, description: str, within_minutes: int) -> bool:
    """最近 N 分钟内是否已做过同一件事（用于防重复执行）。

    与 created_at 的写入口径一致：两边都是本地时间。
    """
    cutoff = (datetime.now() - timedelta(minutes=max(1, within_minutes))).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT 1 FROM scheduler_task_history '
            'WHERE task_id = ? AND description = ? AND created_at >= ? LIMIT 1',
            (task_id, description, cutoff)
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def add_member(member_name, height=None, weight=None, age=None,
               allergies=None, dislikes_main=None, dislikes_ingredient=None,
               dislikes_taste=None, health_notes=''):
    """添加家庭成员"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO family_profile
        (member_name, height, weight, age, allergies, dislikes_main,
         dislikes_ingredient, dislikes_taste, health_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        member_name, height, weight, age,
        json.dumps(allergies or [], ensure_ascii=False),
        json.dumps(dislikes_main or [], ensure_ascii=False),
        json.dumps(dislikes_ingredient or [], ensure_ascii=False),
        json.dumps(dislikes_taste or [], ensure_ascii=False),
        health_notes
    ))
    conn.commit()
    member_id = cursor.lastrowid
    conn.close()
    return member_id


def update_member(member_id, **kwargs):
    """更新家庭成员信息"""
    conn = get_connection()
    cursor = conn.cursor()

    set_clauses = []
    values = []
    for key, value in kwargs.items():
        if key in ('allergies', 'dislikes_main', 'dislikes_ingredient', 'dislikes_taste'):
            value = json.dumps(value, ensure_ascii=False)
        set_clauses.append(f"{key} = ?")
        values.append(value)

    set_clauses.append("updated_at = CURRENT_TIMESTAMP")
    values.append(member_id)

    sql = f"UPDATE family_profile SET {', '.join(set_clauses)} WHERE id = ?"
    cursor.execute(sql, values)
    conn.commit()
    conn.close()


#: 成员的列表型字段。这几个在库里是 JSON 文本，进出都要转换。
MEMBER_LIST_FIELDS = ('allergies', 'dislikes_main', 'dislikes_ingredient', 'dislikes_taste')


def replace_members(members: list) -> int:
    """以传入列表**整体替换**家庭成员。

    替换而不是合并：App 的成员页是用户直接看到的东西，他删掉一位成员或去掉一条忌口后必须
    立刻在推荐结果上生效。只增不减的合并会让后端继续按用户已看不到的旧记录避让食材。
    代价是通过对话补充的信息会被下一次同步覆盖。

    整个替换在**一个事务**里完成：先删后插若中途失败而不回滚，家庭成员会被清空，
    后续推荐将完全失去忌口与过敏约束 —— 一个写库失败演变成安全问题。

    Returns:
        写入的成员数量。
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN')
        cursor.execute('DELETE FROM family_profile')
        for m in members:
            name = str(m.get('member_name') or m.get('name') or '').strip()
            if not name:
                # 无名成员既无法展示也无法按名字合并，静默跳过而不是写一条空记录。
                continue
            cursor.execute('''
                INSERT INTO family_profile
                (member_name, height, weight, age, allergies, dislikes_main,
                 dislikes_ingredient, dislikes_taste, health_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name, m.get('height'), m.get('weight'), m.get('age'),
                json.dumps(m.get('allergies') or [], ensure_ascii=False),
                json.dumps(m.get('dislikes_main') or [], ensure_ascii=False),
                json.dumps(m.get('dislikes_ingredient') or [], ensure_ascii=False),
                json.dumps(m.get('dislikes_taste') or [], ensure_ascii=False),
                m.get('health_notes') or ''
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return len(get_all_members())


def get_all_members():
    """获取所有家庭成员"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM family_profile ORDER BY id')
    rows = cursor.fetchall()
    conn.close()

    members = []
    for row in rows:
        member = dict(row)
        for field in ('allergies', 'dislikes_main', 'dislikes_ingredient', 'dislikes_taste'):
            member[field] = json.loads(member[field])
        members.append(member)
    return members


def collect_allergens() -> list:
    """全家过敏源去重汇总。

    单独成函数是因为有两个消费方：进 prompt 的摘要（[get_profile_summary]）和生成后的
    输出校验。两边必须用同一份清单 —— 若校验用的清单比 prompt 里的窄，模型会因为
    "prompt 说了禁止" 而被信任，却没有人真正拦住漏网的那一项。
    """
    allergens = set()
    for m in get_all_members():
        for item in (m.get('allergies') or []):
            name = str(item).strip()
            if name:
                allergens.add(name)
    return sorted(allergens)


def get_profile_summary():
    """获取家庭画像摘要（用于Prompt）"""
    members = get_all_members()
    if not members:
        return "暂无家庭成员信息"

    lines = []
    for m in members:
        parts = [f"{m['member_name']}"]
        if m.get('height') and m.get('weight'):
            parts.append(f"身高{m['height']}cm/体重{m['weight']}kg")
        if m.get('age'):
            parts.append(f"{m['age']}岁")
        # 过敏与「不吃」分开表述、用「禁用」而不是「不吃」：并列成同一句时模型会把两者
        # 当作同强度的偏好，而后果完全不同 —— 不吃苦瓜是难吃，吃到过敏源是医疗事件。
        if m.get('allergies'):
            parts.append(f"过敏禁用{','.join(m['allergies'])}")
        if m.get('dislikes_main'):
            parts.append(f"不吃{','.join(m['dislikes_main'])}")
        if m.get('dislikes_ingredient'):
            parts.append(f"不要{','.join(m['dislikes_ingredient'])}")
        if m.get('dislikes_taste'):
            parts.append(f"不吃{','.join(m['dislikes_taste'])}")
        if m.get('health_notes'):
            parts.append(m['health_notes'])
        lines.append("，".join(parts))

    summary = "\n".join(lines)

    # 全家过敏源汇总并置顶。两个理由：
    #   1. 菜是一锅做给全家吃的，分餐做不到 —— 逐人列出时模型可能理解成"只有他那份要
    #      避开"，于是给过敏的人配一道含过敏源的菜。
    #   2. 约束放在 prompt 开头比散落在中间被遵守得更好。
    # 复用 collect_allergens()，不在这里重写一遍集合推导：那份实现会 strip 并跳过空串，
    # 两处口径一旦分叉，prompt 里写的过敏源与生成后校验用的就不是同一份清单 ——
    # 而 collect_allergens 的注释要求的正是"两边必须用同一份"。
    allergens = collect_allergens()
    if allergens:
        summary = (
            "⚠️ 全家过敏源（绝对禁止出现在任何菜品、配菜与调料中，"
            f"包括含有它的加工食品）：{'、'.join(allergens)}\n" + summary
        )
    return summary


def get_barcode_cache(barcode: str):
    """查条码缓存。

    Returns:
        None（从未查过）或包含名称、分类、常见保质期与 resolved 的字典。
        注意「查过但认不出」也是一条记录（resolved=False）—— 与「从未查过」必须区分，
        否则每次扫到同一个生僻商品都会重新问一遍模型并等同样的几秒。
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT name, category, shelf_life_days, resolved FROM barcode_cache WHERE barcode = ?',
            (barcode,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "name": row[0], "category": row[1], "shelf_life_days": row[2],
            "resolved": bool(row[3]),
        }
    finally:
        conn.close()


def put_barcode_cache(barcode: str, name=None, category=None, shelf_life_days=None,
                      resolved: bool = False):
    """写入 / 覆盖条码缓存。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO barcode_cache (barcode, name, category, shelf_life_days, resolved)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(barcode) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                shelf_life_days = excluded.shelf_life_days,
                resolved = excluded.resolved,
                created_at = CURRENT_TIMESTAMP
        ''', (barcode, name, category, shelf_life_days, 1 if resolved else 0))
        conn.commit()
    finally:
        conn.close()


def clear_profile():
    """清空家庭画像"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM family_profile')
    conn.commit()
    conn.close()


# ==================== 冰箱库存操作 ====================

def validate_date(date_str):
    """
    校验日期格式

    Args:
        date_str: 日期字符串

    Returns:
        str: 格式化后的日期 (YYYY-MM-DD)

    Raises:
        ValueError: 日期格式错误
    """
    if not date_str:
        return None

    # 支持的格式
    formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d']
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    raise ValueError(f"日期格式错误: {date_str}，支持格式: YYYY-MM-DD")


def validate_category(category):
    """
    校验分类是否在预设列表中

    Args:
        category: 分类名称

    Returns:
        str: 校验后的分类

    Raises:
        ValueError: 分类不在预设列表中
    """
    category = normalize_category(category)
    if category not in DEFAULT_CATEGORIES:
        raise ValueError(f"分类 '{category}' 不在预设列表中，可选: {', '.join(DEFAULT_CATEGORIES)}")
    return category


def add_inventory_item(name, category, quantity=0, unit='个', weight_grams=None,
                       production_date=None, shelf_life_days=None, expiry_date=None,
                       confidence=None, photo_path=None):
    """
    添加库存项

    Args:
        name: 食材名称
        category: 分类（必须在预设列表中）
        quantity: 数量
        unit: 单位
        production_date: 生产日期（可选，格式 YYYY-MM-DD）
        expiry_date: 保质期（可选，格式 YYYY-MM-DD）
        confidence: 识别置信度（高/中/低）；手动录入为 None
        photo_path: 照片路径

    Returns:
        int: 新增项ID

    Raises:
        ValueError: 参数校验失败
    """
    # 上传文件只用于本次识别且随后删除；客户端路径既不可验证也不应落库。
    photo_path = None
    category = correct_known_category(name, category)
    production_date, shelf_life_days, expiry_date = resolve_shelf_life(
        production_date, shelf_life_days, expiry_date
    )
    amount = normalize_inventory_amount(name, quantity, unit, weight_grams)
    quantity = amount['quantity']
    unit = amount['unit']
    weight_grams = amount['weight_grams']
    if quantity <= 0 and (weight_grams is None or weight_grams <= 0):
        raise ValueError("数量与克重至少填写一个")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO fridge_inventory
            (name, category, quantity, unit, weight_grams, production_date,
             shelf_life_days, expiry_date, confidence, photo_path, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (name, category, quantity, unit, weight_grams, production_date,
              shelf_life_days, expiry_date, confidence, photo_path))
        item_id = cursor.lastrowid
        mark_plan_dirty(conn)
        conn.commit()
        return item_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_plan_dirty(conn=None) -> None:
    """库存变化后在同一事务内标记重算，并推进库存 revision。"""
    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        conn.execute('''
            UPDATE daily_plan_state
            SET dirty = 1, inventory_revision = inventory_revision + 1
            WHERE id = 1
        ''')
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def get_daily_plan_state() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT dirty, inventory_revision, last_generated_date '
            'FROM daily_plan_state WHERE id = 1'
        ).fetchone()
        return {
            'dirty': bool(row['dirty']) if row else True,
            'revision': int(row['inventory_revision']) if row else 0,
            'last_generated_date': row['last_generated_date'] if row else None,
        }
    finally:
        conn.close()


def mark_daily_plan_generated(local_date: str, expected_revision=None) -> bool:
    """仅当库存仍是生成开始时的版本，才清除 dirty 标记。"""
    conn = get_connection()
    try:
        if expected_revision is None:
            cursor = conn.execute('''
                UPDATE daily_plan_state
                SET dirty = 0, last_generated_date = ?,
                    progress_date = NULL, progress_revision = NULL, completed_meals = '[]'
                WHERE id = 1
            ''', (local_date,))
        else:
            cursor = conn.execute('''
                UPDATE daily_plan_state
                SET dirty = 0, last_generated_date = ?,
                    progress_date = NULL, progress_revision = NULL, completed_meals = '[]'
                WHERE id = 1 AND inventory_revision = ?
            ''', (local_date, expected_revision))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def get_daily_plan_progress(local_date: str, revision: int) -> set:
    """读取本轮已完成餐次；日期或库存版本变化时开始新一轮。"""
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''
            SELECT progress_date, progress_revision, completed_meals
            FROM daily_plan_state WHERE id = 1
        ''').fetchone()
        if not row or row['progress_date'] != local_date or row['progress_revision'] != revision:
            conn.execute('''
                UPDATE daily_plan_state
                SET progress_date = ?, progress_revision = ?, completed_meals = '[]'
                WHERE id = 1 AND inventory_revision = ?
            ''', (local_date, revision, revision))
            conn.commit()
            return set()
        try:
            completed = set(json.loads(row['completed_meals'] or '[]'))
        except (TypeError, ValueError):
            completed = set()
        conn.commit()
        return completed
    finally:
        conn.close()


def mark_daily_plan_meal_completed(local_date: str, revision: int, meal_key: str) -> bool:
    """在库存版本未变化时记录一个已成功餐次，返回是否写入成功。"""
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''
            SELECT inventory_revision, progress_date, progress_revision, completed_meals
            FROM daily_plan_state WHERE id = 1
        ''').fetchone()
        if (not row or row['inventory_revision'] != revision
                or row['progress_date'] != local_date or row['progress_revision'] != revision):
            conn.rollback()
            return False
        try:
            completed = set(json.loads(row['completed_meals'] or '[]'))
        except (TypeError, ValueError):
            completed = set()
        completed.add(meal_key)
        conn.execute(
            'UPDATE daily_plan_state SET completed_meals = ? WHERE id = 1',
            (json.dumps(sorted(completed), ensure_ascii=False),)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def batch_add_inventory_items(items):
    """
    批量添加库存项（原子操作）

    Args:
        items: 库存项列表 [{"name": "...", "category": "...", "quantity": ...}, ...]

    Returns:
        dict: {"success": [...], "failed": [...]}

    Raises:
        ValueError: 任何一项校验失败时回滚全部
    """
    success_items = []
    failed_items = []

    conn = get_connection()
    try:
        conn.execute('BEGIN')
        cursor = conn.cursor()

        for item in items:
            try:
                category = correct_known_category(item.get('name'), item.get('category', '其他'))
                production_date, shelf_life_days, expiry_date = resolve_shelf_life(
                    item.get('production_date'), item.get('shelf_life_days'),
                    item.get('expiry_date')
                )
                amount = normalize_inventory_amount(
                    item.get('name', ''), item.get('quantity', 0), item.get('unit', '个'),
                    item.get('weight_grams'),
                )
                quantity = amount['quantity']
                weight_grams = amount['weight_grams']

                if quantity <= 0 and (weight_grams is None or weight_grams <= 0):
                    raise ValueError("数量与克重至少填写一个")

                cursor.execute('''
                    INSERT INTO fridge_inventory
                    (name, category, quantity, unit, weight_grams, production_date,
                     shelf_life_days, expiry_date, confidence, photo_path, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ''', (
                    item['name'],
                    category,
                    quantity,
                    amount['unit'],
                    weight_grams,
                    production_date,
                    shelf_life_days,
                    expiry_date,
                    item.get('confidence'),
                    None
                ))

                success_items.append({
                    "item_id": cursor.lastrowid,
                    "name": item['name'],
                    "success": True
                })

            except Exception as e:
                failed_items.append({
                    "name": item.get('name', '未知'),
                    "success": False,
                    "error": str(e)
                })

        if failed_items:
            conn.rollback()
            success_items = []
        else:
            if success_items:
                mark_plan_dirty(conn)
            conn.commit()

    except Exception as e:
        conn.rollback()
        raise ValueError(f"批量添加失败，已回滚: {str(e)}")

    finally:
        conn.close()

    return {"success": success_items, "failed": failed_items}


def get_inventory(category=None, page=1, page_size=50):
    """
    获取库存（分页）

    Args:
        category: 按分类筛选（可选）
        page: 页码（从1开始）
        page_size: 每页数量（默认50）

    Returns:
        dict: {"items": [...], "total": int, "page": int, "page_size": int, "total_pages": int}
    """
    conn = get_connection()
    cursor = conn.cursor()

    if category:
        cursor.execute(
            '''SELECT COUNT(*) FROM fridge_inventory
               WHERE (quantity > 0 OR COALESCE(weight_grams, 0) > 0) AND category = ?''',
            (category,)
        )
    else:
        cursor.execute('''SELECT COUNT(*) FROM fridge_inventory
                          WHERE quantity > 0 OR COALESCE(weight_grams, 0) > 0''')

    total = cursor.fetchone()[0]

    # 分页查询
    offset = (page - 1) * page_size
    if category:
        cursor.execute('''
            SELECT * FROM fridge_inventory
            WHERE (quantity > 0 OR COALESCE(weight_grams, 0) > 0) AND category = ?
            ORDER BY expiry_date, created_at
            LIMIT ? OFFSET ?
        ''', (category, page_size, offset))
    else:
        cursor.execute('''
            SELECT * FROM fridge_inventory
            WHERE quantity > 0 OR COALESCE(weight_grams, 0) > 0
            ORDER BY expiry_date, created_at
            LIMIT ? OFFSET ?
        ''', (page_size, offset))

    rows = cursor.fetchall()
    conn.close()

    items = []
    for row in rows:
        item = dict(row)
        # 计算剩余天数。按**日期**相减而不是 datetime 相减：后者拿 00:00 的到期时刻去减
        # "现在"，今天到期会算出 -1（timedelta.days 向下取整），于是当天到期的东西被显示成
        # 「已过期」。App 侧 ShelfLife.kt 用的是 epoch-day 差值（今天到期 = 0），
        # 两端口径不一致会让同一样食材在手机上写"今天到期"、在设备与摘要里写"已过期"。
        if item.get('expiry_date'):
            try:
                exp = datetime.strptime(item['expiry_date'], '%Y-%m-%d').date()
                item['days_left'] = (exp - date.today()).days
            except (ValueError, TypeError):
                # 库里存了非 Y-m-d 的脏数据。当作"没有保质期"，不要让整个库存查询挂掉。
                item['days_left'] = None
        else:
            item['days_left'] = None
        items.append(item)

    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


def update_inventory_item(item_id, version=None, **kwargs):
    """
    更新库存项（带乐观锁）

    Args:
        item_id: 库存项ID
        version: 版本号（乐观锁，不传则跳过锁检查）
        **kwargs: 要更新的字段

    Raises:
        ValueError: 库存项不存在或版本冲突
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM fridge_inventory WHERE id = ?', (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"库存项 #{item_id} 不存在")

    # 乐观锁检查
    if version is not None and row['version'] != version:
        conn.close()
        raise ValueError(f"版本冲突：当前版本 {row['version']}，传入版本 {version}，请刷新后重试")

    if 'production_date' in kwargs:
        kwargs['production_date'] = validate_date(kwargs['production_date'])
    if 'expiry_date' in kwargs:
        kwargs['expiry_date'] = validate_date(kwargs['expiry_date'])
    if 'category' in kwargs:
        kwargs['category'] = validate_category(kwargs['category'])
    effective_category = correct_known_category(
        kwargs.get('name', row['name']), kwargs.get('category', row['category'])
    )
    if effective_category != row['category'] or 'category' in kwargs:
        kwargs['category'] = effective_category
    if {'quantity', 'unit', 'weight_grams'}.intersection(kwargs):
        amount = normalize_inventory_amount(
            kwargs.get('name', row['name']),
            kwargs.get('quantity', row['quantity']),
            kwargs.get('unit', row['unit']),
            kwargs.get('weight_grams', row['weight_grams']),
            kwargs.get('confidence', row['confidence']),
        )
        kwargs.update(
            quantity=amount['quantity'],
            unit=amount['unit'],
            weight_grams=amount['weight_grams'],
        )
    next_quantity = kwargs.get('quantity', row['quantity'])
    next_weight = kwargs.get('weight_grams', row['weight_grams'])
    if next_quantity <= 0 and (next_weight is None or next_weight <= 0):
        conn.close()
        raise ValueError("数量与克重至少填写一个")

    shelf_fields = {'production_date', 'shelf_life_days', 'expiry_date'}
    if shelf_fields.intersection(kwargs):
        production, days, expiry = resolve_shelf_life(
            kwargs.get('production_date', row['production_date']),
            kwargs.get('shelf_life_days', row['shelf_life_days']),
            kwargs.get('expiry_date', row['expiry_date']),
        )
        kwargs.update(production_date=production, shelf_life_days=days, expiry_date=expiry)

    # 构建更新语句
    allowed_fields = {'name', 'category', 'quantity', 'unit', 'weight_grams',
                      'production_date', 'shelf_life_days', 'expiry_date',
                      'confidence'}
    set_clauses = []
    values = []

    for key, value in kwargs.items():
        if key in allowed_fields:
            set_clauses.append(f"{key} = ?")
            values.append(value)

    if not set_clauses:
        conn.close()
        raise ValueError("没有有效的更新字段")

    set_clauses.append("version = version + 1")
    set_clauses.append("updated_at = CURRENT_TIMESTAMP")
    values.append(item_id)

    sql = f"UPDATE fridge_inventory SET {', '.join(set_clauses)} WHERE id = ?"
    try:
        cursor.execute(sql, values)
        mark_plan_dirty(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_inventory_item(item_id):
    """
    删除库存项（软删除，设为quantity=0）

    Args:
        item_id: 库存项ID

    Raises:
        ValueError: 库存项不存在
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT id FROM fridge_inventory WHERE id = ?', (item_id,))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"库存项 #{item_id} 不存在")

    try:
        cursor.execute('''
            UPDATE fridge_inventory
            SET quantity = 0, weight_grams = NULL,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (item_id,))
        mark_plan_dirty(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_preset_categories():
    """获取预设分类列表（从数据库）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name, icon, sort_order FROM inventory_categories ORDER BY sort_order')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_inventory_categories():
    """获取库存中实际使用的分类"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT category, COUNT(*) as count
        FROM fridge_inventory
        WHERE quantity > 0 OR COALESCE(weight_grams, 0) > 0
        GROUP BY category
        ORDER BY category
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [{"name": row['category'], "count": row['count']} for row in rows]


def get_inventory_summary():
    """获取库存摘要（用于Prompt）"""
    result = get_inventory(page_size=1000)  # 摘要不分页
    items = result['items']

    if not items:
        return "冰箱为空"

    lines = []
    for item in items:
        if not config.recipe_include_non_cooking() and item['category'] in {'饮料', '零食'}:
            continue
        if item.get('weight_grams'):
            count = (
                f"，{item['quantity']:g}{item['unit']}"
                if item['quantity'] > 0 and str(item['unit']).lower() not in {'g', '克'} else ''
            )
            line = f"- {item['name']} × {item['weight_grams']:g}克{count}"
        else:
            line = f"- {item['name']} × {item['quantity']:g}{item['unit']}"
        if item.get('days_left') is not None:
            if item['days_left'] < 0:
                line += "（已过期）"
            elif item['days_left'] <= 3:
                line += f"（{item['days_left']}天后过期⚠️）"
            elif item['days_left'] <= 7:
                line += f"（{item['days_left']}天后过期）"
        lines.append(line)

    return "\n".join(lines) if lines else "冰箱为空"


def deduct_inventory(items_to_deduct):
    """
    扣减库存（FIFO 先进先出 + 事务）

    Args:
        items_to_deduct: [{"name": "鸡蛋", "quantity": 2}, ...]

    Returns:
        list: 扣减结果

    Raises:
        ValueError: 库存不足时回滚
    """
    conn = get_connection()
    try:
        conn.execute('BEGIN')
        cursor = conn.cursor()

        results = []
        for item in items_to_deduct:
            name = item['name']
            deduct_weight = item.get('weight_grams') not in (None, '')
            amount_key = 'weight_grams' if deduct_weight else 'quantity'
            qty_needed = float(item[amount_key])
            unit = '克' if deduct_weight else '个'

            if qty_needed <= 0:
                results.append({"name": name, "error": "数量必须大于0"})
                continue

            # FIFO: 按生产日期排序（先买的先用）
            cursor.execute(f'''
                SELECT id, {amount_key} AS available, production_date, version
                FROM fridge_inventory
                WHERE name = ? AND COALESCE({amount_key}, 0) > 0
                ORDER BY
                    CASE WHEN production_date IS NOT NULL THEN 0 ELSE 1 END,
                    production_date ASC,
                    created_at ASC
            ''', (name,))

            rows = cursor.fetchall()
            remaining = qty_needed
            deduct_details = []

            for row in rows:
                if remaining <= 0:
                    break

                available = row['available']
                deduct_qty = min(available, remaining)
                new_qty = available - deduct_qty

                cursor.execute(f'''
                    UPDATE fridge_inventory
                    SET {amount_key} = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND version = ?
                ''', (new_qty, row['id'], row['version']))

                # 检查是否更新成功（乐观锁）
                if cursor.rowcount == 0:
                    conn.rollback()
                    raise ValueError(f"并发冲突：{name} 在扣减过程中被修改，请重试")

                deduct_details.append({
                    "id": row['id'],
                    "deducted": deduct_qty,
                    "remaining": new_qty,
                    "unit": unit,
                })
                remaining -= deduct_qty

            if remaining > 0:
                conn.rollback()
                raise ValueError(f"库存不足：{name} 需要 {qty_needed}，实际只有 {qty_needed - remaining}")

            results.append({
                "name": name,
                "requested": qty_needed,
                "deducted": qty_needed,
                "unit": unit,
                "details": deduct_details
            })

        mark_plan_dirty(conn)
        conn.commit()
        return results

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_expiring_items(days=3):
    """
    获取即将过期的食材

    Args:
        days: 天数（默认3天）

    Returns:
        list: 即将过期的食材列表
    """
    conn = get_connection()
    cursor = conn.cursor()

    future = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')

    cursor.execute('''
        SELECT * FROM fridge_inventory
        WHERE expiry_date IS NOT NULL
        AND expiry_date <= ? AND expiry_date >= ?
        AND (quantity > 0 OR COALESCE(weight_grams, 0) > 0)
        ORDER BY expiry_date
    ''', (future, today))

    rows = cursor.fetchall()
    conn.close()

    items = []
    for row in rows:
        item = dict(row)
        if item.get('expiry_date'):
            try:
                exp = datetime.strptime(item['expiry_date'], '%Y-%m-%d')
                item['days_left'] = (exp - datetime.now()).days
            except Exception:
                item['days_left'] = None
        items.append(item)

    return items


# ==================== 用餐计划操作 ====================

def add_meal_plan(meal_type, meal_time, people_count, plan_a, plan_b):
    """添加用餐计划"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO meal_plan (meal_type, meal_time, people_count, plan_a, plan_b)
        VALUES (?, ?, ?, ?, ?)
    ''', (meal_type, meal_time, people_count,
          json.dumps(plan_a, ensure_ascii=False),
          json.dumps(plan_b, ensure_ascii=False)))
    conn.commit()
    plan_id = cursor.lastrowid
    conn.close()
    return plan_id


def add_meal_plan_if_latest(meal_type, meal_time, people_count, plan_a, plan_b,
                            expected_latest_id, meal_type_aliases):
    """仅在指定餐次的最新 pending 记录仍是 expected_latest_id 时插入。

    检查与 INSERT 必须在同一个 IMMEDIATE 事务里：两个 apply 若先各自查询、再分别写入，
    会同时通过同一个基准。IMMEDIATE 让 SQLite 在读取前取得写锁，第二个请求只能在第一个
    提交后再读取，因此会看到新 id 并返回冲突。

    Returns:
        (plan_id, newest_id)。基准过期时 plan_id 为 None；newest_id 供调用方记录日志。
    """
    aliases = tuple(str(alias).strip().lower() for alias in meal_type_aliases if alias)
    if not aliases:
        raise ValueError("meal_type_aliases 不能为空")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE')
        placeholders = ','.join('?' for _ in aliases)
        cursor.execute(
            "SELECT id FROM meal_plan WHERE status = 'pending' "
            f"AND lower(trim(meal_type)) IN ({placeholders}) "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            aliases,
        )
        row = cursor.fetchone()
        newest_id = row['id'] if row else None
        if newest_id != expected_latest_id:
            conn.rollback()
            return None, newest_id

        cursor.execute('''
            INSERT INTO meal_plan (meal_type, meal_time, people_count, plan_a, plan_b)
            VALUES (?, ?, ?, ?, ?)
        ''', (meal_type, meal_time, people_count,
              json.dumps(plan_a, ensure_ascii=False),
              json.dumps(plan_b, ensure_ascii=False)))
        plan_id = cursor.lastrowid
        conn.commit()
        return plan_id, newest_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_meal_plan(plan_id):
    """按 id 读取用餐计划，plan_a / plan_b 已解析成 dict。

    供「把已生成的菜谱渲染成设备画面」使用（见 menu_render / display/render-from-plan）：
    菜谱由 AI 生成时就已落库，渲染时按 plan_id 取回即可，不必让调用方重复传一遍数据，
    也保证设备上显示的与当时确认的是同一份。

    plan_a / plan_b 在库里是 JSON 文本；解析失败时返回 None 而不是抛异常 ——
    历史数据可能因模型输出异常而存了非法 JSON，不该让整个渲染请求 500。
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT id, meal_type, meal_time, people_count, plan_a, plan_b,
                   selected_plan, status, created_at
            FROM meal_plan WHERE id = ?
        ''', (plan_id,))
        row = cursor.fetchone()
        if not row:
            return None

        def _load(text):
            if not text:
                return None
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return None

        return {
            'id': row[0],
            'meal_type': row[1],
            'meal_time': row[2],
            'people_count': row[3],
            'plan_a': _load(row[4]),
            'plan_b': _load(row[5]),
            'selected_plan': row[6],
            'status': row[7],
            'created_at': row[8],
        }
    finally:
        conn.close()


def confirm_meal_plan(plan_id, selected_plan):
    """确认用餐计划"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE meal_plan
        SET selected_plan = ?, status = 'confirmed'
        WHERE id = ?
    ''', (selected_plan, plan_id))
    conn.commit()
    conn.close()


def get_pending_plans():
    """获取待确认的计划，最新的排在最前。

    排序必须带 `id DESC` 作为第二关键字：created_at 由 SQLite 的 CURRENT_TIMESTAMP 写入，
    只有**秒**精度。同一秒内插入两条（改一道菜后立刻再改一次，或生成完马上应用改单）时，
    只按 created_at 排序的结果是不确定的 —— 调用方拿到的"最新一份"可能是先插入的那条，
    于是下发到设备上的菜单不是用户刚确认的。id 单调递增，是同秒内唯一可靠的先后判据。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM meal_plan WHERE status = 'pending' "
        "ORDER BY created_at DESC, id DESC"
    )
    rows = cursor.fetchall()
    conn.close()

    plans = []
    for row in rows:
        plan = dict(row)
        plan['plan_a'] = json.loads(plan['plan_a']) if plan['plan_a'] else None
        plan['plan_b'] = json.loads(plan['plan_b']) if plan['plan_b'] else None
        plans.append(plan)
    return plans


def get_recent_meal_plans(within_days: int = 7, limit: int = 60) -> List[Dict]:
    """读取近期方案，供新推荐避开重复菜名。"""
    cutoff = (datetime.utcnow() - timedelta(days=max(1, within_days))).strftime(
        '%Y-%m-%d %H:%M:%S'
    )
    conn = get_connection()
    try:
        rows = conn.execute(
            '''SELECT id, meal_type, plan_a, plan_b, selected_plan, created_at
               FROM meal_plan
               WHERE created_at >= ?
                  OR id IN (SELECT MAX(id) FROM meal_plan GROUP BY meal_type)
               ORDER BY created_at DESC, id DESC LIMIT ?''',
            (cutoff, max(1, min(limit, 200))),
        ).fetchall()
    finally:
        conn.close()

    def load(raw):
        try:
            return json.loads(raw) if raw else None
        except (TypeError, json.JSONDecodeError):
            return None

    return [{
        'id': row[0],
        'meal_type': row[1],
        'plan_a': load(row[2]),
        'plan_b': load(row[3]),
        'selected_plan': row[4],
        'created_at': row[5],
    } for row in rows]


# ==================== 用餐时间配置 ====================

def set_meal_schedule(meal_type, meal_time):
    """设置用餐时间"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO meal_schedule (meal_type, meal_time)
        VALUES (?, ?)
    ''', (meal_type, meal_time))
    conn.commit()
    conn.close()


def get_meal_schedule():
    """获取用餐时间配置"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM meal_schedule WHERE enabled = 1')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ==================== 对话历史 ====================

def add_conversation(session_id, role, content, action=None):
    """添加对话记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversation_history (session_id, role, content, action)
        VALUES (?, ?, ?, ?)
    ''', (session_id, role, content, action))
    conn.commit()
    conn.close()


def get_recent_conversations(session_id, limit=10):
    """获取最近的对话"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM conversation_history
        WHERE session_id = ?
        ORDER BY created_at DESC LIMIT ?
    ''', (session_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


# ==================== 营养数据操作 ====================

def import_nutrition_data(json_path):
    """
    从JSON文件导入营养数据

    Args:
        json_path: JSON文件路径

    Returns:
        int: 导入的记录数
    """
    conn = get_connection()
    cursor = conn.cursor()

    with open(json_path, 'r', encoding='utf-8') as f:
        foods = json.load(f)

    imported = 0
    for food in foods:
        try:
            # 提取分类（从foodCode推断或使用默认）
            food_name = food.get('foodName', '')
            food_code = food.get('foodCode', '')

            # 解析数值，处理 "—" 和 "Tr" 等非数字值
            def parse_num(val):
                if val is None or val == '—' or val == 'Tr' or val == '':
                    return None
                try:
                    return float(val)
                except Exception:
                    return None

            cursor.execute('''
                INSERT OR IGNORE INTO nutrition_data
                (food_code, food_name, edible, water, energy_kcal, energy_kj,
                 protein, fat, cho, dietary_fiber, cholesterol,
                 vitamin_a, vitamin_c, ca, fe, zn)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                food_code,
                food_name,
                parse_num(food.get('edible')),
                parse_num(food.get('water')),
                parse_num(food.get('energyKCal')),
                parse_num(food.get('energyKJ')),
                parse_num(food.get('protein')),
                parse_num(food.get('fat')),
                parse_num(food.get('CHO')),
                parse_num(food.get('dietaryFiber')),
                parse_num(food.get('cholesterol')),
                parse_num(food.get('vitaminA')),
                parse_num(food.get('vitaminC')),
                parse_num(food.get('Ca')),
                parse_num(food.get('Fe')),
                parse_num(food.get('Zn'))
            ))
            imported += 1
        except Exception as e:
            logger.warning('营养数据导入失败：food=%s error=%s',
                           food.get('foodName', '未知'), e)

    conn.commit()
    conn.close()
    return imported


def search_nutrition(keyword, limit=5):
    """
    搜索营养数据

    Args:
        keyword: 搜索关键词
        limit: 返回数量

    Returns:
        list: 匹配的营养数据
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM nutrition_data
        WHERE food_name LIKE ?
        ORDER BY
            CASE WHEN food_name = ? THEN 0
                 WHEN food_name LIKE ? THEN 1
                 ELSE 2 END,
            food_name
        LIMIT ?
    ''', (f'%{keyword}%', keyword, f'{keyword}%', limit))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_nutrition_by_field(order_by='protein', order='DESC', limit=5, exclude_null=True):
    """
    按营养字段排序查询

    Args:
        order_by: 排序字段 (protein/fat/energy_kcal/fe/ca/vitamin_a/vitamin_c/dietary_fiber/cholesterol/cho)
        order: 排序方向 (ASC/DESC)
        limit: 返回数量
        exclude_null: 是否排除空值

    Returns:
        list: 营养数据列表
    """
    # 允许的排序字段（防止 SQL 注入）
    allowed_fields = {
        'protein', 'fat', 'energy_kcal', 'energy_kj', 'fe', 'ca',
        'vitamin_a', 'vitamin_c', 'dietary_fiber', 'cholesterol', 'cho',
        'water', 'edible'
    }

    if order_by not in allowed_fields:
        raise ValueError(f"不允许的排序字段: {order_by}")

    if order not in ('ASC', 'DESC'):
        raise ValueError("排序方向必须是 ASC 或 DESC")

    conn = get_connection()
    cursor = conn.cursor()

    # 构建查询
    where_clause = f"WHERE {order_by} IS NOT NULL" if exclude_null else ""
    sql = f'''
        SELECT food_name, {order_by}, energy_kcal, protein, fat, cho
        FROM nutrition_data
        {where_clause}
        ORDER BY {order_by} {order}
        LIMIT ?
    '''

    cursor.execute(sql, (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_inventory_by_category(category):
    """
    按分类获取库存

    Args:
        category: 分类名称

    Returns:
        list: 库存项列表
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM fridge_inventory
        WHERE category = ? AND (quantity > 0 OR COALESCE(weight_grams, 0) > 0)
        ORDER BY expiry_date
    ''', (category,))

    rows = cursor.fetchall()
    conn.close()

    items = []
    for row in rows:
        item = dict(row)
        if item.get('expiry_date'):
            try:
                from datetime import datetime
                exp = datetime.strptime(item['expiry_date'], '%Y-%m-%d')
                item['days_left'] = (exp - datetime.now()).days
            except Exception:
                item['days_left'] = None
        items.append(item)

    return items


def get_nutrition_by_name(food_name):
    """
    精确获取营养数据

    Args:
        food_name: 食物名称

    Returns:
        dict: 营养数据或None
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 先精确匹配
    cursor.execute('SELECT * FROM nutrition_data WHERE food_name = ?', (food_name,))
    row = cursor.fetchone()

    if not row:
        # 模糊匹配
        cursor.execute('SELECT * FROM nutrition_data WHERE food_name LIKE ? LIMIT 1', (f'%{food_name}%',))
        row = cursor.fetchone()

    conn.close()
    return dict(row) if row else None


def get_nutrition_summary(food_name):
    """
    获取食物营养摘要（用于Prompt）

    Args:
        food_name: 食物名称

    Returns:
        str: 营养摘要文本
    """
    data = get_nutrition_by_name(food_name)
    if not data:
        return None

    parts = [data['food_name']]
    if data.get('energy_kcal'):
        parts.append(f"{data['energy_kcal']}kcal/100g")
    if data.get('protein'):
        parts.append(f"蛋白质{data['protein']}g")
    if data.get('fat'):
        parts.append(f"脂肪{data['fat']}g")
    if data.get('cho'):
        parts.append(f"碳水{data['cho']}g")

    return "，".join(parts)


def get_nutrition_count():
    """获取营养数据总数"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM nutrition_data')
    count = cursor.fetchone()[0]
    conn.close()
    return count


if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    init_db()
    print("数据库初始化完成")
