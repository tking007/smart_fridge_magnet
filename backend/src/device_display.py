"""
设备显示模块 — .film 文件管理、版本控制、manifest 生成

跨端契约：
- POST /api/devices/{device_id}/display       → upload_film()
- GET  /api/devices/{device_id}/display/manifest → get_manifest()
- GET  /api/devices/{device_id}/display/{version}.film → download_film()
- GET  /api/devices/{device_id}/display/{version}/{name}.film → download_film_by_name()
- GET  /api/devices/{device_id}/sync-status    → get_sync_status()

一次发布一套四张（today / breakfast / lunch / dinner）：固件 service_film.c 按文件名
自动切换画面，不依赖 BLE。后端负责让一个版本携带多张图并在 manifest 的 items[] 里
全部列出。
"""

import os
import secrets
import shutil
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from . import config
from .logger import get_logger
logger = get_logger('device_display')

def film_storage() -> str:
    """`.film` 存储根目录，经 config.film_dir() 读取（镜像内指向 VOLUME 下的 /data/devices）。

    用函数而非模块级常量：与 config.py 的取值方式一致，测试可在 import 后改环境变量。
    """
    return config.film_dir()

# 智能冰箱贴六色电子纸规格。与固件 film_validate.h 的常量一一对应，跨端契约以
# docs/contracts/README.md 为准。任何一项不符，固件都会拒收整份文件并保留旧画面。
FILM_TOTAL_SIZE = 209120        # 文件总长 = 32 头 + 209088 像素
FILM_HEADER_SIZE = 32
FILM_PIXEL_BYTES = 209088       # 792×528 @ 4bit/px
FILM_WIDTH = 792
FILM_HEIGHT = 528
FILM_COLOR_COUNT = 6
FILM_COLOR_TABLE_OFFSET = 0x10  # 调色板在文件头中的偏移
# 固件 hal_epd.c color_lut 能识别的真实设备色值：黑/白/黄/红/蓝/绿。
# 文件头的 colorTable 写的是这些值，而【像素半字节写的是索引 0..5】——两者不可混淆。
FILM_VALID_COLOR_CODES = frozenset({0x00, 0xFF, 0xFC, 0xE0, 0x03, 0x1C})

# 固件 manifest_parse.c 的响应上限：超过这个字节数固件**整份拒收** manifest，
# 设备既拿不到新版本也说不出原因。见 get_manifest() 的兜底逻辑。
MANIFEST_MAX_RESPONSE_BYTES = 2048

_TOKEN_HASH_PREFIX = 'sha256:'

# 「单张」槽位名。固件走 Wi-Fi 单张路径时把下载到的文件固定落盘为 current.film
# （service_wifi.c），历史记录用 'current' 表示这一语义。
LEGACY_SLOT = 'current'
LEGACY_SLOT_FILENAME = 'current.film'


def slot_filenames() -> dict:
    """槽位 -> 目标文件名（跨端契约，定义在 menu_render.SLOT_FILENAMES）。

    延迟导入而非顶部 import：menu_render 会拉起 PIL 与字体栈，而 device_display 处在
    纯数据路径上（调度器、CLI 脚本、只读接口都会导入它），顶部导入会让这些路径在
    缺字体/缺 PIL 时连库都打不开。映射不在本文件另抄一份，避免两处漂移。
    """
    from .menu_render import SLOT_FILENAMES
    return SLOT_FILENAMES


def get_connection():
    """获取数据库连接"""
    from .database import get_connection as _get_conn
    return _get_conn()


def _migrate_device_display_slot(conn) -> None:
    """给老库的 device_display 加上 slot 列，并把唯一约束换成 (device_id, version, slot)。

    必须整表重建（建新表 → 搬数据 → 删旧表 → 改名）：SQLite 的 ALTER TABLE 只支持
    RENAME / ADD COLUMN / DROP COLUMN，**无法删除或修改既有的 UNIQUE 约束**，而老库上的
    UNIQUE(device_id, version) 会让「同一版本插入第二张图」撞唯一约束。

    历史行标成 'current'：与固件单张路径的实际落盘名一致（见 LEGACY_SLOT），
    manifest 沿用旧的下载地址，历史数据的对外行为不变。

    整个迁移在一个事务里；失败必须回滚**并把异常抛出去**。静默吞掉的话表还是老 schema，
    后续每次 upload_film_set 的第二行插入都会撞唯一约束，报错现场离成因很远。
    """
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute('PRAGMA table_info(device_display)')}
    if not columns or 'slot' in columns:
        return      # 表不存在（不该发生，上面刚建）或已是新 schema

    logger.info("device_display migration: rebuilding table to add slot column")
    try:
        # 只在还没有事务时显式开启。sqlite3 模块对 DDL 不会隐式 BEGIN（Python 3.6+），
        # 所以这里通常需要自己开；但在已有事务的情况下再 BEGIN 会抛
        # "cannot start a transaction within a transaction"，那会让建表流程整体失败
        # —— 判一下 in_transaction 就能既保证原子性又不挑调用时机。
        if not conn.in_transaction:
            cursor.execute('BEGIN IMMEDIATE')
        # 上一次迁移中途失败可能留下残表，先清掉再建，避免 CREATE 撞名
        cursor.execute('DROP TABLE IF EXISTS device_display_new')
        cursor.execute('''
            CREATE TABLE device_display_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                slot TEXT NOT NULL DEFAULT 'current',
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                uploaded_by TEXT DEFAULT 'android',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, version, slot)
            )
        ''')
        # 显式列出列名：老库的列顺序与新表不同（slot 插在中间），SELECT * 会串列。
        # created_at 照搬，不能刷成迁移时间 —— has_recent_upload() 的定时下发去重按它判。
        cursor.execute('''
            INSERT INTO device_display_new
                (device_id, version, file_path, file_size, sha256, uploaded_by, created_at, slot)
            SELECT device_id, version, file_path, file_size, sha256, uploaded_by, created_at, 'current'
            FROM device_display
        ''')
        migrated = cursor.rowcount
        cursor.execute('DROP TABLE device_display')
        cursor.execute('ALTER TABLE device_display_new RENAME TO device_display')
        conn.commit()
        logger.info(
            "device_display migration done: %d row(s) carried over as slot='current'",
            migrated
        )
    except Exception as exc:
        conn.rollback()
        # 不吞异常：吞掉会让表停留在老 schema，症状（插入撞唯一约束）离成因很远
        logger.error("device_display migration FAILED, rolled back: %s", exc)
        raise


def init_device_tables():
    """初始化设备相关表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 设备表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL UNIQUE,
            device_name TEXT DEFAULT '',
            device_token TEXT,
            wifi_enabled INTEGER DEFAULT 0,
            last_seen TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 设备状态列（电量 / 固件版本 / 状态上报时间）。
    # 用 ALTER TABLE 而不是只改上面的 CREATE：CREATE TABLE IF NOT EXISTS 对**已存在**的
    # 库不生效，光改建表语句会导致老库缺列、线上直接报 no such column。
    for column, ddl in (
        ('battery_level', 'ALTER TABLE devices ADD COLUMN battery_level INTEGER'),
        ('firmware_version', 'ALTER TABLE devices ADD COLUMN firmware_version TEXT'),
        ('status_reported_at', 'ALTER TABLE devices ADD COLUMN status_reported_at TIMESTAMP'),
        ('applied_version', 'ALTER TABLE devices ADD COLUMN applied_version INTEGER'),
        ('sleep_mode', 'ALTER TABLE devices ADD COLUMN sleep_mode INTEGER'),
        ('wake_slots', 'ALTER TABLE devices ADD COLUMN wake_slots TEXT'),
        ('wake_fallback_minutes',
         'ALTER TABLE devices ADD COLUMN wake_fallback_minutes INTEGER'),
        ('poll_interval_seconds',
         'ALTER TABLE devices ADD COLUMN poll_interval_seconds INTEGER'),
        # force_next：下一次 manifest 请求是否带 force=true。用于 BLE 0x45 清空设备文件后
        # 解围 —— 那时设备的 last_applied_version 与云端相同，版本门控会判「已是最新」。
        ('force_next', 'ALTER TABLE devices ADD COLUMN force_next INTEGER DEFAULT 0'),
        # layout：这台设备的画面版式偏好（menu_render.LAYOUTS 的键）。存在设备上而不是
        # 全局配置里 —— 贴在冰箱正面近看的和贴在侧面竖着的，合适的字号与方向不同。
        ("layout", "ALTER TABLE devices ADD COLUMN layout TEXT DEFAULT 'classic'"),
        ("layout_options", "ALTER TABLE devices ADD COLUMN layout_options TEXT DEFAULT '{}'"),
    ):
        try:
            cursor.execute(ddl)
            logger.info(f"devices table migrated: added column {column}")
        except sqlite3.OperationalError:
            pass    # 列已存在，属正常情况

    # 设备显示版本表。
    #
    # slot 表示这张图在设备上的槽位：'today' / 'breakfast' / 'lunch' / 'dinner'（一套四张），
    # 或 'current'（单张发布）。唯一约束因此是 (device_id, version, slot)：一次发布产生
    # **一个版本号、多行记录**。版本号只能有一个，因为固件的版本门控就是一个整数比较
    # （version > last_applied || force），四张图必须作为一个原子的「一套」被应用。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_display (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            slot TEXT NOT NULL DEFAULT 'current',
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_by TEXT DEFAULT 'android',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(device_id, version, slot)
        )
    ''')

    # 老库迁移：CREATE TABLE IF NOT EXISTS 对已存在的表完全不生效，
    # 光改上面的建表语句只能让**全新库**到位，老库仍是 UNIQUE(device_id, version)。
    _migrate_device_display_slot(conn)

    # 设备同步状态表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            version INTEGER,
            detail TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 索引
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_display_device_version
        ON device_display(device_id, version DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_sync_log_device
        ON device_sync_log(device_id, created_at DESC)
    ''')

    conn.commit()
    conn.close()
    logger.info("Device display tables initialized")


def _ensure_device_dir(device_id: str) -> str:
    """确保设备目录存在"""
    device_dir = os.path.join(film_storage(), device_id)
    os.makedirs(device_dir, exist_ok=True)
    return device_dir


def _resolve_film_path(stored_path: str, device_id: str, version: int) -> str:
    """把库里记录的 file_path 解析成当前进程可读的绝对路径。

    新记录存的是相对 film_storage() 的路径（如 `dev001/3.film`），换挂载点或迁移数据
    目录时历史记录不会集体失效；但库里可能仍有存着**绝对路径**的旧记录。解析顺序：
      1) 原样可读        -> 直接用（兼容旧的绝对路径记录）
      2) 当作相对路径拼  -> film_storage()/<stored_path>
      3) 按约定重建      -> film_storage()/<device_id>/<version>.film
    第 3 步兜底：文件按约定躺在存储目录里时，即使库里路径已过时也能找回。
    """
    # 只有绝对路径才直接采信。相对路径不能走这一步：os.path.exists() 会按**当前工作
    # 目录**解析，而 gunicorn / 测试 / CLI 的 cwd 各不相同，可能命中存储目录之外的
    # 同名文件，把错误的字节发给设备。
    if stored_path and os.path.isabs(stored_path) and os.path.exists(stored_path):
        return stored_path

    if stored_path:
        candidate = os.path.join(film_storage(), stored_path)
        if os.path.exists(candidate):
            return candidate

    return os.path.join(film_storage(), device_id, f"{version}.film")


def _calc_sha256(file_path: str) -> str:
    """计算文件 SHA-256"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def _validate_film_header(data: bytes) -> tuple:
    """校验 .film 是否满足固件 film_validate() 的全部结构约束。

    每一条都对应固件 `film_validate.c` 的一个拒收分支，顺序也与之一致。必须逐条对齐：
    固件拒收时会**保留旧画面**，真机表现是「上传成功、设备毫无反应」，只能接串口定位。
    后端在入库前拦下来，把这类问题提前到 HTTP 400。

    返回: (is_valid, error_message)
    """
    if len(data) != FILM_TOTAL_SIZE:
        return False, f"文件长度错误: {len(data)} != {FILM_TOTAL_SIZE}"

    # [0:4] 像素数据长度（小端 u32）。注意这里是**像素长度**不是文件总长。
    pixel_len = int.from_bytes(data[0:4], 'little')
    if pixel_len != FILM_PIXEL_BYTES:
        return False, f"像素数据长度错误: {pixel_len} != {FILM_PIXEL_BYTES}"

    # [4:6] / [6:8] 画布宽高（小端 u16）
    width = int.from_bytes(data[4:6], 'little')
    height = int.from_bytes(data[6:8], 'little')
    if width != FILM_WIDTH or height != FILM_HEIGHT:
        return False, f"尺寸错误: {width}x{height} != {FILM_WIDTH}x{FILM_HEIGHT}"

    # [8] 颜色数
    color_count = data[8]
    if color_count != FILM_COLOR_COUNT:
        return False, f"颜色数错误: {color_count} != {FILM_COLOR_COUNT}"

    # [16:16+color_count] 调色板必须是固件 color_lut 能识别的【真实设备色值】。
    # 写成索引 0..5 是最隐蔽的错误：固件取色是 color_lut[colorTable[nibble]] 两级间接，
    # 索引会落到未映射项而整屏错色，故固件直接拒收。
    for slot in range(color_count):
        value = data[FILM_COLOR_TABLE_OFFSET + slot]
        if value not in FILM_VALID_COLOR_CODES:
            return False, (
                f"调色板槽 {slot} 非法色值 0x{value:02X}，"
                f"只允许 {sorted(hex(v) for v in FILM_VALID_COLOR_CODES)}"
            )

    # 像素半字节必须是调色板索引 0..5，任一 nibble >= color_count 即被固件整份拒收。
    # 先用 set() 把出现过的字节值收敛到至多 256 个（C 实现，一次线性扫描），再检查
    # 去重后各值的高低 nibble，避免 20 万次 Python 循环。
    for byte_value in set(data[FILM_HEADER_SIZE:]):
        if (byte_value >> 4) >= color_count or (byte_value & 0x0F) >= color_count:
            return False, (
                f"存在越界调色板索引：字节 0x{byte_value:02X} 的半字节 "
                f"({byte_value >> 4}, {byte_value & 0x0F}) 超出 0..{color_count - 1}"
            )

    return True, ""


def upload_film(device_id: str, film_data: bytes, uploaded_by: str = 'android') -> dict:
    """
    上传 .film 文件

    Args:
        device_id: 设备 ID
        film_data: .film 文件二进制数据
        uploaded_by: 上传来源 ('android' / 'manual')

    Returns:
        {"success": True, "version": 17, ...} 或 {"success": False, "error": "..."}
    """
    # 验证文件大小
    if len(film_data) != FILM_TOTAL_SIZE:
        return {
            "success": False,
            "error": f"文件大小错误: {len(film_data)} != {FILM_TOTAL_SIZE}"
        }

    # 验证文件头
    is_valid, error_msg = _validate_film_header(film_data)
    if not is_valid:
        return {"success": False, "error": f"文件头验证失败: {error_msg}"}

    sha256 = hashlib.sha256(film_data).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 确保设备存在
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, datetime.now().isoformat())
        )
        cursor.execute(
            'UPDATE devices SET last_seen = ? WHERE device_id = ?',
            (datetime.now().isoformat(), device_id)
        )

        cursor.execute(
            'SELECT MAX(version) FROM device_display WHERE device_id = ?',
            (device_id,)
        )
        row = cursor.fetchone()
        current_version = row[0] if row[0] else 0
        new_version = current_version + 1

        device_dir = _ensure_device_dir(device_id)
        file_name = f"{new_version}.film"
        file_path = os.path.join(device_dir, file_name)

        with open(file_path, 'wb') as f:
            f.write(film_data)

        # 记录版本。file_path 存【相对 film_storage() 的路径】而非绝对路径：
        # 绝对路径一旦更换挂载点（例如从 /app/data/devices 迁到 /data/devices）
        # 就会让所有历史记录同时失效，而这类迁移恰恰是部署阶段的常规操作。
        # 读取侧由 _resolve_film_path() 负责兼容旧的绝对路径记录。
        rel_path = os.path.join(device_id, file_name)
        cursor.execute('''
            INSERT INTO device_display (device_id, version, file_path, file_size, sha256, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (device_id, new_version, rel_path, len(film_data), sha256, uploaded_by))

        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, ?, ?, ?)
        ''', (device_id, 'upload', new_version, f"uploaded by {uploaded_by}, sha256={sha256[:16]}..."))

        conn.commit()

        logger.info(f"Film uploaded: device={device_id}, version={new_version}, sha256={sha256[:16]}...")

        return {
            "success": True,
            "version": new_version,
            "size": len(film_data),
            "sha256": sha256,
            "device_id": device_id
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Upload failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def upload_film_set(device_id: str, items: list, uploaded_by: str = 'backend') -> dict:
    """一次发布一套画面（today / breakfast / lunch / dinner），共享同一个版本号。

    与 upload_film() 并存而不是替换它：后者是 App 上传单张的既有通道。

    Args:
        device_id: 设备 ID
        items: [{'slot': 'today'|'breakfast'|'lunch'|'dinner', 'data': bytes}, ...]
               至少一项、slot 不重复、且必须是 SLOT_FILENAMES 里的已知槽位。
               未知 slot 直接失败不做猜测：slot 决定落盘文件名，文件名决定固件按时段
               显示哪一张，猜错会让设备整天显示错图而无处报错。
        uploaded_by: 上传来源标记

    Returns:
        {"success": True, "version": n, "items": [{"slot","name","size","sha256"}], "device_id": ...}
        或 {"success": False, "error": "..."}

    落盘布局 `<film_storage>/<device_id>/<version>/<文件名>`：一版一目录，
    与单张发布的 `<device_id>/<version>.film` 不冲突，也便于整版删除。
    """
    names = slot_filenames()

    if not items:
        return {"success": False, "error": "items 为空，至少需要一张画面"}

    # 先把全套字节校验完，再动文件系统：固件对不合规文件是「拒收并保留旧画面」，
    # 而一套图只落一半会让设备在时段切换时找不到文件。绝不留半套。
    prepared = []
    seen = set()
    for index, item in enumerate(items):
        # 类型先兜住：调用方传成 [bytes, ...] 时直接 .get 会抛 AttributeError，
        # 而本函数的契约是「失败也返回 {"success": False}」。
        if not isinstance(item, dict):
            return {"success": False, "error": f"items[{index}] 必须是 dict，实际 {type(item).__name__}"}
        slot = (item.get('slot') or '').strip().lower()
        data = item.get('data')

        if slot not in names:
            return {
                "success": False,
                "error": f"items[{index}] 未知 slot: {slot!r}（可选 {'/'.join(sorted(names))}）"
            }
        if slot in seen:
            return {"success": False, "error": f"items[{index}] slot 重复: {slot}"}
        seen.add(slot)

        if not isinstance(data, (bytes, bytearray)):
            return {"success": False, "error": f"items[{index}] ({slot}) data 必须是 bytes"}
        data = bytes(data)

        if len(data) != FILM_TOTAL_SIZE:
            return {
                "success": False,
                "error": f"items[{index}] ({slot}) 文件大小错误: {len(data)} != {FILM_TOTAL_SIZE}"
            }
        is_valid, error_msg = _validate_film_header(data)
        if not is_valid:
            return {
                "success": False,
                "error": f"items[{index}] ({slot}) 文件头验证失败: {error_msg}"
            }

        prepared.append({
            'slot': slot,
            'name': names[slot],
            'data': data,
            'sha256': hashlib.sha256(data).hexdigest(),
        })

    conn = get_connection()
    cursor = conn.cursor()
    version_dir = None

    try:
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, datetime.now().isoformat())
        )
        cursor.execute(
            'UPDATE devices SET last_seen = ? WHERE device_id = ?',
            (datetime.now().isoformat(), device_id)
        )

        # 一次发布只产生**一个**新版本号：固件的版本门控是单个整数比较，
        # 四张图各占一个版本会让设备连着醒四次、刷四次屏，而且中间状态是半套图。
        cursor.execute(
            'SELECT MAX(version) FROM device_display WHERE device_id = ?', (device_id,)
        )
        row = cursor.fetchone()
        new_version = (row[0] or 0) + 1

        version_dir = os.path.join(film_storage(), device_id, str(new_version))
        os.makedirs(version_dir, exist_ok=True)

        for entry in prepared:
            with open(os.path.join(version_dir, entry['name']), 'wb') as f:
                f.write(entry['data'])

            # file_path 存**相对 film_storage() 的路径**，与 upload_film() 的约定一致：
            # 绝对路径一旦更换挂载点就让所有历史记录同时失效。
            rel_path = os.path.join(device_id, str(new_version), entry['name'])
            cursor.execute('''
                INSERT INTO device_display
                    (device_id, version, slot, file_path, file_size, sha256, uploaded_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (device_id, new_version, entry['slot'], rel_path,
                  len(entry['data']), entry['sha256'], uploaded_by))

        slot_list = ', '.join(f"{e['slot']}->{e['name']}" for e in prepared)
        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, ?, ?, ?)
        ''', (device_id, 'upload_set', new_version,
              f"{len(prepared)} slot(s) by {uploaded_by}: {slot_list}"))

        conn.commit()
        logger.info(
            "Film set uploaded: device=%s version=%d slots=%s",
            device_id, new_version, [e['slot'] for e in prepared]
        )

        return {
            "success": True,
            "version": new_version,
            "items": [
                {"slot": e['slot'], "name": e['name'],
                 "size": len(e['data']), "sha256": e['sha256']}
                for e in prepared
            ],
            "device_id": device_id,
        }

    except Exception as e:
        conn.rollback()
        # 落盘阶段失败时把这一版的目录整体清掉：库里已回滚，留着半套文件只会让
        # 下次同版本号（MAX(version)+1 会重新算到同一个数）读到上一次的残留字节。
        if version_dir and os.path.isdir(version_dir):
            shutil.rmtree(version_dir, ignore_errors=True)
        logger.error(f"Upload set failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def has_recent_upload(device_id: str, uploaded_by: str, within_hours: int = 6) -> bool:
    """最近 within_hours 小时内，该设备是否已有来自 uploaded_by 的上传。

    给定时下发做幂等用：调度器是「每 5 分钟扫一次、命中窗口就发」，没有去重就会在
    一个餐前窗口里重复发布六七个版本，设备每次都要唤醒下载 209120 字节并刷屏。

    去重口径刻意用【时间窗】而不是【日历天】：device_display.created_at 由 SQLite
    的 CURRENT_TIMESTAMP 写入，是 **UTC**；而餐点判断用的是本地时间。在 +0800 下，
    早餐 07:30 对应的 UTC 是前一天 23:30，按本地日期去比就会判成「昨天的记录」，
    去重直接失效 —— 而这恰好只在早餐这一档出问题，最难被发现。

    另一个好处是重启不失忆：判据落在库里，而不是进程内存里的执行历史。
    """
    cutoff = (datetime.utcnow() - timedelta(hours=max(1, within_hours))).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT 1 FROM device_display
            WHERE device_id = ? AND uploaded_by = ? AND created_at >= ?
            LIMIT 1
        ''', (device_id, uploaded_by, cutoff))
        return cursor.fetchone() is not None
    finally:
        conn.close()


def record_device_status(device_id: str, headers=None, battery=None, firmware=None) -> None:
    """记录设备随 manifest 请求上报的非机密状态。

    设备在拉取 manifest 时通过请求头 X-Device-Battery / X-Device-Firmware 顺带上报，
    复用那次本就会发生的网络往来，不新增接口也不额外唤醒设备。

    只接受非机密信息；任何缺失或非法值都静默忽略，不影响 manifest 正常返回
    （旧固件不带这些头，必须照常工作）。
    """
    headers = headers or {}
    battery = headers.get('X-Device-Battery', battery)
    firmware = headers.get('X-Device-Firmware', firmware)

    sets, params = [], []
    if battery is not None:
        try:
            value = int(battery)
        except (TypeError, ValueError):
            value = None
        if value is not None and 0 <= value <= 100:
            sets.append('battery_level = ?')
            params.append(value)
    if firmware:
        sets.append('firmware_version = ?')
        params.append(str(firmware)[:64])

    def add_int(header, column, minimum, maximum):
        raw = headers.get(header)
        if raw is None:
            return
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return
        if minimum <= value <= maximum:
            sets.append(f'{column} = ?')
            params.append(value)

    add_int('X-Device-Applied-Version', 'applied_version', 0, 2_147_483_647)
    add_int('X-Device-Sleep-Mode', 'sleep_mode', 0, 1)
    add_int('X-Device-Wake-Fallback-Minutes', 'wake_fallback_minutes', 0, 65_535)
    add_int('X-Device-Poll-Interval-Seconds', 'poll_interval_seconds', 30, 86_400)

    raw_slots = headers.get('X-Device-Wake-Slots')
    if raw_slots is not None:
        try:
            if str(raw_slots).strip().lower() == 'none':
                slots = []
            else:
                slots = sorted({int(part.strip()) for part in str(raw_slots).split(',')})
                if len(slots) > 6 or any(slot < 0 or slot > 1439 for slot in slots):
                    raise ValueError
            sets.append('wake_slots = ?')
            params.append(json.dumps(slots, separators=(',', ':')))
        except (TypeError, ValueError):
            pass

    if not sets:
        return

    now = datetime.now().isoformat()
    sets.append('status_reported_at = ?')
    params.append(now)
    sets.append('last_seen = ?')
    params.append(now)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, now)
        )
        params.append(device_id)
        cursor.execute(
            f'UPDATE devices SET {", ".join(sets)} WHERE device_id = ?',
            params
        )
        conn.commit()
    finally:
        conn.close()


def get_device_status(device_id: str) -> dict:
    """读取设备状态（供 App 展示）。设备从未上报时各字段为 None。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT battery_level, firmware_version, status_reported_at, last_seen, '
            'applied_version, sleep_mode, wake_slots, wake_fallback_minutes, '
            'poll_interval_seconds '
            'FROM devices WHERE device_id = ?',
            (device_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {
                "battery_level": None, "firmware_version": None,
                "status_reported_at": None, "last_seen": None,
                "applied_version": None, "sleep_mode": None, "wake_slots": None,
                "wake_fallback_minutes": None, "poll_interval_seconds": None,
                "status_age_minutes": None,
            }
        reported_at = row[2]
        age_minutes = None
        if reported_at:
            try:
                age_minutes = max(
                    0, int((datetime.now() - datetime.fromisoformat(reported_at)).total_seconds() // 60)
                )
            except (TypeError, ValueError):
                pass
        try:
            wake_slots = json.loads(row[6]) if row[6] is not None else None
        except (TypeError, json.JSONDecodeError):
            wake_slots = None
        return {
            "battery_level": row[0],
            "firmware_version": row[1],
            "status_reported_at": reported_at,
            "last_seen": row[3],
            "applied_version": row[4],
            "sleep_mode": bool(row[5]) if row[5] is not None else None,
            "wake_slots": wake_slots,
            "wake_fallback_minutes": row[7],
            "poll_interval_seconds": row[8],
            "status_age_minutes": age_minutes,
        }
    finally:
        conn.close()


def _device_token_hash(token: str) -> str:
    """把高熵设备令牌转换为不可逆 verifier。"""
    return _TOKEN_HASH_PREFIX + hashlib.sha256(token.encode('utf-8')).hexdigest()


def _stored_token_matches(stored: str, token: str) -> bool:
    if not stored or not token:
        return False
    candidate = _device_token_hash(token) if stored.startswith(_TOKEN_HASH_PREFIX) else token
    return secrets.compare_digest(stored, candidate)


def device_token_registered(device_id: str) -> bool:
    """设备是否已登记令牌，不返回凭据或 verifier。"""
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT device_token FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def device_token_matches(device_id: str, token: str) -> bool:
    """恒时校验指定设备令牌，并把旧库中的明文 verifier 就地迁移为哈希。"""
    if not token:
        return False

    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT device_token FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()
        stored = str(row[0]) if row and row[0] else ''
        matched = _stored_token_matches(stored, token)
        if matched and not stored.startswith(_TOKEN_HASH_PREFIX):
            conn.execute(
                'UPDATE devices SET device_token = ? WHERE device_id = ?',
                (_device_token_hash(token), device_id),
            )
            conn.commit()
        return matched
    finally:
        conn.close()


def token_matches_any_device(token: str) -> str:
    """这个 token 属于哪台已登记的设备？不属于任何一台返回空串。

    只用于**所有设备共享**的资源（固件二进制）。固件不是按设备隔离的数据，
    要求「必须是某台已登记设备的令牌」既挡住了未授权访问，又不必让设备在
    URL 里带上自己的 id（那会让固件端多拼一次地址，且 id 并不参与授权判断）。

    比较用 secrets.compare_digest 逐个走完，不提前 return：按第一个不匹配的字节
    就返回会把「令牌前缀对了几位」变成可测量的时间差。
    """
    if not token:
        return ''

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT device_id, device_token FROM devices "
                       "WHERE device_token IS NOT NULL AND device_token != ''")
        rows = cursor.fetchall()
        matched = ''
        for row in rows:
            stored = str(row[1])
            if _stored_token_matches(stored, token):
                matched = str(row[0])
                if not stored.startswith(_TOKEN_HASH_PREFIX):
                    cursor.execute(
                        'UPDATE devices SET device_token = ? WHERE device_id = ?',
                        (_device_token_hash(token), matched),
                    )
        conn.commit()
        return matched
    finally:
        conn.close()


def set_device_token(device_id: str, token: str) -> dict:
    """登记或轮换设备的 device_token（设备不存在则创建）。

    该 token 需要通过 BLE opcode 0x42 写入设备（固件侧只写不可读）。
    """
    if len(token) < 32:
        return {"success": False, "error": "token 至少需要 32 个字符"}
    if len(token.encode('utf-8')) >= 128 or not token.isascii():
        return {"success": False, "error": "token 必须是不超过 127 字节的 ASCII"}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, datetime.now().isoformat())
        )
        cursor.execute(
            'UPDATE devices SET device_token = ? WHERE device_id = ?',
            (_device_token_hash(token), device_id)
        )
        conn.commit()
        # 不记录 token 明文
        logger.info(f"Device token set: device={device_id}")
        return {"success": True, "device_id": device_id}
    finally:
        conn.close()


def get_device_layout(device_id: str) -> str:
    """读取该设备的画面版式偏好；没有记录或字段为空时返回默认版式。

    回落而不是报错：版式只是展示偏好，取不到时用默认版式仍然能画出一张可读的画面，
    而让下发链路因为一个偏好值失败会导致屏幕整餐留空。

    延迟导入 menu_render 取默认值：本模块处在纯数据路径上，顶部 import 会把 PIL 与
    字体栈拉进只读接口和 CLI 脚本（见 slot_filenames() 的同类说明）。
    """
    from .menu_render import DEFAULT_LAYOUT

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT layout FROM devices WHERE device_id = ?', (device_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return DEFAULT_LAYOUT
        return str(row[0])
    finally:
        conn.close()


def set_device_layout(device_id: str, layout: str, options: dict) -> dict:
    """登记该设备的画面版式偏好与自定义参数（设备不存在则建档）。

    建档而不是报错，与 set_device_token() 一致：App 完全可能在设备第一次联网之前就
    让人选好版式（比如拆封时先在手机上配置），此时设备表里还没有这台设备。
    要求「先注册后设置」只会让这个正常顺序走不通。

    版式与参数一起写：两者共同决定一次渲染，分两条语句写会出现只落一半的中间态。
    取值的合法性由接口层校验：那里错的是调用方，需要立刻收到 400；本函数只负责写入。
    """
    layout = (layout or '').strip().lower()
    if not layout:
        return {"success": False, "error": "layout 不能为空"}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, datetime.now().isoformat())
        )
        cursor.execute(
            'UPDATE devices SET layout = ?, layout_options = ? WHERE device_id = ?',
            (layout, json.dumps(options or {}, ensure_ascii=False, sort_keys=True), device_id),
        )
        conn.commit()
        logger.info("Device layout set: device=%s layout=%s", device_id, layout)
        return {"success": True, "device_id": device_id, "layout": layout}
    finally:
        conn.close()


def get_device_layout_options(device_id: str) -> dict:
    """读取设备版式自定义参数；无记录或 JSON 损坏时返回空字典。"""
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT layout_options FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()
        if not row or not row[0]:
            return {}
        parsed = json.loads(row[0])
        return parsed if isinstance(parsed, dict) else {}
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        logger.warning('设备版式参数读取失败，回落默认值: device=%s %s', device_id, exc)
        return {}
    finally:
        conn.close()


def _resolve_film_base_url(request_base_url: str = '') -> str:
    """决定 manifest 里 film_url 用哪个基址。

    优先级：显式配置的 SFM_BASE_URL > 设备本次请求的来源地址 > 本机默认值。

    显式配置优先，是因为存在「下载域名与访问域名不同」的正当场景（CDN、专用下载域名）；
    但配置与请求来源不一致时一定要告警 —— 最典型的坑是配置里写着开发机的局域网 IP，
    而设备实际是从云平台入口进来的：manifest 能正常返回，设备却会去访问一个它根本
    到不了的地址，失败只在设备串口可见，从后端日志上完全看不出异常。
    """
    configured = config.configured_base_url()

    if configured:
        if request_base_url and request_base_url.rstrip('/') != configured.rstrip('/'):
            logger.warning(
                "film_url 用的是配置值 %s，但设备是从 %s 进来的。"
                "若设备访问不到配置里的地址，下载会失败（症状只在设备串口可见）——"
                "确认 SFM_BASE_URL 是否该改成设备实际使用的入口，或干脆留空由后端自适应。",
                configured, request_base_url,
            )
        base_url = configured
    elif request_base_url:
        base_url = request_base_url
        logger.info("SFM_BASE_URL 未配置，film_url 采用设备请求来源：%s", base_url)
    else:
        # 既没配置也拿不到请求来源（例如被非 HTTP 路径调用），只能回落默认值
        base_url = config.base_url()

    # 固件 manifest_parse.c 强制要求 film_url 为 https，配 http 时设备会直接拒绝整个
    # manifest，真机链路必然失败。这里只能告警不能修正 —— 换成 https 反而会掩盖
    # 「反向代理没配 TLS」这个真实问题。
    if not base_url.lower().startswith('https://'):
        logger.warning(
            "film_url 基址不是 https（%s）：固件强制要求 HTTPS，设备会拒绝该 manifest。"
            "请让设备经 https 入口访问（反向代理 / 云平台 Ingress / 隧道）。",
            base_url,
        )
    return base_url


def _slot_asset_name(slot: str) -> str:
    """槽位 -> 设备上的落盘文件名。

    未知槽位（含历史的 'current'）统一回落 current.film：那是旧固件走 Wi-Fi 时的固定
    落盘名，语义上就是「唯一的那张图」。
    """
    return slot_filenames().get(slot, LEGACY_SLOT_FILENAME)


def _slot_download_url(base_url: str, device_id: str, version: int, slot: str) -> str:
    """槽位 -> 下载地址。

    单张版本（slot='current'）用地址 `.../display/{version}.film`；一套四张用
    `.../display/{version}/{文件名}`。两者分开是因为断网重试队列里的设备可能仍持有
    前一种 URL。
    """
    names = slot_filenames()
    if slot not in names:
        return f"{base_url}/api/devices/{device_id}/display/{version}.film"
    return f"{base_url}/api/devices/{device_id}/display/{version}/{names[slot]}"


def _pick_primary_item(items: list) -> dict:
    """从一版的多张里挑出旧字段（film_url/size/sha256）该指向的那一张。

    优先 today（总览是唯一「单独看也完整」的画面）→ 其次 current（旧的单张发布）
    → 再不然取第一张。调用方已按 slot 排序，故第三档也是确定性的。
    """
    by_slot = {item["slot"]: item for item in items}
    return by_slot.get('today') or by_slot.get(LEGACY_SLOT) or items[0]


def set_force_next(device_id: str) -> dict:
    """置位「下一次 manifest 请求带 force=true」。

    固件的版本门控是 `manifest.version > last_applied_version || manifest.force`，
    所以在版本号没变的情况下，只有 force 能让设备重新下载并应用同一个版本。
    典型用途：设备文件被清空（BLE 0x45）或本地画面异常，需要重新拉一次当前版本。

    force 会保持到设备在后续 manifest 请求中回报已成功应用当前版本。仅仅把 manifest
    发出去不能证明下载、校验、落盘和上屏成功；过早清除会让一次网络失败永久吞掉修复。

    设备不存在时返回失败：force 一个从未注册过的设备没有意义，静默创建反而会掩盖
    device_id 写错这类问题（真机排查时最难发现的就是「参数写错但接口回成功」）。
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT 1 FROM devices WHERE device_id = ?', (device_id,))
        if not cursor.fetchone():
            return {"success": False, "error": f"设备 {device_id} 未注册"}

        cursor.execute(
            'UPDATE devices SET force_next = 1 WHERE device_id = ?', (device_id,)
        )
        conn.commit()
        logger.info(f"Force flag set for device={device_id}")
        return {"success": True, "device_id": device_id}
    finally:
        conn.close()


def acknowledge_applied_version(device_id: str, applied_version) -> bool:
    """设备确认已应用最新版本后清除 force；旧固件/非法值安全忽略。"""
    try:
        version = int(applied_version)
    except (TypeError, ValueError):
        return False
    if version < 0:
        return False

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Record each real version once. Devices send the header on every poll, so
        # blindly inserting here would turn the timeline into duplicate apply rows.
        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            SELECT ?, 'apply', ?, 'device reported display applied'
            WHERE ? > 0
              AND EXISTS (
                  SELECT 1 FROM device_display WHERE device_id = ? AND version = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM device_sync_log
                  WHERE device_id = ? AND event_type = 'apply' AND version = ?
              )
        ''', (device_id, version, version, device_id, version, device_id, version))
        apply_recorded = cursor.rowcount > 0

        # 最新版本判断与清标志必须在同一条写语句里。若先 SELECT 再 UPDATE，两个语句
        # 之间恰好发布新版本时，旧版本 ACK 会误清新版本的 force。
        cursor.execute('''
            UPDATE devices SET force_next = 0
            WHERE device_id = ? AND force_next != 0 AND ? = (
                SELECT MAX(version) FROM device_display WHERE device_id = ?
            )
        ''', (device_id, version, device_id))
        changed = cursor.rowcount > 0
        conn.commit()
        if apply_recorded:
            logger.info('Device apply acknowledged: device=%s version=%s', device_id, version)
        if changed:
            logger.info(
                "Manifest: force acknowledged for device=%s version=%s",
                device_id, version,
            )
        return changed
    finally:
        conn.close()


def record_display_result(device_id: str, version, ok: bool, detail: str = '') -> dict:
    """记录设备上报的「这一版有没有真的画上屏」。

    manifest 请求头 X-Device-Applied-Version 只能在**下一次**唤醒时带上来，而两次定时
    唤醒之间隔着好几个小时。在那段时间里后端只知道设备把文件取走了（download 事件），
    无法区分「已经画在屏上」和「落盘后刷新失败、屏幕还是旧的」。

    成功路径复用 acknowledge_applied_version()：清 force 的判定只应该有一处实现。
    失败路径写 apply_failed 事件。

    @param ok False 表示下载成功但上屏失败：**不清 force**，下次唤醒要重试。
    @return {'success': bool, 'event': 'apply'|'apply_failed', 'force_cleared': bool}
    """
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        return {'success': False, 'error': 'version 非法'}
    if version_int <= 0:
        return {'success': False, 'error': 'version 必须为正整数'}

    if ok:
        force_cleared = acknowledge_applied_version(device_id, version_int)
        return {'success': True, 'event': 'apply', 'force_cleared': force_cleared}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, 'apply_failed', ?, ?)
        ''', (device_id, version_int, (detail or 'device reported display refresh failed')[:200]))
        conn.commit()
    finally:
        conn.close()

    logger.warning('设备上报上屏失败：device=%s version=%s detail=%s',
                   device_id, version_int, detail or '未说明')
    return {'success': True, 'event': 'apply_failed', 'force_cleared': False}


def get_manifest(device_id: str, request_base_url: str = '') -> dict:
    """
    获取设备 manifest（ESP32 调用）

    @param request_base_url 设备本次请求所用的基址（由 app.py 从请求头推导后传入）。
        给了就优先用它拼 film_url。

    Returns:
        {"version": 17, "film_url": "...", "size": 209120, "sha256": "...", "force": false}
        或 None（无版本）

        一整套（today/breakfast/lunch/dinner）的版本另带 `items[]` 列出每一张；
        **单张发布（slot='current'）不带 items[]** —— 见下方 items 那一段的说明。

    film_url 优先用「请求来源地址」而不是配置的 SFM_BASE_URL：它的本质是「设备接下来
    要去哪里下载」，唯一正确取值是设备视角可达的地址，而部署环境里这个地址常不由后端
    决定（容器 IP 随机分配、入口可能是 Ingress / NodePort / LB）。设备能拉到 manifest
    就证明它用的地址是通的。

    SFM_BASE_URL 仍保留且**优先级更高**（CDN、自定义下载域名等场景必须能覆盖），
    但与请求来源不一致时打 warning —— 这种错配只在设备下载阶段才失败，串口才可见。
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            'SELECT MAX(version) FROM device_display WHERE device_id = ?', (device_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return None
        version = row[0]

        # 取该版本的**全部行**（一套四张就是四行）。按 slot 排序只为让输出确定性，
        # 便于 diff 与测试断言。
        cursor.execute('''
            SELECT slot, file_size, sha256
            FROM device_display
            WHERE device_id = ? AND version = ?
            ORDER BY slot
        ''', (device_id, version))
        rows = cursor.fetchall()

        # force 在设备确认成功应用当前版本前保持为 true。清除动作由
        # acknowledge_applied_version() 处理，不能在这里只因 manifest 已发送就消费。
        cursor.execute('SELECT force_next FROM devices WHERE device_id = ?', (device_id,))
        force_row = cursor.fetchone()
        force = bool(force_row[0]) if force_row and force_row[0] else False

        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, ?, ?, ?)
        ''', (device_id, 'manifest_query', version,
              'manifest requested (force)' if force else 'manifest requested'))

        conn.commit()

        base_url = _resolve_film_base_url(request_base_url)

        items = [
            {
                "slot": slot,
                "name": _slot_asset_name(slot),
                "url": _slot_download_url(base_url, device_id, version, slot),
                "size": file_size,
                "sha256": sha256,
            }
            for slot, file_size, sha256 in rows
        ]

        # 旧字段（film_url / size / sha256）挑哪一行：优先 today（总览），没有就 current，
        # 再没有就第一行。旧固件的 manifest_parse.c 只认这三个字段，所以它们必须指向
        # 「一张单独看也说得通的图」。
        primary = _pick_primary_item(items)

        manifest = {
            "version": version,
            "force": force,
            # 旧字段必须保留：老固件只解析这三个，新增的 items 会被它的解析器安全跳过
            # （未知键有界跳过，深度上限 8）。这是新旧固件混跑的唯一依据，不能删。
            "film_url": primary["url"],
            "size": primary["size"],
            "sha256": primary["sha256"],
        }

        # 单张发布（只有 current 槽位）不输出 items[]。
        #
        # items[] 的语义是「这一版包含一整套里的哪几张」，而 'current' 不是一套里的槽位，
        # 它是固件单张路径的固定落盘名（service_wifi.c 的 SFM_WIFI_FILM_FILENAME）。
        # 把它写进 items[] 会让新固件走整套下载路径，而那条路径的收尾动作是删掉
        # current.film（按契约属于单张路径的遗留文件）：版本号已推进 → 图被删 →
        # service_file_get_count() == 0 直接返回、电子纸保留旧像素 → 下一轮判「已是最新」，
        # 发布永久丢失且两端日志都显示成功。
        #
        # 单张版本因此回到 item_count == 0 的路径：新固件走
        # wifi_download_film_streaming()（落盘 current.film、service_file_display_received()
        # 上屏）。
        #
        # 判据是「槽位集合 ⊆ {current}」而不是「只有一行」：若出现「只发一张 today」的
        # 版本，它**应该**输出 items —— 那是一套的一部分，落盘名必须是 1_today.film
        # 才能让固件按时段选到它。
        slots_in_version = {slot for slot, _size, _sha in rows}
        if not slots_in_version.issubset({LEGACY_SLOT}):
            # 新固件优先用 items[]：一次拿到整套四张，按文件名落盘后
            # service_film.c 就能按时段自动切换画面。
            manifest["items"] = items

        # 字节预算兜底：固件 MANIFEST_MAX_RESPONSE_BYTES = 2048，**超限即整份拒收**，
        # 设备连版本号都读不到。降级成单张只是「只能更新总览那一张」，画面仍会更新，
        # 所以宁可丢 items 也不能丢整份 manifest。
        encoded_len = len(json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        if encoded_len > MANIFEST_MAX_RESPONSE_BYTES:
            logger.error(
                "manifest 序列化后 %d 字节 > 固件上限 %d，已降级为单张（去掉 items[]）："
                "超限时固件会拒收整份 manifest，设备将完全停止更新；降级后至少总览还能刷新。"
                "请检查 base_url 是否过长或槽位是否过多。device=%s version=%d",
                encoded_len, MANIFEST_MAX_RESPONSE_BYTES, device_id, version,
            )
            manifest.pop("items", None)

        # force 必须出现在这行里：设备本地版本与后端一致时固件会判「已是最新」而不下载，
        # 「后端发布了、设备却没动」有两种成因（版本没推进 / force 没置上），少了这个
        # 字段两者在日志里长得一样。
        logger.info(
            "Manifest: device=%s, version=%d, force=%s, slots=%s, bytes=%d",
            device_id, version, force, [i["slot"] for i in items], encoded_len
        )
        return manifest

    finally:
        conn.close()


def download_film(device_id: str, version: int, *, record_download: bool = True) -> tuple:
    """下载某个版本的「主图」（旧地址 `.../display/{version}.film` 用）。

    语义扩展：该版本若没有 current 行（一套四张的版本就没有），回落到 today 行。
    这样老固件按旧地址请求时能拿到总览 —— 它只会下载一张、并落盘成 current.film，
    总览是那种场景下唯一说得通的画面。

    Returns:
        (file_path, file_size, sha256) 或 None
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT slot, file_path, file_size, sha256
            FROM device_display
            WHERE device_id = ? AND version = ?
            ORDER BY slot
        ''', (device_id, version))

        rows = cursor.fetchall()
        if not rows:
            return None

        by_slot = {row[0]: row for row in rows}
        chosen = by_slot.get(LEGACY_SLOT) or by_slot.get('today') or rows[0]
        slot, stored_path, file_size, sha256 = chosen[0], chosen[1], chosen[2], chosen[3]
        file_path = _resolve_film_path(stored_path, device_id, version)

        if not os.path.exists(file_path):
            logger.error(
                f"Film file missing: stored={stored_path} resolved={file_path} "
                f"(film_storage={film_storage()})"
            )
            return None

        if record_download:
            cursor.execute('''
                INSERT INTO device_sync_log (device_id, event_type, version, detail)
                VALUES (?, ?, ?, ?)
            ''', (device_id, 'download', version, f"film downloaded (slot={slot})"))
            conn.commit()
            logger.info(f"Film download: device={device_id}, version={version}, slot={slot}")
        return (file_path, file_size, sha256)

    finally:
        conn.close()


def download_film_by_name(
    device_id: str,
    version: int,
    name: str,
    *,
    record_download: bool = True,
) -> tuple:
    """按文件名下载一套画面里的某一张（新地址 `.../display/{version}/{name}` 用）。

    以 name 而不是 slot 作为路径参数，是为了让 URL 的最后一段**就是设备的落盘名** ——
    固件下载后要按这个名字存到 SD 卡（service_film.c 靠文件名判断显示时段），
    URL 与落盘名一致能少一次映射，也让抓包时一眼看出这是哪张图。

    SQL 留在本模块而不是写进 app.py：路由层不该知道 device_display 的表结构。

    Returns:
        (file_path, file_size, sha256) 或 None（版本/文件名不存在，或文件已丢失）
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT slot, file_path, file_size, sha256
            FROM device_display
            WHERE device_id = ? AND version = ?
        ''', (device_id, version))

        target = None
        for slot, stored_path, file_size, sha256 in cursor.fetchall():
            if _slot_asset_name(slot) == name:
                target = (slot, stored_path, file_size, sha256)
                break

        if target is None:
            log = logger.warning if record_download else logger.info
            log(
                "Film not found by name: device=%s version=%s name=%s",
                device_id, version, name
            )
            return None

        slot, stored_path, file_size, sha256 = target
        file_path = _resolve_film_path(stored_path, device_id, version)
        if not os.path.exists(file_path):
            logger.error(
                f"Film file missing: stored={stored_path} resolved={file_path} "
                f"(film_storage={film_storage()})"
            )
            return None

        if record_download:
            cursor.execute('''
                INSERT INTO device_sync_log (device_id, event_type, version, detail)
                VALUES (?, ?, ?, ?)
            ''', (device_id, 'download', version,
                  f"film downloaded (slot={slot}, name={name})"))
            conn.commit()

            # 带上字节数：设备端下载不完整时后端这行仍然是「成功」，只有把预期字节数记下来，
            # 才能和固件串口里的实收字节对比 —— 否则「下载了但没上屏」无从判断断在哪一侧。
            logger.info(
                "Film download: device=%s, version=%d, slot=%s, name=%s, bytes=%d",
                device_id, version, slot, name, file_size
            )
        return (file_path, file_size, sha256)

    finally:
        conn.close()


def get_sync_status(device_id: str) -> dict:
    """
    获取设备同步状态

    Returns:
        {
            "device_id": "xxx",
            "latest_version": 17,
            "latest_upload_time": "2026-07-23T10:00:00",
            "latest_sha256": "...",
            "slots": ["today", "breakfast", "lunch", "dinner"],
            "last_manifest_query": "2026-07-23T10:05:00",
            "recent_events": [...]
        }

    latest_sha256 指向该版本的**主图**（总览优先，与 _pick_primary_item 同一口径），
    不是「该版本里碰巧排在前面的那一行」：一个版本可以有四行记录，按数据库返回顺序取
    会让同一次发布在不同查询下给出不同的 sha256，App 用它比对本地图与云端时会对不上。

    slots 列出这一版包含哪些槽位，App 由此区分「这一版是一整套还是单张」。

    `device_token_registered` 回答「这台设备现在能不能从后端下载画面」。必须暴露：未登记
    时设备每次拉 manifest 都被 401 挡住，而 App 这边发布成功、版本号自增、同步状态正常，
    屏幕却停在旧画面。
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            'SELECT device_token FROM devices WHERE device_id = ?', (device_id,)
        )
        token_row = cursor.fetchone()
        token_registered = bool(token_row and token_row[0])

        # 「这台设备最近一次联系后端是多久以前」——**设备已联网的直接证据**，比 BLE 回读的
        # Wi-Fi 状态更强（那只是设备自己的判断，且要先连上蓝牙才读得到）。
        placeholders = ','.join('?' * len(_DEVICE_CONTACT_EVENTS))
        cursor.execute(f'''
            SELECT created_at FROM device_sync_log
            WHERE device_id = ? AND event_type IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
        ''', (device_id, *_DEVICE_CONTACT_EVENTS))
        contact_row = cursor.fetchone()
        last_contact = contact_row[0] if contact_row else None
        last_contact_minutes = _minutes_since_utc(last_contact)

        # 事件列表在**判断有没有发布过画面之前**就取好：「设备来了但被 401 拒绝」和
        # 「设备来了但后端还没有任何画面」这两种最需要看事件的场景下都还没有已发布版本。
        cursor.execute('''
            SELECT event_type, version, detail, created_at
            FROM device_sync_log
            WHERE device_id = ?
            ORDER BY created_at DESC
            LIMIT 20
        ''', (device_id,))
        events = [
            {"event": row[0], "version": row[1], "detail": row[2], "time": row[3]}
            for row in cursor.fetchall()
        ]

        cursor.execute(
            'SELECT MAX(version) FROM device_display WHERE device_id = ?', (device_id,)
        )
        version_row = cursor.fetchone()
        if not version_row or not version_row[0]:
            return {
                "device_id": device_id,
                "latest_version": 0,
                "latest_upload_time": None,
                "latest_sha256": None,
                "slots": [],
                "slot_files": [],
                "last_manifest_query": None,
                "device_token_registered": token_registered,
                "device_last_contact": last_contact,
                "device_last_contact_minutes": last_contact_minutes,
                "recent_events": events
            }

        latest_version = version_row[0]

        # 取该版本的全部行（一套四张即四行）。按 slot 排序让结果与 get_manifest() 一致，
        # 也让 slots 的输出顺序稳定，便于 diff 与断言。
        cursor.execute('''
            SELECT slot, sha256, created_at
            FROM device_display
            WHERE device_id = ? AND version = ?
            ORDER BY slot
        ''', (device_id, latest_version))
        rows = cursor.fetchall()

        slots = [row[0] for row in rows]
        by_slot = {row[0]: row for row in rows}

        # 顺带给出每个槽位对应的**文件名**。App 要用它去请求预览图，而文件名是由
        # menu_render.SLOT_FILENAMES 定义的跨端契约 —— 让 App 自己拼 "1_today.film"
        # 等于把这份契约抄第三遍（固件、后端、App），而漂移的表现是「预览图取不到」
        # 或更糟的「取到了别的槽位那张」。由后端交出来，客户端零猜测。
        filenames = slot_filenames()
        slot_files = [
            {"slot": slot, "name": filenames[slot]}
            for slot in slots if slot in filenames
        ]
        # 主图口径与 manifest 的旧字段完全一致：today（总览）→ current（旧的单张发布）
        # → 排序后的第一行。三档都是确定性的，同一版本任何时候都得到同一个 sha256。
        primary = by_slot.get('today') or by_slot.get(LEGACY_SLOT) or rows[0]
        latest_sha256, latest_time = primary[1], primary[2]

        # 获取最近 manifest 查询时间
        cursor.execute('''
            SELECT created_at
            FROM device_sync_log
            WHERE device_id = ? AND event_type = 'manifest_query'
            ORDER BY created_at DESC
            LIMIT 1
        ''', (device_id,))

        manifest_row = cursor.fetchone()
        last_manifest_query = manifest_row[0] if manifest_row else None

        return {
            "device_id": device_id,
            "latest_version": latest_version,
            "latest_upload_time": latest_time,
            "latest_sha256": latest_sha256,
            "slots": slots,
            "slot_files": slot_files,
            "last_manifest_query": last_manifest_query,
            "device_token_registered": token_registered,
            "device_last_contact": last_contact,
            "device_last_contact_minutes": last_contact_minutes,
            "recent_events": events
        }

    finally:
        conn.close()


#: 只有设备本人能触发的事件类型。用来回答「这台设备最近一次联系后端是什么时候」。
#
# `auth_failed` 必须算在内：被 401 拒绝的设备**同样证明它联网了**，而那正是最需要这个
# 结论的场景 —— 令牌没登记时设备一次也拉不到画面，但它确实在联网重试。
#
# `apply_failed` 同理：上屏失败的设备也是刚刚联系过后端才报得上来，把它排除在外会让
# 「设备正在报错」被算成「设备失联」，两者的处理方向完全相反。
_DEVICE_CONTACT_EVENTS = ('manifest_query', 'download', 'apply', 'apply_failed',
                          'auth_failed')


def _minutes_since_utc(text) -> Optional[int]:
    """把库里的 UTC 时间串换算成「距今多少分钟」；解析不了返回 None。

    换算放在后端而不是让 App 自己算：库里的时间字段来源不同（SQLite CURRENT_TIMESTAMP 是
    **UTC** 的 `Y-m-d H:M:S`，而 `datetime.now().isoformat()` 写的是**本地时间**），
    客户端用单一 pattern 去解析必然在其中一种上错 8 小时 —— 而这个数字要驱动「设备是否
    在线」的判断，错 8 小时等于结论反了。后端知道每个字段存的是什么，只交出分钟数。
    """
    if not text:
        return None
    raw = str(text).strip().replace('T', ' ')
    # 带微秒 / 不带微秒两种都要接。
    for pattern in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            parsed = datetime.strptime(raw[:26] if '.' in raw else raw[:19], pattern)
        except ValueError:
            continue
        delta = (datetime.utcnow() - parsed).total_seconds() / 60
        # 负值说明时钟或时区判断有问题，当作"刚刚"而不是给一个负数让 UI 显示
        # 「-480 分钟前」。
        return max(0, int(delta))
    return None


def log_device_auth_failure(device_id: str, reason: str, path: str = '') -> None:
    """把设备被鉴权拒绝这件事记进同步日志，让 App 也能看见。

    为什么值得单独记一条：设备被 401 挡住时，**这条链路上唯一知情的两方是设备和后端日志**。
    App 侧看到的是「发布成功、版本号自增、同步状态正常」，而屏幕永远停在旧画面 —— 真机上
    正是这个现象（后端发到 v10，设备还显示前一天那张）。把它记进 device_sync_log 之后，
    设备页的事件列表里就会出现「设备来过、但被拒了」，这是用户能自己看出问题的最短路径。

    设备不存在时也要记：未登记令牌恰恰意味着 devices 表里可能没有这一行，而这时的诊断
    价值最高。所以先 INSERT OR IGNORE 建行。
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR IGNORE INTO devices (device_id, last_seen) VALUES (?, ?)',
            (device_id, datetime.now().isoformat())
        )
        # last_seen 照常更新：设备确实来过，这是它「已经联网」的直接证据 ——
        # 比 BLE 回读的 Wi-Fi 状态更强（那只是设备自己的判断）。
        cursor.execute(
            'UPDATE devices SET last_seen = ? WHERE device_id = ?',
            (datetime.now().isoformat(), device_id)
        )
        detail = f"auth rejected: {reason}"
        if path:
            detail += f" (path={path})"
        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, ?, ?, ?)
        ''', (device_id, 'auth_failed', None, detail))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        # 记日志失败绝不能把鉴权流程带崩：那会把一个 401 变成 500，
        # 而 500 会让固件走完全不同的重试路径。
        logger.warning("记录设备鉴权失败事件时出错 device=%s: %s", device_id, exc)
    finally:
        conn.close()


def list_devices() -> list:
    """列出所有设备"""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT d.device_id, d.device_name, d.last_seen,
                   MAX(dd.version) as latest_version
            FROM devices d
            LEFT JOIN device_display dd ON d.device_id = dd.device_id
            GROUP BY d.device_id
            ORDER BY d.last_seen DESC
        ''')

        devices = []
        for row in cursor.fetchall():
            devices.append({
                "device_id": row[0],
                "device_name": row[1],
                "last_seen": row[2],
                "latest_version": row[3] or 0
            })

        return devices

    finally:
        conn.close()


init_device_tables()
