import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.logger import JsonFormatter, LogManager, RequestIdFilter


def test_json_log_format(monkeypatch):
    monkeypatch.setenv('SFM_LOG_FORMAT', 'json')
    assert config.log_format() == 'json'

    record = logging.LogRecord('unit', logging.INFO, __file__, 12, '菜单 %s', ('完成',), None)
    RequestIdFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))

    assert payload['rid'] == '--------'
    assert payload['level'] == 'INFO'
    assert payload['logger'] == 'unit'
    assert payload['message'] == '菜单 完成'


def test_unknown_log_format_falls_back_to_text(monkeypatch):
    monkeypatch.setenv('SFM_LOG_FORMAT', 'xml')
    assert config.log_format() == 'text'


def test_json_format_applies_to_error_file_handler(tmp_path):
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        LogManager(log_dir=str(tmp_path), log_format='json')
        error_handler = next(
            handler
            for handler in root.handlers
            if getattr(handler, 'baseFilename', '').endswith('error.log')
        )
        assert isinstance(error_handler.formatter, JsonFormatter)
    finally:
        for handler in root.handlers:
            if handler not in previous_handlers:
                handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
