"""Chat Completions compatible text, vision, and speech client.

The provider, endpoint, credentials, and model identifiers are runtime
configuration. ``requests`` is used directly because speech providers may add
fields such as ``asr_options`` that are not represented by a vendor SDK.
"""
import os
import sys
import base64
import time
import uuid
from pathlib import Path
from typing import List, Optional

try:
    from .logger import get_logger
    logger = get_logger('ai_client')
except ImportError:  # pragma: no cover - 直接以脚本运行自检时（python src/ai_client.py）
    # 相对 import 在脚本模式下必然失败，而自检本身不需要项目的日志栈。
    import logging
    logger = logging.getLogger('ai_client')

# 加载 backend/.env；部署环境变量仍由 python-dotenv 默认优先保留。
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# AI 配置
AI_API_KEY = os.environ.get("AI_API_KEY")

#: Chat Completions 兼容基址，末尾到 `/v1`，请求路径由本模块拼 `/chat/completions`。
DEFAULT_BASE_URL = "https://api.example.com/v1"

MODEL_VISION = os.environ.get("AI_MODEL_VISION", "vision-model")
MODEL_TEXT = os.environ.get("AI_MODEL_TEXT", "text-model")
MODEL_ASR = os.environ.get("AI_MODEL_ASR", "speech-model")

MAX_COMPLETION_TOKENS = 4096
# 深度推理与正文共用输出预算；8192 会在复杂家庭画像下截断菜谱 JSON。
RECIPE_MAX_COMPLETION_TOKENS = 16384
#: 文本与视觉请求的超时。菜谱生成实测约 25 秒（真机上遇到过 60+ 秒），留足余量。
CHAT_TIMEOUT_SECONDS = 120

#: 调用失败时返回字符串的前缀。抽成常量是因为 [call_ai_text_with_json] 要靠它把
#: 「请求没成功」与「答了但不是 JSON」区分开 —— 写两遍字面量的话，改了一处就静默失效。
CALL_FAILED_PREFIX = "调用失败: "


def _normalize_base_url(raw: str) -> str:
    """把 ``AI_BASE_URL`` 规范到 Chat Completions 兼容的 ``/v1`` 基址。"""
    url = (raw or '').strip().rstrip('/')
    if not url:
        return DEFAULT_BASE_URL
    original = url
    if url.endswith('/chat/completions'):
        url = url[: -len('/chat/completions')].rstrip('/')
    if not url.endswith('/v1'):
        url = url + '/v1'
    if url != original:
        logger.warning('AI_BASE_URL 已规范化：%s -> %s（Chat Completions 兼容协议，末尾应为 /v1）',
                       original, url)
    return url


AI_BASE_URL = _normalize_base_url(os.environ.get("AI_BASE_URL", DEFAULT_BASE_URL))

# ASR 的音频约束，全部来自官方文档，**不是本项目自己定的余量**：
#   - 只接受 wav 与 mp3；
#   - base64 编码后的字符串上限 10 MB。
# 上限按原始字节记：base64 放大 4/3，7.5 MB 原始字节正好压在 10 MB 线上。
ASR_AUDIO_MIME = {'wav': 'audio/wav', 'mp3': 'audio/mpeg'}
ASR_MAX_AUDIO_BYTES = 7_500_000
ASR_LANGUAGES = ('auto', 'zh', 'en')
#: 识别是同步等待的，不给很长预算。**必须明显小于客户端的读超时**（Android 侧 OkHttp
#: 是 60 秒）：两边都取 60 的话谁先超时取决于上传耗时与调度，同一个故障会有两种表现。
#: 45 秒对一段 ≤60 秒的音频足够宽（文档示例里 4 秒音频的识别耗时是秒级）。
ASR_TIMEOUT_SECONDS = 45


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }


def _describe_payload(payload: dict) -> str:
    """概括一次请求的输入规模，供日志用。

    只统计文本字符数，并把图片/音频**标成存在而不打内容**：base64 后的音频有几 MB，
    整段进日志会把一次调用的记录变成几千行，而排障需要的只是「有没有带、带了几段」。
    """
    text_chars = 0
    attachments = []
    for message in payload.get('messages') or []:
        content = message.get('content')
        if isinstance(content, str):
            text_chars += len(content)
            continue
        for part in content or []:
            kind = part.get('type') if isinstance(part, dict) else None
            if kind == 'text':
                text_chars += len(part.get('text') or '')
            elif kind:
                attachments.append(kind)
    desc = f"text={text_chars}字"
    if attachments:
        desc += f" attachments={','.join(attachments)}"
    return desc


def _post_chat(payload: dict, timeout: int, purpose: str = '', call_id: str = None,
               attempt: int = 1):
    """发一次 chat/completions，返回 `(content, error)`。

    统一在这里处理鉴权、超时、HTTP 状态与响应结构，四类失败各自给出**能指向下一步动作**
    的文案；上层三个 call_* 只负责拼 payload。

    模型调用是整个系统里最慢、最贵、最容易出问题的一步，失败原因（网关 5xx / 超时 /
    key 类型不对 / 只吐了 reasoning）必须落进日志，上层只拿到一句给用户看的话。

    Args:
        purpose: 调用意图（recipe / revise / chat / vision / asr…），用于在日志里区分
            同一个模型的不同用途。
    """
    import requests

    model = payload.get('model')
    tag = purpose or 'unknown'
    call_id = call_id or uuid.uuid4().hex[:12]
    attempt = max(1, int(attempt))

    if not AI_API_KEY:
        logger.error('模型调用未发出：model=%s purpose=%s 原因=AI_API_KEY 未配置', model, tag)
        return None, "AI_API_KEY 未配置"
    url = f"{AI_BASE_URL}/chat/completions"
    logger.info('模型调用开始：model=%s purpose=%s call_id=%s attempt=%d retries=%d %s timeout=%ss',
                model, tag, call_id, attempt, attempt - 1, _describe_payload(payload), timeout)
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    def fail(reason: str, upstream_request_id: str = '-'):
        # 统一在一处打失败：分散在各个 return 前面必然漏，而漏掉的那条往往正是现场遇到的。
        logger.error('模型调用失败：model=%s purpose=%s call_id=%s attempt=%d retries=%d '
                     'upstream_request_id=%s 耗时=%dms 原因=%s',
                     model, tag, call_id, attempt, attempt - 1, upstream_request_id,
                     elapsed_ms(), reason)
        return None, reason

    try:
        resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
    except requests.Timeout:
        return fail(f"请求超时（>{timeout}s）")
    except requests.RequestException as exc:
        return fail(f"网关不可达：{exc}")

    response_headers = getattr(resp, 'headers', None) or {}
    upstream_request_id = (
        response_headers.get('x-request-id')
        or response_headers.get('request-id')
        or response_headers.get('x-trace-id')
        or '-'
    )

    if resp.status_code != 200:
        # 截断响应体：网关的错误页可能很长，日志与用户提示都只需要开头那句。
        return fail(f"网关返回 HTTP {resp.status_code}：{resp.text[:200]}", upstream_request_id)

    try:
        body = resp.json()
        choice = body['choices'][0]
        message = choice['message']
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return fail(f"响应格式异常（{exc}）：{resp.text[:200]}", upstream_request_id)

    content = message.get('content')
    if content is None:
        # 推理模型把预算全花在 reasoning 上时 content 会是空的。这不是格式错误，
        # 但对调用方来说等于没有答案，所以当失败处理并说清成因。
        if message.get('reasoning_content'):
            return fail(f"模型只产出了推理内容、没有正文"
                        f"（max_completion_tokens={payload.get('max_completion_tokens')} 可能不够）",
                        upstream_request_id)
        return fail(f"响应里没有 content：{resp.text[:200]}", upstream_request_id)
    if not isinstance(content, str):
        return fail(f"响应里的 content 不是字符串：{type(content).__name__}", upstream_request_id)

    usage = body.get('usage') or {}
    finish_reason = choice.get('finish_reason') or '-'
    if not content.strip():
        return fail(
            "模型返回空正文"
            f"（finish_reason={finish_reason} "
            f"input_tokens={usage.get('prompt_tokens', '-')} "
            f"output_tokens={usage.get('completion_tokens', '-')} "
            f"total_tokens={usage.get('total_tokens', '-')} "
            f"max_completion_tokens={payload.get('max_completion_tokens', '-')}）",
            upstream_request_id,
        )
    if finish_reason == 'length':
        return fail(
            "模型输出达到长度上限，结果不完整"
            f"（finish_reason=length "
            f"output_tokens={usage.get('completion_tokens', '-')} "
            f"max_completion_tokens={payload.get('max_completion_tokens', '-')}）",
            upstream_request_id,
        )
    logger.info('模型调用成功：model=%s purpose=%s call_id=%s attempt=%d retries=%d '
                'upstream_request_id=%s 耗时=%dms 输出=%d字 input_tokens=%s '
                'output_tokens=%s total_tokens=%s finish_reason=%s max_completion_tokens=%s',
                model, tag, call_id, attempt, attempt - 1, upstream_request_id,
                elapsed_ms(), len(content), usage.get('prompt_tokens', '-'),
                usage.get('completion_tokens', '-'), usage.get('total_tokens', '-'),
                finish_reason, payload.get('max_completion_tokens', '-'))
    return content, None


def _build_messages(prompt_content, system_prompt=None):
    """Chat Completions 协议把 system 放在 messages[0]（另一类 是顶层 `system` 字段）。"""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt_content})
    return messages


def _preflight_error() -> Optional[str]:
    """发请求前能确定失败的原因，没有则 None。

    抽出来给流式与非流式共用，确保缺少凭据时返回一致文案。
    """
    if not AI_API_KEY:
        return "AI_API_KEY 未配置"
    return None


def parse_json_payload(raw):
    """从模型输出里提取 JSON，返回 `(obj, error)`。

    模型常把 JSON 包在 markdown 代码块里，或在前后添一句解释。两种情况都要能取出来 ——
    否则一次内容正确的生成会因为多了三个反引号被判成失败。

    流式与非流式共用一份实现：两条链路对「模型答的算不算合格」必须同一判据。
    """
    import json

    if not isinstance(raw, str) or not raw.strip():
        return None, "模型返回空正文"

    text = raw.strip()
    if text.startswith('```json'):
        text = text[7:]
    if text.startswith('```'):
        text = text[3:]
    if text.endswith('```'):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass

    # 前后有解释文字时，按括号配平截出第一个完整的 JSON 值。
    start = raw.find('[')
    if start == -1:
        start = raw.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] in '[{':
                depth += 1
            elif raw[i] in ']}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1]), None
                    except json.JSONDecodeError:
                        break
    return None, "无法解析JSON"


def stream_ai_text(prompt, system_prompt=None, temperature=0.3, purpose='stream',
                     call_id=None, max_completion_tokens=RECIPE_MAX_COMPLETION_TOKENS,
                     timeout=CHAT_TIMEOUT_SECONDS):
    """流式调用文本模型，逐块产出事件字典。

    产出的事件：
      - `{'type': 'reasoning', 'delta': str}` 思考增量
      - `{'type': 'content', 'delta': str}` 正文增量
      - `{'type': 'done', 'text': str}` 正文全文，供调用方解析或落库
      - `{'type': 'error', 'message': str}` 失败原因，产出后即终止

    失败时只产出 error 事件，调用方不必再判断返回值形态。日志口径与 [_post_chat] 一致，
    因为排障时两条链路的记录会混在一起看。
    """
    import json

    import requests

    tag = purpose or 'unknown'
    call_id = call_id or uuid.uuid4().hex[:12]

    preflight = _preflight_error()
    if preflight:
        logger.error('模型流式调用未发出：model=%s purpose=%s 原因=%s',
                     MODEL_TEXT, tag, preflight)
        yield {'type': 'error', 'message': preflight}
        return

    payload = {
        "model": MODEL_TEXT,
        "messages": _build_messages(prompt, system_prompt),
        "max_completion_tokens": max_completion_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "stream": True,
    }
    logger.info('模型流式调用开始：model=%s purpose=%s call_id=%s %s timeout=%ss',
                MODEL_TEXT, tag, call_id, _describe_payload(payload), timeout)
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    content_parts: List[str] = []
    reasoning_chars = 0
    finish_reason = '-'
    dropped_chunks = 0

    try:
        with requests.post(f"{AI_BASE_URL}/chat/completions", json=payload,
                           headers=_auth_headers(), timeout=timeout, stream=True) as resp:
            if resp.status_code != 200:
                reason = f"网关返回 HTTP {resp.status_code}：{resp.text[:200]}"
                logger.error('模型流式调用失败：model=%s purpose=%s call_id=%s 耗时=%dms 原因=%s',
                             MODEL_TEXT, tag, call_id, elapsed_ms(), reason)
                yield {'type': 'error', 'message': reason}
                return

            # 必须在**字节层**切行、再按 UTF-8 解码，不能用 iter_lines(decode_unicode=True)：
            # SSE 响应一般不带 charset，requests 便按 HTTP 默认的 ISO-8859-1 逐块解码，
            # 中文成了 mojibake，且跨块的行会被切断（切断的行不是合法 JSON 而被丢弃）。
            buffer = b''
            stream_done = False
            for raw_chunk in resp.iter_content(chunk_size=None):
                if not raw_chunk:
                    continue
                buffer += raw_chunk
                while b'\n' in buffer:
                    raw_line, buffer = buffer.split(b'\n', 1)
                    line = raw_line.decode('utf-8', errors='replace').strip()
                    if not line or not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        stream_done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        # 单个坏块不终止生成，但必须计数：静默跳过会让「内容少了一段」
                        # 伪装成「模型答得不对」。
                        dropped_chunks += 1
                        continue

                    choices = chunk.get('choices') or []
                    if not choices:
                        continue
                    delta = choices[0].get('delta') or {}
                    finish_reason = choices[0].get('finish_reason') or finish_reason

                    reasoning = delta.get('reasoning_content')
                    if reasoning:
                        reasoning_chars += len(reasoning)
                        yield {'type': 'reasoning', 'delta': reasoning}

                    piece = delta.get('content')
                    if piece:
                        content_parts.append(piece)
                        yield {'type': 'content', 'delta': piece}
                if stream_done:
                    break
    except requests.Timeout:
        reason = f"请求超时（>{timeout}s）"
        logger.error('模型流式调用失败：model=%s purpose=%s call_id=%s 耗时=%dms 原因=%s',
                     MODEL_TEXT, tag, call_id, elapsed_ms(), reason)
        yield {'type': 'error', 'message': reason}
        return
    except requests.RequestException as exc:
        reason = f"网关不可达：{exc}"
        logger.error('模型流式调用失败：model=%s purpose=%s call_id=%s 耗时=%dms 原因=%s',
                     MODEL_TEXT, tag, call_id, elapsed_ms(), reason)
        yield {'type': 'error', 'message': reason}
        return

    text = ''.join(content_parts)
    if not text.strip():
        # 推理模型可能把预算全花在 reasoning 上。这不是格式错误，但对调用方等于没有答案。
        reason = ("模型只产出了推理内容、没有正文"
                  f"（max_completion_tokens={max_completion_tokens} 可能不够）"
                  if reasoning_chars else "模型返回空正文")
        logger.error('模型流式调用失败：model=%s purpose=%s call_id=%s 耗时=%dms '
                     'finish_reason=%s 思考=%d字 丢弃分块=%d 原因=%s',
                     MODEL_TEXT, tag, call_id, elapsed_ms(), finish_reason,
                     reasoning_chars, dropped_chunks, reason)
        yield {'type': 'error', 'message': reason}
        return

    if finish_reason == 'length':
        reason = (
            "模型输出达到长度上限，结果不完整"
            f"（max_completion_tokens={max_completion_tokens}）"
        )
        logger.error('模型流式调用失败：model=%s purpose=%s call_id=%s 耗时=%dms '
                     '正文=%d字 思考=%d字 finish_reason=%s 丢弃分块=%d 原因=%s',
                     MODEL_TEXT, tag, call_id, elapsed_ms(), len(text), reasoning_chars,
                     finish_reason, dropped_chunks, reason)
        yield {'type': 'error', 'message': reason}
        return

    if dropped_chunks:
        # 内容有缺口时后面的 JSON 解析大概率会失败，届时这条是唯一能指向真正原因的记录。
        logger.warning('模型流式调用有 %d 个分块解析失败，正文可能不完整：'
                       'model=%s purpose=%s call_id=%s',
                       dropped_chunks, MODEL_TEXT, tag, call_id)

    logger.info('模型流式调用成功：model=%s purpose=%s call_id=%s 耗时=%dms '
                '正文=%d字 思考=%d字 finish_reason=%s 丢弃分块=%d',
                MODEL_TEXT, tag, call_id, elapsed_ms(), len(text),
                reasoning_chars, finish_reason, dropped_chunks)
    yield {'type': 'done', 'text': text}


def call_ai_vision(prompt, image_path=None, image_base64=None, system_prompt=None):
    """
    调用 AI 多模态模型（图片识别）

    Args:
        prompt: 提示词
        image_path: 图片文件路径
        image_base64: 图片base64编码
        system_prompt: 系统提示词

    Returns:
        模型回复文本；失败时返回 "调用失败: ..."（保持既有调用方的判定方式不变）
    """
    # 处理图片
    if image_path:
        with open(image_path, 'rb') as f:
            b64_data = base64.standard_b64encode(f.read()).decode('utf-8')
        # 推断 mime type
        ext = Path(image_path).suffix.lower()
        mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
        mime_type = mime_map.get(ext, 'image/jpeg')
    elif image_base64:
        b64_data = image_base64
        mime_type = 'image/jpeg'
    else:
        return "错误：需要提供图片"

    # Chat Completions 协议的图片项是 `image_url` + data URI（另一类 是 `image` + source 三元组）。
    content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}},
        {"type": "text", "text": prompt}
    ]

    text, error = _post_chat({
        "model": MODEL_VISION,
        "messages": _build_messages(content, system_prompt),
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }, CHAT_TIMEOUT_SECONDS, purpose='vision')
    return text if error is None else f"{CALL_FAILED_PREFIX}{error}"


def call_ai_text(prompt, system_prompt=None, temperature=1.0, purpose='text',
                   call_id=None, attempt=1,
                   max_completion_tokens=MAX_COMPLETION_TOKENS):
    """
    调用 AI 文本模型

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词
        temperature: 温度参数
        purpose: 调用意图，只用于日志区分（recipe / revise / chat / …）

    Returns:
        模型回复文本；失败时返回 "调用失败: ..."（保持既有调用方的判定方式不变）
    """
    text, error = _post_chat({
        "model": MODEL_TEXT,
        "messages": _build_messages(prompt, system_prompt),
        # 普通 JSON 首轮若恰好空正文，重试也会放宽到菜谱所需的上限。
        "max_completion_tokens": max(
            max_completion_tokens,
            RECIPE_MAX_COMPLETION_TOKENS if attempt > 1 else MAX_COMPLETION_TOKENS,
        ),
        "temperature": temperature,
        "top_p": 0.95,
    }, CHAT_TIMEOUT_SECONDS, purpose=purpose, call_id=call_id, attempt=attempt)
    return text if error is None else f"{CALL_FAILED_PREFIX}{error}"


def call_ai_text_with_json(prompt, system_prompt=None, temperature=0.3, purpose='json',
                             call_id=None, attempt=1,
                             max_completion_tokens=MAX_COMPLETION_TOKENS):
    """
    调用 AI 文本模型并强制返回 JSON

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词
        temperature: 温度参数
        purpose: 调用意图，只用于日志区分

    Returns:
        解析后的 JSON 对象；失败时是 `{"error": 原因, "raw": 原文}`。

    **调用失败与解析失败给出不同的 error**：`call_ai_text` 失败时返回的是
    `"调用失败: 网关返回 HTTP 502…"` 这样的字符串，它当然也不是 JSON，混在一起会让网关
    5xx、超时、key 类型不对三种故障在上层眼里长得一样，而上层正是决定要不要重试的地方。
    """
    call_id = call_id or uuid.uuid4().hex[:12]
    result = call_ai_text(
        prompt, system_prompt, temperature, purpose=purpose,
        call_id=call_id, attempt=attempt,
        max_completion_tokens=max_completion_tokens,
    )

    if isinstance(result, str) and result.startswith(CALL_FAILED_PREFIX):
        # 请求本身就没成功，别再去解析它。原因已经在 _post_chat 里打过 error 日志。
        reason = result[len(CALL_FAILED_PREFIX):]
        if attempt == 1 and reason.startswith("模型返回空正文"):
            logger.warning('模型返回空正文，仅重试一次：purpose=%s call_id=%s', purpose, call_id)
            return call_ai_text_with_json(
                prompt, system_prompt, temperature, purpose=purpose,
                call_id=call_id, attempt=2,
                max_completion_tokens=max_completion_tokens,
            )
        return {"error": reason, "raw": result}

    parsed, parse_error = parse_json_payload(result)
    if parse_error is None:
        return parsed

    # 模型答了、但答的不是 JSON。记下开头一段：这是判断「prompt 要不要改」的唯一依据。
    logger.warning('模型返回无法解析为 JSON：purpose=%s 长度=%d 开头=%r',
                   purpose, len(result or ''), (result or '')[:300])
    return {"error": parse_error, "raw": result}


def sniff_audio_format(audio_bytes: bytes):
    """按内容判定音频格式，返回 'wav' / 'mp3' / None。

    **按内容而不是按文件名**：客户端传来的名字随时可能是 `recording.tmp` 或干脆没有，
    而 ASR 只收这两种格式，猜错的后果是网关回一条与格式无关的错误。
    """
    if not audio_bytes or len(audio_bytes) < 12:
        return None
    # WAV：RIFF 容器 + WAVE 类型。中间 4 字节是块长度，不参与判定。
    if audio_bytes[:4] == b'RIFF' and audio_bytes[8:12] == b'WAVE':
        return 'wav'
    # MP3：要么带 ID3v2 标签头，要么直接是帧同步字（11 个 1）。
    if audio_bytes[:3] == b'ID3':
        return 'mp3'
    if audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
        return 'mp3'
    return None


def call_ai_asr(audio_bytes, audio_format=None, language='auto'):
    """用配置的语音模型把音频转成文字。

    Args:
        audio_bytes: 原始音频字节，wav 或 mp3
        audio_format: 'wav' / 'mp3'，省略时按内容嗅探
        language: 'auto'（默认）/ 'zh' / 'en'。文档建议语种明确时手动指定以提升效果

    Returns:
        `(text, error)`。成功时 error 为 None；失败时 text 为 None、error 是失败原因。

    返回二元组，而不像本模块其他函数那样把错误拼进返回字符串：调用方（HTTP 端点）要
    区分「识别出的文字」与「失败原因」才能给出正确的状态码，而 `"调用失败: ..."` 这种
    约定会与用户念出的同样文字混淆。
    """
    if not audio_bytes:
        return None, "音频为空"
    if len(audio_bytes) > ASR_MAX_AUDIO_BYTES:
        return None, (f"音频过大：{len(audio_bytes)} 字节，上限 {ASR_MAX_AUDIO_BYTES}"
                      f"（base64 后需 ≤10MB）")

    fmt = (audio_format or '').strip().lower() or sniff_audio_format(audio_bytes)
    if fmt not in ASR_AUDIO_MIME:
        return None, f"音频格式不支持：{fmt or '无法识别'}（只接受 wav / mp3）"

    lang = (language or 'auto').strip().lower()
    if lang not in ASR_LANGUAGES:
        return None, f"language 非法：{lang}（可选 {' / '.join(ASR_LANGUAGES)}）"

    b64 = base64.b64encode(audio_bytes).decode('ascii')
    text, error = _post_chat({
        "model": MODEL_ASR,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                # 用 data URI 形态而不是 {data, format} 两字段形态：两者文档都支持，
                # 但 data URI 把 MIME 与数据绑在一起，不会出现「format 写 wav、
                # 数据实际是 mp3」这种自相矛盾的请求。
                "input_audio": {"data": f"data:{ASR_AUDIO_MIME[fmt]};base64,{b64}"}
            }]
        }],
        # 顶层字段，不在 messages 里。
        "asr_options": {"language": lang},
    }, ASR_TIMEOUT_SECONDS, purpose='asr')

    if error is not None:
        return None, error
    # 识别出空串是合法结果（一段静音、或没说话就松手），交给调用方决定怎么表达。
    return text.strip(), None


def health_check():
    """检查 API 连通性"""
    import time

    result = {}

    # 测试文本模型
    logger.info('AI health check started: model=%s kind=text', MODEL_TEXT)
    start = time.time()
    try:
        resp = call_ai_text("Reply with only: OK")
        latency = time.time() - start
        if "调用失败" in resp:
            result["text"] = {"status": "fail", "latency": round(latency, 2), "error": resp}
            logger.error('AI health check failed: model=%s error=%s', MODEL_TEXT, resp[:60])
        else:
            result["text"] = {"status": "ok", "latency": round(latency, 2)}
            logger.info('AI health check passed: model=%s latency=%.1fs', MODEL_TEXT, latency)
    except Exception as e:
        latency = time.time() - start
        result["text"] = {"status": "fail", "latency": round(latency, 2), "error": str(e)[:80]}
        logger.exception('AI health check failed: model=%s error=%s', MODEL_TEXT, str(e)[:60])

    # 测试视觉模型（生成测试图片）
    logger.info('AI health check started: model=%s kind=vision', MODEL_VISION)
    start = time.time()
    try:
        from PIL import Image, ImageDraw
        import io

        img = Image.new('RGB', (100, 50), color='white')
        d = ImageDraw.Draw(img)
        d.text((10, 15), "TEST", fill='black')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.standard_b64encode(buf.getvalue()).decode('utf-8')

        resp = call_ai_vision("What text is shown?", image_base64=b64)
        latency = time.time() - start
        if "调用失败" in resp:
            result["vision"] = {"status": "fail", "latency": round(latency, 2), "error": resp}
            logger.error('AI health check failed: model=%s error=%s', MODEL_VISION, resp[:60])
        else:
            result["vision"] = {"status": "ok", "latency": round(latency, 2)}
            logger.info('AI health check passed: model=%s latency=%.1fs', MODEL_VISION, latency)
    except Exception as e:
        latency = time.time() - start
        result["vision"] = {"status": "fail", "latency": round(latency, 2), "error": str(e)[:80]}
        logger.exception('AI health check failed: model=%s error=%s', MODEL_VISION, str(e)[:60])

    # 测试语音识别（合成一段 0.5 秒 440Hz 正弦波 WAV）
    #
    # 用合成音频而不是随包附一个录音：一个 .wav 素材要进仓库、要说明它是谁的声音、
    # 还要保证任何机器上都能读。合成音是确定的字节，且这一项探的是「网关能不能收下
    # 一段合法 wav 并回一个 chat.completion」，识别出什么内容不重要（正弦波本来就
    # 没有语音内容，返回空串也算通）。
    logger.info('AI health check started: model=%s kind=asr', MODEL_ASR)
    start = time.time()
    try:
        wav = build_test_wav()
        text, error = call_ai_asr(wav, audio_format='wav', language='zh')
        latency = time.time() - start
        if error is not None:
            result["asr"] = {"status": "fail", "latency": round(latency, 2), "error": error}
            logger.error('AI health check failed: model=%s error=%s', MODEL_ASR, error[:60])
        else:
            result["asr"] = {"status": "ok", "latency": round(latency, 2),
                             "text_len": len(text)}
            logger.info('AI health check passed: model=%s latency=%.1fs chars=%d',
                        MODEL_ASR, latency, len(text))
    except Exception as e:
        latency = time.time() - start
        result["asr"] = {"status": "fail", "latency": round(latency, 2), "error": str(e)[:80]}
        logger.exception('AI health check failed: model=%s error=%s', MODEL_ASR, str(e)[:60])

    return result


def build_test_wav(seconds=0.5, sample_rate=16000, freq=440):
    """合成一段单声道 16-bit PCM WAV。用于自检与测试，不依赖任何素材文件。

    手写 44 字节 RIFF 头而不用 `wave` 模块：头部字段的顺序与含义正好是 Android 侧
    也要手写的那一份（`MediaRecorder` 不产 wav），两边对着同一份注释更不容易写错。
    """
    import math
    import struct

    frames = int(sample_rate * seconds)
    pcm = bytearray()
    for i in range(frames):
        # 0.3 幅度：留够余量，避免 int16 边界上的溢出回绕。
        value = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        pcm += struct.pack('<h', value)

    data_len = len(pcm)
    channels, bits = 1, 16
    byte_rate = sample_rate * channels * bits // 8
    header = b'RIFF' + struct.pack('<I', 36 + data_len) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH',
                                    16,            # fmt 块长度
                                    1,             # PCM
                                    channels,
                                    sample_rate,
                                    byte_rate,
                                    channels * bits // 8,   # block align
                                    bits)
    header += b'data' + struct.pack('<I', data_len)
    return bytes(header) + bytes(pcm)


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 60)
    print("AI API Health Check")
    print("=" * 60)
    print("\n[Config]")
    print(f"  BASE_URL: {AI_BASE_URL}  （Chat Completions 兼容）")
    print(f"  MODEL_VISION: {MODEL_VISION}")
    print(f"  MODEL_TEXT: {MODEL_TEXT}")
    print(f"  MODEL_ASR: {MODEL_ASR}")
    if not AI_API_KEY:
        print("  API_KEY: NOT SET")
    else:
        print("  API_KEY: set")
    print()

    if not AI_API_KEY:
        print("ERROR: AI_API_KEY not set!")
        print(f"Please set it in {env_path}")
        sys.exit(1)

    result = health_check()

    print("\n" + "=" * 60)
    all_ok = all(v["status"] == "ok" for v in result.values())
    status = "ALL OK" if all_ok else "SOME FAILED"
    print(f"Overall: {status}")
