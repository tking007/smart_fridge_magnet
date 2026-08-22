"""pytest 全局配置。

## 两种测试风格

`tests/` 下有两种写法，pytest 只认得第一种：

1. **零参数的 `test_xxx()`** —— pytest 直接收集执行。
2. **脚本式**：模块提供 `main()` 做编排，内部的 `check_xxx(client, results)` 是被 `main()`
   调用的辅助函数，靠 `results.append(check(...))` 累积结论，由 `main()` 的返回码表达成败。

第 2 种在 pytest 下有两种坏法，且**两种都不报错**：辅助函数若叫 `test_` 且带参数，pytest
会把参数名当 fixture 而 `ERROR at setup`；模块里一个零参数 `test_` 都没有时，pytest 收集到
0 个用例 → **静默跳过整个模块**，报告是绿的而覆盖从未运行。

约定因此是：每个脚本式模块补一个 `test_self_check()` 断言 `main() == 0`，辅助函数一律叫
`check_xxx`。

## 集成测试默认跳过而不是删掉

`test_api.py` / `test_e2e.py` / `test_device_display.py` 用 `requests` 打
`http://localhost:5000`，没有服务器时必然失败。它们覆盖真实 HTTP 栈、鉴权与并发，有价值，
但留在默认路径里会让 `pytest tests/` 永远是红的 —— 而一个永远红的套件等于没有套件。

所以默认跳过、需要时显式打开：

    SFM_RUN_INTEGRATION=1 pytest tests/          # 需先启动后端
    pytest tests/ --run-integration              # 等价写法

## 按模块恢复环境变量

几乎每个测试模块都在**模块导入期**给自己开一个 tmpdir 并写进 `SFM_DATABASE_PATH` /
`SFM_FILM_DIR`。单独跑一个文件没问题，但 pytest 是**先把所有模块 import 完再执行用例**，
而 `src/config.py` 的 `database_path()` / `film_dir()` 每次调用现读环境变量。于是执行阶段
所有模块看到的都是「最后一个被导入的模块」的 tmpdir：A 模块建的表，B 模块去另一个库里查，
表现为 `FileNotFoundError` 或断言莫名不成立，且**换一个文件名就可能好**（收集顺序按字母序）。

修法是在每个模块**导入完成的那一刻**给 `SFM_*` 拍快照，执行该模块的用例前恢复这份快照，
各模块互不干扰且不需要改动十几个测试文件里已有的写法。
"""
import os

import pytest

#: 需要真实 HTTP 服务器的模块。它们用 requests 打 BASE_URL，不是进程内 test_client。
INTEGRATION_MODULES = {
    'test_api',
    'test_e2e',
    'test_device_display',
}


def pytest_addoption(parser):
    parser.addoption(
        '--run-integration',
        action='store_true',
        default=False,
        help='同时运行需要真实后端服务器的集成测试（默认跳过）',
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'integration: 需要一个真实运行的后端服务器（默认跳过，用 --run-integration 打开）',
    )


def pytest_collection_modifyitems(config, items):
    """给集成模块自动打标并在未开启时跳过。

    自动打标而不是让每个文件自己写 `pytestmark`：这三个文件是脚本式的，
    顶部加 pytest 专属声明会让它们作为独立脚本运行时多一个 import 依赖。
    归属关系写在这里，三个文件保持"纯脚本 + 恰好能被 pytest 跑"的样子。
    """
    enabled = config.getoption('--run-integration') or os.environ.get('SFM_RUN_INTEGRATION')
    skip_marker = pytest.mark.skip(
        reason='需要真实后端服务器；先启动后端再用 --run-integration 或 SFM_RUN_INTEGRATION=1 运行'
    )
    for item in items:
        module_name = item.module.__name__.rsplit('.', 1)[-1] if item.module else ''
        if module_name in INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
            if not enabled:
                item.add_marker(skip_marker)


#: 模块名 -> 该模块导入完成时的 SFM_* 环境变量快照。
_MODULE_ENV_SNAPSHOT: dict = {}


def pytest_collectstart(collector):
    """在每个测试模块导入完成后，记下它期望的 SFM_* 环境。

    访问 `collector.obj` 会触发（并缓存）该模块的 import，模块顶层那段
    `os.environ['SFM_DATABASE_PATH'] = ...` 正是在这一刻执行的，所以此时的
    环境就是「这个模块想要的环境」。后导入的模块会覆盖同名变量，但那不影响
    本快照 —— 它已经存下来了。
    """
    if not isinstance(collector, pytest.Module):
        return
    try:
        module = collector.obj
    except Exception:      # noqa: BLE001 — 导入失败交给 pytest 自己报，别在这里吞掉
        return
    _MODULE_ENV_SNAPSHOT[module.__name__] = {
        key: value for key, value in os.environ.items() if key.startswith('SFM_')
    }


@pytest.fixture(autouse=True)
def _restore_module_env(request):
    """每个用例执行前，把 SFM_* 环境恢复成所属模块导入时的样子。

    只管 `SFM_*` 前缀：其余环境变量（PATH、语言等）不属于本项目的配置面，
    动它们只会带来意外。

    快照里没有、但当前存在的 SFM_* 变量要**删掉**而不是留着 —— 例如某个模块设了
    `SFM_BASE_URL='https://example.invalid'` 而另一个模块刻意要它为未设置状态，
    只做覆盖而不做删除的话，后者永远拿不到自己想要的「未配置」语义。
    """
    snapshot = _MODULE_ENV_SNAPSHOT.get(request.module.__name__)
    if snapshot is None:
        yield
        return

    for key in [k for k in os.environ if k.startswith('SFM_')]:
        if key not in snapshot:
            del os.environ[key]
    os.environ.update(snapshot)
    _ensure_schema()
    yield


def _ensure_schema() -> None:
    """确保当前 `SFM_DATABASE_PATH` 指向的库里建好了全部表。

    三处建表都是**模块级副作用**，只在各自 import 时执行一次：`database.init_db()`
    （由 `app.py` 顶层调用）、`device_display.init_device_tables()`、
    `settings_store.init_settings_table()`。

    进程里这三次只发生在**第一个**导入它们的测试模块身上，建的是那个模块 tmpdir 里的库；
    此后每个模块把 `SFM_DATABASE_PATH` 指回自己的 tmpdir，那个库却是空的，于是
    `sqlite3.OperationalError: no such table`。所以这里按模块补一次。

    三个函数都是 `CREATE TABLE IF NOT EXISTS` + 幂等迁移，重复调用无副作用。
    """
    from src import database, device_display, settings_store

    database.init_db()
    device_display.init_device_tables()
    settings_store.init_settings_table()
