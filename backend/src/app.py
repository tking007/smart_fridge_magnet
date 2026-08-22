"""
智能冰箱贴 — Flask 主程序
"""
from flask import Flask, request, jsonify, Response, g, stream_with_context
from flask_cors import CORS
from datetime import datetime
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import threading
import time
import traceback
import uuid

# .env 由 config 在模块导入时加载，覆盖 gunicorn / CLI / 测试所有入口，此处不重复调用。
from . import config
from . import barcode_lookup
from . import firmware
from . import database as db
from . import meals
from . import menu_render
from . import ai_client
from . import settings_store
from .recognition import process_photo
from .image_processor import MAX_FILE_SIZE
from . import agent
from .agent import (
    chat, confirm_plan, find_missing_ingredients,
    stream_chat, stream_recommendation,
)
from .intent_parser import parse_query, build_nutrition_query, build_category_query, get_suggestions, is_simple_query, extract_food_name

# 日志系统
from .logger import get_logger
logger = get_logger('app')

# 错误处理
from .errors import (
    AppError, BadRequestError, format_error_response, InventoryNotFoundError, InventoryInsufficientError, InventoryVersionConflictError
)

# 输入校验
from .validators import (
    validate_inventory_item, validate_batch_items, validate_deduct_items,
    validate_integer
)

# 通知系统
from .notification import (
    get_notifications, get_unread_count, mark_read, mark_all_read,
    clear_notifications, get_notification_stats
)

# 定时任务
from .scheduler import (
    init_scheduler, stop_scheduler, is_scheduler_running,
    get_scheduler_tasks, get_task_history,
    trigger_recommendation, trigger_expiring_check, trigger_inventory_check,
    trigger_display_publish
)

# 库存历史
from .inventory_history import (
    get_history, get_item_history, get_statistics, get_daily_summary
)

# 健康检查
from .health import (
    full_health_check, quick_health_check,
    check_database, check_api, check_scheduler,
    check_inventory, check_profile, check_nutrition
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE + 64 * 1024

# CORS 白名单来自 SFM_CORS_ORIGINS；默认为空即不放开跨域（原生 Android 不需要 CORS）。
_cors_origins = config.cors_origins()
if _cors_origins:
    CORS(app, origins=_cors_origins)
    logger.info(f"CORS enabled for origins: {_cors_origins}")

db.init_db()

logger.info("Smart Fridge Magnet started")

# gunicorn 等 WSGI 服务器不经过 start_app()，定时任务需要在此显式启动。
# 必须配套 --workers 1，否则每个 worker 各起一份调度器会重复执行任务。
if config.enable_scheduler():
    init_scheduler()
    logger.info("Scheduler started via SFM_ENABLE_SCHEDULER (WSGI mode)")


# ==================== 鉴权 ====================
#
# 两套互不复用的凭据见 docs/contracts/README.md：app_token（SFM_API_TOKEN，App 等业务
# 调用方）与 device_token（每设备一份，存 devices.device_token）。设备侧接口只认后者。
#
# _PUBLIC_PATHS 按**精确匹配**放行，不是前缀匹配：/api/health 下的子路由会真的去打模型、
# 返回家庭成员真实姓名和 SQLite 绝对路径，按前缀放行会连带开放它们。容器探活只使用
# 非 API 的 /healthz，因此 API 诊断路由不需要任何免鉴权例外。
_PUBLIC_PATHS = frozenset()


class UploadTooLarge(ValueError):
    """上传文件超过识别接口允许的大小。"""


def _save_upload_limited(file_storage, suffix: str = '.jpg') -> str:
    """流式保存单个上传，超过 MAX_FILE_SIZE 时立即中止并清理。"""
    path = ''
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            path = tmp.name
            total = 0
            while chunk := file_storage.stream.read(64 * 1024):
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise UploadTooLarge
                tmp.write(chunk)
        return path
    except Exception:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


def _bearer_token() -> str:
    """从 Authorization 头取 Bearer 凭据；缺失或格式不符时返回空串。"""
    header = request.headers.get('Authorization', '')
    if not header.lower().startswith('bearer '):
        return ''
    return header[7:].strip()


def _is_device_pull_request() -> bool:
    """判断是否为设备（ESP32）主动拉取的接口：manifest 与 .film 下载。

    注意 POST /api/devices/{id}/display（Android 上传）与 GET .../sync-status
    （Android 查询）不属于此类，仍走 app_token。
    """
    if request.method != 'GET':
        return False
    path = request.path
    if not path.startswith('/api/devices/'):
        return False
    return path.endswith('/display/manifest') or path.endswith('.film')


def _is_device_refresh_request() -> bool:
    """判断是否为设备主动请求「重算菜单」：POST .../display/request-refresh。

    与 `_is_device_pull_request()` 分开：一个谓词只表达一种设备行为，device_token 的
    授权范围才能从函数名逐条读出。各谓词在 authenticate_request() 里 OR 起来共用同一条
    device_token 分支。
    """
    if request.method != 'POST':
        return False
    path = request.path
    if not path.startswith('/api/devices/'):
        return False
    return path.endswith('/display/request-refresh')


def _is_device_report_request() -> bool:
    """判断是否为设备上报「这一版有没有画上屏」：POST .../display/applied。

    单独一个谓词的理由同 `_is_device_refresh_request()`。
    """
    if request.method != 'POST':
        return False
    path = request.path
    if not path.startswith('/api/devices/'):
        return False
    return path.endswith('/display/applied')


def _is_device_firmware_request() -> bool:
    """判断是否为设备下载固件二进制：GET /api/firmware/<file>.bin。

    这条路径**不在** /api/devices/ 下，`_extract_device_id()` 取不到 device_id；固件对
    所有设备是同一份，鉴权用的是「任一台已登记设备的令牌」，见 authenticate_request。
    """
    if request.method != 'GET':
        return False
    return (request.path.startswith('/api/firmware/')
            and request.path.endswith('.bin'))


def _extract_device_id() -> str:
    """从 /api/devices/{device_id}/... 中取出 device_id。

    路径以 '/' 开头，split 后首项是空串，device_id 落在下标 3。
    """
    parts = request.path.split('/')
    if len(parts) >= 4:
        return parts[3]
    return ''


# ==================== 请求级日志 ====================
#
# 每行业务日志带 rid 与调用方，便于三端联调时把同一次请求的日志聚在一起。gunicorn 自带的
# access log 有 method/path/status，但没有 rid、不区分调用方，无法与业务日志对齐。

def _ua_kind() -> str:
    """把 User-Agent 归类成调用方（device/app/curl/web/other）。

    固件的 UA 是 `ESP32 HTTP Client/1.0`，Android 用 okhttp；只保留归类结果，
    原始 UA 串太长会把日志行挤满。
    """
    ua = request.headers.get('User-Agent', '').lower()
    if 'esp32' in ua:
        return 'device'
    if 'okhttp' in ua:
        return 'app'
    if 'curl' in ua:
        return 'curl'
    if 'mozilla' in ua or 'chrome' in ua:
        return 'web'
    return 'other'


@app.before_request
def assign_request_id():
    """分配 request id 并记开始时间。

    必须注册在 `authenticate_request` **之前**：before_request 按注册顺序执行，否则鉴权
    拒绝时打的 warning 会没有 rid。

    接受调用方传入的 `X-Request-Id` 以对齐 App 侧日志；截断到 32 字符并只保留
    字母数字与 `-_`，避免借它往日志里注入伪造行。
    """
    incoming = request.headers.get('X-Request-Id', '')
    cleaned = ''.join(c for c in incoming if c.isalnum() or c in '-_')[:32]
    g.request_id = cleaned or uuid.uuid4().hex[:8]
    g.request_started = time.perf_counter()


@app.after_request
def log_access(response):
    """每个 /api 请求打一行结构化日志。

    只打 /api：静态资源与探活（每几秒一次）会把有用的行淹掉。字段用 `key=value` 而不是
    JSON，容器日志靠 grep 读，`grep device=dev001` 能一次拉出该设备的完整时间线。
    """
    path = request.path
    if path.startswith('/api/') and path not in _PUBLIC_PATHS:
        started = getattr(g, 'request_started', None)
        cost_ms = int((time.perf_counter() - started) * 1000) if started else -1
        device_id = _extract_device_id() if path.startswith('/api/devices/') else ''
        parts = [
            f"from={_ua_kind()}",
            f"{request.method} {path}",
            f"-> {response.status_code}",
            f"in {cost_ms}ms",
        ]
        if device_id:
            parts.insert(1, f"device={device_id}")
        # 4xx/5xx 用 WARNING：容器日志默认按级别筛，失败的请求必须能单独捞出来。
        line = ' '.join(parts)
        if response.status_code >= 400:
            logger.warning(line)
        else:
            logger.info(line)

    # 回写 rid：App 报错时可以把它显示给用户，据此在后端日志里定位。
    if hasattr(g, 'request_id'):
        response.headers['X-Request-Id'] = g.request_id
    return response


@app.before_request
def authenticate_request():
    """统一鉴权入口（fail-closed 由启动校验保证，见 config.validate_runtime）。"""
    # CORS 预检不带凭据，放行
    if request.method == 'OPTIONS':
        return None

    path = request.path

    # 非 /api 路径（静态等）与健康探活不鉴权
    if not path.startswith('/api/'):
        return None
    if path in _PUBLIC_PATHS:
        return None

    # 设备下载固件：固件对所有设备是同一份，不按设备隔离，因此只要求「是某台已登记
    # 设备的令牌」。放在按设备的分支之前是因为这条路径不在 /api/devices/ 下。
    if _is_device_firmware_request():
        holder = token_matches_any_device(_bearer_token())
        if not holder:
            logger.warning('Firmware auth rejected: token not registered to any device '
                           'path=%s', path)
            return jsonify({
                'error': '固件下载需要一台已登记设备的 device_token。',
                'code': 4001
            }), 401
        return None

    # 设备侧接口（拉取 manifest/.film、按键请求重算菜单、上报上屏结果）：校验该设备登记的 device_token
    if (_is_device_pull_request() or _is_device_refresh_request()
            or _is_device_report_request()):
        device_id = _extract_device_id()
        if not device_token_registered(device_id):
            # 「未登记」与「令牌不匹配」要给出不同的话：前者要去登记，后者要让两侧一致，
            # 排障方向相反。全新部署的 devices 表是空的，撞到的是前者。
            logger.warning(
                f"Device auth rejected: token not registered device={device_id} "
                f"path={path} | 该设备从未登记 device_token，"
                f"用 POST /api/devices/{device_id}/token 登记与设备侧一致的令牌即可恢复"
            )
            # 同时记进设备同步日志：只打后端日志的话，这条链路的失败在 App 上完全不可见。
            log_device_auth_failure(device_id, 'device_token 未登记', path)
            return jsonify({
                'error': f'设备 {device_id} 未登记 device_token。'
                         f'请用 POST /api/devices/{device_id}/token 登记与设备侧'
                         f'（BLE opcode 0x42 写入的）同一个令牌。',
                'code': 4001
            }), 401
        if not device_token_matches(device_id, _bearer_token()):
            logger.warning(
                f"Device auth failed: device={device_id} path={path} | "
                f"设备携带的令牌与后端登记的不一致，需重新配网让两侧一致"
            )
            log_device_auth_failure(device_id, 'device_token 与后端登记的不一致', path)
            return jsonify({
                'error': '设备令牌与后端登记的不一致，请重新配网使两侧写入同一个值。',
                'code': 4001
            }), 401
        return None

    # 业务接口：校验 app_token
    token = config.api_token()
    if not token:
        # 未配置 token 时放行，仅适用于回环本机开发；绑定非回环地址时
        # config.validate_runtime() 会在启动阶段直接拒绝，不会裸奔对外。
        return None
    if not secrets.compare_digest(_bearer_token(), token):
        return jsonify({'error': '未授权', 'code': 4001}), 401
    return None


# ==================== 错误处理 ====================

@app.errorhandler(AppError)
def handle_app_error(error):
    """处理应用错误"""
    logger.warning(f"AppError: {error.code} - {error.message}")
    response = format_error_response(error)
    return jsonify(response), error.status_code


@app.errorhandler(400)
def handle_bad_request(error):
    """处理 400 错误"""
    logger.warning(f"Bad request: {error}")
    return jsonify({
        'error': '请求参数错误',
        'code': 1001
    }), 400


@app.errorhandler(404)
def handle_not_found(error):
    """处理 404 错误"""
    logger.warning(f"Not found: {request.path}")
    return jsonify({
        'error': '资源不存在',
        'code': 1004
    }), 404


@app.errorhandler(405)
def handle_method_not_allowed(error):
    """处理 405 错误"""
    logger.warning(f"Method not allowed: {request.method} {request.path}")
    return jsonify({
        'error': '请求方法不允许',
        'code': 1001
    }), 405


@app.errorhandler(413)
def handle_request_too_large(_error):
    """统一返回 JSON，避免 Werkzeug HTML 错误页泄露实现细节。"""
    return jsonify({'error': '图片不能超过 10 MiB', 'code': 1002}), 413


@app.errorhandler(500)
def handle_internal_error(error):
    """处理 500 错误"""
    logger.error(f"Internal error: {error}\n{traceback.format_exc()}")
    return jsonify({
        'error': '服务器内部错误',
        'code': 1007
    }), 500


@app.errorhandler(Exception)
def handle_unhandled_exception(error):
    """处理未捕获的异常"""
    logger.error(f"Unhandled exception: {error}\n{traceback.format_exc()}")
    return jsonify({
        'error': '服务器内部错误',
        'code': 1007
    }), 500


# ==================== API 接口 ====================

@app.route('/api/chat', methods=['POST'])
def api_chat():
    """
    对话接口 — 支持本地语义理解优先

    请求：
    {
        "message": "用户输入",
        "session_id": "会话ID（可选）"
    }

    响应：
    {
        "response": "回复文本",
        "handled_locally": true/false,
        "intent": {...},
        "results": [...]
    }
    """
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({"error": "缺少message参数"}), 400

    message = data['message']
    session_id = data.get('session_id', 'default')

    # 1. 先尝试本地语义理解
    parsed = parse_query(message)

    if is_simple_query(message):
        # 简单查询，本地处理
        response = {
            "handled_locally": True,
            "intent": parsed,
            "response": "",
            "results": []
        }

        if parsed["type"] == "nutrition":
            try:
                nutrition_query = build_nutrition_query(parsed["intent"])
                results = db.get_nutrition_by_field(
                    order_by=nutrition_query["order_by"],
                    order=nutrition_query["order"],
                    limit=nutrition_query["limit"]
                )
                response["results"] = results
                label = parsed["intent"].get("label", "相关")
                response["response"] = f"找到 {len(results)} 个{label}食物：\n"
                for i, r in enumerate(results, 1):
                    response["response"] += f"{i}. {r['food_name']}: {r.get(parsed['intent']['field'], '?')}g\n"
            except Exception as e:
                response["response"] = f"查询出错: {str(e)}"

        elif parsed["type"] == "category":
            category = parsed["intent"].get("category", "")
            results = db.get_inventory_by_category(category)
            response["results"] = results
            if results:
                response["response"] = f"找到 {len(results)} 个{category}类食材：\n"
                for r in results:
                    response["response"] += f"- {r['name']} × {r['quantity']}{r['unit']}\n"
            else:
                response["response"] = f"库存中没有{category}类食材"

        elif parsed["type"] == "inventory":
            if parsed["intent"].get("filter") == "expiring_soon":
                results = db.get_expiring_items(days=3)
                response["results"] = results
                if results:
                    response["response"] = f"有 {len(results)} 个食材即将过期：\n"
                    for r in results:
                        days = r.get('days_left', '?')
                        response["response"] += f"- {r['name']}: {days}天后过期\n"
                else:
                    response["response"] = "没有即将过期的食材"

        elif parsed["type"] == "action" and parsed["intent"].get("action") == "query":
            food_name = extract_food_name(message)
            if food_name:
                inventory = db.get_inventory(page_size=1000)["items"]
                matched = [i for i in inventory if food_name in i.get("name", "")]
                if matched:
                    response["results"] = matched
                    response["response"] = f"库存中有 {food_name}：\n"
                    for r in matched:
                        response["response"] += f"- {r['name']} × {r['quantity']}{r['unit']}\n"
                else:
                    response["response"] = f"库存中没有找到 {food_name}"

        return jsonify(response)

    # 2. 复杂查询，调用 AI
    result = chat(message, session_id)
    result["handled_locally"] = False
    return jsonify(result)


@app.route('/api/barcode/<barcode>', methods=['GET'])
def api_barcode_lookup(barcode):
    """条码 → 商品名与分类。数据来自 Open Food Facts。

    响应（查到了）：
      {"success": true, "barcode": "...", "name": "怡宝纯净水", "category": "饮料",
       "shelf_life_days": 180, "cached": false, "source": "openfoodfacts",
       "needs_confirmation": true}
    响应（查不到）：
      {"success": false, "barcode": "...", "reason": "not_in_database",
       "message": "商品库里没有这个条码，请改用拍照识别"}

    ── 覆盖率与定位 ──
    OFF 对国产商品覆盖率低：2026-08 抽测 15 个中国大陆条码，仅 4 个查到，其中只有 1 个
    返回可直接使用的商品名。所以这个接口是「偶尔省一次手输」而非主路径：
      - 查不到就明确说查不到，客户端回落拍照识别（视觉模型看实物，对国产商品更可靠）；
      - 查到也带 needs_confirmation=true，由用户确认后才入库；
      - 整个功能可用 SFM_BARCODE_LOOKUP=false 关掉（见 config.barcode_lookup_enabled）。

    结果无论成败都进缓存，「查不到」也要记住，否则每次扫到它都要重新等一次网络往返。
    """
    code = (barcode or '').strip()
    # 只接受纯数字的商品码。放行任意字符串等于拿二维码里的 URL 去查商品库。
    if not code.isdigit() or not (8 <= len(code) <= 14):
        return jsonify({
            "success": False, "barcode": code, "reason": "invalid",
            "message": "条码应为 8–14 位数字（EAN-8/EAN-13/UPC）",
            "code": 1001,
        }), 400

    if not barcode_lookup.enabled():
        # 明确说「功能关了」而不是伪装成查不到：后者会让人以为再扫一次或换个商品就行。
        return jsonify({
            "success": False, "barcode": code, "reason": "disabled",
            "message": "条码查询已关闭，请用拍照识别添加食材",
            "code": 5031,
        }), 503

    cached = db.get_barcode_cache(code)
    if cached is not None:
        if cached["resolved"]:
            logger.info("条码命中缓存：%s -> %s", code, cached["name"])
            shelf_life_days = cached["shelf_life_days"] or db.DEFAULT_SHELF_LIFE_DAYS[
                cached["category"]
            ]
            return jsonify({
                "success": True, "barcode": code,
                "name": cached["name"], "category": cached["category"],
                "shelf_life_days": shelf_life_days,
                "cached": True, "source": "cache",
                "needs_confirmation": True,
            })
        logger.info("条码命中缓存但商品库里没有：%s", code)
        return jsonify({
            "success": False, "barcode": code, "reason": "not_in_database", "cached": True,
            "message": "商品库里没有这个条码，请改用拍照识别",
        })

    result = barcode_lookup.lookup(code)

    if not result.get('found'):
        reason = result.get('reason') or 'not_in_database'
        if reason == 'lookup_failed':
            # 查询失败**不进缓存**：网络问题是暂时的，缓存下来会让这个条码在
            # 缓存过期前一直查不到，而用户完全无从判断。
            logger.warning("条码查询失败（不缓存）：%s %s", code, result.get('detail'))
            return jsonify({
                "success": False, "barcode": code, "reason": "lookup_failed",
                "message": "商品库暂时连不上，请稍后重试或改用拍照识别",
                "code": 5032,
            }), 503
        db.put_barcode_cache(code, resolved=False)
        logger.info("条码未收录：%s", code)
        return jsonify({
            "success": False, "barcode": code, "reason": "not_in_database", "cached": False,
            "message": "商品库里没有这个条码，请改用拍照识别",
        })

    name = result['name']
    category = result['category']
    if category not in db.DEFAULT_CATEGORIES:
        category = '其他'
    shelf_life_days = db.DEFAULT_SHELF_LIFE_DAYS[category]

    db.put_barcode_cache(
        code, name=name, category=category, shelf_life_days=shelf_life_days, resolved=True
    )
    logger.info("条码查到：%s -> %s（%s，来源 %s）", code, name, category, result['source'])
    return jsonify({
        "success": True, "barcode": code, "name": name, "category": category,
        "shelf_life_days": shelf_life_days,
        "quantity_hint": result.get('quantity') or '',
        "cached": False, "source": result['source'],
        # 必须由用户确认：OFF 中文覆盖差，返回的多是用户不认识的英文名；且它是众包库、
        # 查询端会补零归一，扫错一位可能返回一个看似合理的商品名（实测 99999999999
        # 会匹配到 "Sugo alle Melanzone"）。
        "needs_confirmation": True,
    })


@app.route('/api/recognize', methods=['POST'])
def api_recognize():
    """
    拍照识别接口

    请求：multipart/form-data
    - image: 图片文件（必需）
    - auto_save: 是否自动保存（可选，默认true）

    响应：
    {
        "success": true,
        "items": [...],
        "duplicates": [...],
        "new_items": [...],
        "message": "识别结果消息",
        "quality_info": {...},
        "need_confirm": false
    }
    """
    if 'image' not in request.files:
        return jsonify({"error": "请上传图片"}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    auto_save = request.form.get('auto_save', 'true').lower() == 'true'

    try:
        tmp_path = _save_upload_limited(file)
    except UploadTooLarge:
        return jsonify({'error': '图片不能超过 10 MiB', 'code': 1002}), 413

    try:
        # 处理照片
        result = process_photo(tmp_path, auto_save=auto_save)
        return jsonify(result)
    except Exception:
        logger.exception('Image recognition failed')
        return jsonify({
            "success": False,
            "error": "识别服务暂时不可用",
            "message": "识别失败，请稍后重试"
        }), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route('/api/recognize/confirm', methods=['POST'])
def api_recognize_confirm():
    """
    确认识别结果（用户修改后提交）

    请求：
    {
        "items": [
            {
                "name": "鸡蛋",
                "category": "蛋类",
                "quantity": 6,
                "unit": "个",
                "expiry_date": "2026-08-20",
                "confidence": "高",
                "id": null  // 如果是更新已有项，传id
            }
        ],
        "photo_path": "/tmp/xxx.jpg"  // 可选
    }
    """
    from .recognition import confirm_and_save

    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({"error": "缺少items数组"}), 400

    try:
        result = confirm_and_save(
            items=data['items'],
            photo_path=None
        )
        return jsonify(result)
    except Exception:
        logger.exception('Recognition confirmation failed')
        return jsonify({
            "success": False,
            "error": "保存识别结果失败"
        }), 500


@app.route('/api/recognize/check', methods=['POST'])
def api_recognize_check():
    """
    仅检查图片质量（不识别）

    请求：multipart/form-data
    - image: 图片文件
    """
    from .image_processor import check_image_quality

    if 'image' not in request.files:
        return jsonify({"error": "请上传图片"}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    try:
        tmp_path = _save_upload_limited(file)
    except UploadTooLarge:
        return jsonify({'error': '图片不能超过 10 MiB', 'code': 1002}), 413

    try:
        try:
            result = check_image_quality(tmp_path)
            return jsonify(result)
        except Exception:
            logger.exception('Image quality check failed')
            return jsonify({'error': '图片质量检查失败'}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route('/api/inventory', methods=['GET'])
def api_inventory():
    """
    查看库存接口

    查询参数：
    - format: "summary" 返回摘要文本，"full" 返回完整数据（默认full）
    - category: 按分类筛选（可选）
    - page: 页码（默认1）
    - page_size: 每页数量（默认50，最大200）
    """
    fmt = request.args.get('format', 'full')
    category = request.args.get('category')
    page = validate_integer(request.args.get('page', 1), 'page', min_value=1)
    page_size = validate_integer(request.args.get('page_size', 50), 'page_size', min_value=1, max_value=200)

    if fmt == 'summary':
        summary = db.get_inventory_summary()
        return jsonify({"summary": summary})
    else:
        result = db.get_inventory(category=category, page=page, page_size=page_size)
        result['expiring_soon'] = [
            i for i in result['items']
            if i.get('days_left') is not None and i['days_left'] <= 3
        ]
        return jsonify(result)


@app.route('/api/inventory', methods=['POST'])
def api_inventory_add():
    """
    添加库存项

    请求：
    {
        "name": "鸡蛋",
        "category": "蛋类",
        "quantity": 10,
        "unit": "个",
        "production_date": "2026-07-20",
        "expiry_date": "2026-08-20",
        "confidence": "高"
    }
    """
    data = request.get_json()
    if not data:
        raise BadRequestError(message="缺少请求数据")

    validated = validate_inventory_item(data)

    logger.info(f"Adding inventory: {validated['name']} x {validated['quantity']}")

    item_id = db.add_inventory_item(
        name=validated['name'],
        category=validated['category'],
        quantity=validated['quantity'],
        unit=validated['unit'],
        weight_grams=validated['weight_grams'],
        production_date=validated['production_date'],
        shelf_life_days=validated['shelf_life_days'],
        expiry_date=validated['expiry_date'],
        confidence=validated['confidence']
    )

    logger.info(f"Added inventory item #{item_id}")

    return jsonify({
        "success": True,
        "item_id": item_id,
        "message": f"已添加: {validated['name']} × {validated['quantity']}{validated['unit']}"
    }), 201


@app.route('/api/inventory/batch', methods=['POST'])
def api_inventory_batch_add():
    """
    批量添加库存项（原子操作，任一失败则全部回滚）

    请求：
    {
        "items": [
            {"name": "鸡蛋", "category": "蛋类", "quantity": 10},
            {"name": "牛奶", "category": "乳制品", "quantity": 2, "unit": "盒"}
        ]
    }
    """
    data = request.get_json()
    if not data or 'items' not in data:
        raise BadRequestError(message="缺少items数组")

    validated_items = validate_batch_items(data['items'])

    logger.info(f"Batch adding {len(validated_items)} items")

    result = db.batch_add_inventory_items(validated_items)
    success_count = len(result['success'])
    failed_count = len(result['failed'])

    if failed_count > 0:
        logger.warning(f"Batch add partially failed: {success_count} success, {failed_count} failed")
        return jsonify({
            "success": False,
            "message": f"批量添加部分失败：{success_count}成功，{failed_count}失败",
            "total": len(data['items']),
            "added": success_count,
            "failed": failed_count,
            "results": result
        }), 400
    else:
        logger.info(f"Batch add success: {success_count} items")
        return jsonify({
            "success": True,
            "message": f"批量添加成功：{success_count}项",
            "total": len(data['items']),
            "added": success_count,
            "results": result
        }), 201


@app.route('/api/inventory/<int:item_id>', methods=['PUT'])
def api_inventory_update(item_id):
    """
    更新库存项（支持乐观锁）

    请求（所有字段可选）：
    {
        "name": "土鸡蛋",
        "quantity": 8,
        "expiry_date": "2026-08-25",
        "version": 1
    }
    """
    data = request.get_json()
    if not data:
        raise BadRequestError(message="缺少请求数据")

    logger.info(f"Updating inventory #{item_id}: {data}")

    version = data.pop('version', None)

    try:
        db.update_inventory_item(item_id, version=version, **data)
        logger.info(f"Updated inventory #{item_id}")
        return jsonify({"success": True, "message": f"已更新库存项 #{item_id}"})
    except ValueError as e:
        error_msg = str(e)
        if "版本冲突" in error_msg:
            raise InventoryVersionConflictError(item_id, version, version)
        elif "不存在" in error_msg:
            raise InventoryNotFoundError(item_id)
        else:
            raise BadRequestError(message=error_msg)


@app.route('/api/inventory/<int:item_id>', methods=['DELETE'])
def api_inventory_delete(item_id):
    """
    删除库存项（软删除，设为quantity=0）
    """
    logger.info(f"Deleting inventory #{item_id}")

    try:
        db.delete_inventory_item(item_id)
        logger.info(f"Deleted inventory #{item_id}")
        return jsonify({"success": True, "message": f"已删除库存项 #{item_id}"})
    except ValueError:
        raise InventoryNotFoundError(item_id)


@app.route('/api/inventory/deduct', methods=['POST'])
def api_inventory_deduct():
    """
    扣减库存（FIFO先进先出，事务保护）

    请求：
    {
        "items": [
            {"name": "鸡蛋", "quantity": 2},
            {"name": "牛奶", "quantity": 1}
        ]
    }
    """
    data = request.get_json()
    if not data or 'items' not in data:
        raise BadRequestError(message="缺少items数组")

    validated_items = validate_deduct_items(data['items'])

    logger.info(f"Deducting inventory: {validated_items}")

    try:
        results = db.deduct_inventory(validated_items)
        logger.info(f"Deducted inventory: {len(results)} items")
        return jsonify({
            "success": True,
            "message": "扣减成功",
            "results": results
        })
    except ValueError as e:
        raise InventoryInsufficientError(str(e), 0, 0)


@app.route('/api/inventory/expiring', methods=['GET'])
def api_inventory_expiring():
    """
    获取即将过期的食材

    查询参数：
    - days: 天数（默认3天）
    """
    days = int(request.args.get('days', 3))
    items = db.get_expiring_items(days=days)
    return jsonify({
        "items": items,
        "count": len(items),
        "days": days
    })


@app.route('/api/inventory/categories', methods=['GET'])
def api_inventory_categories():
    """
    获取分类列表

    查询参数：
    - type: "preset" 返回预设分类，"used" 返回实际使用的分类（默认preset）
    """
    category_type = request.args.get('type', 'preset')

    if category_type == 'used':
        categories = db.get_inventory_categories()
    else:
        categories = db.get_preset_categories()

    return jsonify({
        "categories": categories,
        "count": len(categories)
    })


# ==================== 智能查询接口 ====================

@app.route('/api/smart/query', methods=['POST'])
def api_smart_query():
    """
    智能查询接口 — 本地语义理解，减少 AI 调用

    请求：
    {
        "query": "蛋白质高的食物"
    }

    返回：
    {
        "type": "nutrition",
        "intent": {"field": "protein", "order": "DESC", "label": "高蛋白"},
        "keywords": ["蛋白质"],
        "confidence": "high",
        "results": [...],
        "suggestions": [...],
        "handled_locally": true
    }
    """
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "缺少query参数"}), 400

    query = data['query']

    # 本地语义解析
    parsed = parse_query(query)

    response = {
        "type": parsed["type"],
        "intent": parsed["intent"],
        "keywords": parsed["keywords"],
        "confidence": parsed["confidence"],
        "original": parsed["original"],
        "handled_locally": False,
        "results": [],
        "suggestions": []
    }

    # 根据意图类型处理
    if parsed["type"] == "nutrition":
        # 营养查询：按字段排序
        try:
            nutrition_query = build_nutrition_query(parsed["intent"])
            results = db.get_nutrition_by_field(
                order_by=nutrition_query["order_by"],
                order=nutrition_query["order"],
                limit=nutrition_query["limit"]
            )
            response["results"] = results
            response["handled_locally"] = True
            response["message"] = f"找到 {len(results)} 个{parsed['intent'].get('label', '')}食物"
        except Exception as e:
            response["error"] = str(e)

    elif parsed["type"] == "category":
        # 分类查询
        category_query = build_category_query(parsed["intent"])
        results = db.get_inventory_by_category(category_query["category"])
        response["results"] = results
        response["handled_locally"] = True
        response["message"] = f"找到 {len(results)} 个{category_query['category']}类食材"

    elif parsed["type"] == "inventory":
        # 库存查询
        if parsed["intent"].get("filter") == "expiring_soon":
            results = db.get_expiring_items(days=3)
            response["results"] = results
            response["handled_locally"] = True
            response["message"] = f"找到 {len(results)} 个即将过期的食材"
        elif parsed["intent"].get("sort") == "quantity":
            order = parsed["intent"].get("order", "ASC")
            all_items = db.get_inventory(page_size=1000)["items"]
            all_items.sort(key=lambda x: x.get("quantity", 0), reverse=(order == "DESC"))
            response["results"] = all_items[:10]
            response["handled_locally"] = True

    elif parsed["type"] == "action":
        action = parsed["intent"].get("action", "")
        if action == "query":
            # 查询类动作，提取食材名
            food_name = extract_food_name(query)
            if food_name:
                # 先查库存
                inventory = db.get_inventory(page_size=1000)["items"]
                matched = [i for i in inventory if food_name in i.get("name", "")]
                if matched:
                    response["results"] = matched
                    response["handled_locally"] = True
                    response["message"] = f"找到 {len(matched)} 个匹配的库存项"
                else:
                    # 查营养数据
                    nutrition = db.search_nutrition(food_name, limit=5)
                    if nutrition:
                        response["results"] = nutrition
                        response["handled_locally"] = True
                        response["message"] = f"找到 {len(nutrition)} 个匹配的食物"

    # 生成建议
    inventory = db.get_inventory(page_size=1000)["items"] if parsed["type"] in ["action", "scene"] else None
    response["suggestions"] = get_suggestions(query, inventory)

    return jsonify(response)


@app.route('/api/smart/nutrition', methods=['GET'])
def api_smart_nutrition():
    """
    营养排序查询

    查询参数：
    - field: 排序字段 (protein/fat/energy_kcal/fe/ca/vitamin_a/vitamin_c)
    - order: 排序方向 (ASC/DESC，默认DESC)
    - limit: 返回数量（默认5）
    """
    field = request.args.get('field', 'protein')
    order = request.args.get('order', 'DESC').upper()
    limit = int(request.args.get('limit', 5))

    try:
        results = db.get_nutrition_by_field(order_by=field, order=order, limit=limit)
        return jsonify({
            "field": field,
            "order": order,
            "results": results,
            "count": len(results)
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/smart/parse', methods=['POST'])
def api_smart_parse():
    """
    仅解析意图（不执行查询）

    请求：
    {
        "query": "蛋白质高的食物"
    }

    返回：
    {
        "type": "nutrition",
        "intent": {...},
        "keywords": [...],
        "confidence": "high",
        "is_simple": true
    }
    """
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "缺少query参数"}), 400

    query = data['query']
    parsed = parse_query(query)
    parsed["is_simple"] = is_simple_query(query)

    return jsonify(parsed)


@app.route('/api/profile', methods=['GET'])
def api_profile_get():
    """查看家庭画像"""
    members = db.get_all_members()
    schedule = db.get_meal_schedule()

    return jsonify({
        "members": members,
        "meal_schedule": schedule,
        "summary": db.get_profile_summary()
    })


@app.route('/api/profile', methods=['POST'])
def api_profile_update():
    """
    更新家庭画像

    请求：
    {
        "message": "描述家庭信息的自然语言"
    }
    """
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({"error": "缺少message参数"}), 400

    result = chat(data['message'], session_id='profile')
    return jsonify(result)


@app.route('/api/profile/members', methods=['PUT'])
def api_profile_members_replace():
    """用 App 的成员列表整体替换后端家庭画像。

    请求：
    {
      "members": [
        {"name": "爸爸", "age": 40, "height": 175, "weight": 70,
         "allergies": ["花生"], "dislikes_main": ["苦瓜"],
         "dislikes_ingredient": [], "dislikes_taste": ["辣"], "health_notes": ""}
      ]
    }

    ── 为什么需要这个接口 ──
    在它出现之前，家庭画像**只能**通过 `POST /api/profile`（自然语言 → 模型抽取）写入，
    而 App 的成员页是纯本地存储、从不上行。于是用户在 App 里登记的过敏与忌口对推荐
    毫无影响 —— 界面上看起来配好了，生成菜谱时后端根本不知道。

    用 PUT 而非 POST：语义是「整体替换」（幂等），与上面那个走对话增量抽取的 POST
    区分开。替换语义的理由见 `db.replace_members()`。
    """
    data = request.get_json(silent=True) or {}
    members = data.get('members')
    if not isinstance(members, list):
        return jsonify({"error": "members 必须是数组", "code": 1001}), 400

    # 上界：家庭成员是人，两位数就已经不正常了。没有上界时一次请求能写进十万条，
    # 而每一条都会进 prompt。
    if len(members) > 20:
        return jsonify({"error": "成员数量上限 20", "code": 1001}), 400

    for m in members:
        if not isinstance(m, dict):
            return jsonify({"error": "members 的每一项必须是对象", "code": 1001}), 400
        raw_name = m.get('member_name') or m.get('name')
        if not isinstance(raw_name, str):
            return jsonify({"error": "每位成员的名字必须是字符串", "code": 1001}), 400
        name = raw_name.strip()
        if not name:
            return jsonify({"error": "每位成员都必须有名字", "code": 1001}), 400
        if len(name) > 100:
            return jsonify({"error": "成员名字上限 100 个字符", "code": 1001}), 400
        for field in db.MEMBER_LIST_FIELDS:
            value = m.get(field)
            if value is not None and not isinstance(value, list):
                return jsonify({
                    "error": f"{field} 必须是字符串数组", "code": 1001
                }), 400
            if value is not None:
                if len(value) > 50:
                    return jsonify({
                        "error": f"{field} 最多 50 项", "code": 1001
                    }), 400
                for item in value:
                    if not isinstance(item, str) or not item.strip():
                        return jsonify({
                            "error": f"{field} 的每一项都必须是非空字符串", "code": 1001
                        }), 400
                    if len(item.strip()) > 100:
                        return jsonify({
                            "error": f"{field} 的每一项上限 100 个字符", "code": 1001
                        }), 400
        health_notes = m.get('health_notes')
        if health_notes is not None and not isinstance(health_notes, str):
            return jsonify({"error": "health_notes 必须是字符串", "code": 1001}), 400
        if isinstance(health_notes, str) and len(health_notes) > 500:
            return jsonify({"error": "health_notes 上限 500 个字符", "code": 1001}), 400

    count = db.replace_members(members)
    allergens = db.collect_allergens()
    logger.info("家庭画像已替换：members=%d allergens=%s", count, allergens)
    return jsonify({
        "success": True,
        "member_count": count,
        # 回显过敏源，让 App 能确认「我登记的过敏确实被后端收到了」。
        "allergens": allergens,
        "summary": db.get_profile_summary(),
    })


# ==================== 用餐时间接口 ====================
#
# 用餐时间与提前量共同决定设备取图时间；每日发布会自动限制在第一餐取图之前。

_MEAL_TIME_PATTERN = re.compile(r'^(\d{1,2}):(\d{2})$')


def _parse_meal_time(raw) -> str:
    """校验并规范化 'HH:MM'；不合法返回空串。

    接受 '7:30' 这种一位数小时，但一律存成零补齐的 '07:30'：时间表同时被 App 展示和
    调度器解析，两种写法混在库里会让同一份配置看起来像两条不同的记录。
    """
    match = _MEAL_TIME_PATTERN.match(str(raw or '').strip())
    if not match:
        return ''
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ''
    return f'{hour:02d}:{minute:02d}'


def _meal_schedule_payload() -> dict:
    """把库里的用餐时间整理成对外结构。

    按早→中→晚输出，而不是按数据库返回的主键顺序：set_meal_schedule 用
    INSERT OR REPLACE，改一次时间就会换一个 rowid，主键顺序会随之跳动。
    同一餐出现多行时取最后一行 —— INSERT OR REPLACE 把新写入的行放到末尾。
    无法归一的餐次（下午茶、夜宵之类）追加在三餐之后原样带出：它们对定时任务无效，
    但静默隐藏会让人以为数据丢了。
    """
    by_key = {}
    extras = []
    for row in db.get_meal_schedule():
        key = meals.meal_key(row.get('meal_type'))
        if key:
            by_key[key] = row
        else:
            extras.append(row)

    schedule = []
    for key in meals.meal_keys_ordered():
        row = by_key.get(key)
        if row is None:
            continue
        schedule.append({
            "meal_type": key,
            "label": meals.meal_label(key),
            "meal_time": row.get('meal_time'),
            "enabled": row.get('enabled', 1),
        })
    for row in extras:
        schedule.append({
            "meal_type": row.get('meal_type'),
            "label": row.get('meal_type'),
            "meal_time": row.get('meal_time'),
            "enabled": row.get('enabled', 1),
        })

    return {
        "schedule": schedule,
        # daily_publish_time 是后端最终采用的时间，已受第一餐唤醒截止点约束。
        "lead_minutes": settings_store.publish_lead_minutes(),
        "auto_publish": settings_store.auto_publish_enabled(),
        "daily_publish_time": settings_store.daily_publish_time(),
        # 可调范围随响应带出，客户端不必硬编码；写死一份会与后端漂移，
        # 表现为「界面上能选到的值被接口拒掉」。
        "lead_minutes_min": settings_store.LEAD_MINUTES_MIN,
        "lead_minutes_max": settings_store.LEAD_MINUTES_MAX,
    }


@app.route('/api/meal-schedule', methods=['GET'])
def api_meal_schedule_get():
    """读取用餐时间与自动下发参数。

    响应：
    {"schedule": [{"meal_type": "breakfast", "label": "早餐",
                   "meal_time": "07:30", "enabled": 1}, ...],
     "daily_publish_time": "06:30", "auto_publish": true,
     "lead_minutes_min": 5, "lead_minutes_max": 240}

    `lead_minutes` / `auto_publish` 可写（见 PUT）。取值口径是 **DB 覆盖 env**：
    客户端改过之后以库里的值为准，`SFM_AUTO_PUBLISH_LEAD_MIN` / `SFM_AUTO_PUBLISH`
    退化成「初始默认值」。
    """
    return jsonify(_meal_schedule_payload())


@app.route('/api/meal-schedule', methods=['PUT'])
def api_meal_schedule_put():
    """更新用餐时间与自动下发参数。

    请求（三项都可选，但**至少给一个**）：
    {"schedule": [{"meal_type": "lunch", "meal_time": "11:45"}, ...],
     "lead_minutes": 45,
     "auto_publish": true}

    meal_type 中英文皆可，写入库时统一成 breakfast/lunch/dinner。
    `lead_minutes` 合法域 [settings_store.LEAD_MINUTES_MIN, LEAD_MINUTES_MAX]
    （下界 = 调度器的检查周期，更小的提前量会错过发布窗口），必须是 JSON 整数；
    `auto_publish` 必须是 JSON 布尔。

    三项都可选是因为它们同属一个设置页，只改提前量时不必回传整份时间表（回传会把
    中间被别处改过的值覆盖回去）；但请求体全空一定是客户端 bug，不能静默返回 200。

    全有或全无：校验全部做完再统一写入。校验一个写一个的话，写了时间表却因
    lead_minutes 超界返回 400，客户端会按失败回滚而库里其实已经变了。

    响应与 GET 同结构，便于客户端拿返回值直接刷新界面。

    时间表只处理时间，不处理启用开关：db.set_meal_schedule() 用 INSERT OR REPLACE
    整行替换，enabled 会回到默认的 1，即改时间会顺带启用该餐。目前没有停用入口，
    将来若要支持，需先让写入方保留 enabled。
    """
    data = request.get_json(silent=True) or {}

    # 用 `in` 判断「给没给」，不能用 `data.get(...) is not None`：
    # `auto_publish: false` 是有意义的输入，用取值判在场会把它当成没给。
    has_schedule = 'schedule' in data
    has_lead = 'lead_minutes' in data
    has_auto = 'auto_publish' in data
    has_daily_time = 'daily_publish_time' in data

    if not (has_schedule or has_lead or has_auto or has_daily_time):
        return jsonify({
            "success": False,
            "error": "请求体为空，至少提供 schedule / daily_publish_time / "
                     "lead_minutes / auto_publish 之一。"
                     "schedule 格式为 [{\"meal_type\": \"lunch\", \"meal_time\": \"12:00\"}]，"
                     f"lead_minutes 为 {settings_store.LEAD_MINUTES_MIN}–"
                     f"{settings_store.LEAD_MINUTES_MAX} 的整数，auto_publish 为布尔",
            "code": 1001
        }), 400

    # ── 第一阶段：全部校验，任何一处不合法立即返回，此时还没有写过任何东西 ──
    validated = {}
    if has_schedule:
        raw_schedule = data.get('schedule')
        if not isinstance(raw_schedule, list) or not raw_schedule:
            return jsonify({
                "success": False,
                "error": "schedule 必须是非空数组，格式为 "
                         "[{\"meal_type\": \"lunch\", \"meal_time\": \"12:00\"}]",
                "code": 1001
            }), 400

        for index, item in enumerate(raw_schedule):
            if not isinstance(item, dict):
                return jsonify({
                    "success": False,
                    "error": f"schedule[{index}] 必须是对象",
                    "code": 1001
                }), 400

            key = meals.meal_key(item.get('meal_type'))
            if key is None:
                return jsonify({
                    "success": False,
                    "error": f"schedule[{index}] 无法识别餐次 {item.get('meal_type')!r}"
                             f"（可选 {'/'.join(meals.meal_keys_ordered())} 或 早餐/午餐/晚餐）",
                    "code": 1001
                }), 400

            meal_time = _parse_meal_time(item.get('meal_time'))
            if not meal_time:
                return jsonify({
                    "success": False,
                    "error": f"schedule[{index}] 用餐时间非法 {item.get('meal_time')!r}，"
                             f"需为 HH:MM（00:00–23:59）",
                    "code": 1001
                }), 400

            if key in validated and validated[key] != meal_time:
                # 同一餐给了两个不同时间，无法判断以哪个为准 —— 猜一个等于替用户做决定
                return jsonify({
                    "success": False,
                    "error": f"schedule[{index}] 同一餐次 {key} 出现冲突的时间："
                             f"{validated[key]} 与 {meal_time}",
                    "code": 1001
                }), 400
            validated[key] = meal_time

    lead_minutes = None
    if has_lead:
        raw_lead = data.get('lead_minutes')
        # bool 必须先挡掉：Python 里 isinstance(True, int) 为 True，
        # 不先判就会把 {"lead_minutes": true} 当成 1 分钟静默接受。
        if isinstance(raw_lead, bool) or not isinstance(raw_lead, int):
            return jsonify({
                "success": False,
                "error": f"lead_minutes 必须是整数（收到 {raw_lead!r}）。"
                         "不接受字符串或小数：宽松解析会把客户端的类型 bug "
                         "变成静默的行为差异",
                "code": 1001
            }), 400
        if not settings_store.lead_minutes_in_range(raw_lead):
            return jsonify({
                "success": False,
                "error": f"lead_minutes 必须在 {settings_store.LEAD_MINUTES_MIN}–"
                         f"{settings_store.LEAD_MINUTES_MAX} 之间（收到 {raw_lead}）。"
                         f"小于 {settings_store.LEAD_MINUTES_MIN} 分钟的提前量会错过"
                         "调度器的检查窗口，屏幕不会换图；"
                         "提前超过 4 小时渲染的菜单，等设备到饭点时库存可能已经变了，"
                         "而且会与前一餐的发布窗口重叠",
                "code": 1001
            }), 400
        lead_minutes = raw_lead

    auto_publish = None
    if has_auto:
        raw_auto = data.get('auto_publish')
        if not isinstance(raw_auto, bool):
            return jsonify({
                "success": False,
                "error": f"auto_publish 必须是 JSON 布尔 true/false（收到 {raw_auto!r}）。"
                         "字符串 \"true\" 一律拒绝：把它当真会让客户端的序列化 bug "
                         "变成一个看不出来的行为差异",
                "code": 1001
            }), 400
        auto_publish = raw_auto

    daily_publish_time = None
    if has_daily_time:
        daily_publish_time = _parse_meal_time(data.get('daily_publish_time'))
        if not daily_publish_time:
            return jsonify({
                "success": False,
                "error": "daily_publish_time 必须是 HH:MM（00:00–23:59）",
                "code": 1001,
            }), 400

    # ── 第二阶段：全部校验通过，统一写入 ──
    for key in meals.meal_keys_ordered():
        if key in validated:
            db.set_meal_schedule(key, validated[key])
    if lead_minutes is not None:
        settings_store.set_value(settings_store.KEY_LEAD_MINUTES, lead_minutes)
    if auto_publish is not None:
        settings_store.set_value(settings_store.KEY_AUTO_PUBLISH, auto_publish)
    if daily_publish_time is not None:
        settings_store.set_value(settings_store.KEY_DAILY_PUBLISH_TIME, daily_publish_time)

    logger.info("Meal schedule updated: schedule=%s daily_publish_time=%s "
                "lead_minutes=%s auto_publish=%s",
                validated, daily_publish_time, lead_minutes, auto_publish)
    return jsonify(_meal_schedule_payload())


@app.route('/api/recommend', methods=['POST'])
def api_recommend():
    """
    请求菜谱推荐

    请求：
    {
        "meal_type": "晚餐（可选）",
        "people_count": 3（可选）
    }
    """
    data = request.get_json() or {}

    meal_type = data.get('meal_type')
    people_count = data.get('people_count')

    # 如果没有指定，通过对话处理
    if meal_type is None or not str(meal_type).strip():
        message = "该做饭了"
    else:
        meal_key = meals.meal_key(meal_type)
        if meal_key is None:
            return jsonify({
                "success": False,
                "error": f"无法识别餐次 {meal_type!r}"
                         f"（可选 {'/'.join(meals.meal_keys_ordered())} 或 早餐/午餐/晚餐）",
                "code": 1001
            }), 400
        message = f"推荐{meals.meal_label(meal_key)}"
        if people_count:
            message += f"，{people_count}人"

    result = chat(message)
    return jsonify(result)


#: SSE 响应头。`X-Accel-Buffering: no` 是**必须**的：部分反向代理默认会缓冲上游响应，
#: 一开缓冲，逐字生成的内容会攒到全部结束才一次性到达客户端，而两端都不报错。
_SSE_HEADERS = {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}


def _sse_response(events):
    """把事件字典的迭代器包成 SSE 响应。

    每个事件一行 `data: {json}`，异常也转成 error 事件送出去 —— 生成器里抛出的异常在
    流式响应里不会变成 HTTP 5xx（响应头早已发出），客户端只会看到连接莫名断开。
    """
    def body():
        try:
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            # 客户端主动断开（切走页面 / 取消请求）。不是错误，也无处可送事件。
            raise
        except Exception as exc:
            logger.exception('流式生成异常中断：%s', exc)
            payload = {'type': 'error', 'message': '生成中断，请重试'}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(body()), headers=_SSE_HEADERS)


@app.route('/api/recommend/stream', methods=['POST'])
def api_recommend_stream():
    """流式生成菜谱（SSE）。

    请求：`{"meal_type": "晚餐", "people_count": 3, "user_input": "想吃清淡的"}`
    响应：`text/event-stream`，事件见 [agent.stream_recommendation]。

    与 `/api/recommend` 并存而不是替换它：调度器（餐前自动下发）跑在没有客户端连接的
    后台线程里，流式对它没有意义。
    """
    data = request.get_json(silent=True) or {}
    meal_type = data.get('meal_type') or '晚餐'
    meal_key = meals.meal_key(meal_type)
    if meal_key is None:
        return jsonify({
            "success": False,
            "error": f"无法识别餐次 {meal_type!r}"
                     f"（可选 {'/'.join(meals.meal_keys_ordered())} 或 早餐/午餐/晚餐）",
            "code": 1001
        }), 400

    people_count = data.get('people_count')
    if not people_count:
        members = db.get_all_members()
        people_count = len(members) if members else 3

    return _sse_response(stream_recommendation(
        meals.meal_label(meal_key), int(people_count),
        str(data.get('user_input') or '')
    ))


@app.route('/api/chat/stream', methods=['POST'])
def api_chat_stream():
    """流式对话（SSE）。

    请求：`{"message": "该做晚饭了", "session_id": "default"}`
    响应：`text/event-stream`，事件见 [agent.stream_chat]。

    菜谱类意图会转到流式菜谱生成，纯数据库类意图直接产出 result 事件。
    """
    data = request.get_json(silent=True) or {}
    message = str(data.get('message') or '').strip()
    if not message:
        return jsonify({"success": False, "error": "缺少 message", "code": 1001}), 400

    return _sse_response(stream_chat(
        message, str(data.get('session_id') or 'default')
    ))


@app.route('/api/meal-plan/revise', methods=['POST'])
def api_meal_plan_revise():
    """按一句自然语言改某一餐的菜单，**只算不写**。

    请求：{"meal_type": "晚餐", "instruction": "把红烧肉换成清淡点的"}
    响应：{"success": true, "meal_type", "before": [...], "after": [...],
           "added": [...], "removed": [...], "plan": {...}, "missing_ingredients": [...],
           "base_plan_id": 42}

    结果不落库 —— 用户要先看 diff 再决定。确认时调 `/api/meal-plan/apply`，把这里返回的
    `plan` 与 `base_plan_id` 原样带回：让「确认」再调一次模型会得到与用户看过的 diff 不同
    的结果；不带 `base_plan_id` 则会盖掉这期间的其它写入（定时推荐、另一台手机）。

    失败时 HTTP 仍是 200 + success=false（除参数错误），因为「还没有这一餐的菜单」和
    「改后命中过敏源」都是正常业务态，客户端要按不同文案引导而不是显示网络错误。
    """
    data = request.get_json(silent=True) or {}
    instruction = str(data.get('instruction') or '').strip()
    if not instruction:
        return jsonify({
            "success": False, "error": "缺少 instruction", "code": 1001
        }), 400
    if len(instruction) > 200:
        # 上界防的是把整段聊天记录塞进来：那会挤掉 prompt 里的库存与画像，
        # 而挤掉画像意味着过敏约束也一起没了。
        return jsonify({
            "success": False, "error": "修改要求太长了，请说重点（200 字以内）", "code": 1001
        }), 400

    result = agent.revise_plan(
        meal_type=data.get('meal_type'),
        instruction=instruction,
    )
    return jsonify(result)


@app.route('/api/meal-plan/remove-dish', methods=['POST'])
def api_meal_plan_remove_dish():
    """从某一餐删掉一道菜（表单式微调，不调模型）。

    请求：{"meal_type": "晚餐", "dish_name": "红烧肉"}
    响应：{"success": true, "meal_type", "plan_id", "dishes": [...], "missing_ingredients": [...]}

    删菜放在后端而不是让 App 提交改好的整份方案：客户端的 DTO 是强类型的，只声明了要显示
    的字段，回传时 steps / ingredients 这些未声明的字段会静默丢失 —— 落库的菜谱没有做法，
    而删菜这个操作看起来完全成功。详见 agent.remove_dish 的注释。
    """
    data = request.get_json(silent=True) or {}
    dish_name = str(data.get('dish_name') or '').strip()
    if not dish_name:
        return jsonify({"success": False, "error": "缺少 dish_name", "code": 1001}), 400

    result = agent.remove_dish(meal_type=data.get('meal_type'), dish_name=dish_name)
    # 「这是最后一道菜」「没有这道菜」都是业务态，用 200 + success=false 让客户端按文案引导；
    # 只有请求本身不合法才用 4xx。基准在读取与落库之间被顶掉是并发冲突，与 apply 同口径回 409，
    # 客户端刷新一次当前菜单就能重试。
    if result.get('reason') == 'stale_base':
        return jsonify(result), 409
    return jsonify(result)


@app.route('/api/meal-plan/apply', methods=['POST'])
def api_meal_plan_apply():
    """把一份改好的菜单落库，**只写不算**（不调模型）。

    请求：{"meal_type": "晚餐", "plan": {...}, "people_count": 3, "base_plan_id": 42}
    响应：{"success": true, "meal_type", "plan_id", "dishes": [...], "missing_ingredients": [...]}

    两个入口共用它：说人话改单的「确认」（plan 来自 revise），以及表单式微调的删菜
    （plan 由 App 从现有方案里筛掉几道后提交）。

    `base_plan_id` 来自 revise 的响应，是乐观并发令牌。基准已被别的写入顶掉时回 **409**，
    客户端应提示「菜单变过了，请重新调整」并刷新当前菜单；与 400（提交的数据本身不能用）
    分开是必要的，409 重新 revise 一次就能继续。

    落库后**不自动下发**：要不要现在让设备换画面是另一个决定。
    App 在需要时调 `/api/scheduler/trigger/publish`。
    """
    data = request.get_json(silent=True) or {}
    plan = data.get('plan')
    if plan is None:
        return jsonify({"success": False, "error": "缺少 plan", "code": 1001}), 400

    base_plan_id = data.get('base_plan_id')
    if base_plan_id is not None:
        try:
            base_plan_id = int(base_plan_id)
        except (TypeError, ValueError):
            return jsonify({
                "success": False, "error": "base_plan_id 必须是整数", "code": 1001
            }), 400

    people_count = data.get('people_count')
    if people_count is not None:
        try:
            people_count = int(people_count)
        except (TypeError, ValueError):
            return jsonify({
                "success": False, "error": "people_count 必须是整数", "code": 1001
            }), 400
        if not (1 <= people_count <= 20):
            return jsonify({
                "success": False, "error": "people_count 应在 1..20", "code": 1001
            }), 400

    result = agent.apply_revised_plan(
        meal_type=data.get('meal_type'),
        plan=plan,
        people_count=people_count,
        base_plan_id=base_plan_id,
    )
    if result.get('success'):
        return jsonify(result), 200
    # 基准过期是并发冲突而非提交数据有问题，客户端重新 revise 一次就能继续，故与 400 分开；
    # 校验不通过（结构错 / 命中过敏源）用 400。
    if result.get('reason') == 'stale_base':
        return jsonify(result), 409
    return jsonify(result), 400


def _guess_meal_key_by_clock() -> str:
    """按当前时间推断「现在关心的是哪一餐」，口径与 trigger_recommendation() 一致。

    10 点前看早餐、14 点前看午餐、其余看晚餐。用本地时间是对的：这是在回答
    「站在冰箱前的人此刻想看哪一餐」，那个判断只能按人所在时区的钟点来做。
    """
    hour = datetime.now().hour
    if hour < 10:
        return 'breakfast'
    if hour < 14:
        return 'lunch'
    return 'dinner'


def _plan_age_minutes(created_at: str):
    """菜谱方案距今多少分钟；无法解析 created_at 时返回 None。

    meal_plan.created_at 由 SQLite 的 CURRENT_TIMESTAMP 写入，是 **UTC** 文本
    （'YYYY-MM-DD HH:MM:SS'），必须拿 datetime.utcnow() 去比；用本地时间在 +0800 下会
    凭空多算 8 小时，当天早上刚生成的方案会被判成隔夜旧菜单。
    """
    if not created_at:
        return None
    text = str(created_at).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M'):
        try:
            created = datetime.strptime(text, fmt)
        except ValueError:
            continue
        delta = (datetime.utcnow() - created).total_seconds() / 60
        return max(0, int(delta))
    return None


# 超过这个时长的方案标记为 stale：食材大概已经动过，菜单只能当参考。
# 与调度器复用旧方案的窗口 _utc_window_start(12) 取同一个数，避免「App 说菜单过期了、
# 设备上还在用它」。
_PLAN_STALE_MINUTES = 12 * 60


@app.route('/api/recommend/latest', methods=['GET'])
def api_recommend_latest():
    """读取**已经生成并落库**的最近一份菜谱方案，不调用模型。

    查询参数：
      meal_type  可选，中英文皆可（breakfast / 早餐 / lunch / 午餐 ...）。
                 不给就按当前钟点推断：10 点前早餐、14 点前午餐、其余晚餐。

    与 POST /api/recommend 分开：后者内部走 chat()，每次必然打一次模型（实测约 25 秒），
    且每次菜品都不一样。餐前定时任务已把当天方案生成好落库，所以菜谱页只做读取，
    换菜单是一次显式的 POST /api/recommend。

    响应 200：
    {"success": true, "plan_id": 12, "meal_type": "lunch", "meal_label": "午餐",
     "people_count": 3, "created_at": "...", "selected_plan": null,
     "age_minutes": 18, "stale": false, "plan_a": {...}, "plan_b": {...}}

    响应 404（库里一份都没有）：{"success": false, "error": "...", "code": 4004}
    App 据此展示「点击生成」的入口。
    """
    raw_meal_type = (request.args.get('meal_type') or '').strip()
    if raw_meal_type:
        key = meals.meal_key(raw_meal_type)
        if key is None:
            # 未知餐次报错而不是回落到某一餐：静默返回另一餐的菜单会让人以为后端串了数据。
            return jsonify({
                "success": False,
                "error": f"无法识别餐次 {raw_meal_type!r}"
                         f"（可选 {'/'.join(meals.meal_keys_ordered())} 或 早餐/午餐/晚餐）",
                "code": 1001
            }), 400
    else:
        key = _guess_meal_key_by_clock()

    # get_pending_plans() 已按 created_at DESC 排序，第一条命中的就是最近一份
    for plan in db.get_pending_plans():
        if meals.meal_key(plan.get('meal_type')) != key:
            continue

        age_minutes = _plan_age_minutes(plan.get('created_at'))
        # 采购清单每次读取时重算而不是随方案落库：库存一直在变，存下来的那份很快就与
        # 冰箱实况不符，而它恰恰用来指导「现在要不要出门买」。成本只是一次库存查询。
        inventory_names = [
            item['name'] for item in db.get_inventory(page_size=1000).get('items', [])
        ]
        plans_for_check = {'plan_a': plan.get('plan_a'), 'plan_b': plan.get('plan_b')}
        missing = find_missing_ingredients(plans_for_check, inventory_names)

        return jsonify({
            "success": True,
            "plan_id": plan.get('id'),
            "meal_type": key,
            "meal_label": meals.meal_label(key),
            "people_count": plan.get('people_count'),
            "created_at": plan.get('created_at'),
            "selected_plan": plan.get('selected_plan'),
            "age_minutes": age_minutes,
            # created_at 解析不出来时不断言过期：一条脏数据不该把可用的菜单标成不可信。
            "stale": age_minutes is not None and age_minutes > _PLAN_STALE_MINUTES,
            "plan_a": plan.get('plan_a'),
            "plan_b": plan.get('plan_b'),
            "missing_ingredients": missing,
        })

    return jsonify({
        "success": False,
        "error": "暂无已生成的菜谱，请先生成",
        "code": 4004
    }), 404


@app.route('/api/confirm', methods=['POST'])
def api_confirm():
    """
    确认菜谱方案

    请求：
    {
        "plan_id": 1,
        "selected_plan": "A"
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少参数"}), 400

    plan_id = data.get('plan_id')
    selected_plan = data.get('selected_plan')

    if not plan_id or not selected_plan:
        return jsonify({"error": "缺少plan_id或selected_plan"}), 400

    result = confirm_plan(plan_id, selected_plan)
    return jsonify(result)


# ==================== 通知接口 ====================

@app.route('/api/notifications', methods=['GET'])
def api_notifications():
    """
    获取通知

    查询参数：
    - unread_only: 是否只返回未读（默认true）
    - type: 按类型筛选（可选）
    """
    unread_only = request.args.get('unread_only', 'true').lower() == 'true'
    notif_type = request.args.get('type')

    if notif_type:
        from .notification import NotificationType, notification_manager
        try:
            ntype = NotificationType(notif_type)
            notifications = notification_manager.get_by_type(ntype, unread_only=unread_only)
        except ValueError:
            notifications = []
    else:
        notifications = get_notifications(unread_only)

    return jsonify({
        "notifications": notifications,
        "count": len(notifications),
        "unread_count": get_unread_count()
    })


@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
def api_notification_read(notification_id):
    """标记通知为已读"""
    success = mark_read(notification_id)
    return jsonify({"success": success})


@app.route('/api/notifications/read-all', methods=['POST'])
def api_notification_read_all():
    """标记所有通知为已读"""
    count = mark_all_read()
    return jsonify({"success": True, "count": count})


@app.route('/api/notifications/clear', methods=['POST'])
def api_notification_clear():
    """
    清空通知

    请求：
    {
        "type": "recipe"  // 可选，指定类型清空
    }
    """
    data = request.get_json() or {}
    notif_type = data.get('type')

    count = clear_notifications(notif_type)
    return jsonify({"success": True, "count": count})


@app.route('/api/notifications/stats', methods=['GET'])
def api_notification_stats():
    """获取通知统计"""
    stats = get_notification_stats()
    return jsonify(stats)


# ==================== 定时任务接口 ====================

@app.route('/api/scheduler/status', methods=['GET'])
def api_scheduler_status():
    """获取定时任务状态"""
    return jsonify({
        "running": is_scheduler_running(),
        "tasks": get_scheduler_tasks(),
        "history_count": len(get_task_history())
    })


@app.route('/api/scheduler/tasks', methods=['GET'])
def api_scheduler_tasks():
    """获取所有定时任务"""
    return jsonify({
        "tasks": get_scheduler_tasks()
    })


@app.route('/api/scheduler/history', methods=['GET'])
def api_scheduler_history():
    """获取任务执行历史"""
    return jsonify({
        "history": get_task_history()
    })


@app.route('/api/scheduler/trigger/recommend', methods=['POST'])
def api_trigger_recommendation():
    """
    手动触发推荐

    请求：
    {
        "meal_type": "晚餐",  // 可选
        "people_count": 3     // 可选
    }
    """
    data = request.get_json() or {}
    meal_type = data.get('meal_type')
    people_count = data.get('people_count')

    result = trigger_recommendation(meal_type, people_count)
    return jsonify(result)


@app.route('/api/scheduler/trigger/expiring', methods=['POST'])
def api_trigger_expiring():
    """手动触发过期检查"""
    result = trigger_expiring_check()
    return jsonify(result)


@app.route('/api/scheduler/trigger/inventory', methods=['POST'])
def api_trigger_inventory():
    """手动触发库存检查"""
    result = trigger_inventory_check()
    return jsonify(result)


@app.route('/api/scheduler/trigger/publish', methods=['POST'])
def api_trigger_display_publish():
    """手动触发一次整套画面下发（渲染当天菜单并发布为新版本）。

    请求：{"meal_type": "晚餐", "force": true, "device_id": "dev001"}
      - meal_type 可选，中英文皆可；留空按当前时间推断
      - force 可选，JSON 布尔，默认 false
      - device_id 可选；提供时只发布到该设备，省略时保持原有的全部目标设备行为

    自动下发每天只有一个窗口，这个入口让改动可以立即验证，不必等到次日更新时间。
    响应里的 skipped=true 表示「该餐次近期已发过、按幂等规则跳过」，不是失败。

    force=true 供 App 的显式下发按钮使用，跳过 24 小时幂等；自动任务
    （`scheduler:daily`）绝不传，默认 false 保持既有调用方行为不变。

    force 非布尔一律 400，与 /api/meal-schedule 的 auto_publish 同一约定：宽松解析
    （把字符串 "true" 当真）会让客户端的序列化 bug 变成看不出来的行为差异。
    """
    data = request.get_json(silent=True) or {}

    force = data.get('force', False)
    if not isinstance(force, bool):
        return jsonify({
            "success": False,
            "error": f"force 必须是 JSON 布尔 true/false（收到 {force!r}）。"
                     "字符串 \"true\" 一律拒绝：把它当真会让本该幂等的调用变成每次都发，"
                     "而调用方看不出任何差别",
            "code": 1001
        }), 400

    device_id = None
    if 'device_id' in data:
        raw_device_id = data.get('device_id')
        if not isinstance(raw_device_id, str) or not raw_device_id.strip():
            return jsonify({
                "success": False,
                "error": "device_id 必须是非空字符串",
                "code": 1001,
            }), 400
        device_id = raw_device_id.strip()

    result = trigger_display_publish(
        data.get('meal_type'), force=force, device_id=device_id)
    return jsonify(result), 200 if result.get('success') else 500


# ==================== 库存历史接口 ====================

@app.route('/api/inventory/history', methods=['GET'])
def api_inventory_history():
    """
    获取库存历史

    查询参数：
    - item_id: 按库存项筛选（可选）
    - action: 按动作筛选（add/deduct/update/delete）
    - days: 查询最近N天（默认7）
    - limit: 返回数量（默认100）
    """
    item_id = request.args.get('item_id', type=int)
    action = request.args.get('action')
    days = int(request.args.get('days', 7))
    limit = int(request.args.get('limit', 100))

    history = get_history(item_id=item_id, action=action, days=days, limit=limit)
    return jsonify({
        "history": history,
        "count": len(history)
    })


@app.route('/api/inventory/history/<int:item_id>', methods=['GET'])
def api_inventory_item_history(item_id):
    """获取特定库存项的历史"""
    limit = int(request.args.get('limit', 20))
    history = get_item_history(item_id, limit=limit)
    return jsonify({
        "item_id": item_id,
        "history": history,
        "count": len(history)
    })


@app.route('/api/inventory/history/statistics', methods=['GET'])
def api_inventory_statistics():
    """
    获取库存统计

    查询参数：
    - days: 统计最近N天（默认7）
    """
    days = int(request.args.get('days', 7))
    stats = get_statistics(days=days)
    return jsonify(stats)


@app.route('/api/inventory/history/daily', methods=['GET'])
def api_inventory_daily():
    """
    获取每日摘要

    查询参数：
    - days: 统计最近N天（默认7）
    """
    days = int(request.args.get('days', 7))
    daily = get_daily_summary(days=days)
    return jsonify({
        "daily": daily,
        "count": len(daily)
    })


# ==================== 健康检查接口 ====================

@app.route('/', methods=['GET'])
@app.route('/healthz', methods=['GET'])
def liveness_probe():
    """存活探测 —— 极轻量，不碰数据库、不做任何外部调用。

    根路径存在是因为 K8s / 容器平台的探针默认常常打 `GET /`，而 HTTP 探针只认 2xx/3xx。
    `/healthz` 是同一处理器的别名。

    与 `/api/health` 的分工：本端点只回答「进程还活着吗」，不依赖任何下游，适合
    liveness/startup（若去查数据库，一次 DB 抖动就会让 kubelet 杀掉健康进程）；
    `/api/health` 回答「各子系统是否正常」，更重，适合人工排查或 readiness。

    无需鉴权：非 /api/ 前缀不经过 token 校验（见 authenticate_request），且只返回
    存活状态与服务名。
    """
    return jsonify({
        'status': 'ok',
        'service': 'smart-fridge-magnet-backend',
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    """
    健康检查

    查询参数：
    - full: 是否完整检查（默认false，只做快速检查）
    """
    full = request.args.get('full', 'false').lower() == 'true'

    if full:
        result = full_health_check()
    else:
        result = quick_health_check()

    return jsonify(result)


@app.route('/api/health/database', methods=['GET'])
def health_database():
    """数据库检查"""
    return jsonify(check_database())


@app.route('/api/health/api', methods=['GET'])
def health_api():
    """API 检查"""
    return jsonify(check_api())


@app.route('/api/health/scheduler', methods=['GET'])
def health_scheduler():
    """定时任务检查"""
    return jsonify(check_scheduler())


@app.route('/api/health/inventory', methods=['GET'])
def health_inventory():
    """库存检查"""
    return jsonify(check_inventory())


@app.route('/api/health/profile', methods=['GET'])
def health_profile():
    """画像检查"""
    return jsonify(check_profile())


@app.route('/api/health/nutrition', methods=['GET'])
def health_nutrition():
    """营养数据检查"""
    return jsonify(check_nutrition())


# ==================== 设备显示接口（Wi-Fi 上屏核心）====================

from .device_display import (
    MANIFEST_MAX_RESPONSE_BYTES,
    upload_film, get_manifest, download_film, download_film_by_name,
    get_sync_status, list_devices,
    device_token_matches, device_token_registered, set_device_token,
    get_device_layout, set_device_layout, get_device_layout_options,
    record_device_status, get_device_status, acknowledge_applied_version,
    record_display_result,
    token_matches_any_device,
    set_force_next, log_device_auth_failure
)


@app.route('/api/devices', methods=['GET'])
def api_devices_list():
    """列出所有设备"""
    devices = list_devices()
    return jsonify({"devices": devices, "count": len(devices)})


@app.route('/api/devices/<device_id>/display', methods=['POST'])
def api_device_display_upload(device_id):
    """
    Android 上传 .film 文件

    请求：application/octet-stream，body 为 .film 二进制数据

    响应：
    {
        "success": true,
        "version": 17,
        "size": 209120,
        "sha256": "...",
        "device_id": "..."
    }
    """
    # 检查 Content-Type
    content_type = request.content_type or ''
    if 'octet-stream' not in content_type and 'film' not in content_type:
        return jsonify({
            "success": False,
            "error": f"Content-Type 必须是 application/octet-stream，当前: {content_type}"
        }), 415

    # 读取二进制数据
    film_data = request.get_data()
    if not film_data:
        return jsonify({"success": False, "error": "请求体为空"}), 400

    logger.info(f"Receiving film upload: device={device_id}, size={len(film_data)}")

    result = upload_film(device_id, film_data, uploaded_by='android')

    if result["success"]:
        return jsonify(result), 201
    else:
        # 区分错误类型
        if "大小错误" in result.get("error", ""):
            return jsonify(result), 413
        elif "文件头验证失败" in result.get("error", ""):
            return jsonify(result), 422
        else:
            return jsonify(result), 500


def _request_base_url() -> str:
    """推导「设备本次请求所用的对外基址」，用于拼 manifest 里的 film_url。

    film_url 必须是**设备视角可达**的地址，而该地址常不由后端决定（容器 IP 随机分配、
    入口可能是 Ingress / NodePort / LB）。设备既然拉到了 manifest，它用的地址一定是通的。

    本服务跑在反向代理之后，代理终结 TLS 后到后端这一跳是明文，request.scheme 永远是
    http，外部真实协议只能从代理注入的头读。不同代理头名不同：
      - caddy / 多数云 LB：X-Forwarded-Proto、X-Forwarded-Host（事实标准）
      - 仅提供 X-Scheme 的反向代理：**X-Scheme**、Host（不发 X-Forwarded-Proto/-For）
    只认 X-Forwarded-Proto 会在部分反向代理后静默降级成 http，固件按 https 硬约束拒收
    manifest，而后端日志一切 200。故取值优先级：
        scheme: X-Forwarded-Proto → X-Scheme → request.scheme
        host:   X-Forwarded-Host → Host
    两类头都可能是逗号分隔的链式值（多层代理），取第一个即最初的外部值。

    这些头客户端也能伪造，但影响面仅限「返回给该请求者自己的 URL」，不构成实际风险。
    """
    def _first(value: str) -> str:
        return value.split(',')[0].strip()

    forwarded_host = request.headers.get('X-Forwarded-Host', '')
    host = _first(forwarded_host) if forwarded_host else (request.host or '')
    if not host:
        return ''

    # 依次尝试各代理的协议头，全都没有才回退到本跳的 scheme。
    scheme = ''
    for header in ('X-Forwarded-Proto', 'X-Scheme'):
        raw = request.headers.get(header, '')
        if raw:
            scheme = _first(raw).lower()
            break
    if not scheme:
        scheme = request.scheme
    return f'{scheme}://{host}'


def _render_and_publish(device_id: str, film_bytes: bytes, kind: str):
    """把渲染好的 .film 走既有上传流程入库，并统一构造响应。

    复用 upload_film()：版本递增、SHA-256、结构校验、落盘路径、同步日志都在那里。
    """
    result = upload_film(device_id, film_bytes, uploaded_by=f'backend-render:{kind}')
    if not result.get('success'):
        # 后端自产的图没通过自己的校验，说明渲染或编码有 bug，不是调用方的错
        logger.error(f"Rendered film rejected by validator: {result.get('error')}")
        return jsonify(result), 500
    result['kind'] = kind
    return jsonify(result), 201


@app.route('/api/devices/<device_id>/display/render', methods=['POST'])
def api_device_display_render(device_id):
    """后端渲染菜单画面并作为新版本发布（无需 App 生成 .film）。

    请求（application/json，字段均可选）：
    {
      "kind": "overview" | "breakfast" | "lunch" | "dinner",   默认 overview
      // kind=overview 时：
      "title": "今日菜单", "date": "7月27日",
      "dishes": ["午餐|红烧肉（40分钟）", "汤品|冬瓜排骨汤"],
      "expiring": "上海青 2天内到期",
      // kind=breakfast/lunch/dinner 时：
      "lines": ["红烧肉", "食材：五花肉500g、冰糖20g", "用时 40分钟"]
    }

    **本端点是单张接口**：一次请求只渲染一张，发布出的版本里也只有这一张
    （落在 slot='current'，下载地址为 `.../display/{version}.film`）。

    整套发布走调度器那条路：`POST /api/scheduler/trigger/publish`（餐前窗口也执行同一段
    逻辑），它按设备的 layout 偏好渲染四张并一次 upload_film_set。本端点发布的版本只含
    一张，设备上其余三张仍是上一套旧图，到了那个时段固件会照常显示过期详情页。
    需要四张互相一致时不要用本端点。

    单张版本在 manifest 里**不输出 items[]**，只给 film_url / size / sha256。新固件因此
    item_count == 0，走单张路径 wifi_download_film_streaming()（落盘 current.film 并立即
    上屏）；旧固件本来就只解析这三个字段。见 device_display.get_manifest() 与
    service_wifi.c —— 单张若混进 items[]，整套下载路径的收尾清理会删掉刚提交的
    current.film。
    """
    data = request.get_json(silent=True) or {}
    try:
        kind, film_bytes = _film_from_payload(data, device_id=device_id)
    except RuntimeError as exc:
        # 缺中文字体属于部署问题，必须明确报出来 —— 否则会渲染出方框图并正常下发，
        # 错误只在电子纸上可见。
        logger.error(f"Render failed: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    return _render_and_publish(device_id, film_bytes, kind)


@app.route('/api/render/film', methods=['POST'])
def api_render_film():
    """只渲染、不发布：直接返回 `.film` 字节（application/octet-stream）。

    请求体与 /display/render 完全一致（kind / title / date / dishes / expiring / lines）。

    为 BLE 直传而存在。Android 经蓝牙推画面时是自己把字节写进设备的，用不到云端版本
    记录；若走 /display/render 会顺带发布一个新版本，设备下次联网轮询又会把同一张图
    再下载一遍（一次整帧 + 一次刷屏）。

    渲染实现只有后端一份（见 docs/contracts/README.md），App 取它的产物，
    BLE 与 Wi-Fi 两条通道显示的就是同一张图。

    可带 device_id：BLE 菜谱直传据此使用该设备已保存的版式；省略时保持默认版式兼容旧客户端。
    """
    data = request.get_json(silent=True) or {}
    try:
        raw_device_id = data.get('device_id')
        if raw_device_id is not None and not isinstance(raw_device_id, str):
            raise ValueError('device_id 必须是字符串')
        device_id = (raw_device_id or '').strip() or None
        kind, film_bytes = _film_from_payload(data, device_id=device_id)
    except RuntimeError as exc:
        logger.error(f"Render failed: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    logger.info(f"Film rendered without publishing: kind={kind}, size={len(film_bytes)}")
    return Response(
        film_bytes,
        mimetype='application/octet-stream',
        headers={
            'Content-Length': str(len(film_bytes)),
            # 让调用方无需解析响应体就能确认渲染的是哪一张、以及字节是否完整
            'X-Film-Kind': kind,
            'X-Film-Sha256': hashlib.sha256(film_bytes).hexdigest(),
        }
    )


def _film_from_payload(data: dict, device_id: str = None) -> tuple:
    """把渲染请求体转成 (kind, film_bytes)。

    两个调用方共用（/display/render 与 /render/film），字段语义、默认值、kind 白名单
    必须一致。

    Raises:
        ValueError: 请求参数不合法（kind 未知、行数据非法）→ 调用方应回 400
        RuntimeError: 渲染环境问题（缺中文字体）→ 调用方应回 500
    """
    kind = (data.get('kind') or 'overview').strip().lower()
    layout = get_device_layout(device_id) if device_id else None
    layout_options = (
        menu_render.normalize_layout_options(get_device_layout_options(device_id))
        if device_id else None
    )

    if kind == 'overview':
        date_text = (data.get('date') or '').strip()
        if not date_text:
            now = datetime.now()
            date_text = f'{now.month}月{now.day}日'
        return kind, menu_render.render_overview_film(
            title=(data.get('title') or '今日菜单'),
            date_text=date_text,
            dishes=data.get('dishes') or [],
            expiring=(data.get('expiring') or '').strip(),
            meal_times=data.get('meal_times') or {},
            details=data.get('details') or {},
            layout=layout,
            layout_options=layout_options,
        )

    if kind in menu_render.MEAL_STYLES:
        return kind, menu_render.render_meal_detail_film(
            kind,
            data.get('lines') or [],
            layout=layout,
            layout_options=layout_options,
        )

    raise ValueError(
        f"未知 kind: {kind}（可选 overview / {' / '.join(sorted(menu_render.MEAL_STYLES))}）"
    )


# ==================== 自定义图片上屏（手绘涂鸦 / 相册照片）====================
#
# App 的「手绘涂鸦」与「相册照片」两个入口都落到这里：任意图片 -> 792x528 六色 `.film`。
#
# **第一版只服务 BLE 直传**，所以只有「只渲染不发布」这一条端点，没有发布版。
# 走 Wi-Fi 需要给自定义图在 manifest 里占一个槽位，而固件的 MANIFEST_MAX_ITEMS 是 4
# （总览 + 早/中/晚），要抬到 5 并同步扩后端的 slot 白名单 —— 两处都在跨端契约上
# （docs/contracts/README.md），改完必须重烧固件才能验证。BLE 直传本来就绕开 manifest，
# 第一版不值得为此付跨端的代价。

from PIL import Image                              # 与 menu_render 共用同一份 Pillow
from . import film_encoder as fe
# EXIF 方向纠正复用既有实现，不另写一份：两份逻辑必然漂移，而漂移的表现是
# 「手机上看着是正的、设备上是躺着的」，看起来像渲染 bug 而不像方向 bug。
from .image_processor import fix_orientation

# 缩放策略。语义与网页调试工具的 fitToImageData() 一一对应 —— 那个工具是排障基准
# （「工具里预览成什么样，设备上就是什么样」），两边语义一漂就没法互相印证了。
_IMAGE_FIT_MODES = ('cover', 'contain', 'stretch')
# 默认 cover：contain 的留白在六色电子纸上是一大片死白，观感远差于裁掉一点边缘。
_DEFAULT_IMAGE_FIT = 'cover'

# 画布底色取调色板里那个精确的白（索引 1），不是随便一个 (255,255,255)：
# 留白区域因此会被量化成索引 1，不经过任何最近色判定。
_CANVAS_WHITE = fe.PALETTE_RGB[1]

# 解码后的像素总数上限。5000 万约是手机相机的十几倍分辨率，正常照片远达不到；
# 它拦的是解码炸弹 —— 几十 KB 的 PNG 能解出上亿像素，那不是慢，是直接吃满内存。
#
# 刻意**不设** Flask 的 MAX_CONTENT_LENGTH（本应用至今没有设过，这里也不引入）：
# 那是全局开关，会一并卡住 /api/recognize 的上传，而识别那条路允许 10MB 原图
# （image_processor.MAX_FILE_SIZE）。限制只落在本端点内部，按解码后的像素数判。
_MAX_SOURCE_PIXELS = 50_000_000


def _fit_to_canvas(source: Image.Image, fit: str) -> Image.Image:
    """把任意尺寸的图放进 792x528 白底画布，返回 RGB 图像。

    三种策略的几何与 devtool fitToImageData() 对应：
      cover   等比放大到填满，居中裁掉溢出（默认）
      contain 等比缩到装得下，居中留白（白底）
      stretch 直接拉伸到 792x528，不保持比例

    两个必须如此的细节：

    1. 底色必须是白，且**先**按 alpha 合成到白底再缩放。六色电子纸没有 alpha，直接
       `convert('RGB')` 会把透明像素当黑；先缩放后合成会在边缘留灰晕，因为全透明像素的
       RGB 往往是垃圾值，LANCZOS 会把它混进邻近可见像素。

    2. 重采样用 `Image.LANCZOS`：最近邻会让涂鸦斜线出锯齿，双线性偏软。中间色交给
       量化按最近色归并（这也是不做抖动的前提，见端点 docstring）。
    """
    canvas_w, canvas_h = fe.FILM_WIDTH, fe.FILM_HEIGHT

    if source.mode in ('RGBA', 'LA') or (
            source.mode == 'P' and 'transparency' in source.info):
        rgba = source.convert('RGBA')
        flat = Image.new('RGB', rgba.size, _CANVAS_WHITE)
        flat.paste(rgba, (0, 0), rgba.getchannel('A'))
        source = flat
    elif source.mode != 'RGB':
        source = source.convert('RGB')

    if fit == 'stretch':
        return source.resize((canvas_w, canvas_h), Image.LANCZOS)

    scale = (max if fit == 'cover' else min)(
        canvas_w / source.width, canvas_h / source.height)
    # round 而非 int()：向下取整会让 cover 算出比画布小 1px 的尺寸，右/下边缘留一条白线。
    scaled_w = max(1, round(source.width * scale))
    scaled_h = max(1, round(source.height * scale))
    resized = source.resize((scaled_w, scaled_h), Image.LANCZOS)

    canvas = Image.new('RGB', (canvas_w, canvas_h), _CANVAS_WHITE)
    # 负偏移（cover 时图比画布大）由 paste 自行裁剪，等价于 canvas 的 drawImage(dx,dy,dw,dh)。
    # devtool 的 (w-dw)/2 可能是半像素，这里取整；逐字节一致的硬约束只针对 film_encoder
    # 的量化与打包。
    canvas.paste(resized, ((canvas_w - scaled_w) // 2, (canvas_h - scaled_h) // 2))
    return canvas


@app.route('/api/render/image', methods=['POST'])
def api_render_image():
    """把任意图片（手绘涂鸦 / 相册照片）渲染成 `.film` 字节，只渲染、不发布。

    请求：multipart/form-data
      image  图片文件（必填），PNG / JPEG / WebP 等 Pillow 能解码的格式
      fit    cover（默认）| contain | stretch，见 _fit_to_canvas()

    响应：200 `application/octet-stream`，字节数见 docs/contracts/README.md，
          带 `Content-Length` / `X-Film-Sha256`（小写 hex）/ `X-Film-Fit`。
    400：缺 image、无法解码、fit 非法、像素总数超上限。500：渲染异常。
    鉴权与其余业务接口一致（before_request 的 app_token）。

    用 multipart 而不是 JSON + base64：与既有 /api/recognize 一致，且 base64 会把
    请求里最大的一块（图片本身）再放大 1/3。

    App 侧预览只做缩放/裁剪，不自己量化：拿到字节后跳过 32 字节头、按半字节取索引
    0..5 查 6 色调色板（film_encoder.PALETTE 的 RGB 列，偶数像素在高 4 位）即可。
    编码只有后端一份，见 docs/contracts/README.md。

    不做抖动（dither）：后端、网页调试工具、固件校验三端依赖同一份纯最近色量化，
    才有「调试工具里预览什么样，设备上就是什么样」这个排障前提。

    照片是这条路上最慢的输入：film_encoder.quantize_rgb() 按精确 RGB 值做字典缓存，
    菜单图只有几百种取值故极快；照片去重后可能上万种颜色，缓存基本不命中，退化为
    逐像素全精度计算（每像素一次 HSL + 三次 Lab + sqrt）。实测数字见 backend/README.md。
    本端点不改 quantize_rgb 的算法 —— tests/test_film_encoder.py 用它与网页工具的 JS
    实现做逐字节 SHA-256 交叉验证。
    """
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "请上传图片（multipart 字段 image）"}), 400

    upload = request.files['image']
    if not upload.filename:
        return jsonify({"success": False, "error": "未选择文件"}), 400

    fit = (request.form.get('fit') or _DEFAULT_IMAGE_FIT).strip().lower()
    if fit not in _IMAGE_FIT_MODES:
        # 不静默回落到 cover：参数拼错时画面偏差在设备上看起来像渲染 bug，不像参数错。
        return jsonify({
            "success": False,
            "error": f"未知 fit: {fit}（可选 {' / '.join(_IMAGE_FIT_MODES)}）",
            "code": 1001
        }), 400

    started = time.perf_counter()
    try:
        # Image.open 只读文件头、还没解码像素，尺寸检查放在真正解码之前，解码炸弹在这里
        # 就被拦住。用 with 并只把尺寸带出块：Image 对象会持有文件句柄直到被 GC，而
        # canvas 是 _fit_to_canvas 产出的新对象，不依赖原句柄。
        with Image.open(upload.stream) as opened:
            pixels = opened.width * opened.height
            if pixels > _MAX_SOURCE_PIXELS:
                return jsonify({
                    "success": False,
                    "error": f"图片像素过多：{opened.width}x{opened.height} = {pixels}，"
                             f"上限 {_MAX_SOURCE_PIXELS}",
                    "code": 1001
                }), 400
            source_size = (opened.width, opened.height)
            canvas = _fit_to_canvas(fix_orientation(opened), fit)
    except OSError as exc:
        # Pillow 认不出格式或数据截断都是 OSError（UnidentifiedImageError 是其子类），
        # 属于调用方数据问题，回 400。
        logger.warning(f"Custom image decode failed: {exc}")
        return jsonify({
            "success": False, "error": f"无法解码图片：{exc}", "code": 1001
        }), 400
    except Exception as exc:
        logger.error(f"Custom image prepare failed: {exc}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": f"渲染失败：{exc}"}), 500

    try:
        # 量化与打包一律走 film_encoder：`.film` 的生产实现只有一份，
        # 见 docs/contracts/README.md。
        film_bytes = fe.encode_rgb_image(canvas.tobytes())
    except Exception as exc:
        # 画布尺寸/编码出错说明是后端自己的 bug，不是调用方的输入问题。
        logger.error(f"Custom image encode failed: {exc}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": f"编码失败：{exc}"}), 500

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        f"Custom image rendered: fit={fit} source={source_size[0]}x{source_size[1]} "
        f"size={len(film_bytes)} in {elapsed_ms:.0f}ms"
    )
    return Response(
        film_bytes,
        mimetype='application/octet-stream',
        headers={
            'Content-Length': str(len(film_bytes)),
            # 让 App 无需解析响应体就能校验字节完整（BLE 直传前后各算一次即可）
            'X-Film-Sha256': hashlib.sha256(film_bytes).hexdigest(),
            # 回显实际生效的策略：fit 省略时是 cover
            'X-Film-Fit': fit,
        }
    )


# ==================== 语音识别 ====================

@app.route('/api/asr', methods=['POST'])
def api_asr():
    """用配置的语音模型把一段录音转成文字。

    请求：multipart/form-data
      audio     音频文件（必填），**只接受 wav / mp3**（ASR 的硬限制，不是本端点加的）
      language  auto（默认）| zh | en

    响应：200 `{"success": true, "text": "...", "empty": false, "language": "zh",
               "duration_ms": 1234}`
    400：缺 audio / 格式不支持 / 过大 / language 非法
    502：网关不可达、超时、或返回非 200 —— 问题在上游而不在这一层，不该报成 500
    503：AI_API_KEY 未配置

    走后端而不让 App 直连 AI：直连要把 AI_API_KEY 放进 APK，反编译取常量字符串是
    分钟级的事。用 multipart 而不是 JSON + base64：与既有 /api/recognize、
    /api/render/image 一致，且 base64 会给移动网络上传的那一块再加 1/3 体积
    （ASR 网关只收 base64，转码在服务端做）。

    识别出空串不算失败：按到麦克风又立刻松手、或全程静音都会得到空串，这里回
    200 + `text: ""` 并带 `empty: true`，让 App 显示「没听到内容」而不是一条错误。
    """
    if 'audio' not in request.files:
        return jsonify({
            "success": False, "error": "请上传音频（multipart 字段 audio）", "code": 1001
        }), 400

    # 体积检查必须在 read() **之前**，而且 read() 也要限量。
    #
    # 本应用没有设 Flask 的 MAX_CONTENT_LENGTH（那是全局开关，会一并卡住 /api/recognize
    # 的 10MB 原图），所以这条路径要自己拦：裸 `upload.read()` 会把整份上传读进内存后才
    # 判上限，而后端只有一个 gunicorn worker，OOM 会连带整个服务。
    #
    # 两道都要：Content-Length 让绝大多数超限请求在读一个字节之前就被拒；`read(limit+1)`
    # 兜住没有 Content-Length 的分块传输。多读 1 字节用于区分「正好等于上限」与「超过」。
    declared = request.content_length
    if declared is not None and declared > ai_client.ASR_MAX_AUDIO_BYTES:
        return jsonify({
            "success": False,
            "error": f"音频过大：请求体 {declared} 字节，"
                     f"上限 {ai_client.ASR_MAX_AUDIO_BYTES}（base64 后需 ≤10MB）",
            "code": 1001
        }), 413

    upload = request.files['audio']
    audio_bytes = upload.read(ai_client.ASR_MAX_AUDIO_BYTES + 1)
    if not audio_bytes:
        return jsonify({"success": False, "error": "音频为空", "code": 1001}), 400

    language = (request.form.get('language') or 'auto').strip().lower()
    if language not in ai_client.ASR_LANGUAGES:
        return jsonify({
            "success": False,
            "error": f"language 非法：{language}"
                     f"（可选 {' / '.join(ai_client.ASR_LANGUAGES)}）",
            "code": 1001
        }), 400

    # 格式与大小在进网关前先判掉：这两条是确定的、可以本地判的规则，
    # 让它们走完一次上传再由网关拒绝，只是白花一次移动网络流量。
    fmt = ai_client.sniff_audio_format(audio_bytes)
    if fmt is None:
        return jsonify({
            "success": False,
            "error": "音频格式不支持：只接受 wav / mp3（按文件内容判定，与扩展名无关）",
            "code": 1001
        }), 400
    if len(audio_bytes) > ai_client.ASR_MAX_AUDIO_BYTES:
        # 走到这里说明请求没带 Content-Length（分块传输），上面那道判不出来。
        # 413 而不是 400：这是「实体过大」而不是「参数不对」，客户端的处置也不同
        # （前者要重录短一点，后者要改参数）。
        return jsonify({
            "success": False,
            "error": f"音频过大：超过 {ai_client.ASR_MAX_AUDIO_BYTES} 字节"
                     f"（base64 后需 ≤10MB）",
            "code": 1001
        }), 413

    if not ai_client.AI_API_KEY:
        return jsonify({
            "success": False, "error": "后端未配置 AI_API_KEY，无法语音识别", "code": 1005
        }), 503

    started = time.perf_counter()
    text, error = ai_client.call_ai_asr(audio_bytes, audio_format=fmt, language=language)
    elapsed_ms = (time.perf_counter() - started) * 1000

    if error is not None:
        # 不把上游原文直接抛给用户界面，但要完整记进日志 —— 「网关返回 HTTP 401」这类
        # 信息对用户没用，对排查却是唯一线索。
        logger.error(f"ASR failed: fmt={fmt} bytes={len(audio_bytes)} error={error}")
        return jsonify({
            "success": False, "error": f"语音识别失败：{error}", "code": 1006
        }), 502

    logger.info(
        f"ASR ok: fmt={fmt} bytes={len(audio_bytes)} lang={language} "
        f"chars={len(text)} in {elapsed_ms:.0f}ms"
    )
    return jsonify({
        "success": True,
        "text": text,
        "empty": not text,
        "language": language,
        "duration_ms": round(elapsed_ms),
    })


# ==================== 版式选择与预览 ====================
#
# 预览用的示例菜单写死在这里，**不读库存、不读方案、不打模型**。
# 版式是「这块屏放在哪、从多远看」的决定，人在选的时候冰箱可能是空的、菜谱还没生成，
# 那时若预览出不了图，选版式这件事就直接卡住了。示例数据保证任何时候都能出图。

_PREVIEW_DISHES = [
    '早餐|小米南瓜粥（15分钟）',
    '早餐|水煮蛋（8分钟）',
    '午餐|番茄炒蛋（15分钟）',
    '午餐|蒜蓉上海青（8分钟）',
    '晚餐|红烧排骨（40分钟）',
    '晚餐|冬瓜排骨汤（50分钟）',
]
_PREVIEW_EXPIRING = '上海青、番茄 即将到期'
# 分栏版式的预览要带上餐次时间与用料摘要，否则 App 里预览出来是空栏头、无用料，
# 而设备上是有的 —— 预览必须等于设备真实字节，这是跨端架构不变量。
_PREVIEW_MEAL_TIMES = {'早餐': '07:30', '午餐': '12:00', '晚餐': '18:30'}
_PREVIEW_DETAILS = {
    '小米南瓜粥（15分钟）': '小米 · 南瓜',
    '水煮蛋（8分钟）': '鸡蛋',
    '番茄炒蛋（15分钟）': '番茄 · 鸡蛋 · 葱',
    '蒜蓉上海青（8分钟）': '上海青 · 蒜',
    '红烧排骨（40分钟）': '排骨 · 姜 · 冰糖',
    '冬瓜排骨汤（50分钟）': '排骨 · 冬瓜',
}
_PREVIEW_DETAIL_LINES = {
    'breakfast': ['小米南瓜粥', '食材：小米80g、南瓜200g', '用时 15分钟 · 难度 简单',
                  '营养：膳食纤维丰富', '1. 南瓜切块，小米淘洗', '2. 加水煮沸后小火熬稠'],
    'lunch': ['番茄炒蛋', '食材：番茄2个、鸡蛋3个、葱1根', '用时 15分钟 · 难度 简单',
              '营养：优质蛋白和番茄红素', '1. 番茄切块，鸡蛋打散', '2. 先炒蛋，再下番茄调味'],
    'dinner': ['红烧排骨', '食材：排骨500g、冰糖20g、生姜3片', '用时 40分钟 · 难度 中等',
               '营养：富含蛋白质', '1. 排骨焯水沥干', '2. 炒糖色后放入排骨', '3. 加水焖煮至收汁'],
}


@app.route('/api/render/layouts', methods=['GET'])
def api_render_layouts():
    """列出可选的画面版式。

    不挂在某台设备下面：App 在还没绑定设备时（开箱引导、纯预览）也需要把版式列出来，
    而版式清单本身与设备无关。选定后再用 PUT /api/devices/{id}/prefs 落到具体设备。

    响应：{"layouts": [{"key","label","description","orientation"}, ...]}
    """
    return jsonify({
        "layouts": menu_render.layout_choices(),
        "option_choices": menu_render.layout_option_choices(),
        "option_defaults": menu_render.LAYOUT_OPTION_DEFAULTS,
    })


@app.route('/api/render/preview', methods=['GET'])
def api_render_preview():
    """用示例菜单渲染指定版式，直接返回 PNG（image/png），供 App 里预览挑版式。

    查询参数：
      layout  版式键（见 /api/render/layouts），默认 classic
      kind    overview（默认）| breakfast | lunch | dinner
      natural 1 时把竖版转回 528×792，便于 App 按自然阅读方向展示

    返回 PNG 而不是 `.film`：`.film` 是给固件的 4bit 索引格式，任何图片控件都显示不了。
    PNG 是同一张画布的无损导出，所见即设备所见。

    默认返回设备真实像素方向（竖版也是 792×528）以兼容既有调用方；App 可传 `natural=1`
    取得 528×792 的自然阅读方向，这只改变预览 PNG，不改变下发的 `.film`。

    竖版是「按 528×792 排好版，再旋转成屏幕的横向像素」（见 menu_render._finalize）：
    屏幕物理上是横的，固件收到裸像素流，没有旋转能力。

    响应头 X-Film-Layout / X-Film-Orientation 说明这张图用的版式与朝向。
    """
    layout_key = (request.args.get('layout') or menu_render.DEFAULT_LAYOUT).strip().lower()
    # 归一旧版式键：老客户端与库里存过的偏好仍会发 'bold' 这类已合并的键。
    layout_key = menu_render.LAYOUT_ALIASES.get(layout_key, layout_key)
    if layout_key not in menu_render.LAYOUTS:
        # 接口层严格报错；渲染层 menu_render.get_layout() 对未知值回落默认版式，
        # 那是给库里可能被手工改坏的偏好兜底，两者分工不同。
        return jsonify({
            "success": False,
            "error": f"未知 layout: {layout_key}（可选 {' / '.join(menu_render.LAYOUTS)}）",
            "code": 1001
        }), 400

    kind = (request.args.get('kind') or 'overview').strip().lower()
    natural = request.args.get('natural') == '1'
    spec = menu_render.get_layout(layout_key)
    raw_options = {
        key: request.args.get(key)
        for key in menu_render.LAYOUT_OPTION_DEFAULTS
        if request.args.get(key) is not None
    }
    try:
        layout_options = menu_render.normalize_layout_options(raw_options, strict=True)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc), "code": 1001}), 400

    try:
        if kind == 'overview':
            now = datetime.now()
            image = menu_render.render_overview(
                title='今日菜单',
                date_text=f'{now.month}月{now.day}日',
                dishes=_PREVIEW_DISHES,
                expiring=_PREVIEW_EXPIRING,
                layout=spec,
                layout_options=layout_options,
                meal_times=_PREVIEW_MEAL_TIMES,
                details=_PREVIEW_DETAILS,
            )
        elif kind in menu_render.MEAL_STYLES:
            label, header_color, _filename = menu_render.MEAL_STYLES[kind]
            image = menu_render.render_meal_detail(
                label, header_color, _PREVIEW_DETAIL_LINES[kind], layout=spec,
                layout_options=layout_options,
            )
        else:
            return jsonify({
                "success": False,
                "error": f"未知 kind: {kind}"
                         f"（可选 overview / {' / '.join(sorted(menu_render.MEAL_STYLES))}）",
                "code": 1001
            }), 400
    except RuntimeError as exc:
        # 缺中文字体属于部署问题，必须明确报出来（否则预览会是一张方框图）
        logger.error(f"Preview render failed: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500

    if natural and spec.portrait:
        image = image.rotate(-90, expand=True)

    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    png_bytes = buffer.getvalue()

    return Response(
        png_bytes,
        mimetype='image/png',
        headers={
            'Content-Length': str(len(png_bytes)),
            'X-Film-Layout': spec.key,
            'X-Film-Orientation': 'portrait' if spec.portrait else 'landscape',
            # 预览是纯函数产物（示例数据 + 版式），可以放心让客户端缓存一段时间
            'Cache-Control': 'public, max-age=300',
        }
    )


@app.route('/api/devices/<device_id>/display/render-from-plan', methods=['POST'])
def api_device_display_render_from_plan(device_id):
    """按已生成的菜谱方案渲染画面并发布。

    请求：{"plan_id": 12, "plan": "a" | "b", "kind": "overview" | "lunch" | ...}

    菜谱由 /api/recommend 生成时已落库，这里按 plan_id 取回渲染 —— 既省得调用方
    把菜品数据再传一遍，也保证设备上显示的与当时生成的是同一份。
    kind=overview 渲染整套菜品列表；kind=餐段 则渲染该方案第一道菜的详细做法。
    """
    data = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id')
    if not plan_id:
        return jsonify({"success": False, "error": "缺少 plan_id"}), 400

    record = db.get_meal_plan(plan_id)
    if record is None:
        return jsonify({"success": False, "error": f"方案 {plan_id} 不存在"}), 404

    which = (data.get('plan') or record.get('selected_plan') or 'a').strip().lower()
    plan = record.get('plan_b') if which == 'b' else record.get('plan_a')
    if not plan:
        return jsonify({
            "success": False,
            "error": f"方案 {plan_id} 的 plan_{which} 为空或解析失败"
        }), 422

    kind = (data.get('kind') or 'overview').strip().lower()
    layout = get_device_layout(device_id)
    layout_options = menu_render.normalize_layout_options(
        get_device_layout_options(device_id))
    try:
        if kind == 'overview':
            now = datetime.now()
            meal_type = record.get('meal_type') or ''
            dishes = [
                f"{meal_type}|{line}" if meal_type else line
                for line in menu_render.dishes_from_plan(plan)
            ]
            expiring_items = db.get_expiring_items(days=3) or []
            expiring = '、'.join(item['name'] for item in expiring_items[:4])
            if expiring:
                expiring += ' 即将到期'
            film_bytes = menu_render.render_overview_film(
                title=f'{meal_type}菜单' if meal_type else '今日菜单',
                date_text=f'{now.month}月{now.day}日',
                dishes=dishes,
                expiring=expiring,
                meal_times={
                    (row.get('meal_type') or ''): (row.get('meal_time') or '')
                    for row in db.get_meal_schedule() or []
                },
                details=menu_render.ingredient_summaries(plan),
                layout=layout,
                layout_options=layout_options,
            )
        elif kind in menu_render.MEAL_STYLES:
            dish_list = plan.get('dishes') or []
            if not dish_list:
                return jsonify({"success": False, "error": "该方案没有菜品"}), 422
            lines = menu_render.detail_lines_from_dish(dish_list[0])
            film_bytes = menu_render.render_meal_detail_film(
                kind, lines, layout=layout, layout_options=layout_options)
        else:
            return jsonify({
                "success": False,
                "error": f"未知 kind: {kind}"
            }), 400
    except RuntimeError as exc:
        logger.error(f"Render failed: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500

    response, status = _render_and_publish(device_id, film_bytes, kind)
    payload = response.get_json()
    payload['plan_id'] = plan_id
    payload['plan'] = which
    return jsonify(payload), status


@app.route('/api/devices/<device_id>/display/manifest', methods=['GET'])
def api_device_manifest(device_id):
    """
    ESP32 获取 manifest（版本检查）

    响应：
    {
        "version": 17,
        "film_url": "https://...",
        "size": 209120,
        "sha256": "...",
        "force": false
    }

    一整套（today/breakfast/lunch/dinner）的版本另带 `items[]` 逐张列出；
    **单张发布（slot='current'）不带 items[]** —— 设备因此走单张兼容路径。
    形态判定见 device_display.get_manifest()。
    """
    # 设备在这次请求里顺带上报了电量与固件版本（复用本就会发生的 manifest 检查，
    # 不额外唤醒设备）。旧固件不带这些头，record_device_status 会静默跳过。
    record_device_status(device_id, headers=request.headers)

    # force 只能在设备确认当前版本已经成功落盘后清除。旧固件不带此头时保持
    # force=true，宁可重试，也不能在仅发送 manifest 后丢掉修复机会。
    acknowledge_applied_version(
        device_id, request.headers.get('X-Device-Applied-Version')
    )

    manifest = get_manifest(device_id, request_base_url=_request_base_url())

    if manifest is None:
        logger.warning('设备拉 manifest 但没有可下发内容：device=%s（后端从未发布过画面）',
                       device_id)
        return jsonify({
            "error": "该设备暂无显示内容",
            "code": 4004
        }), 404

    # 设备拉取成功是整条下发链路上最关键的一跳，必须能在日志里看清它拿到了什么。
    # 尤其是 film_url 的 scheme：SFM_BASE_URL 留空时按请求来源推导，若入口不透传
    # X-Forwarded-Proto / X-Scheme 就会推出 http 而固件拒收，表现为「manifest 200、
    # 设备却不下载」。
    film_url = str(manifest.get('film_url') or '')
    item_count = len(manifest.get('items') or [])
    logger.info(
        'manifest 已下发：device=%s version=%s force=%s items=%d scheme=%s applied=%s',
        device_id, manifest.get('version'), manifest.get('force'),
        item_count, (film_url.split('://', 1)[0] or '?') if film_url else '无',
        request.headers.get('X-Device-Applied-Version') or '未报'
    )
    if film_url.startswith('http://'):
        logger.error(
            'manifest 里的 film_url 是 http（device=%s url前缀=%s…）—— 固件会拒收，'
            '设备将一直停在旧画面。请让入口透传 X-Forwarded-Proto/X-Scheme，'
            '或显式配置 SFM_BASE_URL',
            device_id, film_url[:40]
        )
        # 主动失败，不把一份注定被拒的 manifest 当成功交出去。
        #
        # 固件（manifest_parse.c）强制 film_url 为 https，http 的 manifest 会被整份丢弃。
        # 若照旧回 200，后端「下发成功」、App「云端版本已更新」、屏幕却不动，三方都看不出
        # 问题在哪。回 503 后固件把「http 503」写进 last_sync_error 并经 BLE 0x43 交给 App，
        # 与上面那条 ERROR 指向同一个原因。
        #
        # 用 503：这是服务端配置未就绪、重试可恢复，与 config.validate_runtime() 在
        # SFM_BASE_URL 显式配成 http 时的启动期 fail-closed 同一立场（那条只在「配了但
        # 配错」时生效，留空自适应时够不着）。SFM_ALLOW_INSECURE_BASE_URL=true 时豁免，
        # 与启动期检查共用同一个开关。
        if not config.allow_insecure_base_url():
            return jsonify({
                "error": "film_url 只能是 https，当前推导出的是 http，固件会拒收整份 manifest。"
                         "请让设备经 https 入口访问，并让入口透传 X-Forwarded-Proto 或 "
                         "X-Scheme；或显式配置 SFM_BASE_URL。"
                         "仅本机联调可设 SFM_ALLOW_INSECURE_BASE_URL=true 豁免。",
                "code": 5031
            }), 503

    # 顺带回答「有没有新固件」。搭在设备本就会发的这次请求上，而不是让设备再拉一个
    # 接口：设备是电池供电、平时深睡的，每多一次往返都要多醒一段射频。
    # 旧固件不认识这个字段会忽略它，不影响 manifest 本身的契约。
    ota = firmware.check_update(
        request.headers.get('X-Device-Firmware'),
        base_url=_request_base_url(),
    )
    if ota.get('update_available'):
        manifest['firmware'] = {
            'version': ota['latest_version'],
            'url': ota['url'],
            'size': ota['size'],
            'sha256': ota['sha256'],
        }
        encoded_len = len(json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        if encoded_len > MANIFEST_MAX_RESPONSE_BYTES:
            manifest.pop('firmware', None)
            logger.error(
                '加入 OTA 后 manifest 为 %d 字节，超过固件上限 %d，已省略本次升级字段：'
                'device=%s version=%s', encoded_len, MANIFEST_MAX_RESPONSE_BYTES,
                device_id, ota['latest_version'])
        else:
            logger.info('manifest 附带固件升级：device=%s %s -> %s (%d 字节)',
                        device_id, request.headers.get('X-Device-Firmware') or '未报',
                        ota['latest_version'], ota['size'])

    return jsonify(manifest)


# 设备侧「请求重算菜单」的限流状态：device_id -> 上次受理的时间戳（time.monotonic()）。
#
# 模块级 dict 成立的前提是**单进程**：镜像的 CMD 固定 `--workers 1`（见 Dockerfile），
# 并发由线程承载。将来若放开多 worker，这里必须换成共享存储（DB/Redis），
# 否则限流会按 worker 数被放大成 N 倍。
_REFRESH_MIN_INTERVAL_SEC = 300
_last_refresh_request: dict = {}
_last_refresh_lock = threading.Lock()
_refresh_publish_lock = threading.Lock()


@app.route('/api/devices/<device_id>/display/request-refresh', methods=['POST'])
def api_device_request_refresh(device_id):
    """设备侧请求重新生成并发布菜单画面（挂在设备物理按键上）。

    与 `POST /api/scheduler/trigger/publish` 的分工：
      - 那个入口用 **app_token**、**同步**执行并把发布结果（含 skipped/失败原因）
        回给调用方，是给**人**（联调、运维、App）用的；
      - 本入口用 **device_token**（调用方就是设备本身）、**异步**执行、立即返回 202，
        设备拿不到也不需要发布结果——它随后按正常的 manifest 版本门控去拉新版本。

    鉴权：走 `_is_device_refresh_request()` 那条 device_token 分支（见文件顶部鉴权段）。
    """
    # 立即返回 202、把发布放到后台线程：一次 AI 生成实测约 25 秒，而固件的单次 HTTP
    # 超时远小于此（SFM_WIFI_HTTP_TIMEOUT_MS = 30s，连接/TLS 也占同一预算）。同步执行
    # 会让设备超时重试，把「一次生成」变成多次并发生成。
    # 另加进程级互斥：设备请求会进入模型、渲染和 SQLite 写入，仅按 device_id 限流
    # 挡不住多设备并发突发。
    if not _refresh_publish_lock.acquire(blocking=False):
        return jsonify({
            "success": False,
            "accepted": False,
            "error": "已有画面生成任务正在执行，请稍后重试",
            "code": 4029,
            "retry_after": 30,
        }), 429, {"Retry-After": "30"}

    now = time.monotonic()
    with _last_refresh_lock:
        last = _last_refresh_request.get(device_id)
        if last is not None and (now - last) < _REFRESH_MIN_INTERVAL_SEC:
            retry_after = int(_REFRESH_MIN_INTERVAL_SEC - (now - last)) + 1
            logger.warning(
                f"Refresh request throttled: device={device_id} retry_after={retry_after}s"
            )
            response = jsonify({
                "success": False,
                "accepted": False,
                "error": f"请求过于频繁，{retry_after} 秒后可再次请求重算菜单",
                "code": 4029,
                "retry_after": retry_after
            }), 429, {"Retry-After": str(retry_after)}
            _refresh_publish_lock.release()
            return response
        _last_refresh_request[device_id] = now

    # 餐段留空 -> 由 trigger_display_publish 按当前时间推断，设备无需（也无从）判断餐段。
    def _publish_in_background():
        try:
            result = trigger_display_publish(None, device_id=device_id)
            logger.info(f"Device-requested publish finished: device={device_id} result={result}")
        except Exception as e:
            # 后台线程里的异常没有调用方可以承接，只记日志；设备侧表现为「等了 30 秒
            # 仍是旧版本」，可再手动同步一次。
            logger.error(f"Device-requested publish failed: device={device_id} error={e}")
        finally:
            _refresh_publish_lock.release()

    try:
        threading.Thread(
            target=_publish_in_background,
            name=f"refresh-{device_id}",
            daemon=True
        ).start()
    except Exception:
        _refresh_publish_lock.release()
        with _last_refresh_lock:
            _last_refresh_request.pop(device_id, None)
        raise

    # 响应里的 meal 只是「告诉调用方这次按哪个餐段发」的提示，判据与
    # trigger_display_publish 内的推断一致（hour<10 早餐 / <14 午餐 / 其余晚餐）。
    hour = datetime.now().hour
    meal = 'breakfast' if hour < 10 else ('lunch' if hour < 14 else 'dinner')

    return jsonify({
        "success": True,
        "accepted": True,
        "meal": meal,
        "detail": "已开始重新生成并发布，约 30 秒后可拉取新版本"
    }), 202


@app.route('/api/devices/<device_id>/display/preview.png', methods=['GET'])
def api_device_film_preview(device_id):
    """把已发布的 `.film` 反解成 PNG，供 App 显示「设备上这张画面长什么样」。

    查询参数：
      version  版本号，省略 = 最新版本
      name     一套画面里的某一张（如 `1_today.film`），省略 = 该版本的单张
      w        缩放到这个宽度（用于列表缩略图），省略 = 原尺寸 792x528
      orientation  portrait 时顺时针转为实体竖屏方向；省略 = 原始方向

    ── 为什么需要这个接口 ──
    BLE 协议里**没有**「读取设备上 .film 内容」的命令：网页调试工具显示的预览其实是本地
    那份待传字节，不是从设备读回来的。所以 App 想显示「设备上现在有哪几张、每张什么样」，
    只能从后端这边取 —— 后端手里有每个版本的 .film 原文件，而设备上的图正是从这里下载的。

    覆盖不到的情况：通过 BLE 直传上去的自定义图（手绘/相册）后端没有副本，这个接口给不出
    预览，由 App 侧用自己传出去时留的缩略图兜底。

    ── 为什么用 `.png` 结尾 + query 参数而不是路径 ──
    `.png` 结尾让它走 app_token 鉴权分支（`_is_device_pull_request()` 判的是
    `path.endswith('.film')`）—— 预览是给 App 看的，不该要设备令牌。
    而 name 若放进路径，`/display/<int:version>/<path:name>` 那个设备下载路由会先匹配到，
    然后因为不以 `.film` 结尾而回 400，表现成"预览接口没注册"。
    """
    raw_version = request.args.get('version', '').strip()
    name = (request.args.get('name') or '').strip()
    raw_width = request.args.get('w', '').strip()
    orientation = request.args.get('orientation', '').strip().lower()
    if orientation not in ('', 'portrait'):
        return jsonify({"error": "orientation 仅支持 portrait", "code": 1001}), 400

    version = None
    if raw_version:
        if not raw_version.isdigit():
            return jsonify({"error": "version 必须是数字", "code": 1001}), 400
        version = int(raw_version)
    else:
        status = get_sync_status(device_id)
        version = status.get('latest_version') if status else None
        if not version:
            return jsonify({
                "error": f"设备 {device_id} 还没有已发布的画面", "code": 4004
            }), 404

    if name:
        # 与设备下载端点同一套校验：库里存的是文件名，放行 `../` 等于把库外文件也暴露出去。
        if name != os.path.basename(name) or name in ('.', '..') or not name.endswith('.film'):
            return jsonify({"error": "非法文件名", "code": 4003}), 400
        result = download_film_by_name(device_id, version, name, record_download=False)
    else:
        result = download_film(device_id, version, record_download=False)

    if result is None:
        target = name or f"{version}.film"
        return jsonify({
            "error": f"版本 {version} 没有 {target}", "code": 4004
        }), 404

    file_path = result[0]
    try:
        with open(file_path, 'rb') as f:
            film_bytes = f.read()
        indices = fe.film_to_indices(film_bytes)
    except (OSError, ValueError) as exc:
        # 长度或头部不对时 film_to_indices 会抛 ValueError。不降级出一张花屏图：
        # 排障工具给出错误画面会把人引向渲染和固件，而问题在这个文件本身。
        logger.error("预览反解失败 device=%s version=%s name=%s: %s",
                     device_id, version, name or '-', exc)
        return jsonify({
            "error": f"这个 .film 无法解析：{exc}", "code": 5000
        }), 500

    # 出 **P 模式**（调色板）图而不是 RGB：这张图只有 6 种颜色，调色板 PNG 比
    # RGB + optimize 快 25 倍（实测 48ms vs 1235ms）又小 19%。RGB 那条路的开销在
    # `optimize=True` 的暴力压缩尝试，而本接口跑在 --workers 1 的进程里，那 1.2 秒
    # 期间设备拉 manifest 只能排队。
    image = Image.frombytes('P', (fe.FILM_WIDTH, fe.FILM_HEIGHT), bytes(indices))
    image.putpalette(fe.palette_bytes())

    if orientation == 'portrait':
        image = image.transpose(Image.Transpose.ROTATE_270)

    if raw_width:
        if not raw_width.isdigit():
            return jsonify({"error": "w 必须是数字", "code": 1001}), 400
        width = int(raw_width)
        # 下界 32：再小就看不出是什么了；上界为原图宽度：放大只是让字更糊、字节更多。
        width = max(32, min(width, image.width))
        height = max(1, round(width * image.height / image.width))
        # NEAREST 而不是平滑缩放：这是六色量化后的图，插值会造出调色板里不存在的中间色，
        # 让缩略图看起来比设备上的实际画面"更好"，而预览的意义正是如实反映设备观感。
        # 在 P 模式下缩放，NEAREST 只会挑选已有的索引，调色板原样保留。
        image = image.resize((width, height), Image.NEAREST)

    buf = io.BytesIO()
    # 不加 optimize：理由见上面那段注释。P 模式的默认压缩已经比 RGB + optimize 更小。
    image.save(buf, format='PNG')
    payload = buf.getvalue()

    response = Response(payload, mimetype='image/png')
    response.headers['Content-Length'] = str(len(payload))
    # ETag 用真实解析出的版本号（不是请求里的），两种缓存策略下都给。
    response.headers['ETag'] = (
        f'"{device_id}-{version}-{name or "single"}-{orientation or "raw"}-{len(payload)}"'
    )
    if raw_version:
        # 显式指定了版本：这个 version+name 的内容永不改变（新发布拿到的是新版本号），
        # 可以放心长缓存 —— 列表里滚动时不必反复反解同一张图。
        response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
    else:
        # 省略 version 时这个地址代表"当前最新版"，**内容会随每次发布变化**，而 App 在还
        # 没连上设备（拿不到 lastAppliedVersion）时正是这么请求的。按 immutable 缓存会让
        # 用户在发布新画面之后最多 24 小时里一直看到旧预览，而界面上没有任何线索。
        response.headers['Cache-Control'] = 'public, max-age=60'
    return response


@app.route('/api/devices/<device_id>/display/<int:version>.film', methods=['GET'])
def api_device_film_download(device_id, version):
    """
    ESP32 下载 .film 文件（旧地址，单张）

    语义扩展：该版本若没有 current 行（一套四张的版本就没有），download_film() 会回落到
    today 行，于是老固件按旧地址请求也能拿到总览 —— 它只下载一张、落盘成 current.film，
    总览是这种场景下唯一说得通的画面。

    响应：application/octet-stream
    """
    result = download_film(device_id, version)

    if result is None:
        return jsonify({
            "error": f"版本 {version} 不存在",
            "code": 4004
        }), 404

    file_path, file_size, sha256 = result

    # 使用 send_file 发送二进制
    from flask import send_file
    return send_file(
        file_path,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=f"{version}.film"
    )


@app.route('/api/devices/<device_id>/display/<int:version>/<path:name>', methods=['GET'])
def api_device_film_download_by_name(device_id, version, name):
    """ESP32 下载一套画面里的某一张（新地址）。

    URL 最后一段就是设备的落盘名（1_today.film / 2_breakfast.film / ...），
    固件按这个名字存到 SD 卡后，service_film.c 便能按时段自动切换画面。

    鉴权无需额外处理：`_is_device_pull_request()` 判的是 `path.endswith('.film')`，
    本路由的 name 必须以 .film 结尾，因此天然走设备 device_token 那条分支
    —— 不是漏了鉴权。

    name 用 `<path:...>` 接收（Flask 的默认 string 转换器不匹配含 `/` 的值，会直接 404，
    看起来像"路由没注册"），再在这里显式拒掉带路径分隔符的值：库里存的是文件名，
    放行 `../` 等于把库外的文件也暴露成可下载。
    """
    # 只接受安全基名：不能含路径分隔符、不能是 . / ..、必须以 .film 结尾。
    if name != os.path.basename(name) or name in ('.', '..') or not name.endswith('.film'):
        return jsonify({"error": "非法文件名", "code": 4003}), 400

    result = download_film_by_name(device_id, version, name)
    if result is None:
        return jsonify({
            "error": f"版本 {version} 没有 {name}",
            "code": 4004
        }), 404

    file_path, file_size, sha256 = result

    from flask import send_file
    return send_file(
        file_path,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=name
    )


@app.route('/api/devices/<device_id>/sync-status', methods=['GET'])
def api_device_sync_status(device_id):
    """
    Android 查看设备同步状态

    响应：
    {
        "device_id": "...",
        "latest_version": 17,
        "latest_upload_time": "...",
        "latest_sha256": "...",
        "last_manifest_query": "...",
        "recent_events": [...]
    }
    """
    status = get_sync_status(device_id)
    # 附上设备自报状态（电量 / 固件版本 / 上报时间），App 一次请求即可展示设备信息，
    # 不必为了看电量去连蓝牙（设备平时深睡、并不持续广播）。
    status['device_status'] = get_device_status(device_id)
    return jsonify(status)


@app.route('/api/devices/<device_id>/link-check', methods=['GET'])
def api_device_link_check(device_id):
    """一次请求回答「这台设备为什么取不到画面」。

    ── 为什么单独做一个端点 ──
    这条链路断掉时，判断需要的证据散在四处：`devices.device_token` 是否登记、
    `device_sync_log` 里设备最近来过没有、`device_display` 有没有可下发的版本、
    以及 manifest 里的 `film_url` 推导出来是不是 https。真机排查时这些要么翻容器日志、
    要么分别调好几个接口再自己对照 —— 而**每一条单独看都像正常的**：
    发布成功、版本号在涨、同步状态有值，屏幕却停在旧画面。

    这里把四项证据合成一个结论加一条**可执行的下一步**，而不是只把原始字段堆出来。
    「问题在哪」和「我该做什么」是两个问题，后者才是排查时真正要的。

    ── 为什么挂在 /api/devices 下而不是 /api/health ──
    本端点会回答「哪台设备还没登记令牌」，那等于对外公布哪些设备是未受保护的，属于信息泄露。
    挂在设备路径下即自动继承 app_token 鉴权，不必依赖 `_PUBLIC_PATHS` 的维护是否到位。
    """
    status = get_sync_status(device_id)
    film_base = _request_base_url()
    manifest = get_manifest(device_id, request_base_url=film_base)
    film_url = str((manifest or {}).get('film_url') or '')

    token_registered = bool(status.get('device_token_registered'))
    latest_version = status.get('latest_version') or 0
    # 三个版本号分别代表链路上三个不同的确证强度，不能混成一个：
    #   downloaded  设备把文件取走了（download 事件）
    #   applied     设备回报这一版真的画上屏了（apply 事件，POST display/applied 或
    #               下一次 manifest 的 X-Device-Applied-Version）
    #   apply_failed 取走了但刷新失败（apply_failed 事件）—— 冰箱上是旧菜单
    # 分开报的意义在于「取走 ≠ 已上屏」：把 downloaded 说成 applied 就是替设备说了
    # 它没说过的话，而这两者的差别恰好是最难查的那类故障。
    downloaded_version = 0
    applied_version = 0
    apply_failed_version = 0
    for event in status.get('recent_events') or []:
        kind, ver = event.get('event'), event.get('version')
        if not ver:
            continue
        if kind == 'download':
            downloaded_version = max(downloaded_version, int(ver))
        elif kind == 'apply':
            applied_version = max(applied_version, int(ver))
        elif kind == 'apply_failed':
            apply_failed_version = max(apply_failed_version, int(ver))
    contact_minutes = status.get('device_last_contact_minutes')
    film_url_https = film_url.startswith('https://') if film_url else None

    # 按「最先挡住链路的那一环」排序给结论：修完前一环才轮得到后一环，
    # 一次报三个问题会让人不知道先做哪个。
    if not token_registered:
        verdict, action = 'blocked', (
            '设备令牌未登记到后端，设备的每次拉取都会被 401 拒绝。'
            f'用 POST /api/devices/{device_id}/token 登记与设备侧（BLE 0x42 写入的）同一个令牌；'
            'App 里在「设备页 → 登记设备令牌」一键完成，不必重新配网。'
        )
    elif latest_version == 0:
        verdict, action = 'blocked', (
            '后端还没有为这台设备发布过任何画面。'
            '在 App 里触发一次下发，或等餐前定时任务执行。'
        )
    elif film_url_https is False and not config.allow_insecure_base_url():
        verdict, action = 'blocked', (
            f'film_url 推导为 http（{film_url[:60]}），固件只接受 https，会拒收整份 manifest。'
            '让入口透传 X-Forwarded-Proto 或 X-Scheme，或显式配置 SFM_BASE_URL。'
        )
    elif contact_minutes is None:
        verdict, action = 'waiting', (
            '后端从未收到过这台设备的请求。检查设备是否已配网、'
            'manifest_url 是否指向本后端（BLE 0x3E 写入）、以及设备是否已到唤醒时间。'
        )
    elif apply_failed_version >= latest_version:
        verdict, action = 'blocked', (
            f'设备取走了 v{latest_version} 但**上屏失败**（设备已回报），冰箱上显示的还是上一版。'
            '问题在设备侧的电子纸刷新那一跳，不是下发链路：'
            '连蓝牙用 0x43 读 last_sync_error，或看串口 HAL_EPD 日志。'
        )
    elif applied_version >= latest_version:
        verdict, action = 'ok', (
            f'设备已回报 v{latest_version} 成功上屏，整条链路贯通。'
        )
    elif downloaded_version >= latest_version:
        verdict, action = 'ok', (
            f'设备已取走最新版本 v{latest_version}，后端侧链路正常。'
            '设备尚未回报上屏结果（取走不等于已上屏）；旧固件不会回报，属正常。'
            '若屏幕仍未更新，连蓝牙用 0x43 读 last_sync_error 可进一步定位。'
        )
    else:
        verdict, action = 'waiting', (
            f'链路正常，设备已取到 v{downloaded_version}、云端最新 v{latest_version}，'
            '等待设备下次唤醒同步即可；需要立刻生效可在 App 里点「强制重新下发」。'
        )

    return jsonify({
        'device_id': device_id,
        'verdict': verdict,                 # ok | waiting | blocked
        'action': action,
        'checks': {
            'device_token_registered': token_registered,
            'latest_version': latest_version,
            'device_downloaded_version': downloaded_version,
            'device_applied_version': applied_version,
            'device_apply_failed_version': apply_failed_version,
            'device_last_contact_minutes': contact_minutes,
            'film_url_is_https': film_url_https,
            'film_url_base': film_base,
        },
    })


@app.route('/api/devices/<device_id>/token', methods=['POST'])
def api_device_set_token(device_id):
    """登记 / 轮换设备的 device_token（需 app_token 鉴权）。

    请求：{"device_token": "..."}

    登记后该设备拉取 manifest 与 .film 时必须携带
    "Authorization: Bearer {device_token}"。同一个 token 需要通过 BLE opcode 0x42
    写入设备（固件侧只写不可读）。响应不回显 token 明文。
    """
    data = request.get_json(silent=True) or {}
    token = (data.get('device_token') or '').strip()
    if not token:
        return jsonify({"success": False, "error": "缺少 device_token"}), 400

    result = set_device_token(device_id, token)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


def _device_prefs_payload(device_id: str) -> dict:
    """设备的展示偏好 + 可选值。

    把可选值一起返回，App 就不必先请求一次版式清单再请求一次当前值 —— 设备详情页
    只需一次往返即可把选择器画完整。
    """
    return {
        "device_id": device_id,
        "layout": get_device_layout(device_id),
        "layout_options": menu_render.normalize_layout_options(
            get_device_layout_options(device_id)),
        "layout_choices": menu_render.layout_choices(),
        "layout_option_choices": menu_render.layout_option_choices(),
    }


@app.route('/api/devices/<device_id>/prefs', methods=['GET'])
def api_device_prefs_get(device_id):
    """读取该设备的展示偏好。

    响应：{"device_id": "...", "layout": "classic", "layout_choices": [...]}

    设备从未设置过（或库里的值为空）时返回默认版式，而不是 null：调用方要的是
    「这台设备现在按什么版式出图」，那个问题永远有答案 —— 餐前下发用的就是默认版式。
    """
    return jsonify(_device_prefs_payload(device_id))


@app.route('/api/devices/<device_id>/prefs', methods=['PUT'])
def api_device_prefs_put(device_id):
    """设置展示偏好，并在已有菜单时重新发布完整四张画面。

    请求：{"layout": "bold", "layout_options": {...}, "republish": false}

    未知 layout 报 400 并列出可选值：这里错的是调用方，需要立刻知道自己写错了。
    渲染层 menu_render.get_layout() 对未知值回落默认版式，那是给库里可能被手工改坏的
    偏好兜底 —— 那条路径上没有人在等答复，让屏幕整餐留空是更坏的结果。
    接口严格、渲染宽容，两者分工不同。

    从未发布过菜单时只保存偏好，首次发布会直接采用它；已有菜单且偏好发生实际变化时
    强制重渲染并发布该设备的完整四张，避免设备同时保留新旧版式。
    `republish=true` 用于人的显式重试：版式没变也照发一次，否则用户在屏幕没更新时
    没有任何手段再触发一遍。
    """
    data = request.get_json(silent=True) or {}
    if 'layout' not in data and 'layout_options' not in data:
        return jsonify({
            "success": False,
            "error": "至少提供 layout 或 layout_options",
            "code": 1001,
        }), 400

    current_layout = menu_render.get_layout(get_device_layout(device_id)).key
    current_options = menu_render.normalize_layout_options(
        get_device_layout_options(device_id))
    layout = str(data.get('layout') or current_layout).strip().lower()
    # 归一后落库，而不是原样存：存下别名的话每次读都要再翻一次映射表，
    # 而那张表是为兼容旧客户端存在的，不该渗进数据。
    layout = menu_render.LAYOUT_ALIASES.get(layout, layout)

    if layout not in menu_render.LAYOUTS:
        return jsonify({
            "success": False,
            "error": f"未知 layout: {layout!r}（可选 {' / '.join(menu_render.LAYOUTS)}）",
            "code": 1001
        }), 400

    try:
        layout_options = menu_render.normalize_layout_options(
            data.get('layout_options', current_options), strict=True)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc), "code": 1001}), 400

    changed = layout != current_layout or layout_options != current_options
    republish = bool(data.get('republish'))
    had_display = get_sync_status(device_id).get('latest_version', 0) > 0

    result = set_device_layout(device_id, layout, layout_options)
    if not result.get("success"):
        return jsonify(result), 400

    payload = _device_prefs_payload(device_id)
    payload["success"] = True
    payload["display_updated"] = False

    if not changed and not republish:
        payload["display_update_detail"] = "版式未变化，无需重新发布菜单"
        return jsonify(payload)

    if not had_display:
        payload["display_update_detail"] = "版式已保存，首次发布菜单时生效"
        return jsonify(payload)

    publish = trigger_display_publish(None, force=True, device_id=device_id)
    if not publish.get('success'):
        detail = f"重新发布失败：{publish.get('error') or '未知错误'}"
        # 版式没变时没有需要回滚的东西，直接把失败原因回给调用方。
        if changed:
            restored = set_device_layout(device_id, current_layout, current_options)
            detail = (
                f"{detail}，已恢复原版式" if restored.get('success')
                else f"{detail}；原版式恢复也失败，当前保存的是新版式"
            )
            payload = _device_prefs_payload(device_id)
            payload["preferences_saved"] = not restored.get('success')
        payload.update({
            "success": False,
            "display_updated": False,
            "error": detail,
            "code": 5005,
        })
        return jsonify(payload), 500

    payload["display_updated"] = True
    payload["display_update_detail"] = "版式已保存，并已重新发布今日总览与三餐菜单"
    return jsonify(payload)


@app.route('/api/firmware/latest', methods=['GET'])
def api_firmware_latest():
    """列出后端当前托管的固件（App 侧查看用）。

    不需要设备的当前版本 —— 那是 manifest 里 `firmware` 字段负责的判断。
    这里只回答「后端手上有什么」，供人确认发布是否上传成功。
    """
    return jsonify({
        'success': True,
        'latest': firmware.latest_firmware(),
        'available': firmware.list_firmware(),
        'max_bytes': firmware.MAX_FIRMWARE_BYTES,
    })


@app.route('/api/firmware/<path:filename>', methods=['GET'])
def api_firmware_download(filename):
    """设备下载固件二进制（Wi-Fi OTA）。

    文件名必须匹配 `sfm-<version>.bin`，这同时挡掉路径穿越 —— `../` 不可能匹配那个正则。
    """
    path = firmware.firmware_path(filename)
    if path is None:
        return jsonify({"error": f"固件 {filename} 不存在", "code": 4004}), 404

    from flask import send_file
    return send_file(path, mimetype='application/octet-stream',
                     as_attachment=True, download_name=filename)


@app.route('/api/devices/<device_id>/display/applied', methods=['POST'])
def api_device_display_applied(device_id):
    """设备上报「这一版画面已经真的画上屏 / 或刷新失败」。

    请求：{"version": 17, "ok": true}          上屏成功
          {"version": 17, "ok": false, "detail": "epd timeout"}  下载成功但刷新失败

    manifest 请求头 X-Device-Applied-Version 只能在**下一次**唤醒时带上来，而定时唤醒
    之间隔着几小时。设备在同一次唤醒里刷完屏就报，后端才能在当下区分「已上屏」与
    「取走了但没画上」。

    失败上报**不清 force**：下次唤醒仍要重试。
    """
    data = request.get_json(silent=True) or {}
    result = record_display_result(
        device_id,
        data.get('version'),
        ok=bool(data.get('ok', True)),
        detail=str(data.get('detail') or ''),
    )
    if not result.get('success'):
        return jsonify({"success": False, "error": result.get('error'), "code": 1001}), 400
    return jsonify(result)


@app.route('/api/devices/<device_id>/display/force', methods=['POST'])
def api_device_display_force(device_id):
    """强制设备在下一次 manifest 查询时重新应用当前版本（不递增版本号）。

    置位 devices.force_next，由 get_manifest() 读取并在下发后立即清零。

    用途：设备文件被 BLE 0x45 清空后，设备记着与云端相同的 last_applied_version，
    版本门控会判定「已是最新」而拒绝下载。force 是唯一不用递增版本号就能解围的手段。
    """
    from .database import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT MAX(version) FROM device_display WHERE device_id = ?', (device_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return jsonify({"error": "设备无显示版本"}), 404
        current_version = row[0]

        cursor.execute('''
            INSERT INTO device_sync_log (device_id, event_type, version, detail)
            VALUES (?, ?, ?, ?)
        ''', (device_id, 'force_refresh', current_version, "manual force refresh"))
        conn.commit()
    finally:
        conn.close()

    result = set_force_next(device_id)
    if not result.get("success"):
        return jsonify(result), 404

    return jsonify({
        "success": True,
        "device_id": device_id,
        "forced_version": current_version,
        "message": "已置位 force，设备下次 manifest 查询时会强制重新下载并应用该版本"
    })


# ==================== 启动 ====================

def log_timezone_offset() -> None:
    """把本地时区与 UTC 的偏移写进启动日志。

    「每日更新时间」「餐段推断」「印在电子纸上的日期」都按**本地钟点**判断，而容器默认是
    UTC。配错的表现是「设置的 06:30 到了 14:30 才执行」，很难让人想到时区，
    所以启动时打一行。
    """
    import time as _time

    offset_hours = -_time.timezone / 3600
    if _time.daylight and _time.localtime().tm_isdst:
        offset_hours = -_time.altzone / 3600
    tz_name = os.environ.get('TZ') or _time.tzname[0]
    logger.info('本地时区：TZ=%s UTC%+g（按钟点的判断都用它：每日更新时间、餐段、画面日期）',
                tz_name, offset_hours)
    if offset_hours == 0 and not os.environ.get('TZ'):
        logger.warning(
            '当前时区是 UTC 且未设置 TZ：「每日更新时间」等按钟点的设置会偏 8 小时'
            '（06:30 实际在 14:30 执行）。容器请设 TZ=Asia/Shanghai。'
        )


def start_app(host=None, port=None, debug=None):
    """启动应用。

    默认值由 SFM_HOST / SFM_PORT / SFM_DEBUG 提供（默认 127.0.0.1 / 5000 / False）。

    启动前执行 config.validate_runtime()：绑定非回环地址却缺少 SFM_API_TOKEN、
    或在非回环地址上开启 debug，都会直接拒绝启动（fail-closed）。
    """
    host = config.host() if host is None else host
    port = config.port() if port is None else port
    debug = config.debug() if debug is None else debug

    # 安全前置校验：不满足则抛错终止，绝不以不安全配置对外提供服务
    config.validate_runtime(host, debug)

    db.init_db()
    init_scheduler()

    scheme_hint = 'http'
    logger.info('智能冰箱贴服务启动：地址=%s://%s:%s 存活检查=%s://%s:%s/healthz',
                scheme_hint, host, port, scheme_hint, host, port)
    log_timezone_offset()
    if config.api_token():
        logger.info('已启用 app_token 鉴权（业务接口需 Authorization: Bearer ...）')
    else:
        logger.warning('未设置 SFM_API_TOKEN：业务接口不鉴权，仅可用于本机开发')
    if not config.base_url().lower().startswith('https://'):
        logger.warning('SFM_BASE_URL 非 https：固件要求 film_url 必须 HTTPS，真机拉取会被拒绝')

    try:
        app.run(host=host, port=port, debug=debug)
    finally:
        stop_scheduler()


if __name__ == '__main__':
    start_app()
