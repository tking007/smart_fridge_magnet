#!/usr/bin/env python3
"""校验语音识别：`ai_client` 的请求构造与 `POST /api/asr` 的端点行为。

**不打真网关。** 这里断言的是「我们发出去的 JSON 长什么样」和「各类失败回什么状态码」，
这两件事都能离线验，而且恰恰是最容易悄悄错掉的部分：

  1. 协议形状。音频要放在 `content[].input_audio` 里，`asr_options` 必须在顶层。
  2. base_url 规范化。用户可能填写主机、`/v1` 基址或完整请求端点。
  3. 格式嗅探。ASR 只收 wav / mp3。猜错的后果可能是把噪声识别成一串乱码 —— 一路 200，
     没有任何一层报错。

用法：python tests/test_asr.py
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

_tmp = tempfile.mkdtemp(prefix='sfm-asr-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import ai_client               # noqa: E402
from src.app import app                    # noqa: E402

_failures = []


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        _failures.append(label)
        if detail:
            print(f"        {detail}")
    return ok


# ---------------------------------------------------------------- 假网关
class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


def _asr_ok(content='今天想吃点辣的'):
    return _FakeResponse(payload={
        'id': 'x', 'object': 'chat.completion', 'model': ai_client.MODEL_ASR,
        'choices': [{'index': 0, 'finish_reason': 'stop',
                     'message': {'role': 'assistant', 'content': content}}],
    })


class _Recorder:
    """替掉 requests.post，记下最后一次请求并返回预设响应。

    打桩 `requests.post` 而不是打桩 `call_ai_asr`：要验的正是请求体的形状，
    在更上层打桩就把待验的那段代码一起跳过了。
    """

    def __init__(self, response):
        self.response = response
        self.url = None
        self.json = None
        self.headers = None
        self.timeout = None
        self.calls = 0

    def __call__(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.calls += 1
        self.url = url
        self.json = json
        self.headers = headers
        self.timeout = timeout
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _patch_requests(recorder):
    """把 ai_client 内部 `import requests` 拿到的模块换掉。

    `_post_chat` 里是函数级 import，所以改 `sys.modules['requests'].post` 才拦得住。
    """
    import requests
    original = requests.post
    requests.post = recorder
    return original


def _restore_requests(original):
    import requests
    requests.post = original


# ---------------------------------------------------------------- base_url 规范化
def test_base_url_normalize():
    print('=== base_url 规范化 ===')
    cases = [
        # 已经是对的：原样保留（含尾斜杠）
        ('https://api.example.com/v1', 'https://api.example.com/v1'),
        ('https://api.example.com/v1/', 'https://api.example.com/v1'),
        # 把完整的 OpenAI 端点填进来
        ('https://api.example.com/v1/chat/completions', 'https://api.example.com/v1'),
        # 只给了域名：补 /v1
        ('https://api.example.com', 'https://api.example.com/v1'),
        # 空值：回落默认
        ('', ai_client.DEFAULT_BASE_URL),
        (None, ai_client.DEFAULT_BASE_URL),
    ]
    for raw, want in cases:
        got = ai_client._normalize_base_url(raw)
        check(f'{raw!r} -> {want}', got == want, f'实际 {got!r}')


# ---------------------------------------------------------------- 格式嗅探
def test_sniff():
    print('=== 音频格式按内容嗅探（不看文件名）===')
    wav = ai_client.build_test_wav(seconds=0.05)
    check('合成 WAV 判为 wav', ai_client.sniff_audio_format(wav) == 'wav')
    check('RIFF 头完整', wav[:4] == b'RIFF' and wav[8:12] == b'WAVE')
    check('WAV 头恰 44 字节',
          len(wav) == 44 + int(16000 * 0.05) * 2,
          f'实际 {len(wav)}')

    check('ID3 头判为 mp3', ai_client.sniff_audio_format(b'ID3\x04\x00' + b'\x00' * 32) == 'mp3')
    check('帧同步字判为 mp3', ai_client.sniff_audio_format(b'\xff\xfb' + b'\x00' * 32) == 'mp3')
    # AAC/MP4 是 Android MediaRecorder 的默认输出 —— 必须被判出来，否则会一路发到网关
    check('MP4/M4A（ftyp）不被接受',
          ai_client.sniff_audio_format(b'\x00\x00\x00\x20ftypM4A ' + b'\x00' * 16) is None)
    check('OGG 不被接受', ai_client.sniff_audio_format(b'OggS' + b'\x00' * 32) is None)
    check('AMR 不被接受', ai_client.sniff_audio_format(b'#!AMR\n' + b'\x00' * 32) is None)
    check('RIFF 但不是 WAVE（如 AVI）不被接受',
          ai_client.sniff_audio_format(b'RIFF\x00\x00\x00\x00AVI ' + b'\x00' * 16) is None)
    check('空字节返回 None', ai_client.sniff_audio_format(b'') is None)
    check('过短返回 None', ai_client.sniff_audio_format(b'RIFF') is None)


# ---------------------------------------------------------------- 请求构造
def test_request_shape():
    print('=== ASR 请求体形状（Chat Completions 协议，asr_options 在顶层）===')
    wav = ai_client.build_test_wav(seconds=0.05)
    rec = _Recorder(_asr_ok('你好'))
    original = _patch_requests(rec)
    saved_key, saved_url = ai_client.AI_API_KEY, ai_client.AI_BASE_URL
    api_key = disposable_token('sk-')
    ai_client.AI_API_KEY = api_key
    ai_client.AI_BASE_URL = 'https://api.example.com/v1'
    try:
        text, error = ai_client.call_ai_asr(wav, audio_format='wav', language='zh')
    finally:
        ai_client.AI_API_KEY, ai_client.AI_BASE_URL = saved_key, saved_url
        _restore_requests(original)

    check('识别成功', error is None and text == '你好', f'text={text!r} error={error!r}')
    check('端点是 /v1/chat/completions',
          rec.url == 'https://api.example.com/v1/chat/completions', f'实际 {rec.url}')
    check('鉴权头是 Bearer', rec.headers.get('Authorization') == f'Bearer {api_key}')
    check('超时用 ASR 的预算', rec.timeout == ai_client.ASR_TIMEOUT_SECONDS)

    body = rec.json or {}
    check('model 使用配置值', body.get('model') == ai_client.MODEL_ASR)
    # asr_options 在顶层 —— 塞进 messages 里网关只会回一条笼统的 400
    check('asr_options 在顶层', body.get('asr_options') == {'language': 'zh'},
          f"实际 {body.get('asr_options')!r}")
    parts = (body.get('messages') or [{}])[0].get('content')
    check('content 是数组', isinstance(parts, list) and len(parts) == 1)
    part = parts[0] if isinstance(parts, list) and parts else {}
    check('content[0].type 是 input_audio', part.get('type') == 'input_audio')
    data_uri = (part.get('input_audio') or {}).get('data', '')
    check('音频用 data URI 且 MIME 为 audio/wav',
          data_uri.startswith('data:audio/wav;base64,'), f'实际前缀 {data_uri[:40]!r}')
    import base64 as _b64
    check('data URI 里的 base64 能还原成原音频',
          _b64.b64decode(data_uri.split(',', 1)[1]) == wav)
    check('没有 messages 之外的旧协议字段',
          'system' not in body and 'max_tokens' not in body,
          f'实际 keys={sorted(body.keys())}')

    print('=== 文本与视觉也走同一协议 ===')
    rec2 = _Recorder(_FakeResponse(payload={
        'choices': [{'message': {'role': 'assistant', 'content': 'OK'}}]}))
    original = _patch_requests(rec2)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        out = ai_client.call_ai_text('hi', system_prompt='be brief')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    body2 = rec2.json or {}
    check('文本调用返回内容', out == 'OK', f'实际 {out!r}')
    check('system 是 messages[0]（不是顶层字段）',
          (body2.get('messages') or [{}])[0] == {'role': 'system', 'content': 'be brief'},
          f"实际 {body2.get('messages')!r}")
    check('用 max_completion_tokens（OpenAI 侧的名字）',
          body2.get('max_completion_tokens') == ai_client.MAX_COMPLETION_TOKENS
          and 'max_tokens' not in body2)
    check('文本超时用 chat 的预算', rec2.timeout == ai_client.CHAT_TIMEOUT_SECONDS)

    rec3 = _Recorder(_FakeResponse(payload={
        'choices': [{'message': {'role': 'assistant', 'content': 'a cat'}}]}))
    original = _patch_requests(rec3)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        ai_client.call_ai_vision('what is this', image_base64='QUJD')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    vparts = (rec3.json or {}).get('messages', [{}])[0].get('content')
    kinds = [p.get('type') for p in vparts] if isinstance(vparts, list) else []
    check('图片项是 image_url + data URI',
          kinds == ['image_url', 'text']
          and vparts[0]['image_url']['url'].startswith('data:image/jpeg;base64,'),
          f'实际 {kinds}')


# ---------------------------------------------------------------- 失败路径
def test_failures():
    print('=== 失败路径各自给出能指向下一步的原因 ===')
    wav = ai_client.build_test_wav(seconds=0.05)
    saved_key = ai_client.AI_API_KEY

    # 未配置 key
    original = _patch_requests(_Recorder(_asr_ok()))
    ai_client.AI_API_KEY = None
    try:
        text, error = ai_client.call_ai_asr(wav, audio_format='wav')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    check('未配置 key 时报未配置', text is None and 'AI_API_KEY' in (error or ''))

    # 网关 401
    rec = _Recorder(_FakeResponse(status_code=401, text='{"error":"unauthorized"}'))
    original = _patch_requests(rec)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        text, error = ai_client.call_ai_asr(wav, audio_format='wav')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    check('HTTP 401 原样带上状态码', text is None and '401' in (error or ''), f'error={error!r}')

    # 超时
    import requests as _rq
    rec = _Recorder(_rq.Timeout('timed out'))
    original = _patch_requests(rec)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        text, error = ai_client.call_ai_asr(wav, audio_format='wav')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    check('超时说超时', text is None and '超时' in (error or ''), f'error={error!r}')

    # 只有 reasoning_content、没有正文
    rec = _Recorder(_FakeResponse(payload={'choices': [{'message': {
        'role': 'assistant', 'content': None, 'reasoning_content': '想了很久'}}]}))
    original = _patch_requests(rec)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        text, error = ai_client.call_ai_asr(wav, audio_format='wav')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    check('只有推理内容时说清成因',
          text is None and '推理内容' in (error or ''), f'error={error!r}')

    # 本地就能判的输入错误：不该发出请求
    rec = _Recorder(_asr_ok())
    original = _patch_requests(rec)
    ai_client.AI_API_KEY = disposable_token('sk-')
    try:
        _, e_fmt = ai_client.call_ai_asr(b'\x00' * 64)
        _, e_big = ai_client.call_ai_asr(
            b'RIFF' + b'\x00' * 4 + b'WAVE' + b'\x00' * ai_client.ASR_MAX_AUDIO_BYTES)
        _, e_lang = ai_client.call_ai_asr(wav, audio_format='wav', language='fr')
        _, e_empty = ai_client.call_ai_asr(b'')
    finally:
        ai_client.AI_API_KEY = saved_key
        _restore_requests(original)
    check('格式不支持不发请求', '格式不支持' in (e_fmt or '') and rec.calls == 0)
    check('过大不发请求', '过大' in (e_big or '') and rec.calls == 0)
    check('language 非法不发请求', 'language' in (e_lang or '') and rec.calls == 0)
    check('空音频不发请求', '为空' in (e_empty or '') and rec.calls == 0)


# ---------------------------------------------------------------- HTTP 端点
def test_endpoint():
    print('=== POST /api/asr ===')
    client = app.test_client()
    wav = ai_client.build_test_wav(seconds=0.05)
    saved_key = ai_client.AI_API_KEY

    def post(audio=wav, filename='rec.wav', language=None, response=None,
             key=disposable_token('sk-')):
        data = {}
        if audio is not None:
            data['audio'] = (io.BytesIO(audio), filename)
        if language is not None:
            data['language'] = language
        rec = _Recorder(response if response is not None else _asr_ok())
        original = _patch_requests(rec)
        ai_client.AI_API_KEY = key
        try:
            resp = client.post('/api/asr', data=data, content_type='multipart/form-data')
        finally:
            ai_client.AI_API_KEY = saved_key
            _restore_requests(original)
        return resp, rec

    resp, rec = post(language='zh')
    body = resp.get_json()
    check('成功 200', resp.status_code == 200, f'实际 {resp.status_code} {resp.data[:120]}')
    check('返回识别文本', body.get('text') == '今天想吃点辣的', f'实际 {body}')
    check('empty=false', body.get('empty') is False)
    check('回显 language', body.get('language') == 'zh')
    check('带 duration_ms', isinstance(body.get('duration_ms'), int))
    check('端点把嗅探出的格式传给客户端（data URI 为 audio/wav）',
          (rec.json['messages'][0]['content'][0]['input_audio']['data']
           .startswith('data:audio/wav;base64,')))

    # 空串是合法结果：200 + empty=true，不是错误
    resp, _ = post(response=_asr_ok(''))
    body = resp.get_json()
    check('识别出空串仍是 200', resp.status_code == 200, f'实际 {resp.status_code}')
    check('空串标记 empty=true', body.get('empty') is True and body.get('text') == '')

    # 文件名与格式无关：叫 .mp3 但内容是 wav，按内容走
    resp, rec = post(filename='whatever.mp3')
    check('按内容而不是扩展名判定格式',
          resp.status_code == 200
          and rec.json['messages'][0]['content'][0]['input_audio']['data']
          .startswith('data:audio/wav;base64,'),
          f'实际 {resp.status_code}')

    # 缺字段 / 空文件 / 格式不支持 / 过大 / language 非法 -> 400，且不打网关
    resp, rec = post(audio=None)
    check('缺 audio -> 400', resp.status_code == 400 and rec.calls == 0)
    resp, rec = post(audio=b'')
    check('空音频 -> 400', resp.status_code == 400 and rec.calls == 0)
    resp, rec = post(audio=b'\x00\x00\x00\x20ftypM4A ' + b'\x00' * 16)
    check('MP4/M4A -> 400（Android MediaRecorder 的默认输出必须被拦住）',
          resp.status_code == 400 and rec.calls == 0,
          f'实际 {resp.status_code} {resp.data[:160]}')
    # 过大回 413（实体过大）而不是 400（参数不对）：客户端的处置不同 —— 前者要重录短一点，
    # 后者要改参数。同时断言 calls == 0：体积判定必须在打网关之前，否则白花一次上传。
    resp, rec = post(audio=b'RIFF' + b'\x00' * 4 + b'WAVE'
                     + b'\x00' * ai_client.ASR_MAX_AUDIO_BYTES)
    check('过大 -> 413', resp.status_code == 413 and rec.calls == 0,
          f'实际 {resp.status_code}')

    # 带 Content-Length 的超大请求必须在 read() 之前就被拒 —— 裸 read() 会把整份上传读进
    # 内存后才判上限，一个传错的大文件就能把单 worker 的后端打到 OOM。
    # Flask 测试客户端会按 body 长度自动设 Content-Length，所以这里走的正是那条早退。
    oversized = b'RIFF' + b'\x00' * 4 + b'WAVE' + b'\x00' * (ai_client.ASR_MAX_AUDIO_BYTES + 64)
    resp, rec = post(audio=oversized)
    check('超大请求体在读取前被拒（413 且不打网关）',
          resp.status_code == 413 and rec.calls == 0, f'实际 {resp.status_code}')
    resp, rec = post(language='fr')
    check('language 非法 -> 400', resp.status_code == 400 and rec.calls == 0)

    # 上游失败 -> 502（不是 500：问题不在这一层）
    resp, _ = post(response=_FakeResponse(status_code=500, text='gateway boom'))
    check('上游 5xx -> 502', resp.status_code == 502, f'实际 {resp.status_code}')
    resp, _ = post(response=_FakeResponse(status_code=401, text='unauthorized'))
    check('上游 401 -> 502', resp.status_code == 502, f'实际 {resp.status_code}')

    # 未配置 key -> 503
    resp, rec = post(key=None)
    check('未配置 AI_API_KEY -> 503', resp.status_code == 503 and rec.calls == 0,
          f'实际 {resp.status_code}')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    test_base_url_normalize()
    test_sniff()
    test_request_shape()
    test_failures()
    test_endpoint()
    print()
    if _failures:
        print(f'FAILED ({len(_failures)}): ' + '; '.join(_failures))
        sys.exit(1)
    print('asr self-check passed')
