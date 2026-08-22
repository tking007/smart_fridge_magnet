# 智能冰箱贴后端 — 生产镜像
#
# 用 gunicorn 而非 Flask 自带的 app.run()（后者是开发服务器，明确不可对外）。
# 容器内绑 0.0.0.0 是必要的（否则宿主无法访问），因此镜像强制要求提供 SFM_API_TOKEN：
# src/config.py 的 validate_runtime() 会在非回环绑定且缺少 token 时拒绝启动。
# gunicorn 直接加载 WSGI app，不经过 start_app()，故在 entrypoint 里显式做同样的校验。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SFM_HOST=0.0.0.0 \
    SFM_PORT=5000 \
    SFM_DEBUG=false \
    SFM_DATABASE_PATH=/data/fridge.db \
    SFM_FILM_DIR=/data/devices \
    SFM_LOG_DIR=/data/logs \
    SFM_FONT_PATH=/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
    SFM_ENABLE_SCHEDULER=true \
    TZ=Asia/Shanghai

# 时区必须显式给。镜像默认 UTC，而「几点做什么」的判断（每日更新时间、餐段推断、
# 印在电子纸上的日期）全部按**本地钟点**写的：UTC 下「06:30 更新」实际发生在北京时间
# 14:30，00:00–08:00 期间屏幕上印的还是前一天的日期。这类偏差不报错，只是「设置的时间
# 不生效」，而这句话很难让人想到时区。
#
# 库里的时间戳是另一回事：`CURRENT_TIMESTAMP` 是 UTC，与之比较的地方用的也是
# `utcnow()`，那部分本来就是对的，不受这里影响。

WORKDIR /app

# Debian 包镜像。境外 deb.debian.org 在国内构建机上可能极慢（实测过 16 kB/s，
# 下 11MB 花了 11 分钟），换国内镜像后同样内容 5300 kB/s。
# 置空则使用镜像自带的官方源。
ARG APT_MIRROR=mirrors.ustc.edu.cn

# 中文字体：后端要用 Pillow 把菜单渲染成 .film，而 python:*-slim 镜像里
# 【一个字体文件都没有】（实测 /usr/share/fonts 为空、fc-list 无输出），
# PIL 拿不到字体会渲染成空白或方框 —— 而那份图会被正常编码、正常下发、正常上屏，
# 也就是说错误只在电子纸上看得见，链路上任何一环都不会报错。
#
# 选 fonts-wqy-zenhei（17MB）而不是 fonts-noto-cjk（上百 MB）：
# 屏幕只有 792x528、正文字号 26~48px，两者字形差异在这个尺寸上看不出来，
# 不值得为此让镜像大一个数量级。
#
# 换源带**自动回退**：构建机的出网策略未知，若镜像域名不可达，硬切会让构建直接
# 失败 —— 那比慢更糟。先备份原始源，镜像不可用时恢复并重试官方源，
# 最坏情况只是退回原来的速度。
#
# 判据不能用 `apt-get update` 的退出码：源不可达时它只打 W: 警告而**仍然返回 0**，
# 失败会推迟到 install 阶段变成 "Unable to locate package"，届时回退分支早已错过。
# 改用 `apt-get install -s`（模拟安装）验证目标包确实可解析，这才是真正的成功条件。
#
# Debian 13 (trixie) 用 deb822 格式的 /etc/apt/sources.list.d/debian.sources，
# 已无 /etc/apt/sources.list，故两个路径都尝试以兼容其他基础镜像。
# 保持 http 而非改 https：官方源本就是 http（完整性由 GPG 签名保证），
# 而 slim 镜像未必带 ca-certificates，换成 https 反而可能握手失败。
RUN set -eux; \
    SRC=/etc/apt/sources.list.d/debian.sources; \
    [ -f "$SRC" ] || SRC=/etc/apt/sources.list; \
    cp "$SRC" "$SRC.orig"; \
    apt_ready() { \
        rm -rf /var/lib/apt/lists/*; \
        apt-get update -qq >/dev/null 2>&1 || true; \
        apt-get install -s -y --no-install-recommends fonts-wqy-zenhei >/dev/null 2>&1; \
    }; \
    if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" "$SRC"; \
    fi; \
    if ! apt_ready; then \
        echo "[build] apt 镜像 ${APT_MIRROR} 不可用，回退官方源" >&2; \
        cp "$SRC.orig" "$SRC"; \
        apt_ready; \
    fi; \
    apt-get install -y --no-install-recommends fonts-wqy-zenhei tzdata; \
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime; \
    echo "$TZ" > /etc/timezone; \
    rm -rf /var/lib/apt/lists/* "$SRC.orig"

# Python 包索引。与 apt 同理：境外 pypi.org 在国内构建机上往往是主要耗时来源，
# 换安装器（pip→uv）只能压缩并发等待，压不掉物理往返，换源才是根治。
# 已核对本项目 pin 的 8 个版本在 ustc / tuna / aliyun 镜像上全部齐全 ——
# 这一步必须核对：国内镜像同步有延迟，缺任一 pin 死的版本都会让构建失败。
# 置空则用官方 PyPI；也可指向私有镜像：
#   docker build --build-arg PIP_INDEX_URL=<私有镜像>/simple \
#                --build-arg PIP_TRUSTED_HOST=<私有镜像主机名> ...
ARG PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple
# 仅当索引走 http 或证书不被信任时才需要填；https 公共镜像留空即可。
ARG PIP_TRUSTED_HOST=""
# 镜像不可用时的回退目标，理由同 apt：构建失败比构建慢更糟。
ARG PIP_FALLBACK_INDEX_URL=https://pypi.org/simple

# 只先拷 requirements.txt（不是整个源码目录）：这样改 src/ 不会让依赖层失效，
# 镜像层缓存才能真正起作用。
COPY requirements.txt ./

# 装依赖。requirements.txt 未变时整层复用，不重新下载。
#
# 用 uv 而非 pip：本镜像的依赖已全部 pin 死，装的都是现成 wheel，耗时几乎全在
# 下载往返上，而 uv 并行下载。同条件实测（python:3.13-slim + 默认 PyPI，均禁用缓存）
# pip 23 秒 / uv 2 秒，即便算上装 uv 自己的 3 秒仍是 5 比 23。
# 装出的版本与 pip 完全一致（flask 3.1.3 / Pillow 12.3.0 等）。
#
# uv 仍从索引装而不用 ghcr.io/astral-sh/uv 的 COPY 方式：那能省几秒，但要求构建机
# 可访问 ghcr.io，内网未必可达；走 PyPI 只依赖本就必需的通道，少一个失败点。
# 装完即卸载，不留在最终镜像里。
#
# --system：容器里没有 venv，uv 默认会拒绝安装到全局解释器，必须显式声明。
# 保留 --retries/--timeout 语义（uv 用 UV_HTTP_TIMEOUT），应对不稳定网络。
#
# 两级回退：装 uv 或装依赖任一步在镜像源上失败，就整体改用官方 PyPI 重试一次。
# 不做「部分包走镜像、部分走官方」的混合：那会让构建结果取决于镜像同步进度，
# 同一个 Dockerfile 在不同时间产出不同镜像，比慢或失败都更难排查。
RUN set -eux; \
    install_all() { \
        idx="$1"; \
        pip install --no-cache-dir --retries 3 --timeout 60 \
            --index-url "$idx" ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} uv \
        && UV_HTTP_TIMEOUT=120 UV_INDEX_URL="$idx" \
            uv pip install --system --no-cache -r requirements.txt; \
    }; \
    PRIMARY="${PIP_INDEX_URL:-$PIP_FALLBACK_INDEX_URL}"; \
    if ! install_all "$PRIMARY"; then \
        echo "[build] pip 索引 ${PRIMARY} 不可用，回退 ${PIP_FALLBACK_INDEX_URL}" >&2; \
        install_all "$PIP_FALLBACK_INDEX_URL"; \
    fi; \
    pip uninstall -y uv; \
    python -c "import flask, PIL, gunicorn, apscheduler, requests, dotenv, flask_cors"

# 注意：这里刻意**不**清理 /root/.cache。
# 上面两条安装命令都已禁用缓存（pip --no-cache-dir、uv --no-cache），实测构建完
# 该目录根本不存在，清理它清不到任何东西；而 CI（CI/Kaniko）会把
# /root/.cache/pip 等子目录作为全局缓存挂载进来，在挂载点上 rm -rf 会得到
# "Device or resource busy"，配合 set -e 直接让整个构建失败。
# 也就是说那条命令没有收益、只有风险，正确处理是删掉而不是加 `|| true` 容错 ——
# 后者会把「在挂载点上误操作」这件事继续留在脚本里。

COPY src/ ./src/
# scripts/ 也要进镜像：云端部署后没有宿主 shell 环境，运维/联调只能
# `docker compose exec backend python scripts/xxx.py` 这样跑。
# 少了这一句，脚本在云上就完全不可用（容器内 /app/scripts 不存在）。
COPY scripts/ ./scripts/
# tests/ 同理。这些自检（TLS 头推导、.film 编码与固件校验器交叉验证、餐前下发幂等）
# 恰恰要在**部署后的真实环境**里跑才有意义：本机跑过一遍不能证明容器里的字体、
# 时区、挂载路径也对。此前镜像里没有 tests/，README 让人跑的命令在云上直接
# "No such file or directory"，等于文档说的自检根本做不了。
# 体积可忽略（几十 KB 纯 Python），换来的是部署后能自证。
COPY tests/ ./tests/

# 启动前置校验：容器对外暴露，必须有 app_token，否则 fail-closed 退出。
# 用 COPY --chmod 直接带上执行位，避免额外一层 RUN chmod —— 后者一旦排在 USER 之后
# 就会因为目标文件属主是 root 而失败（Operation not permitted），构建直接中断。
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# 非 root 运行：容器对外提供服务，没有任何理由让 gunicorn 以 root 跑。
# 两处顺序都很关键：
#  1) mkdir + chown 必须在 VOLUME 声明【之前】——docker 首次创建 named volume 时
#     会以镜像里该路径的属主/权限做初始化；反过来的话卷仍归 root，
#     非 root 进程启动后写 /data 会 Permission denied。
#  2) USER 必须放在所有需要 root 的指令【之后】（安装、chown、写 /usr/local/bin），
#     否则那些指令会以非特权身份执行而失败。
RUN useradd --system --create-home --shell /usr/sbin/nologin sfm \
    && mkdir -p /data/logs /data/devices \
    && chown -R sfm:sfm /data /app

# 数据与日志挂载点（SQLite 与 .film 落盘，需持久化）
# .film 必须落在这个卷内（SFM_FILM_DIR=/data/devices）：否则容器重建后
# 库里的版本记录仍在、文件却全丢，设备侧表现为 manifest 200 但下载 404。
VOLUME ["/data"]

USER sfm

# 5000 是默认值；实际监听端口由 SFM_PORT 决定（见下方 CMD）。
# 这里把容器平台常用的 8443 也声明出来，便于「探针固定打 8443」的平台直接对接 ——
# 只需注入 SFM_PORT=8443，不必改平台探针配置。
# EXPOSE 仅为声明性元数据，不影响实际监听。
EXPOSE 5000 8443

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# 用 shell 形式而非 exec 形式，是为了让 ${SFM_PORT} 真正展开。
# 之前 CMD 里硬编码了 5000，导致 SFM_PORT 这个环境变量【看起来可配、实际无效】——
# 而容器平台的探针端口往往是固定的（如 8443），端口对不上就会出现
# 「应用正常启动却被探针反复杀掉」，事件里只有 probe failed、connection refused，
# 很难第一时间想到是应用没监听那个端口。
#
# 前面加 exec：让 gunicorn 取代 shell 成为 PID 1，否则 SIGTERM 会被 shell 吞掉，
# 容器停止时不能优雅退出，只能等超时被 SIGKILL。
#
# workers 固定为 1：APScheduler 在多 worker 下会每进程各起一份调度器，导致定时任务
# 重复执行。并发用线程承载（本服务以 I/O 等待为主）。
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${SFM_PORT:-5000} --workers 1 --threads 8 --timeout 120 --access-logfile - src.app:app"]
