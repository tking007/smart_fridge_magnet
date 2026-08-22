#!/usr/bin/env python3
"""校验流式（SSE）菜谱与对话链路。

盯住的行为：
  1. `stream_ai_text` 能把 Chat Completions 协议的 SSE 分块拆成 reasoning / content / done 事件，
     且顺序与上游一致 —— 顺序错了，前端把思考渲染进正文，用户看到的是一堆内部推理。
  2. 上游失败（HTTP 5xx、超时、只有推理没正文）只产出一个 error 事件，不产出 done。
     调用方据此决定显示什么，多产出一个 done 会让失败被当成成功落库。
  3. 坏分块（不是合法 JSON）被跳过而不是终止整段生成。
  4. 流式菜谱**不做约束重试**：越界时剔菜落库，而不是让用户眼前的内容被另一份替换。
  5. SSE 端点响应头带 `X-Accel-Buffering: no`。缺了它部分反向代理会缓冲，
     流式端点退化成更慢的非流式端点，而两端都不报错。
  6. 客户端中途断开不产生异常噪音。

用法：backend/.venv/bin/python -m pytest tests/test_streaming.py -v
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

_tmp = tempfile.mkdtemp(prefix='sfm-stream-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

import pytest                               # noqa: E402

from src import agent                       # noqa: E402
from src import database as db               # noqa: E402
from src import ai_client                 # noqa: E402

db.init_db()


# ==================== 假上游 ====================

class _FakeResponse:
    """够用的 requests.Response 替身。

    刻意只实现 `iter_content`（字节）而**不实现** `iter_lines`：被测代码必须在字节层
    切行再按 UTF-8 解码，用了 iter_lines 就会在这里 AttributeError。
    """

    def __init__(self, chunks, status_code=200, text=''):
        self._chunks = chunks
        self.status_code = status_code
        self.text = text
        self.headers = {}

    def iter_content(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk if isinstance(chunk, bytes) else chunk.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _chunk(finish_reason=None, **delta):
    """构造一个 Chat Completions 协议的流式分块行（含结尾换行）。"""
    choice = {'delta': delta}
    if finish_reason is not None:
        choice['finish_reason'] = finish_reason
    return 'data: ' + json.dumps({'choices': [choice]}, ensure_ascii=False) + '\n'


def _stub_stream(monkeypatch, chunks, status_code=200, text='', exc=None):
    """把 requests.post 换成产出给定字节块的假上游。"""
    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))

    import requests

    def fake_post(url, **kwargs):
        assert kwargs['json']['stream'] is True, '流式调用必须带 stream=True'
        if exc is not None:
            raise exc
        return _FakeResponse(chunks, status_code=status_code, text=text)

    monkeypatch.setattr(requests, 'post', fake_post)


# ==================== stream_ai_text ====================

def test_stream_splits_reasoning_and_content_in_order(monkeypatch):
    _stub_stream(monkeypatch, [
        _chunk(reasoning_content='先看库存'),
        _chunk(reasoning_content='有土豆'),
        _chunk(content='{"plan_a"'),
        '\n',                                # 空行是 SSE 的分隔，必须被跳过
        _chunk(content=': {}}'),
        'data: [DONE]\n',
    ])

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    kinds = [e['type'] for e in events]
    assert kinds == ['reasoning', 'reasoning', 'content', 'content', 'done'], kinds
    assert [e['delta'] for e in events[:2]] == ['先看库存', '有土豆']
    assert events[-1]['text'] == '{"plan_a": {}}', '全文必须是各 content 块的顺序拼接'
    print('  ✓ reasoning/content 分流且顺序保持')


def test_stream_survives_multibyte_split_across_tcp_chunks(monkeypatch):
    """多字节字符与整行被 TCP 分块切断时，内容不得丢失或变成 mojibake。

    这是真机上出现过的故障：`iter_lines(decode_unicode=True)` 按响应声明的编码
    （SSE 无 charset → ISO-8859-1）逐块解码，中文成 mojibake，跨块的行被切断；
    切断的半行不是合法 JSON 而被丢弃，于是 `{"name": "生抽", "amount": "1勺"}`
    到达时成了 `{"name": "amount": "1勺"}`，报错却指向「模型返回格式不对」，
    看不出问题在读取层。
    """
    payload = json.dumps(
        {'plan_a': {'dishes': [{'name': '土豆丝', 'ingredients': [
            {'name': '生抽', 'amount': '1勺'}, {'name': '盐', 'amount': '适量'},
        ]}]}},
        ensure_ascii=False,
    )
    line = ('data: ' + json.dumps({'choices': [{'delta': {'content': payload}}]},
                                  ensure_ascii=False) + '\n')
    blob = line.encode('utf-8') + b'data: [DONE]\n'

    # 每 7 字节切一刀：必然切在某个中文字符的 UTF-8 三字节序列中间。
    _stub_stream(monkeypatch, [blob[i:i + 7] for i in range(0, len(blob), 7)])

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert events[-1]['type'] == 'done', f'不应因分块切断而失败：{events[-1]}'
    assert events[-1]['text'] == payload, '正文必须与上游逐字一致'

    parsed, error = ai_client.parse_json_payload(events[-1]['text'])
    assert error is None, f'拼出来的正文必须仍是合法 JSON：{error}'
    assert parsed['plan_a']['dishes'][0]['ingredients'][0]['name'] == '生抽'
    print('  ✓ 多字节字符跨 TCP 分块不丢字、不 mojibake')


def test_stream_skips_malformed_chunks(monkeypatch):
    """单个坏块不该终止整段生成 —— 能否成菜由全文决定。"""
    _stub_stream(monkeypatch, [
        _chunk(content='好的'),
        'data: {这不是合法 JSON\n',
        'data: \n',
        _chunk(content='继续'),
        'data: [DONE]\n',
    ])

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert events[-1]['type'] == 'done'
    assert events[-1]['text'] == '好的继续'
    print('  ✓ 坏分块被跳过，生成继续')


def test_stream_upstream_http_error_yields_only_error(monkeypatch):
    _stub_stream(monkeypatch, [], status_code=502, text='bad gateway')

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert [e['type'] for e in events] == ['error']
    assert '502' in events[0]['message']
    print('  ✓ 上游 5xx 只产出 error')


def test_stream_timeout_yields_only_error(monkeypatch):
    import requests
    _stub_stream(monkeypatch, [], exc=requests.Timeout())

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert [e['type'] for e in events] == ['error']
    assert '超时' in events[0]['message']
    print('  ✓ 超时只产出 error')


def test_stream_reasoning_only_is_an_error(monkeypatch):
    """推理模型把预算全花在思考上时没有正文 —— 对调用方等于没有答案。"""
    _stub_stream(monkeypatch, [
        _chunk(reasoning_content='想了很久'),
        'data: [DONE]\n',
    ])

    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert [e['type'] for e in events] == ['reasoning', 'error']
    assert '只产出了推理内容' in events[1]['message']
    print('  ✓ 只有思考没有正文被判为失败')


def test_stream_length_finish_with_partial_json_is_an_error(monkeypatch):
    """上游耗尽输出预算时，已有正文也只是截断片段，不能产出 done。"""
    _stub_stream(monkeypatch, [
        _chunk(content='{"plan_a":{"dishes":['),
        _chunk(finish_reason='length'),
        'data: [DONE]\n',
    ])

    events = list(ai_client.stream_ai_text('prompt', purpose='recipe_stream'))

    assert [event['type'] for event in events] == ['content', 'error']
    assert '达到长度上限' in events[-1]['message']
    assert not any(event['type'] == 'done' for event in events)


def test_stream_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.setattr(ai_client, 'AI_API_KEY', '')
    events = list(ai_client.stream_ai_text('prompt', purpose='test'))
    assert [e['type'] for e in events] == ['error']
    assert 'AI_API_KEY' in events[0]['message']

    print('  ✓ key 缺失时不发请求，且与非流式同一文案')


# ==================== stream_recommendation ====================

def _stub_recipe_context(monkeypatch, inventory=('土豆',), allergens=()):
    runtime = agent.stream_recommendation.__globals__
    runtime_db = runtime['db']
    monkeypatch.setattr(runtime_db, 'get_inventory_summary',
                        lambda: '- ' + '、'.join(inventory))
    monkeypatch.setattr(runtime_db, 'get_profile_summary', lambda: '无家庭画像')
    monkeypatch.setattr(runtime_db, 'get_expiring_items', lambda **kw: [])
    monkeypatch.setattr(runtime_db, 'get_inventory', lambda **kw: {
        'items': [{'name': n} for n in inventory], 'total': len(inventory),
    })
    monkeypatch.setattr(runtime_db, 'collect_allergens', lambda: list(allergens))
    monkeypatch.setitem(runtime, 'get_nutrition_for_inventory', lambda: None)
    persisted = []
    monkeypatch.setattr(runtime_db, 'add_meal_plan',
                        lambda **kw: persisted.append(kw) or 7)
    return persisted


def _stub_model_stream(monkeypatch, plans_json, reasoning='思考中'):
    def fake_stream(*args, **kwargs):
        yield {'type': 'reasoning', 'delta': reasoning}
        yield {'type': 'content', 'delta': plans_json}
        yield {'type': 'done', 'text': plans_json}

    monkeypatch.setitem(agent.stream_recommendation.__globals__,
                        'stream_ai_text', fake_stream)


def test_stream_recommendation_emits_stages_then_result(monkeypatch):
    persisted = _stub_recipe_context(monkeypatch)
    _stub_model_stream(monkeypatch, json.dumps({'plan_a': {'dishes': [
        {'name': '土豆丝', 'ingredients': [{'name': '土豆'}, {'name': '盐'}]},
    ]}}, ensure_ascii=False))

    events = list(agent.stream_recommendation('晚餐', 2))
    kinds = [e['type'] for e in events]

    assert kinds[0] == 'stage', '第一个事件应是阶段标注，让前端立刻有东西可显示'
    assert 'reasoning' in kinds and 'content' in kinds
    assert kinds[-1] == 'result'
    assert events[-1]['action'] == 'recommend'
    assert events[-1]['data']['plan_id'] == 7
    assert len(persisted) == 1, '流式路径也必须落库'
    print('  ✓ 阶段 → 思考 → 正文 → 结果')


def test_stream_recommendation_degrades_without_retry(monkeypatch):
    """越界时剔菜落库，且**不重调模型** —— 重试会让已显示的内容被另一份替换。"""
    persisted = _stub_recipe_context(monkeypatch)
    calls = []

    def counting_stream(*args, **kwargs):
        calls.append(1)
        payload = json.dumps({'plan_a': {'dishes': [
            {'name': '土豆丝', 'ingredients': [{'name': '土豆'}]},
            {'name': '香菇汤', 'ingredients': [{'name': '香菇'}]},
        ]}}, ensure_ascii=False)
        yield {'type': 'content', 'delta': payload}
        yield {'type': 'done', 'text': payload}

    monkeypatch.setitem(agent.stream_recommendation.__globals__,
                        'stream_ai_text', counting_stream)

    result = list(agent.stream_recommendation('晚餐', 2))[-1]

    assert len(calls) == 1, '流式路径不得重调模型'
    assert result['action'] == 'recommend'
    assert result['data']['dropped_dishes'] == ['香菇汤']
    assert agent.dish_names_of(persisted[0]['plan_a']) == ['土豆丝']
    assert '香菇汤' in result['reply']
    print('  ✓ 剔菜落库，不重试')


def test_stream_recommendation_blocks_cross_meal_core_ingredient(monkeypatch):
    """早餐吃过鸡蛋后，午餐流式结果里的鸡蛋菜不得落库。"""
    persisted = _stub_recipe_context(monkeypatch, inventory=('鸡蛋', '番茄'))
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(db, 'get_recent_meal_plans', lambda: [{
        'meal_type': 'breakfast',
        'plan_a': {'dishes': [{
            'name': '水煮蛋', 'ingredients': [{'name': '鸡蛋'}, {'name': '盐'}],
        }]},
        'plan_b': None, 'selected_plan': None, 'created_at': now,
    }])
    _stub_model_stream(monkeypatch, json.dumps({'plan_a': {'dishes': [{
        'name': '番茄炒蛋',
        'ingredients': [{'name': '番茄'}, {'name': '鸡蛋'}],
    }]}}, ensure_ascii=False))

    result = list(agent.stream_recommendation('午餐', 2))[-1]

    assert result['action'] == 'recommend_constraints_blocked'
    assert '当天其它餐次' in result['reply']
    assert persisted == []


def test_stream_recommendation_blocks_on_allergen(monkeypatch):
    """过敏源是硬约束，不能靠剔菜挽救。"""
    persisted = _stub_recipe_context(monkeypatch, inventory=('花生',), allergens=('花生',))
    _stub_model_stream(monkeypatch, json.dumps({'plan_a': {'dishes': [
        {'name': '花生米', 'ingredients': [{'name': '花生'}]},
    ]}}, ensure_ascii=False))

    result = list(agent.stream_recommendation('晚餐', 2))[-1]
    assert result['action'] == 'recommend_allergen_blocked'
    assert persisted == []
    print('  ✓ 命中过敏源不落库')


def test_stream_recommendation_reports_model_error(monkeypatch):
    _stub_recipe_context(monkeypatch)

    def failing(*args, **kwargs):
        yield {'type': 'error', 'message': '网关返回 HTTP 502'}

    monkeypatch.setitem(agent.stream_recommendation.__globals__,
                        'stream_ai_text', failing)

    events = list(agent.stream_recommendation('晚餐', 2))
    assert events[-1]['type'] == 'error'
    assert not any(e['type'] == 'result' for e in events), \
        '模型失败时不得产出 result —— 那会让失败被当成一次成功的生成'
    print('  ✓ 模型失败只产出 error')


def test_stream_recommendation_empty_inventory(monkeypatch):
    runtime = agent.stream_recommendation.__globals__
    monkeypatch.setattr(runtime['db'], 'get_inventory_summary', lambda: '冰箱为空')

    result = list(agent.stream_recommendation('晚餐', 2))[-1]
    assert result['action'] == 'inventory_empty'
    print('  ✓ 空库存直接给可操作提示')


# ==================== SSE 端点 ====================

@pytest.fixture
def client():
    from src.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _sse_events(raw: bytes):
    """把 SSE 响应体解析回事件列表。"""
    events = []
    for block in raw.decode('utf-8').split('\n\n'):
        line = block.strip()
        if line.startswith('data:'):
            events.append(json.loads(line[5:].strip()))
    return events


def test_sse_headers_disable_proxy_buffering(client, monkeypatch):
    """缺 X-Accel-Buffering 会让部分反向代理缓冲整段响应，流式静默退化。"""
    _stub_recipe_context(monkeypatch)
    _stub_model_stream(monkeypatch, json.dumps({'plan_a': {'dishes': [
        {'name': '土豆丝', 'ingredients': [{'name': '土豆'}]},
    ]}}, ensure_ascii=False))

    resp = client.post('/api/recommend/stream', json={'meal_type': '晚餐', 'people_count': 2})
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('text/event-stream')
    assert resp.headers['X-Accel-Buffering'] == 'no'
    assert resp.headers['Cache-Control'] == 'no-cache'

    events = _sse_events(resp.data)
    assert events[-1]['type'] == 'result'
    assert events[-1]['action'] == 'recommend'
    print('  ✓ SSE 头正确且事件可解析')


def test_sse_rejects_unknown_meal(client):
    resp = client.post('/api/recommend/stream', json={'meal_type': '夜宵宵'})
    assert resp.status_code == 400
    assert resp.get_json()['code'] == 1001
    print('  ✓ 餐次非法时快速失败而不是开一条流')


def test_chat_stream_requires_message(client):
    resp = client.post('/api/chat/stream', json={})
    assert resp.status_code == 400
    print('  ✓ 空 message 快速失败')


def test_chat_stream_non_model_intent_returns_result(client, monkeypatch):
    """纯数据库意图直接给 result，不必也不该走模型。"""
    monkeypatch.setitem(agent.stream_chat.__globals__, 'detect_intent', lambda _: 'help')

    resp = client.post('/api/chat/stream', json={'message': '帮助'})
    events = _sse_events(resp.data)
    assert [e['type'] for e in events] == ['result']
    assert events[0]['action'] == 'help'
    print('  ✓ 非模型意图直接产出 result')


def test_sse_survives_generator_exception(client, monkeypatch):
    """生成器抛异常时响应头已发出，不能变成 5xx —— 必须转成 error 事件。"""
    def exploding(*args, **kwargs):
        yield {'type': 'stage', 'stage': '开始'}
        raise RuntimeError('boom')

    monkeypatch.setitem(agent.stream_chat.__globals__, 'detect_intent', lambda _: 'help')
    monkeypatch.setattr('src.app.stream_chat', exploding)

    resp = client.post('/api/chat/stream', json={'message': 'x'})
    assert resp.status_code == 200
    events = _sse_events(resp.data)
    assert events[-1]['type'] == 'error'
    print('  ✓ 生成器异常转成 error 事件')


def test_sse_client_disconnect_is_not_an_error():
    """客户端切走页面时生成器被关闭，不该留下异常噪音。"""
    from src import app as app_module

    def slow():
        yield {'type': 'stage', 'stage': '开始'}
        yield {'type': 'content', 'delta': 'a'}
        yield {'type': 'content', 'delta': 'b'}

    with app_module.app.test_request_context('/api/chat/stream', method='POST'):
        resp = app_module._sse_response(slow())
        stream = iter(resp.response)
        assert json.loads(next(stream)[5:].strip())['type'] == 'stage'
        resp.response.close()  # 模拟客户端断开
    print('  ✓ 客户端断开不抛异常')


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
