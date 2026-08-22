#!/usr/bin/env python3
"""校验 manifest 里 film_url 的基址推导，覆盖各类反向代理注入的头。

为什么单独一个测试文件：film_url 推导错了是**静默失败** —— 后端一路 200，
设备却永远不下载（固件按 https 硬约束拒收明文 manifest），排查成本极高。
尤其是「只发 X-Scheme 不发 X-Forwarded-Proto」的仅提供 X-Scheme 的反向代理，
如果推导逻辑只认事实标准头，就会静默降级成 http。

用法：python tests/test_manifest_base_url.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

# 必须在导入 app 之前设好：绑定非回环地址时 token 是必填的
os.environ.setdefault('SFM_API_TOKEN', disposable_token('app-'))
os.environ.setdefault('SFM_HOST', '127.0.0.1')
os.environ['SFM_BASE_URL'] = ''          # 留空 → 走请求来源自适应
os.environ.pop('SFM_ENV_FILE', None)
os.environ['SFM_SKIP_ENV_FILE'] = '1'    # 别让本机 .env 干扰断言

# 本文件的价值之一是**能在部署后的容器里直接跑**（TLS 头推导正不正确，只有在真实入口
# 后面才说得清）。为此必须先把运行时状态隔到临时目录：导入 src.app 会执行建表，
# 而容器里 SFM_DATABASE_PATH 指向的是 /data/fridge.db —— 那是**生产库**。
# 一个自检脚本没有任何理由去碰它。
_tmp = tempfile.mkdtemp(prefix='sfm-baseurl-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')

# 同理关掉调度器：容器里 SFM_ENABLE_SCHEDULER=true，导入 app 就会在**测试进程里**
# 再起一份定时任务，而其中的餐前下发会向真实设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src.app import app, _request_base_url  # noqa: E402


def derive(headers: dict) -> str:
    """在一个伪造的请求上下文里跑推导逻辑。"""
    with app.test_request_context('/api/devices/dev001/display/manifest',
                                  headers=headers):
        return _request_base_url()


def check(label: str, headers: dict, expected: str) -> bool:
    actual = derive(headers)
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        期望 {expected!r}")
        print(f"        实际 {actual!r}")
    return ok


def main() -> int:
    print('manifest film_url 基址推导：')
    results = []

    # 事实标准：caddy / 多数云 LB
    results.append(check(
        'X-Forwarded-Proto + X-Forwarded-Host（caddy / 云 LB）',
        {'X-Forwarded-Proto': 'https',
         'X-Forwarded-Host': 'fridge.example.com',
         'Host': 'backend-internal:5000'},
        'https://fridge.example.com'))

    # 部分反向代理：只有 X-Scheme + Host，没有任何 X-Forwarded-* 头。
    # 这是本文件存在的主要原因。
    results.append(check(
        'X-Scheme + Host（仅提供 X-Scheme 的反向代理，无 X-Forwarded-*）',
        {'X-Scheme': 'https',
         'Host': 'fridge.example.com',
         'X-Real-IP': '192.0.2.20',
         'X-NginX-Proxy': 'true'},
        'https://fridge.example.com'))

    # X-Forwarded-Proto 优先于 X-Scheme（多层代理时外层更权威）
    results.append(check(
        '两个协议头同时存在 → X-Forwarded-Proto 优先',
        {'X-Forwarded-Proto': 'https',
         'X-Scheme': 'http',
         'Host': 'fridge.example.com'},
        'https://fridge.example.com'))

    # 多层代理：链式值取第一个（最初的外部值）
    results.append(check(
        '链式值取第一跳',
        {'X-Forwarded-Proto': 'https, http',
         'X-Forwarded-Host': 'fridge.example.com, inner.svc',
         'Host': 'inner.svc'},
        'https://fridge.example.com'))

    # 大小写归一：nginx 可能给 HTTPS
    results.append(check(
        '协议值大小写归一',
        {'X-Scheme': 'HTTPS', 'Host': 'fridge.example.com'},
        'https://fridge.example.com'))

    # 无任何代理头：直连，回退本跳 scheme（本地开发场景）
    results.append(check(
        '无代理头 → 回退本跳 scheme',
        {'Host': '127.0.0.1:5000'},
        'http://127.0.0.1:5000'))

    print()
    if all(results):
        print('manifest base url self-check passed')
        return 0
    print(f'FAILED: {results.count(False)}/{len(results)} 项不符合预期')
    return 1


if __name__ == '__main__':
    sys.exit(main())


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。

    为什么需要它：本模块是**脚本式**的 —— 编排在 `main()` 里，断言靠 `check()`
    累积。pytest 只收集 `test_` 开头的函数，而本模块里没有一个零参数的 `test_`，
    于是 pytest **一个用例都收集不到，整个模块被静默跳过**：测试报告是绿的，
    这些覆盖却从未运行过。这比失败更糟，因为它不留痕迹。

    这里不复述各条断言，只把 `main()` 的成败翻译成 pytest 的成败：
    断言逻辑只有一份，脚本直跑（`python tests/test_manifest_base_url.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
