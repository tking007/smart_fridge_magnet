#!/bin/sh
# 启动前置安全校验。
#
# gunicorn 直接加载 WSGI app，不经过 src/app.py 的 start_app()，因此那里的
# config.validate_runtime() 不会被执行。这里显式复用同一份校验逻辑（而不是在 shell 里
# 重写一遍规则），保证容器绑 0.0.0.0 时必须配置 SFM_API_TOKEN、且不允许开 debug。
set -e

python - <<'PY'
import os
import sys
from src import config

# 先尝试加载挂载进来的 .env。
#
# 注意「.env 文件」在不同编排下的含义完全不同：docker compose 的 env_file 是由 docker
# 读文件并注入环境变量，应用侧从不解析；而 K8s 没有这个机制 —— 把 .env 挂进容器
# （哪怕路径正确）默认不会有任何效果，下面的校验照样读不到 SFM_API_TOKEN 而退出，
# 表现为 CrashLoopBackOff 且文件明明就在那里，非常难自证。故在此显式支持读文件。
#
# 已存在的环境变量优先（不被文件覆盖）：编排层注入的值代表更明确的部署意图，
# 而镜像里可能烤进过时的 .env。
loaded_from = config.load_env_file()
if loaded_from:
    sys.stderr.write(f"[entrypoint] 已加载环境文件：{loaded_from}"
                     "（不覆盖已注入的环境变量）\n")

# 把关键配置的**来源与状态**打出来，不打印任何凭据值。
# 这几行是排查启动失败最有用的信息：能立刻区分「变量没注入」和「注入了但值不对」。
sys.stderr.write(
    "[entrypoint] 配置概览: "
    f"SFM_HOST={config.host()} "
    f"SFM_API_TOKEN={'已设置' if config.api_token() else '【缺失】'} "
    f"SFM_BASE_URL={config.configured_base_url() or '(留空→按请求来源自适应)'} "
    f"AI_API_KEY={'已设置' if os.environ.get('AI_API_KEY') else '未设置'}\n"
)

try:
    config.validate_runtime()
except RuntimeError as exc:
    sys.stderr.write(f"[entrypoint] {exc}\n")
    # 缺 token 是这里最常见的失败，且在 K8s 上极易被误判为「.env 挂载路径不对」。
    # 直接把两条正确做法写在错误旁边，省掉一轮查文档。
    if not config.api_token():
        sys.stderr.write(
            "[entrypoint] 提示：K8s 等编排环境请用环境变量注入，例如\n"
            "  envFrom: [{secretRef: {name: sfm-backend-env}}]\n"
            "或把 .env 挂进容器后用 SFM_ENV_FILE 指向它（默认已尝试 /app/.env）。\n"
            "光把 .env 挂进镜像目录不够 —— 除本 entrypoint 显式加载外，"
            "应用本身只读环境变量。\n"
        )
    sys.exit(1)

# 显式配置了非 https 的 base_url 时，validate_runtime() 已直接拒绝启动（第三条硬约束），
# 所以这里不重复校验那种情况。
#
# 需要区分三种状态，否则提示会误报：
#   1) 显式配了 https        -> 正常，无需提示
#   2) 留空                  -> film_url 按设备请求来源自适应推导，是受支持的部署方式
#                              （云上容器 IP 随机、入口地址可能变动，留空往往比写死更可靠）
#   3) 显式配了 http 且开了豁免 -> 知情的过渡状态，但真机必定失败，必须留一条记录，
#                              避免过渡状态被遗忘后拿去联调真机、白花一轮排查时间
explicit = config.configured_base_url()
if not explicit:
    sys.stderr.write(
        "[entrypoint] 提示（非错误）：SFM_BASE_URL 未配置，manifest 里的 film_url 将按"
        "【设备请求来源】自适应推导 —— 这是云端推荐配置。\n"
        "[entrypoint]   协议识别支持 X-Forwarded-Proto（caddy / 多数云 LB）与 "
        "X-Scheme（仅提供 X-Scheme 的反向代理），主机名取 X-Forwarded-Host 或 Host。\n"
        "[entrypoint]   若入口两者都不透传，film_url 会退化成 http 而被固件拒收；"
        "可请求一次 manifest 检查返回的 film_url 是否为 https。\n"
    )
elif not explicit.lower().startswith('https://'):
    sys.stderr.write(
        "[entrypoint] 注意：SFM_ALLOW_INSECURE_BASE_URL 已开启且 SFM_BASE_URL 为 http"
        f"（{explicit}）。此状态下真机 Wi-Fi 同步必定失败"
        "（固件强制要求 film_url 为 HTTPS），仅可用于本地联调。\n"
    )
PY

# 持久化目录可写性预检。
#
# 镜像里 /data 及其两个子目录是 root 建好后 chown 给 sfm 的，容器以 sfm 运行。
# 但**挂载卷会盖掉镜像里的那份目录**，两种编排的行为还不一样：
#   - docker named volume：首次创建时会把镜像里该路径的内容与属主复制进卷，属主得以保留；
#   - K8s / 多数云平台的 PVC：不复制、不继承，新卷通常是 root:root 0755 —— 非 root 的
#     sfm 于是对 /data 只读。
# 后者的表现是 sqlite3.OperationalError: unable to open database file，发生在导入应用时，
# 于是 gunicorn worker 起不来、容器 CrashLoop，而真正的原因（一个权限位）埋在 traceback 里。
# 与其让它以那种形式暴露，不如在这里明确报出来并给出可执行的修法。
python - <<'PY'
import os
import sys
from src import config

targets = {
    'SQLite 目录': os.path.dirname(os.path.abspath(config.database_path())) or '.',
    '.film 目录': config.film_dir(),
    '日志目录': config.log_dir(),
}

problems = []
for label, path in targets.items():
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        problems.append(f'{label} {path} —— 无法创建：{exc.strerror}')
        continue
    if not os.access(path, os.W_OK | os.X_OK):
        problems.append(f'{label} {path} —— 存在但当前用户不可写')

if problems:
    sys.stderr.write('[entrypoint] 持久化目录不可写，拒绝启动：\n')
    for item in problems:
        sys.stderr.write(f'[entrypoint]   ✗ {item}\n')
    try:
        import pwd
        who = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        who = '?'
    sys.stderr.write(
        f'[entrypoint] 当前进程身份：{who} uid={os.getuid()} gid={os.getgid()}\n'
        '[entrypoint] 典型成因：把持久卷挂到 /data 后，卷的属主是 root，而本镜像以非 root\n'
        '[entrypoint]   用户运行（docker 的 named volume 会继承镜像里的属主，K8s 的 PVC 不会）。\n'
        '[entrypoint] 修法（任选其一）：\n'
        f'[entrypoint]   1) 部署配置里加 securityContext.fsGroup={os.getgid()}（推荐，挂载时自动改组属主）\n'
        '[entrypoint]   2) 平台支持时把挂载目录属主设为该 uid/gid\n'
        '[entrypoint]   3) 临时绕过：把 SFM_DATABASE_PATH / SFM_FILM_DIR / SFM_LOG_DIR 指到\n'
        '[entrypoint]      容器内可写路径 —— 但那样数据不持久，容器重建即丢，仅可用于验证。\n'
    )
    sys.exit(1)

sys.stderr.write(
    '[entrypoint] 持久化目录检查通过: '
    + ' '.join(f'{label}={path}' for label, path in targets.items()) + '\n'
)
PY

exec "$@"
