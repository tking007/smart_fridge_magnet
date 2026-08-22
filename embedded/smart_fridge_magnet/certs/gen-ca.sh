#!/usr/bin/env bash
# 生成「自签 CA + 后端服务器证书」，用于设备通过局域网 HTTPS 拉取 manifest 与 .film。
#
# 为什么需要这套东西：
#   固件用 esp_crt_bundle 做 TLS 信任锚，默认只信任 Mozilla 公共 CA。局域网自建后端
#   拿不到公共 CA 签发的证书，因此我们自建一个 CA，把它的**公钥证书**编进固件的
#   certificate bundle（CONFIG_MBEDTLS_CUSTOM_CERTIFICATE_BUNDLE_PATH），再用它签发
#   后端服务器证书。这是「追加」而非「替换」——公共 CA 仍然受信任，所以 Cloudflare
#   隧道之类的方案可以随时作为备份，切换只需改 manifest_url，不必重烧固件。
#
# 为什么用 EC P-256 而不是 RSA2048：
#   ESP32-S3 没有 RSA 硬件加速，RSA2048 握手要 1~2 秒且占用更多 RAM；P-256 明显更快
#   更省。设备靠电池供电、每次唤醒同步都要重新握手，这个差异是实打实的。
#
# 用法：
#   bash gen-ca.sh <后端主机局域网IP> [额外DNS名]
#   例：bash gen-ca.sh 192.0.2.10
#
# 产物：
#   certs/sfm_ca.pem            CA 公钥证书  -> 按需编进本地固件【不入库】
#   certs/sfm_ca.key            CA 私钥      -> 【绝不入库】，仓库外妥善保管
#   ../../../backend/certs/sfm_server.pem    后端服务器证书【不入库】
#   ../../../backend/certs/sfm_server.key    后端服务器私钥【绝不入库】
#
# 安全提醒：
#   sfm_ca.key 一旦泄露，持有者可以签出任何域名的、被本设备信任的证书。
#   本脚本生成的证书和私钥均由 .gitignore 排除，请再确认它们没有被提交。
set -euo pipefail

CERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_FILES=("$CERT_DIR/.sfm_server.csr" "$CERT_DIR/.server_ext.cnf" "$CERT_DIR/.srl" "$CERT_DIR/sfm_ca.srl")
cleanup() { rm -f "${TEMP_FILES[@]}"; }
trap cleanup EXIT

if [[ $# -lt 1 ]]; then
    cat >&2 <<'USAGE'
用法: bash gen-ca.sh <地址> [地址...]

地址可以是 IPv4 或域名，可混合、可给多个 —— 全部写入服务器证书的 SAN。
设备访问后端时用哪个地址，那个地址就必须在 SAN 里，否则 TLS 校验失败。

例:
  bash gen-ca.sh 192.0.2.10                           # 示例地址
  bash gen-ca.sh sfm.example.com                      # 示例域名
  bash gen-ca.sh 192.0.2.20 sfm.example.com           # IP + 域名都签上
                                                       （先用 IP 联调、后切域名，无需重签）
USAGE
    exit 1
fi

# 把入参分类成 IP / DNS 两组，拼成 openssl 的 subjectAltName 值。
# 之所以要区分：证书里 iPAddress 与 dNSName 是两种不同的 SAN 类型，写错类型不会报错，
# 但校验时匹配不上 —— 表现为「证书明明签了这个地址却还是握手失败」。
SAN_ENTRIES=()
ADDR_SUMMARY=()
PRIMARY_ADDR=""
for addr in "$@"; do
    if [[ "$addr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        # 逐段范围校验，避免 999.1.1.1 这类被正则放过
        IFS='.' read -r o1 o2 o3 o4 <<< "$addr"
        for octet in "$o1" "$o2" "$o3" "$o4"; do
            if (( octet > 255 )); then
                echo "错误: '$addr' 不是合法的 IPv4 地址（字段 $octet > 255）" >&2
                exit 1
            fi
        done
        SAN_ENTRIES+=("IP:${addr}")
    elif [[ "$addr" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
        SAN_ENTRIES+=("DNS:${addr}")
    else
        echo "错误: '$addr' 既不是合法 IPv4 也不是合法域名" >&2
        exit 1
    fi
    ADDR_SUMMARY+=("$addr")
    [[ -z "$PRIMARY_ADDR" ]] && PRIMARY_ADDR="$addr"
done

SAN_VALUE="$(IFS=,; echo "${SAN_ENTRIES[*]}")"

BACKEND_CERT_DIR="$(cd "$CERT_DIR/../../../backend" && pwd)/certs"
mkdir -p "$BACKEND_CERT_DIR"

CA_KEY="$CERT_DIR/sfm_ca.key"
CA_PEM="$CERT_DIR/sfm_ca.pem"
SRV_KEY="$BACKEND_CERT_DIR/sfm_server.key"
SRV_PEM="$BACKEND_CERT_DIR/sfm_server.pem"

# ---------------------------------------------------------------- CA（10 年）
# 有效期刻意设长：更换 CA 需要重新烧写固件，而更换服务器证书不需要（同一 CA 签即可）。
# 所以 CA 应尽量长寿，日常轮换只动服务器证书。
if [[ -f "$CA_KEY" && -f "$CA_PEM" ]]; then
    echo "[1/3] 复用已有 CA: $CA_PEM"
    echo "      （要重建 CA 请先删除 sfm_ca.key/sfm_ca.pem —— 注意重建后必须重烧固件）"
else
    echo "[1/3] 生成自签 CA（EC P-256，10 年）"
    openssl ecparam -name prime256v1 -genkey -noout -out "$CA_KEY"
    chmod 600 "$CA_KEY"
    openssl req -x509 -new -key "$CA_KEY" -sha256 -days 3650 \
        -subj "/CN=SFM Local CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$CA_PEM"
fi

# ------------------------------------------------------- 服务器证书（2 年）
echo "[2/3] 生成后端服务器证书（SAN: $SAN_VALUE）"
openssl ecparam -name prime256v1 -genkey -noout -out "$SRV_KEY"
chmod 600 "$SRV_KEY"
openssl req -new -key "$SRV_KEY" -subj "/CN=sfm-backend" -out "$CERT_DIR/.sfm_server.csr"

# SAN 里把所有入参都写上，IP 与域名可共存：
#   IP   —— mbedtls 4.1 的 x509_crt_check_san_ip 支持 iPAddress SAN 校验，
#           设备直接用 IP 访问即可，不需要额外架 DNS。
#   DNS  —— 云平台通常给内网域名；先用 IP 联调、之后切域名时不必重签证书。
# 多签几个地址没有代价，而漏签一个就意味着换地址时要重跑一次（虽然不必重烧固件）。
cat > "$CERT_DIR/.server_ext.cnf" <<EOF
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyAgreement
extendedKeyUsage=serverAuth
subjectAltName=${SAN_VALUE}
EOF

openssl x509 -req -in "$CERT_DIR/.sfm_server.csr" \
    -CA "$CA_PEM" -CAkey "$CA_KEY" -CAcreateserial \
    -days 730 -sha256 -extfile "$CERT_DIR/.server_ext.cnf" \
    -out "$SRV_PEM"


# 刻意【不】把 CA 证书复制到 backend/：CA 是设备的信任锚，只应有一份权威副本
# （本目录，随固件一起编译）。复制副本会漂移 —— 只更新一处就会出现
# 「后端 curl 验得过、设备验不过」这类最难排查的不一致。
# 需要用 CA 校验时直接引用本目录的路径（见下方提示与 backend/README.md）。

# ------------------------------------------------------------------ 自检
echo "[3/3] 自检"
# 证书链必须能被 CA 验证通过 —— 这一步失败就不必浪费时间去烧固件了
openssl verify -CAfile "$CA_PEM" "$SRV_PEM"
echo -n "      SAN: "
openssl x509 -in "$SRV_PEM" -noout -ext subjectAltName | tail -n +2 | tr -d ' \n'
echo

cat <<EOF

完成。证书已覆盖地址：${ADDR_SUMMARY[*]}

接下来：

  1. 固件：在 idf.py menuconfig 中启用自定义证书 bundle，并将路径设为
       certs/sfm_ca.pem；然后执行：
       cd $(cd "$CERT_DIR/.." && pwd) && idf.py build && idf.py -p <PORT> flash

  2. 后端 .env 里设置（端口与 SFM_TLS_PORT 保持一致）
       SFM_BASE_URL=https://${PRIMARY_ADDR}:8443
       SFM_TLS_PORT=8443

  3. 起服务并用 CA 校验（不要加 -k，那样就验不出证书问题了）
       cd $(cd "$CERT_DIR/../../../backend" && pwd) && docker compose up -d --build
       curl --cacert $CA_PEM \\
            https://${PRIMARY_ADDR}:8443/api/health

  提醒：
   - 设备必须能从它所连的网络访问到 ${PRIMARY_ADDR}。若后端在云平台内网，
     设备所连的 Wi-Fi 也必须在同一内网（或有可达路由），否则拉取会超时。
   - sfm_ca.pem、sfm_ca.key、sfm_server.pem 与 sfm_server.key 均为本地部署产物，
     不要入库；私钥也不要通过聊天工具传递。
EOF
