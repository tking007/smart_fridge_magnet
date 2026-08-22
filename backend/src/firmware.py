"""固件托管与 Wi-Fi OTA 版本判定。

设备每次唤醒拉 manifest 时顺带问一次「有没有新固件」，有就在同一次唤醒里下载并重启。
固件文件放在 config.firmware_dir()，文件名即版本：`sfm-<version>.bin`。

版本比较要解析成数字元组而不是比字符串：字符串比较下 "0.9.0" > "0.10.0"（'9' > '1'），
设备升到 0.9.0 之后再也收不到 0.10.0，且不报任何异常，只表现成「某些设备就是不升级」。

不做「强制降级」：本模块只在「候选版本严格大于设备当前版本」时才回内容。要回滚一个有问题
的发布，正确做法是发一个版本号更高的修复版 —— 设备侧的 last_applied 之类状态随固件版本
演进，往回推等于让新状态遇上旧代码。
"""
import hashlib
import os
import re
from typing import Optional

from . import config
from .logger import get_logger

logger = get_logger(__name__)

#: 固件文件名格式：sfm-<version>.bin。版本段允许 1~4 组数字，可带 -suffix。
_FIRMWARE_NAME_RE = re.compile(r'^sfm-(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z.]+))?\.bin$')

#: ESP32-S3 的 app 分区上限（partitions.csv 里 ota_0/ota_1 各 1920K）。
#: 超过这个大小的文件不可能刷进去，与其让设备下载完才失败，不如在这里就不列出来。
MAX_FIRMWARE_BYTES = 1920 * 1024

_ESP_IMAGE_MAGIC = 0xE9
_ESP_APP_DESC_OFFSET = 24 + 8
_ESP_APP_DESC_MAGIC = 0xABCD5432
_ESP_APP_VERSION_OFFSET = _ESP_APP_DESC_OFFSET + 16
_ESP_APP_PROJECT_OFFSET = _ESP_APP_VERSION_OFFSET + 32
_ESP_APP_TEXT_BYTES = 32
_ESP_PROJECT_NAME = 'smart_fridge_magnet'


def parse_version(text) -> Optional[tuple]:
    """把 "1.2.3" 解析成 (1, 2, 3) 供比较；解析不了返回 None。

    带后缀的预发布版（"1.2.3-rc1"）只取数字段比较，后缀不参与：后缀的排序规则
    （rc < beta？alpha < rc？）没有共识，猜一个出来会让「哪个更新」变成掷硬币。
    """
    if not text:
        return None
    cleaned = str(text).strip().lstrip('vV')
    match = re.match(r'^(\d+(?:\.\d+){0,3})', cleaned)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split('.'))


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _embedded_identity(path: str) -> Optional[tuple[str, str]]:
    """读取 ESP app descriptor 中的 `(version, project_name)`；镜像头非法返回 None。"""
    required = _ESP_APP_PROJECT_OFFSET + _ESP_APP_TEXT_BYTES
    try:
        with open(path, 'rb') as handle:
            header = handle.read(required)
    except OSError:
        return None
    if (len(header) < required or header[0] != _ESP_IMAGE_MAGIC
            or int.from_bytes(header[_ESP_APP_DESC_OFFSET:_ESP_APP_DESC_OFFSET + 4],
                              'little') != _ESP_APP_DESC_MAGIC):
        return None

    def text_at(offset: int) -> str:
        raw = header[offset:offset + _ESP_APP_TEXT_BYTES].split(b'\0', 1)[0]
        return raw.decode('utf-8', errors='strict').strip()

    try:
        version = text_at(_ESP_APP_VERSION_OFFSET)
        project_name = text_at(_ESP_APP_PROJECT_OFFSET)
    except UnicodeDecodeError:
        return None
    return (version, project_name) if version and project_name else None


def list_firmware() -> list:
    """列出可用固件，按版本从新到旧。

    过滤掉超过分区容量的文件：设备下载到最后一刻才发现刷不进去，白耗一次射频与电量。
    """
    directory = config.firmware_dir()
    if not os.path.isdir(directory):
        return []

    found = []
    for name in os.listdir(directory):
        match = _FIRMWARE_NAME_RE.match(name)
        if not match:
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        if size == 0:
            logger.warning('固件文件为空，已跳过：%s', name)
            continue
        if size > MAX_FIRMWARE_BYTES:
            logger.warning('固件 %s 有 %d 字节，超过 app 分区上限 %d，已跳过',
                           name, size, MAX_FIRMWARE_BYTES)
            continue
        version_text = match.group(1) + (f'-{match.group(2)}' if match.group(2) else '')
        identity = _embedded_identity(path)
        if identity != (version_text, _ESP_PROJECT_NAME):
            logger.warning(
                '固件镜像身份与文件名不一致，已跳过：file=%s expected=(%s,%s) actual=%r',
                name, version_text, _ESP_PROJECT_NAME, identity)
            continue
        found.append({
            'version': version_text,
            'filename': name,
            'size': size,
            'sort_key': parse_version(version_text) or (0,),
        })

    found.sort(key=lambda item: item['sort_key'], reverse=True)
    for item in found:
        item.pop('sort_key', None)
    return found


def latest_firmware() -> Optional[dict]:
    """最新的一个固件；目录为空返回 None。"""
    available = list_firmware()
    return available[0] if available else None


def firmware_path(filename: str) -> Optional[str]:
    """把文件名解析成磁盘路径；名字不合格式或文件不存在返回 None。

    只接受 _FIRMWARE_NAME_RE 匹配的名字，这同时挡掉了路径穿越（`../` 不可能匹配）。
    """
    if not _FIRMWARE_NAME_RE.match(filename or ''):
        return None
    path = os.path.join(config.firmware_dir(), filename)
    return path if os.path.isfile(path) else None


def check_update(current_version, base_url: str = '') -> dict:
    """回答「这台设备该不该升级」。

    @param current_version 设备自报的固件版本（manifest 请求头 X-Device-Firmware）。
    @param base_url        设备本次请求所用的基址，用来拼可达的下载地址。
        与 manifest 的 film_url 同一套理由：设备能拉到本接口就证明这个地址通。

    @return {'update_available': bool, 'latest_version': str|None, ...}
        update_available 为 True 时附 url / size / sha256。
    """
    latest = latest_firmware()
    if latest is None:
        return {'update_available': False, 'latest_version': None,
                'reason': '后端没有可用固件'}

    current = parse_version(current_version)
    candidate = parse_version(latest['version'])

    if candidate is None:
        # 文件名通过了正则却解析不出版本，说明正则与解析器不一致，是代码缺陷而非数据问题。
        logger.error('固件 %s 的版本解析失败，正则与 parse_version 不一致',
                     latest['filename'])
        return {'update_available': False, 'latest_version': latest['version'],
                'reason': '固件版本无法解析'}

    if current is None:
        # 设备没报版本（旧固件不带这个头）。此时无法判断新旧，不推送：
        # 盲推等于让每次唤醒都下载一遍 1.7MB，而设备是电池供电的。
        return {'update_available': False, 'latest_version': latest['version'],
                'reason': '设备未上报固件版本，无法判断是否需要升级'}

    if candidate <= current:
        return {'update_available': False, 'latest_version': latest['version'],
                'reason': '设备已是最新'}

    path = firmware_path(latest['filename'])
    if path is None:
        logger.error('固件 %s 在列表里但读不到文件', latest['filename'])
        return {'update_available': False, 'latest_version': latest['version'],
                'reason': '固件文件不可读'}

    return {
        'update_available': True,
        'latest_version': latest['version'],
        'url': f"{base_url.rstrip('/')}/api/firmware/{latest['filename']}",
        'size': latest['size'],
        'sha256': _sha256_of(path),
    }
