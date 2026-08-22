"""下发测试菜单到设备 —— 验证「后端渲染 → 发布版本 → 设备 Wi-Fi 拉取上屏」这条链路。

⚠️ 这是**临时测试脚手架**，不是产品功能：安卓端尚未接入后端，而后端的定时任务只发站内
通知，所以「谁来触发渲染」这一环目前是空的。等安卓接入或补上自动编排之后本脚本应删除，
删除时记得一并清理它塞进库里的测试数据（--clear-inventory）。

走 HTTP 而不是直接 import 后端模块，是为了连带验证 TLS、鉴权、端点路由、版本递增与落盘 ——
直接调库只能测到渲染函数本身。

用法（在 backend/ 目录下）：

  # 最小验证：按当前时间选一餐，发它的今日总览
  python scripts/publish_test_menu.py

  # 指定某一餐的总览
  python scripts/publish_test_menu.py --meal lunch

  # 发某一餐的详细做法页（步骤 + 食材）
  python scripts/publish_test_menu.py --page detail --meal lunch

  # 顺便置位 force：设备已是最新版本时也强制重新拉一次
  python scripts/publish_test_menu.py --force

  # 持续下发，用来观察设备靠【自己的轮询】是否会自动更新（不用手点 SYNC_NOW）
  python scripts/publish_test_menu.py --watch 120

  # 导出渲染结果，肉眼确认版式（不经过设备）
  python scripts/publish_test_menu.py --preview /tmp/menu.png

  # 走完整 AI 链路：先塞测试食材，再让 AI 生成菜谱并渲染（需要真实 AI_API_KEY）
  python scripts/publish_test_menu.py --seed-inventory --from-ai
  python scripts/publish_test_menu.py --clear-inventory     # 测完清掉测试食材

连接参数默认从 backend/.env 读（SFM_BASE_URL / SFM_API_TOKEN），也可用命令行覆盖。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_CA = REPO_ROOT / 'embedded' / 'smart_fridge_magnet' / 'certs' / 'sfm_ca.pem'

sys.path.insert(0, str(BACKEND_DIR))

try:
    import requests
except ImportError:
    sys.exit('缺少 requests：pip install requests（或用 backend/.venv 里的解释器）')


# --------------------------------------------------------------- 示例菜单数据
# 刻意写死而不是随机生成：每次下发的内容可预期，屏幕上看到什么就能对上是哪一次下发。
# 菜品带「分类|」前缀，分类会被渲染成彩色小色块（配色见 menu_render.CATEGORY_COLORS）。
DEMO_OVERVIEW = {
    'breakfast': {
        'title': '早餐菜单',
        'dishes': [
            '早餐|小米南瓜粥（25分钟）',
            '早餐|水煮蛋（8分钟）',
            '主食|全麦馒头',
            '素菜|清炒时蔬（10分钟）',
        ],
        'expiring': '全脂牛奶 明天到期',
    },
    'lunch': {
        'title': '午餐菜单',
        'dishes': [
            '午餐|红烧肉（40分钟）',
            '午餐|蒜蓉西兰花（15分钟）',
            '汤品|冬瓜排骨汤（60分钟）',
            '主食|米饭',
        ],
        'expiring': '上海青、内酯豆腐 2天内到期',
    },
    'dinner': {
        'title': '晚餐菜单',
        'dishes': [
            '晚餐|番茄炒蛋（10分钟）',
            '素菜|手撕包菜（12分钟）',
            '汤品|紫菜蛋花汤（8分钟）',
            '主食|杂粮饭',
        ],
        'expiring': '圣女果 3天内到期',
    },
}

DEMO_DETAIL = {
    'breakfast': [
        '小米南瓜粥',
        '食材：小米80g、南瓜200g、清水1L',
        '1. 南瓜去皮切块，小米淘洗干净',
        '2. 冷水下锅，大火煮开转小火',
        '3. 慢煮 20 分钟，边煮边搅防粘底',
        '用时 25分钟 · 难度 简单',
    ],
    'lunch': [
        '红烧肉',
        '食材：五花肉500g、冰糖20g、生姜3片、老抽1勺',
        '1. 五花肉切块，冷水下锅焯去血沫',
        '2. 冰糖小火炒至琥珀色，下肉块翻炒上色',
        '3. 加热水没过肉，小火焖 35 分钟收汁',
        '用时 40分钟 · 难度 中等',
    ],
    'dinner': [
        '番茄炒蛋',
        '食材：番茄2个、鸡蛋3个、白糖少许',
        '1. 鸡蛋打散炒至凝固盛出',
        '2. 番茄炒出汁，加少许白糖提味',
        '3. 倒回鸡蛋翻炒均匀即可',
        '用时 10分钟 · 难度 简单',
    ],
}

# 走 AI 链路时用的测试食材。数量刻意给足，避免 AI 因为「量太少」而推荐不出菜。
SEED_INVENTORY = [
    {'name': '五花肉', 'quantity': 500, 'unit': 'g', 'category': '肉类'},
    {'name': '西兰花', 'quantity': 300, 'unit': 'g', 'category': '蔬菜'},
    {'name': '番茄', 'quantity': 4, 'unit': '个', 'category': '蔬菜'},
    {'name': '鸡蛋', 'quantity': 8, 'unit': '个', 'category': '蛋类'},
    {'name': '土豆', 'quantity': 3, 'unit': '个', 'category': '蔬菜'},
    {'name': '大米', 'quantity': 2000, 'unit': 'g', 'category': '主食'},
]


def meal_slot_now() -> str:
    """按当前时间选餐段。

    分界与固件 service_film.c 的做饭窗口保持同一意图（早 06–09 / 午 11–13:30 /
    晚 17–19:30），窗口之外按最近的下一餐取，保证任何时刻都有确定结果。
    """
    minutes = datetime.now().hour * 60 + datetime.now().minute
    if minutes < 9 * 60 + 30:
        return 'breakfast'
    if minutes < 14 * 60:
        return 'lunch'
    return 'dinner'


class BackendClient:
    def __init__(self, base_url: str, token: str, ca_bundle, device_id: str, timeout: int = 60):
        self.base = base_url.rstrip('/')
        self.device_id = device_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Bearer {token}'
        # verify 传 CA 文件路径 —— 刻意不用 verify=False：真机链路必须走可校验的证书，
        # 脚本这里放松校验就失去了「提前发现证书配错」的价值（设备侧可没有 -k 开关）。
        self.session.verify = str(ca_bundle) if ca_bundle else True

    def _url(self, path: str) -> str:
        return f'{self.base}/api{path}'

    def health(self) -> dict:
        r = self.session.get(self._url('/health'), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def render(self, payload: dict) -> dict:
        r = self.session.post(
            self._url(f'/devices/{self.device_id}/display/render'),
            json=payload, timeout=self.timeout,
        )
        return self._unwrap(r)

    def render_from_plan(self, payload: dict) -> dict:
        r = self.session.post(
            self._url(f'/devices/{self.device_id}/display/render-from-plan'),
            json=payload, timeout=self.timeout,
        )
        return self._unwrap(r)

    def set_force(self) -> dict:
        r = self.session.post(
            self._url(f'/devices/{self.device_id}/display/force'), timeout=self.timeout
        )
        return self._unwrap(r)

    def sync_status(self) -> dict:
        r = self.session.get(
            self._url(f'/devices/{self.device_id}/sync-status'), timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def add_inventory(self, items: list) -> dict:
        r = self.session.post(self._url('/inventory/batch'),
                              json={'items': items}, timeout=self.timeout)
        return self._unwrap(r)

    def list_inventory(self) -> dict:
        r = self.session.get(self._url('/inventory'), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def delete_inventory(self, item_id: int) -> None:
        self.session.delete(self._url(f'/inventory/{item_id}'), timeout=self.timeout)

    def recommend(self, meal_type: str, people: int) -> dict:
        # AI 生成可能较慢，单独给更长超时
        r = self.session.post(self._url('/recommend'),
                              json={'meal_type': meal_type, 'people_count': people},
                              timeout=180)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            payload = {'raw': response.text}
        if not response.ok:
            # 把最常见的失败翻译成可直接照做的动作，而不是抛一个裸状态码
            hint = {
                401: '凭据不对：核对 SFM_API_TOKEN（--token 或 backend/.env）',
                404: 'device_id 或 plan_id 不存在',
                415: 'Content-Type 不对',
                422: '数据未通过校验，看返回里的 error',
                500: '服务端错误，看 docker compose logs backend',
            }.get(response.status_code, '')
            raise SystemExit(
                f'✗ HTTP {response.status_code} {hint}\n  {json.dumps(payload, ensure_ascii=False)}'
            )
        return payload


def in_container() -> bool:
    """粗略判断是否运行在本项目的容器内。

    判据是「本脚本就躺在镜像的 /app 布局里」（WORKDIR=/app，且 src/ 在旁边）。

    刻意**不再要求 /.dockerenv 存在**：那个文件只有 docker 引擎会创建，
    containerd / CRI-O（K8s、以及本项目实际用的云平台）都不会 —— 于是在真正的部署
    环境里这个判据恒为假，脚本转而去走「容器外」分支：读对外 https 地址、找镜像里
    根本不存在的自签 CA 文件，最后回落到一个错的端口。表现就是「脚本在云上不可用」。

    用途只是挑默认值，判断错了也不会出错，命令行参数随时可覆盖。
    """
    here = Path(__file__).resolve()
    return here.parent == Path('/app/scripts') and Path('/app/src').is_dir()


def self_base_url() -> str:
    """容器内直连自身的地址。

    端口必须取 SFM_PORT 而不能写死 5000：部署时平台注入的可能是 `SFM_PORT=8443`
    （探针端口固定），gunicorn 实际监听那个端口，连 5000 只会得到 Connection refused。
    """
    port = os.environ.get('SFM_PORT', '').strip() or '5000'
    return f'http://127.0.0.1:{port}'


def base_url_from_certificate(cert_path: Path, port: str) -> str:
    """从服务器证书的 SAN 推导一个可访问的后端地址。

    为什么需要这一步：`SFM_BASE_URL` 现在通常留空（让后端按设备请求来源自适应推导
    film_url），于是脚本没法再从它拿到「我该访问哪里」—— 而这两件事本来就不同：
    前者是**设备视角**的下载地址，后者是**本脚本视角**的访问地址。

    证书 SAN 恰好是唯一可靠的线索：能被这套 TLS 正常校验的地址，就是签进 SAN 的那些。
    取第一条（gen-ca.sh 把主用地址放在最前），配上 caddy 的监听端口。
    """
    if not cert_path.exists():
        return ''
    try:
        import subprocess
        out = subprocess.run(
            ['openssl', 'x509', '-in', str(cert_path), '-noout', '-ext', 'subjectAltName'],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ''

    for token in out.replace('\n', ',').split(','):
        token = token.strip()
        for prefix in ('IP Address:', 'IP:', 'DNS:'):
            if token.startswith(prefix):
                host = token[len(prefix):].strip()
                if host:
                    return f'https://{host}:{port}'
    return ''


def load_env_defaults() -> dict:
    """读取连接参数。

    优先级：**进程环境变量 > backend/.env 文件**。
    这个顺序很关键：容器里根本没有 .env 文件 —— compose 的 `env_file` 是把变量注入
    进程环境，不是把文件复制进镜像。只读文件的话，在云端容器内跑就永远拿不到配置。
    反过来本机开发时通常没导出这些变量，所以文件作为回退仍然必要。
    """
    values = {}
    env_file = BACKEND_DIR / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()

    for key in ('SFM_BASE_URL', 'SFM_API_TOKEN'):
        from_env = os.environ.get(key, '').strip()
        if from_env:
            values[key] = from_env
    return values


def do_preview(page: str, slot: str, output: Path) -> None:
    """本地渲染并导出量化后的预览图（所见即设备所示），不经过网络。"""
    os.environ.setdefault('SFM_DATABASE_PATH', '/tmp/sfm_preview/preview.db')
    from PIL import Image
    from src import film_encoder as fe
    from src import menu_render as mr

    if page == 'detail':
        label, color, _name = mr.MEAL_STYLES[slot]
        image = mr.render_meal_detail(label, color, DEMO_DETAIL[slot])
    else:
        data = DEMO_OVERVIEW[slot]
        image = mr.render_overview(
            title=data['title'],
            date_text=f'{datetime.now().month}月{datetime.now().day}日',
            dishes=data['dishes'],
            expiring=data['expiring'],
        )

    # 量化一遍再导出：直接存渲染图会带上抗锯灰阶，与设备实际显示不同
    indices = fe.quantize_rgb(image.convert('RGB').tobytes())
    quantized = Image.new('RGB', (mr.WIDTH, mr.HEIGHT))
    quantized.putdata([fe.index_to_rgb(i) for i in indices])
    output.parent.mkdir(parents=True, exist_ok=True)
    quantized.save(output)
    print(f'✓ 预览已导出（已量化为六色，与设备显示一致）：{output}')


def publish_once(client: BackendClient, page: str, slot: str,
                 use_ai: bool, people: int, stamp: bool = False) -> dict:
    """下发一次。

    @param page 渲染哪种页面：'overview'（今日总览）或 'detail'（某餐详细做法）
    @param slot 用哪一餐的示例数据：breakfast / lunch / dinner

    page 与 slot 是两个独立维度，刻意分开：总览页也需要知道是哪一餐（标题与菜品不同），
    而「按当前时间自动选」调整的是 slot，不该顺带把页面类型也改掉。
    """
    meal_names = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}

    if use_ai:
        meal_type = meal_names[slot]
        print(f'→ 请求 AI 生成{meal_type}菜谱（较慢，最多等 180s）…')
        result = client.recommend(meal_type, people)
        plan_id = (result.get('data') or {}).get('plan_id')
        if not plan_id:
            raise SystemExit(
                '✗ AI 未返回 plan_id。常见原因：库存为空（先跑 --seed-inventory）、'
                'AI_API_KEY 未配置或无效。\n  返回：'
                + json.dumps(result, ensure_ascii=False)[:300]
            )
        print(f'  已生成 plan_id={plan_id}')
        return client.render_from_plan({
            'plan_id': plan_id, 'plan': 'a',
            'kind': 'overview' if page == 'overview' else slot,
        })

    if page == 'detail':
        print(f'→ 渲染{meal_names[slot]}详细做法页')
        return client.render({'kind': slot, 'lines': DEMO_DETAIL[slot]})

    data = DEMO_OVERVIEW[slot]
    now = datetime.now()
    # 连续下发时把时刻打进日期区：内容每轮都不同，屏幕上能直接读出「这是哪一次下发」。
    # 否则每轮字节完全一样，既让设备白下 209KB，也无法从画面判断到底更新了没有 ——
    # 版本号变了只证明后端发布成功，不证明设备真的重新拉取并刷屏了。
    date_text = f'{now.month}月{now.day}日'
    if stamp:
        date_text += f' {now:%H:%M:%S}'
    print(f'→ 渲染总览：{data["title"]}（日期区 {date_text}）')
    return client.render({
        'kind': 'overview',
        'title': data['title'],
        'date': date_text,
        'dishes': data['dishes'],
        'expiring': data['expiring'],
    })


def report_device_state(client: BackendClient) -> None:
    status = client.sync_status()
    device = status.get('device_status') or {}
    print(f"  云端最新版本 : {status.get('latest_version')}")
    print(f"  设备自报状态 : 电量 {device.get('battery_level')}% / "
          f"固件 {device.get('firmware_version')} / 上报于 {device.get('status_reported_at')}")
    print(f"  最近一次拉取 : {status.get('last_manifest_query')}")
    # 字段名以 device_display.get_sync_status() 为准：event / version / detail / time
    events = status.get('recent_events') or []
    if events:
        recent = ', '.join(
            f"{e.get('event')} v{e.get('version')}" for e in events[:3]
        )
        print(f"  最近事件     : {recent}（最新在前，时间 {events[0].get('time')}）")


def main() -> None:
    env = load_env_defaults()

    # 默认地址按运行位置自适应：
    #  - 容器内：直连自身的明文 5000。SFM_BASE_URL 指向的是【对外】的 https 地址，
    #    从容器里访问它要绕出去再回来（hairpin NAT，很多环境不通），而且镜像里
    #    也没有校验用的 CA 文件。走 127.0.0.1:5000 少两个变量，验的仍是同一套代码。
    #  - 容器外：用 .env / 环境变量里的对外地址，连带把 TLS 与反向代理一起验到。
    if in_container():
        default_base = self_base_url()
        default_ca = ''
    else:
        # SFM_BASE_URL 现在通常**留空**（让后端按设备请求来源自适应推导 film_url），
        # 所以不能只依赖它 —— 它是「设备视角的下载地址」，而这里需要的是
        # 「本脚本视角的访问地址」，两者不是一回事。
        # 留空时改从服务器证书的 SAN 推导：能被这套 TLS 正常校验的地址，
        # 恰好就是签进 SAN 的那些，这是唯一可靠且无需新增配置的线索。
        default_base = env.get('SFM_BASE_URL', '').strip()
        if not default_base:
            default_base = base_url_from_certificate(
                BACKEND_DIR / 'certs' / 'sfm_server.pem',
                env.get('SFM_TLS_PORT', '8443').strip() or '8443',
            )
        if not default_base:
            default_base = self_base_url()
        default_ca = str(DEFAULT_CA)

    parser = argparse.ArgumentParser(
        description='下发测试菜单，验证后端渲染与设备 Wi-Fi 拉取链路（临时脚手架）',
    )
    parser.add_argument('--base-url', default=default_base,
                        help='后端地址。容器内默认直连自身 http://127.0.0.1:${SFM_PORT}；'
                             '容器外默认取环境变量或 backend/.env 的 SFM_BASE_URL')
    parser.add_argument('--token', default=env.get('SFM_API_TOKEN', ''),
                        help='app_token，默认取环境变量或 backend/.env 的 SFM_API_TOKEN')
    parser.add_argument('--device', default='dev001', help='device_id（默认 dev001）')
    parser.add_argument('--ca', default=default_ca,
                        help='校验后端证书用的 CA（容器外默认用固件里那份自签 CA；'
                             '容器内默认留空，因为直连明文端口不需要）')
    parser.add_argument('--page', default='overview', choices=['overview', 'detail'],
                        help='overview=今日总览（默认）；detail=某餐的详细做法页')
    parser.add_argument('--meal', default=None,
                        choices=['breakfast', 'lunch', 'dinner'],
                        help='用哪一餐的示例数据；不给则按当前时间自动选')
    parser.add_argument('--force', action='store_true',
                        help='顺便置位 force，设备已是最新版本时也强制重新拉一次')
    parser.add_argument('--watch', type=int, metavar='SEC',
                        help='每 SEC 秒重复下发一次，用于观察设备靠自身轮询是否会自动更新'
                             '（自动在画面上打时刻，便于确认屏幕真的换了）')
    parser.add_argument('--stamp', action='store_true',
                        help='在总览的日期区打上当前时刻（单次下发时也想确认屏幕更新用）')
    parser.add_argument('--preview', metavar='PNG',
                        help='只在本地渲染并导出量化预览图，不联网')
    parser.add_argument('--from-ai', action='store_true',
                        help='走 AI 链路：/api/recommend 生成菜谱后按 plan 渲染（需真实 AI_API_KEY + 有库存）')
    parser.add_argument('--seed-inventory', action='store_true',
                        help='往库里塞测试食材（AI 链路的前置条件）')
    parser.add_argument('--clear-inventory', action='store_true',
                        help='删除本脚本塞进去的测试食材')
    parser.add_argument('--people', type=int, default=3, help='AI 生成时的人数（默认 3）')
    args = parser.parse_args()

    if args.preview:
        do_preview(args.page, args.meal or meal_slot_now(), Path(args.preview))
        return

    ca = Path(args.ca) if args.ca else None
    if args.base_url.lower().startswith('https://') and ca and not ca.exists():
        sys.exit(f'✗ 找不到 CA 文件：{ca}\n  先跑 embedded/smart_fridge_magnet/certs/gen-ca.sh')

    client = BackendClient(args.base_url, args.token, ca, args.device)

    print(f'后端 : {args.base_url}')
    print(f'设备 : {args.device}')
    try:
        health = client.health()
        print(f"健康 : {health.get('overall_status')}")
    except requests.exceptions.SSLError as exc:
        sys.exit(f'✗ TLS 校验失败：{exc}\n'
                 '  证书的 SAN 是否包含你访问用的地址？重跑 gen-ca.sh 把该地址签进去。')
    except requests.exceptions.ConnectionError as exc:
        # 端口不匹配是这里最常见也最容易看错的失败：日志里服务「已启动」，脚本却连不上。
        # 部署时平台常把 SFM_PORT 注成 8443，而默认值是 5000。
        port_hint = os.environ.get('SFM_PORT', '').strip()
        sys.exit(
            f'✗ 连不上后端：{exc}\n'
            f'  当前尝试的地址：{args.base_url}\n'
            + (f'  本进程看到 SFM_PORT={port_hint}，服务应监听该端口 —— '
               f'若地址里的端口与它不符，用 --base-url http://127.0.0.1:{port_hint} 重试。\n'
               if port_hint else
               '  未设置 SFM_PORT，服务默认监听 5000。\n')
            + '  容器内请直连自身回环地址（对外 https 地址要绕出去再回来，多数环境不通）；\n'
              '  容器外请确认服务已启动且这台机器能路由到该地址。'
        )
    except requests.exceptions.RequestException as exc:
        sys.exit(f'✗ 请求失败：{exc}\n'
                 '  确认服务已启动，且这台机器能路由到该地址。')

    if args.clear_inventory:
        removed = 0
        seed_names = {item['name'] for item in SEED_INVENTORY}
        for item in client.list_inventory().get('items', []):
            if item.get('name') in seed_names:
                client.delete_inventory(item['id'])
                removed += 1
        print(f'✓ 已删除 {removed} 条测试食材')
        if not (args.seed_inventory or args.from_ai):
            return

    if args.seed_inventory:
        client.add_inventory(SEED_INVENTORY)
        # 以库里实际条数为准回报，而不是照抄请求条数：批量接口可能对同名同单位做累加，
        # 打印「发了 6 条」但库里其实是 3 条的话，排查 AI 推荐结果时会被带偏。
        total = client.list_inventory().get('total')
        print(f'✓ 已写入测试食材，当前库存共 {total} 条')

    slot = args.meal or meal_slot_now()
    if not args.meal:
        print(f'餐段 : {slot}（按当前时间自动选）')
    rounds = 0
    while True:
        rounds += 1
        if args.watch:
            print(f'\n—— 第 {rounds} 轮 · {datetime.now():%H:%M:%S} ——')

        result = publish_once(client, args.page, slot, args.from_ai, args.people,
                              stamp=bool(args.watch) or args.stamp)
        print(f"✓ 已发布 version={result.get('version')} "
              f"size={result.get('size')} sha256={str(result.get('sha256'))[:16]}…")

        if args.force:
            client.set_force()
            print('✓ 已置位 force（设备下次查询会强制重新下载）')

        report_device_state(client)

        if not args.watch:
            break
        print(f'（等待 {args.watch}s；设备靠自身轮询更新，无需手点 SYNC_NOW。Ctrl-C 结束）')
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            print('\n已停止')
            break

    if not args.watch:
        print('\n设备侧：点调试工具的「立即同步（SYNC_NOW）」，或等它自己轮询。')
        print('串口应依次出现：SNTP synced → sync gate: ok → manifest v* > last v* '
              '→ sha-verified → sync applied → Refresh event completed')


if __name__ == '__main__':
    main()
