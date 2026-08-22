"""运行时配置 — 集中读取 SFM_* 环境变量并提供 fail-closed 校验。

运行时契约（默认只绑回环、默认关闭 debug、非回环绑定时必须配置 SFM_API_TOKEN、可选
CORS 白名单与自定义数据/日志路径）声明在 `backend/.env.example`，本模块负责落地。

所有取值都用函数动态读取而非模块级常量：测试会先设置环境变量再 import 应用，
用常量会因 import 时机不同而固化成错误值。
"""
import os

# 回环地址：仅这些视为「本机可信」绑定
_LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1', '')

_PUBLIC_TOKEN_PLACEHOLDERS = frozenset({
    'replace-with-random-demo-token',
    'dev-app-token-please-rotate',
})

# .env 文件的默认查找位置（相对本文件：backend/.env）
_DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

_env_file_loaded = False


def load_env_file(path: str = '', override: bool = False) -> str:
    """把 .env 文件里的键值补进 os.environ，返回实际加载的文件路径（未加载则空串）。

    应用自己解析 .env，不依赖编排层：docker compose 的 `env_file:` 是编排层能力，
    在 K8s 上把 .env 挂进容器不会有任何效果，validate_runtime() 仍读不到 SFM_API_TOKEN
    而 fail-closed 退出，文件明明就在那里。envFrom(secretRef/configMapRef) 仍是首选，
    这里支持直接读文件作为防呆。

    默认 override=False，**不覆盖**已注入的环境变量：编排层注入的值（Secret / envFrom /
    docker -e）代表更明确的部署意图，而镜像里可能烤进了一份过时的 .env。反过来会造成
    「改了 Secret 却不生效」。

    @param path 指定文件；留空则取 SFM_ENV_FILE，再回落 backend/.env
    """
    global _env_file_loaded

    candidate = path or _env_str('SFM_ENV_FILE') or _DEFAULT_ENV_FILE
    if not os.path.isfile(candidate):
        return ''

    try:
        with open(candidate, 'r', encoding='utf-8') as handle:
            lines = handle.readlines()
    except OSError:
        return ''

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        # 去掉整体包裹的引号（.env 里写 KEY="value" 很常见）
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value

    _env_file_loaded = True
    return candidate


def _env_str(name: str, default: str = '') -> str:
    """读取字符串环境变量；未设置或全空白时回落默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if raw == '':
        return default
    return raw


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，接受 1/true/yes/on（大小写不敏感）。"""
    raw = _env_str(name).lower()
    if raw == '':
        return default
    return raw in ('1', 'true', 'yes', 'on')


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；非法值回落默认值而不是抛异常。"""
    raw = _env_str(name)
    if raw == '':
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def host() -> str:
    """监听地址。默认 127.0.0.1（仅本机），不再默认对外全网卡暴露。"""
    return _env_str('SFM_HOST', '127.0.0.1')


def port() -> int:
    return _env_int('SFM_PORT', 5000)


def debug() -> bool:
    """Flask debug。默认 False：debug 会启用 Werkzeug 调试器，等于远程代码执行入口。"""
    return _env_bool('SFM_DEBUG', False)


def api_token() -> str:
    """Android 等业务调用方使用的 app_token（与固件 device_token、AI Key 互不复用）。"""
    return _env_str('SFM_API_TOKEN')


def api_token_is_unsafe(token: str = None) -> bool:
    """公开占位值和低熵短值不能作为对外服务凭据。"""
    value = api_token() if token is None else (token or '').strip()
    return len(value) < 32 or value in _PUBLIC_TOKEN_PLACEHOLDERS


def cors_origins() -> list:
    """浏览器跨域白名单。原生 Android 不需要 CORS，故默认为空=不放开跨域。"""
    raw = _env_str('SFM_CORS_ORIGINS')
    if raw == '':
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


def database_path() -> str:
    """SQLite 路径。未配置时回落到 backend/data/fridge.db。"""
    configured = _env_str('SFM_DATABASE_PATH')
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), '..', 'data', 'fridge.db')


def film_dir() -> str:
    """`.film` 文件的落盘根目录（其下按 device_id 分子目录）。

    必须与 SQLite 同在持久卷内：容器只把 `/data` 声明为 VOLUME，`.film` 落在容器可写层
    时容器一重建文件全没、库里的版本记录还在，设备侧表现为「manifest 200 但下载 404」。

    默认值跟随 database_path() 的所在目录，可用 SFM_FILM_DIR 覆盖；镜像内设为 /data/devices。
    """
    configured = _env_str('SFM_FILM_DIR')
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(database_path())), 'devices')


def font_path() -> str:
    """渲染菜单用的中文字体文件路径。

    容器内由 Dockerfile 设为 fonts-wqy-zenhei 的安装路径。本机开发时未设置的话，
    按常见发行版路径依次探测，全都找不到就返回空串，由 require_font_path() 报错。

    为什么必须显式处理：python:*-slim 镜像里一个字体文件都没有，PIL 拿不到字体会把
    中文渲染成空白或方框 —— 而那份图会被正常编码、正常下发、正常上屏，链路上没有
    任何一环报错，错误只在电子纸上看得见。这类问题必须在启动阶段就暴露。
    """
    configured = _env_str('SFM_FONT_PATH')
    if configured:
        return configured

    for candidate in (
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/System/Library/Fonts/PingFang.ttc',
    ):
        if os.path.isfile(candidate):
            return candidate
    return ''


def require_font_path() -> str:
    """返回可用的字体路径，找不到就抛错（fail-fast，不返回空让下游静默降级）。"""
    path = font_path()
    if not path or not os.path.isfile(path):
        raise RuntimeError(
            f"找不到可用的中文字体（SFM_FONT_PATH={_env_str('SFM_FONT_PATH') or '未设置'}）。"
            "缺字体时 PIL 会把中文渲染成空白或方框，而该图仍会被正常编码下发到设备 —— "
            "错误只在电子纸上可见，因此这里直接拒绝渲染。"
            "请安装 fonts-wqy-zenhei（Debian/Ubuntu: apt install fonts-wqy-zenhei）"
            "或用 SFM_FONT_PATH 指定字体文件。"
        )
    return path


def allow_insecure_base_url() -> bool:
    """允许 SFM_BASE_URL 为 http（仅限 TLS 尚未就位的过渡期）。

    固件要求 manifest 里的 film_url 必须是 HTTPS，因此对外服务时 http 的 base_url
    等于「服务能正常启动、上传也成功，但真机拉取 100% 失败」，而失败点在设备侧、
    要靠串口才看得出来 —— 排查成本远高于启动时直接报错。故默认 fail-closed。

    但部署是分步进行的（先起后端、再配证书与反向代理），一上来就强制 https 会让
    中间态无法启动。因此保留这个显式开关：设为 true 表示「我知道真机拉不通，
    现在只是本地联调」。默认 false。
    """
    return _env_bool('SFM_ALLOW_INSECURE_BASE_URL', False)


def barcode_lookup_enabled() -> bool:
    """条码查询是否开启（数据源 Open Food Facts）。

    实测国产商品覆盖率约 27%、可直接使用的不足 10%（见 barcode_lookup.py 顶部说明），
    所以这个功能被设计成随时可以整体下掉：设为 false 后接口明确回「功能已关闭」，
    客户端据此只展示拍照识别入口，而不是让用户对着一个永远查不到的按钮反复试。
    """
    return _env_bool('SFM_BARCODE_LOOKUP', True)


def firmware_dir() -> str:
    """固件 `.bin` 的存放目录（Wi-Fi OTA 从这里取）。

    与 film_dir() 同一套理由：默认跟随数据库所在目录，保证与 SQLite 同生共死，
    否则容器重建后「有版本记录、没有文件」，设备侧表现成 OTA 一直 404。

    可用 SFM_FIRMWARE_DIR 覆盖；镜像内建议设为 /data/firmware。
    """
    configured = _env_str('SFM_FIRMWARE_DIR')
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(database_path())), 'firmware')


def log_dir() -> str:
    return _env_str('SFM_LOG_DIR', 'logs')


def log_level() -> str:
    return _env_str('SFM_LOG_LEVEL', 'INFO')


def log_format() -> str:
    """Log rendering mode: human-readable text locally, JSON in cloud deployments."""
    value = _env_str('SFM_LOG_FORMAT', 'text').lower()
    return value if value in {'text', 'json'} else 'text'


def recipe_include_non_cooking() -> bool:
    """饮料和零食是否参与菜谱生成；默认关闭。"""
    return _env_bool('SFM_RECIPE_INCLUDE_NON_COOKING', False)


def daily_publish_time() -> str:
    """每日生成并发布整套菜单的本地时间。"""
    return _env_str('SFM_DAILY_PUBLISH_TIME', '06:30')


def enable_scheduler() -> bool:
    """是否在 WSGI 加载时启动定时任务调度器。

    `start_app()` 会自行启动调度器，但 gunicorn 等 WSGI 服务器直接加载 app 对象、
    不经过 start_app()，定时任务就不会跑。反之若开多 worker 又会每个进程各起一份调度器，
    造成任务重复执行。因此用显式开关控制，并要求配套 --workers 1。
    默认 False：不影响本地开发与测试（测试会 import src.app）。
    """
    return _env_bool('SFM_ENABLE_SCHEDULER', False)


def auto_publish_enabled() -> bool:
    """是否在餐前自动把菜单画面发布为新版本，让设备靠自身轮询换图。

    默认开启：这是产品的核心承诺（「不用碰手机，饭点前冰箱贴自己换图」）。
    在此之前定时任务只写站内通知、对设备零作用，画面全靠人在 App 或脚本里手点。
    需要临时静音（例如联调时不想被自动版本打断）可设 SFM_AUTO_PUBLISH=0。

    注意本项受 enable_scheduler() 约束：调度器没起，这里再开也不会执行。
    """
    return _env_bool('SFM_AUTO_PUBLISH', True)


def auto_publish_devices() -> list:
    """自动下发的目标设备白名单；留空表示「所有已注册设备」。

    留空即全量是刻意的默认：本项目一户一台，手工维护一份 device_id 清单只会在换设备后
    忘记更新，表现为「设备在线、云端有新版本、就是不换图」。
    多台设备但只想给其中一台自动换图时再显式配置。
    """
    raw = _env_str('SFM_AUTO_PUBLISH_DEVICES')
    if raw == '':
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


def auto_publish_lead_minutes() -> int:
    """读取设备饭点前取图提前量的环境默认值。"""
    return max(1, _env_int('SFM_AUTO_PUBLISH_LEAD_MIN', 30))


def base_url() -> str:
    """对外可达的基址，用于拼 manifest 里的 film_url。

    注意：固件 manifest_parse.c 强制要求 film_url 使用 HTTPS，因此真机链路必须把本项
    配成 https 外部地址（反向代理或隧道）。默认值仅供本机自测，设备拉取会拒绝 http。

    多数部署**不需要设置本项** —— 留空时后端会用「设备本次请求所用的地址」来拼
    film_url（见 device_display._resolve_film_base_url）。容器 IP 由编排系统随机分配、
    云平台入口地址也可能变动，写死一个值往往是错的。
    """
    return _env_str('SFM_BASE_URL', 'http://127.0.0.1:5000')


def configured_base_url() -> str:
    """返回**显式配置且对设备有意义**的 SFM_BASE_URL；否则返回空串。

    base_url() 带有本机默认值，无法区分「没配」与「就是要用本机地址」，而这个区分很重要：
    没配时才允许从请求自适应推导 film_url。

    回环地址也算作「没配」：设备永远不可能通过 127.0.0.1 访问后端，
    那个值对 film_url 而言必然是错的，把它当成有效配置只会让请求带着一个死地址返回。
    """
    raw = _env_str('SFM_BASE_URL')
    if not raw:
        return ''
    lowered = raw.lower().rstrip('/')
    for loopback in ('http://127.0.0.1', 'https://127.0.0.1',
                     'http://localhost', 'https://localhost',
                     'http://[::1]', 'https://[::1]'):
        if lowered == loopback or lowered.startswith(loopback + ':'):
            return ''
    return raw


def is_loopback_host(value: str) -> bool:
    """判断绑定地址是否仅限本机。"""
    return (value or '').strip() in _LOOPBACK_HOSTS


def validate_runtime(bind_host: str = None, bind_debug: bool = None) -> None:
    """启动前的 fail-closed 校验。

    三条硬约束（前两条对应 .env.example 中「非回环必须带 token」的声明）：
      1. 绑定到非回环地址却没有配置 SFM_API_TOKEN -> 拒绝启动。否则任何能访问该端口的
         人都能读写库存、画像并向设备推送画面。
      2. 绑定到非回环地址却开着 debug -> 拒绝启动。Werkzeug 调试器可执行任意代码。
      3. 绑定到非回环地址却把 SFM_BASE_URL 配成 http -> 拒绝启动。固件强制要求
         manifest 里的 film_url 为 HTTPS，配 http 会让真机拉取必然失败，且失败只在
         设备串口可见。需要过渡期启动时用 SFM_ALLOW_INSECURE_BASE_URL=true 显式豁免。

    仅在真正启动服务时调用；测试用 app.test_client() 不受影响。
    """
    effective_host = host() if bind_host is None else bind_host
    effective_debug = debug() if bind_debug is None else bind_debug

    if is_loopback_host(effective_host):
        return

    if api_token_is_unsafe():
        raise RuntimeError(
            f"拒绝启动：绑定到非回环地址 {effective_host} 时 SFM_API_TOKEN 缺失、"
            "过短或仍是公开占位值。请用 `openssl rand -hex 32` 生成独立凭据，"
            "或改为 SFM_HOST=127.0.0.1 仅本机访问。"
        )

    if effective_debug:
        raise RuntimeError(
            f"拒绝启动：绑定到非回环地址 {effective_host} 时不允许开启 SFM_DEBUG。"
            "debug 模式会暴露 Werkzeug 调试器（可执行任意代码）。"
        )

    # 只在【显式配置了】SFM_BASE_URL 时校验它是 https。
    # 留空是受支持的部署方式（film_url 改由设备请求来源自适应推导），此时无从校验，
    # 真正的 https 判定发生在生成 manifest 时（那里能看到设备实际用的 scheme）。
    # 早期版本无条件校验 base_url()，而它带有 http 默认值 —— 结果「留空自适应」
    # 这条路会在启动阶段就被拒，恰好把云上最需要的部署方式堵死了。
    explicit_base_url = configured_base_url()
    if (explicit_base_url
            and not explicit_base_url.lower().startswith('https://')
            and not allow_insecure_base_url()):
        raise RuntimeError(
            f"拒绝启动：绑定到非回环地址 {effective_host} 时 SFM_BASE_URL 必须是 https"
            f"（当前 {explicit_base_url}）。固件要求 manifest 的 film_url 为 HTTPS，"
            "配 http 时设备会直接拒绝该 manifest、真机链路必然失败。"
            "可以改成 https 外部地址，或【直接留空】让后端按设备请求来源自适应；"
            "若明知真机拉不通、仅做本地联调，可设 SFM_ALLOW_INSECURE_BASE_URL=true 豁免。"
        )


# ── 模块导入时自动加载 .env ──────────────────────────────────────────────
#
# 放在这里而不是各个入口里：本模块是所有配置读取的唯一通道，因此在这里加载能一次性
# 覆盖全部入口 —— gunicorn worker、`python -m src.app`、scripts/ 下的 CLI、测试。
# 若改为在 app.py 里调用，任何直接 `from . import config` 的路径都会漏掉，
# 产生「entrypoint 校验通过、业务代码却读不到配置」这种极难定位的不一致。
#
# 安全性由 override=False 保证：已存在的环境变量一律胜出，文件只补缺失项。
# 因此编排层注入（K8s envFrom / docker -e / compose env_file）永远优先于镜像里
# 可能过时的 .env，不会出现「改了 Secret 却不生效」。
#
# 需要完全禁用（例如不希望测试受本机 .env 影响）时设 SFM_SKIP_ENV_FILE=1。
if not _env_bool('SFM_SKIP_ENV_FILE', False):
    load_env_file()
