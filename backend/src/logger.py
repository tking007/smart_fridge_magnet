"""
日志系统模块 — 统一的日志管理
支持：分级日志、文件输出、控制台输出、日志轮转
"""
import os
import json
import logging
import time as _time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from datetime import datetime

try:
    # 仅用于给日志补 request id。放在 try 里是因为这个模块也被不带请求上下文的入口用到
    # （定时任务、CLI 脚本、单测），那些场景没有 Flask 也必须照常记日志。
    from flask import g as _flask_g, has_request_context as _has_request_context
except ImportError:  # pragma: no cover - 仅在无 Flask 的环境走到
    _flask_g = None
    _has_request_context = None


# 日志级别映射
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

#: 不属于任何请求的日志（启动、定时任务、脚本）用这个占位，长度与真实 rid 一致，
#: 便于按列对齐扫读。
NO_REQUEST_ID = '--------'


class RequestIdFilter(logging.Filter):
    """给每条日志补上当前请求的 request id。

    用 Filter 统一注入，而不是要求每个日志点自己带上 rid：全仓有上百处 logger 调用，
    靠自觉传参必然漏，而漏掉的往往正是出问题时最需要串起来的那几行。

    挂在 handler 上而不是 logger 上：`Handler.handle()` 会在 `emit()` 之前执行 filter，
    这保证 formatter 取 `%(rid)s` 时该属性一定存在 —— 若挂在某个 logger 上，其他 logger
    产生的记录不会经过它，formatter 会直接抛 KeyError 把日志系统本身弄坏。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'rid'):
            record.rid = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line for cloud log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'timestamp': datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec='milliseconds'
            ),
            'rid': getattr(record, 'rid', NO_REQUEST_ID),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'source': f'{record.pathname}:{record.lineno}',
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def current_request_id() -> str:
    """当前请求的 request id；不在请求上下文里时返回 [NO_REQUEST_ID]。"""
    if _has_request_context is None or not _has_request_context():
        return NO_REQUEST_ID
    return getattr(_flask_g, 'request_id', None) or NO_REQUEST_ID


class LogManager:
    """日志管理器"""

    def __init__(self, log_dir: str = 'logs', log_level: str = 'INFO', log_format: str = 'text'):
        """
        初始化日志管理器

        Args:
            log_dir: 日志目录
            log_level: 日志级别
        """
        self.log_dir = log_dir
        self.log_level = LOG_LEVELS.get(log_level.upper(), logging.INFO)
        self.log_format = log_format if log_format in {'text', 'json'} else 'text'
        self._loggers = {}

        os.makedirs(log_dir, exist_ok=True)

        self._setup_root_logger()

    def _setup_root_logger(self):
        """配置根日志"""
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)

        # 清除现有处理器
        root_logger.handlers.clear()

        # 每个 handler 都要挂一份：filter 不会从 root 继承到 handler。
        rid_filter = RequestIdFilter()

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        # rid 排在最前面：容器日志是按行扫的，把关联键放在行首才能一眼看出哪几行属于
        # 同一次请求。
        console_format = logging.Formatter(
            '%(asctime)s [%(rid)s] [%(levelname)s] %(name)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(JsonFormatter() if self.log_format == 'json' else console_format)
        console_handler.addFilter(rid_filter)
        root_logger.addHandler(console_handler)

        # 文件处理器（所有日志）
        all_handler = RotatingFileHandler(
            os.path.join(self.log_dir, 'app.log'),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        all_handler.setLevel(self.log_level)
        file_format = logging.Formatter(
            '%(asctime)s [%(rid)s] [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        all_handler.setFormatter(JsonFormatter() if self.log_format == 'json' else file_format)
        all_handler.addFilter(rid_filter)
        root_logger.addHandler(all_handler)

        # 错误日志处理器
        error_handler = RotatingFileHandler(
            os.path.join(self.log_dir, 'error.log'),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter() if self.log_format == 'json' else file_format)
        error_handler.addFilter(rid_filter)
        root_logger.addHandler(error_handler)

    def get_logger(self, name: str) -> logging.Logger:
        """
        获取日志器

        Args:
            name: 日志器名称

        Returns:
            logging.Logger: 日志器实例
        """
        if name not in self._loggers:
            logger = logging.getLogger(name)
            logger.setLevel(self.log_level)
            self._loggers[name] = logger

        return self._loggers[name]

    def set_level(self, level: str):
        """
        设置日志级别

        Args:
            level: 日志级别
        """
        self.log_level = LOG_LEVELS.get(level.upper(), logging.INFO)
        logging.getLogger().setLevel(self.log_level)

        for logger in self._loggers.values():
            logger.setLevel(self.log_level)


# ==================== 全局实例 ====================

# 目录与级别取自 SFM_LOG_DIR / SFM_LOG_LEVEL。
from . import config as _config

log_manager = LogManager(
    log_dir=_config.log_dir(),
    log_level=_config.log_level(),
    log_format=_config.log_format(),
)


# ==================== 便捷函数 ====================

def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    return log_manager.get_logger(name)


def debug(msg: str, *args, **kwargs):
    """调试日志"""
    logging.debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs):
    """信息日志"""
    logging.info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs):
    """警告日志"""
    logging.warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs):
    """错误日志"""
    logging.error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs):
    """严重错误日志"""
    logging.critical(msg, *args, **kwargs)


def exception(msg: str, *args, **kwargs):
    """异常日志（包含堆栈）"""
    logging.exception(msg, *args, **kwargs)


# ==================== 装饰器 ====================

def log_function(logger_name: str = None, level: str = 'INFO'):
    """
    函数日志装饰器

    Args:
        logger_name: 日志器名称
        level: 日志级别
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            name = logger_name or func.__module__
            log = get_logger(name)
            log_level = LOG_LEVELS.get(level.upper(), logging.INFO)

            log.log(log_level, f"Calling {func.__name__}")

            try:
                result = func(*args, **kwargs)
                log.log(log_level, f"{func.__name__} completed successfully")
                return result
            except Exception as e:
                log.error(f"{func.__name__} failed: {str(e)}")
                raise

        return wrapper
    return decorator


# ==================== 分段计时 ====================

class StageTimer:
    """把一次慢请求拆成若干阶段，最后用一行日志交代每段花了多久。

    access 行的总耗时回答「慢不慢」，这一行回答「慢在哪」：63 秒可能花在模型上（该调
    prompt / 换模型），也可能花在库存查询或渲染上（完全不同的修法），只看总耗时分辨不出。

    不做成装饰器是因为阶段边界在函数**内部**（读库 → 拼 prompt → 打模型 → 校验 → 落库），
    装饰器只能包住整个函数；不引入 APM 是因为这里要的只是一行进容器日志的文本。

    用法：

        st = StageTimer(logger, '生成菜谱', meal='dinner')
        with st.stage('db'):
            ...
        with st.stage('llm'):
            ...
        st.done(plan_id=12)      # -> 生成菜谱 完成 总计=63345ms db=120ms llm=63100ms … meal=dinner plan_id=12

    即使中途抛异常也会记一行（`done` 放 finally，或用 `with StageTimer(...) as st`），
    否则最需要看耗时的那次（失败那次）恰好没有记录。
    """

    def __init__(self, log: logging.Logger, action: str, **context):
        self._log = log
        self._action = action
        self._context = context
        self._stages: list = []
        self._start = _time.monotonic()
        self._finished = False

    @contextmanager
    def stage(self, name: str):
        """计一个阶段。异常照常向上抛，但耗时仍会被记下 —— 失败那次的耗时同样有用。"""
        begin = _time.monotonic()
        try:
            yield
        finally:
            self._stages.append((name, int((_time.monotonic() - begin) * 1000)))

    def mark(self, name: str, elapsed_ms: int) -> None:
        """补记一个在别处已经量好的阶段（例如底层客户端自己算出的模型耗时）。"""
        self._stages.append((name, int(elapsed_ms)))

    def done(self, **extra) -> None:
        """输出汇总行。重复调用只记一次，方便放进 finally 又不担心与正常路径重复。"""
        if self._finished:
            return
        self._finished = True
        total = int((_time.monotonic() - self._start) * 1000)
        parts = ' '.join(f'{name}={ms}ms' for name, ms in self._stages)
        context = ' '.join(f'{k}={v}' for k, v in {**self._context, **extra}.items() if v != '')
        self._log.info('%s 完成：总计=%dms %s %s', self._action, total, parts, context)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # 失败路径也要有耗时行：一次 60 秒后失败的请求，正是最需要知道时间花在哪的那次。
        self.done(**({'结果': '失败'} if exc_type else {}))
        return False


# ==================== 测试 ====================

if __name__ == '__main__':
    print("Logger Module Test")
    print("=" * 50)

    # 测试日志
    log = get_logger('test')
    log.debug("This is debug")
    log.info("This is info")
    log.warning("This is warning")
    log.error("This is error")

    # 测试装饰器
    @log_function('test_decorator')
    def test_func():
        return "success"

    result = test_func()
    print(f"\nResult: {result}")

    log_dir = 'logs'
    if os.path.exists(log_dir):
        print("\nLog files:")
        for f in os.listdir(log_dir):
            path = os.path.join(log_dir, f)
            size = os.path.getsize(path)
            print(f"  {f}: {size} bytes")

    print("\nDone!")
