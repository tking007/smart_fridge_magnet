import logging
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

from src import ai_client

_REAL_CALL_AI_TEXT = ai_client.call_ai_text
_REAL_CALL_AI_TEXT_WITH_JSON = ai_client.call_ai_text_with_json


class _Response:
    status_code = 200
    text = '{}'
    headers = {'x-request-id': 'upstream-42'}

    @staticmethod
    def json():
        return {
            'choices': [{'message': {'content': 'ok'}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14},
        }


def test_model_log_contains_usage_retry_and_upstream_id(monkeypatch, caplog):
    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: _Response())
    caplog.set_level(logging.INFO, logger='ai_client')

    content, error = ai_client._post_chat(
        {'model': 'ai-test', 'messages': []}, 1,
        purpose='recipe_allergen_retry', call_id='recipe-7', attempt=2,
    )

    assert (content, error) == ('ok', None)
    line = next(record.getMessage() for record in caplog.records if '模型调用成功' in record.getMessage())
    assert 'call_id=recipe-7' in line
    assert 'attempt=2 retries=1' in line
    assert 'upstream_request_id=upstream-42' in line
    assert 'input_tokens=11 output_tokens=3 total_tokens=14' in line


def test_model_log_tolerates_response_without_headers(monkeypatch, caplog):
    class HeaderlessResponse(_Response):
        headers = None

    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: HeaderlessResponse())
    caplog.set_level(logging.INFO, logger='ai_client')

    assert ai_client._post_chat(
        {'model': 'ai-test', 'messages': []}, 1, purpose='asr'
    ) == ('ok', None)
    line = next(record.getMessage() for record in caplog.records if '模型调用成功' in record.getMessage())
    assert 'upstream_request_id=-' in line


def test_empty_model_content_is_failure(monkeypatch, caplog):
    class EmptyContentResponse(_Response):
        @staticmethod
        def json():
            return {
                'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}],
                'usage': {'prompt_tokens': 1005, 'completion_tokens': 4096, 'total_tokens': 5101},
            }

    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: EmptyContentResponse())
    caplog.set_level(logging.INFO, logger='ai_client')

    content, error = ai_client._post_chat(
        {'model': 'ai-test', 'messages': [], 'max_completion_tokens': 4096},
        1, purpose='recipe',
    )

    assert content is None
    assert '空正文' in error
    assert 'finish_reason=length' in error
    assert 'max_completion_tokens=4096' in error


def test_partial_model_content_with_length_finish_is_failure(monkeypatch):
    class TruncatedResponse(_Response):
        @staticmethod
        def json():
            return {
                'choices': [{
                    'message': {'content': '{"plan_a":{"dishes":['},
                    'finish_reason': 'length',
                }],
                'usage': {'prompt_tokens': 1005, 'completion_tokens': 8192,
                          'total_tokens': 9197},
            }

    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: TruncatedResponse())

    content, error = ai_client._post_chat(
        {'model': 'ai-test', 'messages': [], 'max_completion_tokens': 8192},
        1, purpose='recipe',
    )

    assert content is None
    assert '达到长度上限' in error
    assert 'finish_reason=length' in error


def test_json_call_retries_empty_content_once(monkeypatch):
    class EmptyContentResponse(_Response):
        @staticmethod
        def json():
            return {
                'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}],
                'usage': {'prompt_tokens': 1005, 'completion_tokens': 4096, 'total_tokens': 5101},
            }

    class JsonResponse(_Response):
        @staticmethod
        def json():
            return {
                'choices': [{'message': {'content': '{"plan_a": {}}'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            }

    responses = iter([EmptyContentResponse(), JsonResponse()])
    calls = []
    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setitem(
        _REAL_CALL_AI_TEXT_WITH_JSON.__globals__,
        'call_ai_text', _REAL_CALL_AI_TEXT,
    )
    monkeypatch.setitem(
        _REAL_CALL_AI_TEXT_WITH_JSON.__globals__,
        'call_ai_text_with_json', _REAL_CALL_AI_TEXT_WITH_JSON,
    )
    monkeypatch.setattr(
        requests, 'post',
        lambda *args, **kwargs: calls.append(kwargs['json'].copy()) or next(responses),
    )

    result = _REAL_CALL_AI_TEXT_WITH_JSON('prompt', purpose='recipe', call_id='recipe-8')

    assert result == {'plan_a': {}}
    assert len(calls) == 2
    assert calls[0]['max_completion_tokens'] == 4096
    assert calls[1]['max_completion_tokens'] == ai_client.RECIPE_MAX_COMPLETION_TOKENS


def test_json_call_can_start_with_recipe_budget(monkeypatch):
    class JsonResponse(_Response):
        @staticmethod
        def json():
            return {
                'choices': [{'message': {'content': '{"plan_a": {}}'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            }

    calls = []
    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setitem(
        _REAL_CALL_AI_TEXT_WITH_JSON.__globals__,
        'call_ai_text', _REAL_CALL_AI_TEXT,
    )
    monkeypatch.setattr(
        requests, 'post',
        lambda *args, **kwargs: calls.append(kwargs['json'].copy()) or JsonResponse(),
    )

    result = _REAL_CALL_AI_TEXT_WITH_JSON(
        'prompt', purpose='recipe',
        max_completion_tokens=ai_client.RECIPE_MAX_COMPLETION_TOKENS,
    )

    assert result == {'plan_a': {}}
    assert calls[0]['max_completion_tokens'] == ai_client.RECIPE_MAX_COMPLETION_TOKENS


def test_json_call_does_not_retry_http_failure(monkeypatch):
    class ErrorResponse(_Response):
        status_code = 503
        text = 'temporarily unavailable'

    calls = []
    monkeypatch.setattr(ai_client, 'AI_API_KEY', disposable_token('sk-'))
    monkeypatch.setitem(
        _REAL_CALL_AI_TEXT_WITH_JSON.__globals__,
        'call_ai_text', _REAL_CALL_AI_TEXT,
    )
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: calls.append(1) or ErrorResponse())

    result = _REAL_CALL_AI_TEXT_WITH_JSON('prompt', purpose='recipe')

    assert 'HTTP 503' in result['error']
    assert len(calls) == 1
