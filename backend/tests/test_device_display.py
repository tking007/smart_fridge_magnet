#!/usr/bin/env python3
"""
设备显示路由测试

测试项：
1. 上传 .film 文件
2. 获取 manifest
3. 下载 .film 文件
4. 查看同步状态
"""

import requests
import os
import sys
import hashlib

# 刻意不再重绑 sys.stdout。
#
# 这里原本有一句 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`，
# 它在**模块导入期**就执行。pytest 收集用例时会 import 每个测试模块，于是这一句会把
# pytest 自己的捕获流换成一个新的 TextIOWrapper；该包装器被回收时连带关掉底层 buffer，
# pytest 收尾读取捕获内容就炸成 `ValueError: I/O operation on closed file` ——
# **整个测试套件一个用例都跑不了**，而报错信息里完全看不出是哪个模块干的。
#
# 编码问题本身也不需要这句：Python 3.7+ 在 Linux/容器里默认 UTF-8；真要强制可以用
# 环境变量 PYTHONIOENCODING=utf-8 或 PYTHONUTF8=1，那是进程级设置，不会破坏调用方的流。

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE_URL = os.environ.get('SFM_BASE_URL', 'http://127.0.0.1:5000')
DEVICE_ID = 'test-device-001'

# 智能冰箱贴六色电子纸规格
FILM_SIZE = 209120
FILM_HEADER_SIZE = 32
PIXEL_DATA_SIZE = FILM_SIZE - FILM_HEADER_SIZE


def create_test_film() -> bytes:
    """创建测试用 .film 文件"""
    # 文件头：像素数据长度 (4 bytes, little-endian)
    header = PIXEL_DATA_SIZE.to_bytes(4, 'little')
    # 补充到 32 字节
    header += b'\x00' * (FILM_HEADER_SIZE - 4)
    # 像素数据（填充随机色）
    pixels = os.urandom(PIXEL_DATA_SIZE)
    return header + pixels


def test_upload():
    """测试上传 .film"""
    print("\n=== 测试上传 .film ===")

    film_data = create_test_film()
    print(f"创建测试文件: {len(film_data)} bytes")

    expected_sha256 = hashlib.sha256(film_data).hexdigest()
    print(f"SHA-256: {expected_sha256}")

    url = f"{BASE_URL}/api/devices/{DEVICE_ID}/display"
    headers = {'Content-Type': 'application/octet-stream'}

    try:
        response = requests.post(url, data=film_data, headers=headers)
        print(f"状态码: {response.status_code}")
        result = response.json()
        print(f"响应: {result}")

        if result.get('success'):
            print(f"✅ 上传成功，版本: {result['version']}")
            return result['version']
        else:
            print(f"❌ 上传失败: {result.get('error')}")
            return None
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return None


def test_manifest():
    """测试获取 manifest"""
    print("\n=== 测试获取 manifest ===")

    url = f"{BASE_URL}/api/devices/{DEVICE_ID}/display/manifest"

    try:
        response = requests.get(url)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            manifest = response.json()
            print(f"Manifest: {manifest}")
            print(f"✅ 版本: {manifest['version']}, 大小: {manifest['size']}")
            return manifest
        else:
            print(f"❌ 获取失败: {response.json()}")
            return None
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return None


def test_download(version: int):
    """测试下载 .film"""
    print(f"\n=== 测试下载 .film (版本 {version}) ===")

    url = f"{BASE_URL}/api/devices/{DEVICE_ID}/display/{version}.film"

    try:
        response = requests.get(url)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            data = response.content
            sha256 = hashlib.sha256(data).hexdigest()
            print(f"下载大小: {len(data)} bytes")
            print(f"SHA-256: {sha256[:16]}...")
            print("✅ 下载成功")
            return True
        else:
            print(f"❌ 下载失败: {response.json()}")
            return False
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False


def test_sync_status():
    """测试同步状态"""
    print("\n=== 测试同步状态 ===")

    url = f"{BASE_URL}/api/devices/{DEVICE_ID}/sync-status"

    try:
        response = requests.get(url)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            status = response.json()
            print(f"设备: {status['device_id']}")
            print(f"最新版本: {status['latest_version']}")
            print(f"最近事件: {len(status['recent_events'])} 条")
            print("✅ 查询成功")
            return True
        else:
            print(f"❌ 查询失败: {response.json()}")
            return False
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False


def test_invalid_upload():
    """测试无效上传"""
    print("\n=== 测试无效上传 ===")

    url = f"{BASE_URL}/api/devices/{DEVICE_ID}/display"
    headers = {'Content-Type': 'application/octet-stream'}

    # 测试错误大小
    wrong_size_data = b'\x00' * 100
    try:
        response = requests.post(url, data=wrong_size_data, headers=headers)
        print(f"错误大小测试 - 状态码: {response.status_code} (期望 413)")
        assert response.status_code == 413, f"期望 413, 得到 {response.status_code}"
        print("✅ 错误大小正确拒绝")
    except Exception as e:
        print(f"❌ 测试失败: {e}")


def test_health():
    """测试健康检查"""
    print("\n=== 测试健康检查 ===")

    url = f"{BASE_URL}/api/health"

    try:
        response = requests.get(url)
        print(f"状态码: {response.status_code}")
        health = response.json()
        print(f"状态: {health.get('status', 'unknown')}")
        print("✅ 健康检查通过")
        return True
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("设备显示路由测试")
    print(f"目标: {BASE_URL}")
    print("=" * 60)

    # 先检查服务
    if not test_health():
        print("\n❌ 服务未启动，请先运行后端")
        sys.exit(1)

    # 测试上传
    version = test_upload()
    if not version:
        print("\n❌ 上传失败，跳过后续测试")
        sys.exit(1)

    # 测试 manifest
    test_manifest()

    # 测试下载
    test_download(version)

    # 测试同步状态
    test_sync_status()

    # 测试无效上传
    test_invalid_upload()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
