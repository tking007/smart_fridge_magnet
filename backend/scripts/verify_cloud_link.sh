#!/usr/bin/env bash
# 云端链路端到端自检：确认设备真机同步所需的每个前置条件都已满足。
#
# 为什么需要这个脚本：这条链路的失败大多是【静默】的 —— 后端全程 200、日志无异常，
# 只有设备永远不下载。最典型的是 manifest 里的 film_url 退化成 http：固件按 https
# 硬约束直接拒收，而后端侧看不到任何错误。与其等真机失败再回头排查，不如在烧写前
# 把每个条件逐项断言一遍。
#
# 用法：
#   SFM_API_TOKEN=<你的app_token> ./scripts/verify_cloud_link.sh
#   SFM_API_TOKEN=xxx SFM_URL=https://其他域名 ./scripts/verify_cloud_link.sh
#
# 退出码 0 表示可以进入真机验证；非 0 表示有阻塞项，输出里会指明是哪一项。

set -uo pipefail

URL="${SFM_URL:-}"
DEVICE="${SFM_DEVICE_ID:-dev001}"
TOKEN="${SFM_API_TOKEN:-}"
# 私有 CA（本机 caddy 用自签证书时需要）。云端走公网 CA，留空即可。
CA="${SFM_CA_CERT:-}"

if [ -z "$TOKEN" ] || [ -z "$URL" ]; then
    echo "用法: SFM_URL=https://<host>:<port> SFM_API_TOKEN=<app_token> $0" >&2
    echo "（就是你在云平台环境变量里配的那个 SFM_API_TOKEN）" >&2
    echo >&2
    echo "可选环境变量:" >&2
    echo "  SFM_URL       必填，目标 HTTPS 基址" >&2
    echo "  SFM_DEVICE_ID 设备号，默认 dev001" >&2
    echo "  SFM_CA_CERT   私有 CA 证书路径（本机自签环境用）" >&2
    echo "  SFM_DEVICE_TOKEN 设备侧 token；设备已登记过 token 时必填" >&2
    exit 2
fi

# 设备侧 token。manifest 与 .film 下载走的是【设备】鉴权（固件发的
# Authorization: Bearer {device_token}），与 Android 用的 app_token 是两套互不
# 复用的凭据。若该设备尚未在 devices 表登记 token，后端会放行，此时回落到
# app_token 即可；一旦登记过，就必须用登记的值，否则这两个接口返回 4001。
DEVICE_TOKEN="${SFM_DEVICE_TOKEN:-$TOKEN}"

HOST=$(printf '%s' "$URL" | sed -E 's#^https?://##; s#/.*$##; s#:.*$##')
PORT=$(printf '%s' "$URL" | sed -nE 's#^https?://[^:/]+:([0-9]+).*#\1#p')
PORT="${PORT:-443}"

CURL_CA=()
OSSL_CA=()
if [ -n "$CA" ]; then
    CURL_CA=(--cacert "$CA")
    OSSL_CA=(-CAfile "$CA")
fi

fails=0

# 仅在输出到终端时着色：重定向到文件或经管道时输出裸文本，
# 否则 ANSI 转义码会混进日志，让 grep / 计数这类后续处理失效。
if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_NG=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_OK=''; C_NG=''; C_OFF=''
fi

pass() { printf '  %sPASS%s  %s\n' "$C_OK" "$C_OFF" "$1"; }
fail() { printf '  %sFAIL%s  %s\n' "$C_NG" "$C_OFF" "$1"; fails=$((fails+1)); }
info() { printf '        %s\n' "$1"; }

echo "目标: $URL   设备: $DEVICE"
echo

# ── 1. TLS：证书链必须能被公共信任锚验证 ─────────────────────────────
# 固件用 esp_crt_bundle 校验服务端证书，本机 openssl 的信任库与之高度重叠，
# 因此这里验证通过是固件能验证通过的必要条件（非充分，故下面还要查根 CA）。
echo "[1/6] TLS 证书与协议"
tls=$(timeout 25 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" \
        "${OSSL_CA[@]}" </dev/null 2>&1)
if grep -q "Verify return code: 0 (ok)" <<<"$tls"; then
    pass "证书链校验通过"
    info "$(grep -oE 'subject=CN *= *[^ ,]+' <<<"$tls" | head -1)"
    root=$(grep -E '^ *[0-9]+ s:' <<<"$tls" | tail -1 | sed 's/^ *//')
    info "根 CA: ${root#* s:}"
else
    fail "证书链校验失败 —— 固件同样会拒绝"
    info "$(grep -E 'Verify return code|verify error' <<<"$tls" | head -2)"
fi

# 固件的 mbedTLS 只启用了 TLS 1.2（CONFIG_MBEDTLS_SSL_PROTO_TLS1_3 未设）。
# 服务端若只允许 1.3，握手会直接失败。
if timeout 25 openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" -tls1_2 \
     "${OSSL_CA[@]}" </dev/null 2>&1 | grep -q "Verify return code: 0 (ok)"; then
    pass "TLS 1.2 可协商（固件仅支持 1.2）"
else
    fail "TLS 1.2 不可用 —— 入口可能配了仅 TLS 1.3 的套餐，固件无法握手"
fi

# ── 2. 存活探针 ───────────────────────────────────────────────────
# 这一项是后面所有检查的前提。连不上就直接收尾：继续跑只会产出一串由同一个原因
# 派生的 FAIL，把真正的问题埋掉 —— 而且「拿不到响应」和「响应内容不对」是两种
# 完全不同的结论，混在一起会误导判断。
echo "[2/6] 服务可达性"
code=$(curl -sS -m 20 "${CURL_CA[@]}" -o /dev/null -w '%{http_code}' \
         "$URL/healthz" 2>/dev/null)
if [ "$code" = "200" ]; then
    pass "GET /healthz -> 200"
elif [ "$code" = "000" ]; then
    fail "无法建立连接（curl 000）"
    info "常见原因：DNS 未解析、入口未放通、或证书不被信任。"
    info "若目标是本机自签环境，用 SFM_CA_CERT=<ca.pem> 指定信任锚。"
    echo
    echo "后续检查全部依赖此连接，已跳过。先解决连通性再重跑。"
    exit 1
else
    fail "GET /healthz -> $code（入口到后端的转发可能未配好）"
fi

# ── 3. 鉴权 ──────────────────────────────────────────────────────
echo "[3/6] 鉴权"
code=$(curl -sS -m 20 "${CURL_CA[@]}" -o /dev/null -w '%{http_code}' \
         -H "Authorization: Bearer $TOKEN" "$URL/api/inventory" 2>/dev/null)
if [ "$code" = "200" ]; then
    pass "app_token 有效"
elif [ "$code" = "401" ]; then
    fail "app_token 无效 —— 与云端 SFM_API_TOKEN 不一致"
else
    fail "GET /api/inventory -> $code"
fi

# 公开占位值必须不可用；当前 .env.example 要求留空，历史模板值也一并拒绝。
# 只有确实收到了 HTTP 响应才能下结论 —— 把连接失败当成「占位 token 有效」
# 会给出一个既错误又骇人的安全结论。
PLACEHOLDER_TOKEN='replace-with-random-demo-token'
placeholder=$(curl -sS -m 15 "${CURL_CA[@]}" -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer $PLACEHOLDER_TOKEN" \
        "$URL/api/inventory" 2>/dev/null)
# 后端只比对一个 token，所以「占位值可用」只有两种成因，危险程度不同，
# 混为一谈会让人找错方向：
case "$placeholder" in
    401)
        pass "示例占位 token 已失效"
        ;;
    000)
        info "占位 token 检查跳过（未收到响应）"
        ;;
    *)
        if [ "$TOKEN" = "$PLACEHOLDER_TOKEN" ]; then
            fail "当前使用的就是 .env.example 里的示例 token"
            info "该值随仓库公开，对外服务前必须换成 openssl rand -hex 32 的输出。"
        else
            fail "占位 token 返回 $placeholder，而你传入的是另一个值"
            info "后端只比对单个 token，出现这种情况说明它根本没校验 ——"
            info "极可能 SFM_API_TOKEN 未注入，此时所有请求都被放行。"
        fi
        ;;
esac

# ── 4. 渲染并发布一张测试画面 ──────────────────────────────────────
# 用后端自带的渲染能力造数据，不依赖 App，这样链路验证与 App 进度解耦。
echo "[4/6] 渲染并发布测试画面"
resp=$(curl -sS -m 60 "${CURL_CA[@]}" -X POST \
         -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
         -d '{"kind":"overview","title":"链路自检","subtitle":"verify_cloud_link"}' \
         "$URL/api/devices/$DEVICE/display/render" 2>/dev/null)
if grep -q '"success": *true' <<<"$resp"; then
    ver=$(sed -nE 's/.*"version": *([0-9]+).*/\1/p' <<<"$resp" | head -1)
    pass "渲染并发布成功（version=${ver:-?}）"
else
    fail "渲染发布失败"
    info "$(head -c 300 <<<"$resp")"
fi

# ── 5. 最关键一项：manifest 里的 film_url 必须是 https ───────────────
# 反向代理终结 TLS 后，后端这一跳是明文的，外部协议只能从代理注入的头还原：
# caddy/多数云 LB 用 X-Forwarded-Proto，部分反向代理 用 X-Scheme。两者都不透传时
# film_url 会退化成 http，固件拒收 —— 后端却毫无异常，是最难查的一类故障。
echo "[5/6] manifest 的 film_url"
man=$(curl -sS -m 25 "${CURL_CA[@]}" -H "Authorization: Bearer $DEVICE_TOKEN" \
        "$URL/api/devices/$DEVICE/display/manifest" 2>/dev/null)
film=$(sed -nE 's/.*"film_url": *"([^"]+)".*/\1/p' <<<"$man" | head -1)
if [ -z "$film" ]; then
    fail "manifest 里没有 film_url"
    info "$(head -c 300 <<<"$man")"
    # 4001 在这里几乎总是「用了 app_token 去请求设备接口」，而不是配置错误。
    if grep -q '4001' <<<"$man"; then
        info "该设备已在 devices 表登记 device_token，请用它重跑："
        info "  SFM_DEVICE_TOKEN=<设备登记的token> $0"
    fi
elif [[ "$film" == https://* ]]; then
    pass "film_url 为 https"
    info "$film"
    [[ "$film" == https://"$HOST"* ]] \
        && pass "film_url 主机名与入口一致（证书 SAN 覆盖）" \
        || fail "film_url 主机名($film)与入口($HOST)不一致，证书可能不覆盖"
else
    fail "film_url 不是 https，固件会直接拒收该 manifest"
    info "$film"
    info "原因通常是入口既不透传 X-Forwarded-Proto 也不透传 X-Scheme；"
    info "或后端镜像未包含 X-Scheme 兼容修复。"
fi

# ── 6. 按 manifest 真实下载一次，核对 SHA-256 ──────────────────────
# 固件会校验摘要，这里提前用同样的方式验证一遍：能避免「manifest 200 但
# 下载 404」这类数据库与文件不同步的问题（多见于 /data 未挂持久卷）。
echo "[6/6] 按 manifest 下载并校验摘要"
if [ -n "$film" ]; then
    sha=$(sed -nE 's/.*"sha256": *"([^"]+)".*/\1/p' <<<"$man" | head -1)
    tmp=$(mktemp)
    dl=$(curl -sS -m 120 "${CURL_CA[@]}" -H "Authorization: Bearer $DEVICE_TOKEN" \
           -o "$tmp" -w '%{http_code}' "$film" 2>/dev/null)
    if [ "$dl" = "200" ]; then
        size=$(stat -c%s "$tmp")
        [ "$size" = "209120" ] && pass "下载成功，大小 209120 字节（符合 .film 契约）" \
                              || fail "大小 $size 字节，应为 209120"
        actual=$(sha256sum "$tmp" | cut -d' ' -f1)
        if [ "$actual" = "$sha" ]; then
            pass "SHA-256 与 manifest 一致（固件会通过校验）"
        else
            fail "SHA-256 不一致：manifest=$sha actual=$actual"
        fi
    else
        fail "下载 film_url -> HTTP $dl"
        [ "$dl" = "404" ] && info "manifest 有记录但文件不存在 —— /data 可能未挂持久卷"
    fi
    rm -f "$tmp"
else
    fail "跳过（无 film_url）"
fi

echo
if [ "$fails" -eq 0 ]; then
    echo "全部通过 —— 可以进入真机验证：给设备配网后触发一次 Wi-Fi 同步。"
    exit 0
fi
echo "有 $fails 项未通过，上面已标出。真机同步在这些项修复前不会成功。"
exit 1
