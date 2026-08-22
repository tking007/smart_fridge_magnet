#!/usr/bin/env python3
"""校验慢请求能在日志里交代「时间花在哪」。

access 行的总耗时回答「慢不慢」，分段行回答「慢在哪」：一次 60 秒的请求可能花在模型上
（该调 prompt / 换模型 / 加进度提示），也可能花在读库或渲染上（完全不同的修法），
只看总耗时分辨不出，而线上环境未必能本地复现。

这一套钉住三件事：
  1. 正常路径要输出分段耗时，且 `llm` 必须单独成段（它是大头，也是唯一能优化的那段）。
  2. **失败路径同样要输出** —— 一次 60 秒后失败的请求，正是最需要知道时间花在哪的那次。
  3. `done()` 幂等：它同时出现在正常返回前和 finally 里，重复输出会让日志里出现
     两条互相矛盾的耗时行。

用法：python tests/test_stage_timing.py
"""
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-timing-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src.logger import StageTimer, get_logger      # noqa: E402

_results = []


def check(desc, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}" + (f"\n        {detail}" if not ok else ''))
    _results.append(bool(ok))
    return bool(ok)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _timer_with_capture(action, **ctx):
    log = get_logger('timing-test')
    handler = _Capture()
    log.addHandler(handler)
    return StageTimer(log, action, **ctx), handler, log


def check_正常路径给出每段耗时():
    print('正常路径：')
    timer, cap, log = _timer_with_capture('生成菜谱', meal='dinner')
    try:
        with timer.stage('db'):
            time.sleep(0.01)
        with timer.stage('llm'):
            time.sleep(0.03)
        timer.done(plan_id=7)
    finally:
        log.removeHandler(cap)

    line = cap.lines[-1] if cap.lines else ''
    check('输出了一行汇总', len(cap.lines) == 1, f'实际 {cap.lines}')
    check('带总耗时', '总计=' in line, line)
    # llm 必须单独成段：它是耗时大头，也是唯一真正能优化的那段。
    # 只给总耗时等于回到「63 秒不知道花在哪」的原点。
    check('llm 单独成段', 'llm=' in line, line)
    check('db 单独成段', 'db=' in line, line)
    check('带上业务上下文（meal / plan_id）',
          'meal=dinner' in line and 'plan_id=7' in line, line)
    # 段耗时必须与真实等待成比例，否则这行数字是假的、比没有更糟。
    llm_ms = int(line.split('llm=')[1].split('ms')[0])
    check('llm 段耗时接近真实等待（>=25ms）', llm_ms >= 25, f'实际 {llm_ms}ms')


def check_失败路径同样留下耗时():
    print('失败路径（最需要耗时的那次）：')
    log = get_logger('timing-test')
    cap = _Capture()
    log.addHandler(cap)
    try:
        with StageTimer(log, '改菜单', meal='lunch') as timer:
            with timer.stage('llm'):
                time.sleep(0.01)
            raise RuntimeError('模型超时')
    except RuntimeError:
        pass
    finally:
        log.removeHandler(cap)

    line = cap.lines[-1] if cap.lines else ''
    check('异常路径也输出了汇总行', len(cap.lines) == 1, f'实际 {cap.lines}')
    check('标注为失败', '失败' in line, line)
    check('失败时仍带分段耗时', 'llm=' in line, line)


def check_done_幂等():
    print('done() 幂等：')
    log = get_logger('timing-test')
    cap = _Capture()
    log.addHandler(cap)
    try:
        timer = StageTimer(log, '生成菜谱')
        with timer.stage('db'):
            pass
        timer.done(plan_id=1)
        timer.done(plan_id=2)      # 模拟 finally 里的兜底调用
        timer.done()
    finally:
        log.removeHandler(cap)

    # 不幂等的话，正常返回前那次与 finally 那次会各打一行，
    # 日志里出现两条耗时不同的「完成」，读的人无从判断哪条是真的。
    check('重复调用只输出一行', len(cap.lines) == 1, f'实际 {cap.lines}')
    check('保留的是第一次的上下文', 'plan_id=1' in cap.lines[0], cap.lines[0])


def check_两条慢路径都已接入():
    """generate_recommendation 与 revise_plan 必须真的用上 StageTimer。

    只测 StageTimer 本身不够：它可以完美工作却一处都没被调用，
    而那正是「日志还是只有总耗时」的现状。

    ── 为什么读源文件而不是 inspect.getsource(agent.xxx) ──
    别的测试模块会把 `agent.generate_recommendation` 替换成桩函数（模型不能真调），
    而模块属性是**进程内共享**的：整套跑时这里拿到的可能是那个桩，
    于是断言看的是桩的源码，必然找不到 StageTimer —— 一个纯粹由测试间干扰造成的假失败。
    直接按 AST 从磁盘上的源文件取，结果与执行顺序无关。
    """
    print('慢路径接入情况：')
    import ast

    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'agent.py'
    )
    source = Path(src_path).read_text(encoding='utf-8')
    tree = ast.parse(source)
    bodies = {
        node.name: ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for fn_name in ('generate_recommendation', 'revise_plan'):
        body = bodies.get(fn_name) or ''
        check(f'{fn_name} 在源码里存在', bool(body))
        check(f'{fn_name} 创建了 StageTimer', 'StageTimer(' in body)
        check(f'{fn_name} 单独计量了 llm 段', "stage('llm')" in body)
        check(f'{fn_name} 会输出汇总（调用了 done）', '.done(' in body)


def main() -> int:
    print('=' * 60)
    print('慢请求分段计时测试')
    print('=' * 60)
    check_正常路径给出每段耗时()
    check_失败路径同样留下耗时()
    check_done_幂等()
    check_两条慢路径都已接入()

    print()
    if all(_results):
        print(f'stage timing self-check passed（{len(_results)} 项）')
        return 0
    print(f'FAILED: {_results.count(False)}/{len(_results)} 项不符合预期')
    return 1


if __name__ == '__main__':
    sys.exit(main())


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。"""
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
