"""Small self-check for the imported backend's runtime security defaults."""
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

APP_TOKEN = disposable_token('app-')
FIRST_DEVICE_TOKEN = disposable_token('device-')
SECOND_DEVICE_TOKEN = disposable_token('device-')
REJECTED_DEVICE_TOKEN = disposable_token('rejected-')


def check_auth(app):
    """业务接口必须校验 app_token。"""
    client = app.test_client()
    assert client.get('/api/inventory').status_code == 401
    response = client.get(
        '/api/inventory',
        headers={'Authorization': f'Bearer {APP_TOKEN}'}
    )
    assert response.status_code == 200


def check_public_placeholder_token_is_rejected(config):
    """仓库公开的示例值不能满足非回环启动校验。"""
    saved = os.environ.get('SFM_API_TOKEN')
    try:
        os.environ['SFM_API_TOKEN'] = 'replace-with-random-demo-token'
        try:
            config.validate_runtime('0.0.0.0', False)
        except RuntimeError as exc:
            assert 'SFM_API_TOKEN' in str(exc)
        else:
            raise AssertionError('公开占位 token 不得用于非回环监听')
    finally:
        if saved is None:
            os.environ.pop('SFM_API_TOKEN', None)
        else:
            os.environ['SFM_API_TOKEN'] = saved


def check_device_auth_and_refresh(app_module, device_display):
    """未知设备 fail-closed；已鉴权刷新只命中自身且全局单飞。"""
    client = app_module.app.test_client()

    assert client.get('/api/devices/unknown/display/manifest').status_code == 401
    assert client.post('/api/devices/unknown/display/request-refresh').status_code == 401

    first = 'refresh-auth-1'
    second = 'refresh-auth-2'
    assert device_display.set_device_token(first, FIRST_DEVICE_TOKEN)['success']
    assert device_display.set_device_token(second, SECOND_DEVICE_TOKEN)['success']
    assert client.get(f'/api/devices/{first}/display/manifest').status_code == 401
    assert client.get(
        f'/api/devices/{first}/display/manifest',
        headers={'Authorization': f'Bearer {REJECTED_DEVICE_TOKEN}'},
    ).status_code == 401

    started = threading.Event()
    release = threading.Event()
    called = []
    original = app_module.trigger_display_publish

    def fake_publish(meal_type=None, force=False, device_id=None):
        called.append(device_id)
        started.set()
        release.wait(timeout=2)
        return {'success': True, 'devices': [device_id]}

    app_module.trigger_display_publish = fake_publish
    try:
        accepted = client.post(
            f'/api/devices/{first}/display/request-refresh',
            headers={'Authorization': f'Bearer {FIRST_DEVICE_TOKEN}'},
        )
        assert accepted.status_code == 202, accepted.data
        assert started.wait(timeout=1), '后台发布任务未启动'

        busy = client.post(
            f'/api/devices/{second}/display/request-refresh',
            headers={'Authorization': f'Bearer {SECOND_DEVICE_TOKEN}'},
        )
        assert busy.status_code == 429, busy.data
        assert called == [first], f'设备刷新越权命中了其它目标: {called}'
    finally:
        release.set()
        app_module.trigger_display_publish = original


def check_inventory_batch_is_atomic(database):
    """数据库层出现单项失败时，前面的成功 INSERT 也必须回滚。"""
    before = database.get_inventory(page=1, page_size=200)['total']
    result = database.batch_add_inventory_items([
        {'name': '原子测试苹果', 'category': '水果', 'quantity': 1},
        {'name': '原子测试坏项', 'category': '不存在的分类', 'quantity': 1},
    ])
    after = database.get_inventory(page=1, page_size=200)['total']
    assert result['failed'] and result['success'] == []
    assert after == before, f'批量失败后库存仍从 {before} 变成 {after}'


def check_base_url_fail_closed(config):
    """显式配置的 SFM_BASE_URL 若不是 https，绑定非回环地址必须拒绝启动。

    固件强制要求 manifest 的 film_url 是 HTTPS。配 http 时服务能起、上传也成功，
    只有真机拉取会失败，且失败只在设备串口可见 —— 必须在启动阶段就挡住，
    否则要花一次真机联调才能发现。

    但「留空」是受支持的部署方式（film_url 改由设备请求来源自适应推导），
    不能连它一起拒绝：云上容器 IP 随机分配、入口地址可能变动，留空往往比写死更可靠。
    回环地址视同留空 —— 设备永远不可能通过 127.0.0.1 访问后端。
    """
    saved = {
        key: os.environ.get(key)
        for key in ('SFM_BASE_URL', 'SFM_ALLOW_INSECURE_BASE_URL')
    }
    try:
        os.environ.pop('SFM_ALLOW_INSECURE_BASE_URL', None)

        # 1) 留空：允许启动（走自适应推导）
        os.environ.pop('SFM_BASE_URL', None)
        config.validate_runtime('0.0.0.0', False)
        assert config.configured_base_url() == ''

        # 2) 回环值视同留空，同样允许 —— 它对 film_url 而言必然是错的，
        #    当作有效配置只会让设备拿到一个死地址
        os.environ['SFM_BASE_URL'] = 'http://127.0.0.1:5000'
        assert config.configured_base_url() == ''
        config.validate_runtime('0.0.0.0', False)
        config.validate_runtime('127.0.0.1', False)

        # 3) 显式非回环 http + 未豁免：拒绝启动
        os.environ['SFM_BASE_URL'] = 'http://sfm.example.invalid'
        assert config.configured_base_url() == 'http://sfm.example.invalid'
        try:
            config.validate_runtime('0.0.0.0', False)
        except RuntimeError as exc:
            assert 'SFM_BASE_URL' in str(exc)
        else:
            raise AssertionError('非回环绑定 + 显式 http base_url 本应拒绝启动')

        # 4) 显式 http + 豁免：放行（部署过渡期用）
        os.environ['SFM_ALLOW_INSECURE_BASE_URL'] = 'true'
        config.validate_runtime('0.0.0.0', False)

        # 5) 显式 https：放行
        os.environ.pop('SFM_ALLOW_INSECURE_BASE_URL', None)
        os.environ['SFM_BASE_URL'] = 'https://sfm.example.invalid:8443'
        config.validate_runtime('0.0.0.0', False)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_film_url_adapts_to_request(device_display):
    """film_url 的基址选择：显式配置优先，未配置时按设备请求来源自适应。

    回归的是「配置写死一个设备到不了的地址」这类错误 —— 容器 IP 由编排系统随机分配、
    云平台入口也可能变动，写死往往是错的，而症状（设备拉到 manifest 却下载失败）
    只在设备串口可见，后端日志上完全正常。
    """
    saved = os.environ.get('SFM_BASE_URL')
    try:
        device_id = 'baseurl-selfcheck'
        assert device_display.upload_film(device_id, _make_valid_film())['success']

        # 未配置 -> 用请求来源
        os.environ.pop('SFM_BASE_URL', None)
        manifest = device_display.get_manifest(
            device_id, request_base_url='https://from-request.example:8443')
        assert manifest['film_url'].startswith('https://from-request.example:8443/'), \
            manifest['film_url']

        # 显式配置 -> 覆盖请求来源（CDN / 专用下载域名场景）
        os.environ['SFM_BASE_URL'] = 'https://configured.example'
        manifest = device_display.get_manifest(
            device_id, request_base_url='https://from-request.example:8443')
        assert manifest['film_url'].startswith('https://configured.example/'), \
            manifest['film_url']

        # 回环配置视同未配置 -> 仍用请求来源
        os.environ['SFM_BASE_URL'] = 'http://127.0.0.1:5000'
        manifest = device_display.get_manifest(
            device_id, request_base_url='https://from-request.example:8443')
        assert manifest['film_url'].startswith('https://from-request.example:8443/'), \
            manifest['film_url']
    finally:
        if saved is None:
            os.environ.pop('SFM_BASE_URL', None)
        else:
            os.environ['SFM_BASE_URL'] = saved


def check_film_dir(config):
    """.film 目录必须可经 SFM_FILM_DIR 覆盖，且默认跟随数据库所在目录。

    默认值跟随 database_path() 是关键：容器只把 /data 声明为 VOLUME，
    若 .film 落在别处，容器重建后库里有版本记录、文件却全丢。
    """
    saved = os.environ.get('SFM_FILM_DIR')
    try:
        os.environ.pop('SFM_FILM_DIR', None)
        expected_parent = os.path.dirname(os.path.abspath(config.database_path()))
        assert config.film_dir() == os.path.join(expected_parent, 'devices')

        os.environ['SFM_FILM_DIR'] = '/data/devices'
        assert config.film_dir() == '/data/devices'
    finally:
        if saved is None:
            os.environ.pop('SFM_FILM_DIR', None)
        else:
            os.environ['SFM_FILM_DIR'] = saved


def _make_valid_film() -> bytes:
    """构造一份结构合法的 .film（792x528 六色，全为调色板索引 1）。

    头部字段与 colorTable 必须与固件 film_validate.c 的期望完全一致，否则
    upload_film() 的校验会先把它拒掉，测不到我们真正想测的东西。
    """
    header = bytearray(32)
    header[0:4] = (209088).to_bytes(4, 'little')   # 像素长度（不是文件总长）
    header[4:6] = (792).to_bytes(2, 'little')
    header[6:8] = (528).to_bytes(2, 'little')
    header[8] = 6
    header[16:22] = bytes([0x00, 0xFF, 0xFC, 0xE0, 0x03, 0x1C])  # 真实设备色值
    return bytes(header) + bytes([0x11]) * 209088   # 半字节均为索引 1，合法


def check_force_requires_apply_ack(device_display):
    """force 必须持续到设备确认成功应用，不能在发送 manifest 时消费。

    钉住两件事：
      1. get_manifest() 真的读 devices.force_next，`/display/force` 真的置位 —— 设备文件被
         BLE 0x45 清空后版本门控会判「已是最新」拒绝下载，force 是唯一不递增版本号就能
         解围的手段。
      2. manifest 已收到不等于文件已应用；只有设备回报最新版本后才能清除 force。
    """
    device_id = 'force-selfcheck'
    assert device_display.upload_film(device_id, _make_valid_film())['success']

    assert device_display.get_manifest(device_id)['force'] is False

    assert device_display.set_force_next(device_id)['success'] is True
    assert device_display.get_manifest(device_id)['force'] is True, 'force 未进入 manifest'
    assert device_display.get_manifest(device_id)['force'] is True, 'force 被 manifest 过早消费'
    assert not device_display.acknowledge_applied_version(device_id, 0), '旧版本不应清除 force'
    assert device_display.acknowledge_applied_version(device_id, 1), '最新版本 ACK 未清除 force'
    assert device_display.get_manifest(device_id)['force'] is False, 'ACK 后 force 仍未清除'

    assert device_display.set_force_next(device_id)['success'] is True
    assert device_display.upload_film(device_id, _make_valid_film())['version'] == 2
    assert not device_display.acknowledge_applied_version(device_id, 1), '旧 ACK 清除了新版本 force'
    assert device_display.get_manifest(device_id)['force'] is True, '新版本 force 被旧 ACK 消费'
    assert device_display.acknowledge_applied_version(device_id, 2)

    # 未注册设备不应静默成功：否则 device_id 写错时接口仍回 success，
    # 真机排查时几乎无从发现。
    assert device_display.set_force_next('never-registered')['success'] is False


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ['SFM_DATABASE_PATH'] = str(Path(temp_dir) / 'test.db')
        os.environ['SFM_LOG_DIR'] = str(Path(temp_dir) / 'logs')
        os.environ['SFM_FILM_DIR'] = str(Path(temp_dir) / 'devices')
        os.environ['SFM_API_TOKEN'] = APP_TOKEN
        # 容器里 SFM_ENABLE_SCHEDULER=true，导入 app 会在**测试进程里**再起一份定时任务，
        # 其中的餐前下发会真的向设备发布新版本。自检不该有这种副作用。
        os.environ['SFM_ENABLE_SCHEDULER'] = '0'
        os.environ['SFM_AUTO_PUBLISH'] = '0'

        from src import app as app_module
        from src import database
        from src import config
        from src import device_display

        # 指到新库之后必须显式建表：`database.py` 现读配置，临时目录隔离是真的，
        # 不建表则第一个碰 devices 表的请求会 500（而不是被断言期望的 401）。
        database.init_db()
        device_display.init_device_tables()

        check_auth(app_module.app)
        check_public_placeholder_token_is_rejected(config)
        check_device_auth_and_refresh(app_module, device_display)
        check_base_url_fail_closed(config)
        check_film_dir(config)
        check_force_requires_apply_ack(device_display)
        check_film_url_adapts_to_request(device_display)
        check_inventory_batch_is_atomic(database)


if __name__ == '__main__':
    main()
    print('runtime config self-check passed')


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。

    为什么需要它：本模块是**脚本式**的 —— 编排在 `main()` 里，断言靠 `check()`
    累积。pytest 只收集 `test_` 开头的函数，而本模块里没有一个零参数的 `test_`，
    于是 pytest **一个用例都收集不到，整个模块被静默跳过**：测试报告是绿的，
    这些覆盖却从未运行过。这比失败更糟，因为它不留痕迹。

    这里不复述各条断言，只把 `main()` 的成败翻译成 pytest 的成败：
    断言逻辑只有一份，脚本直跑（`python tests/test_runtime_config.py`）与 pytest 跑的是同一套。
    """
    # 这一族的 main() 不返回值，失败时直接 raise SystemExit ——
    # 让它自然抛出即可，pytest 会如实记成失败并带上原始信息。
    main()
