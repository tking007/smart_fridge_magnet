# 后端分区规范（AGENTS.md）

> 继承根目录 `../AGENTS.md`，本文件只描述后端特有规则。

## 结构

- 源码位于 `src/`，新增逻辑进入对应职责模块，避免堆入 `app.py`。
- `menu_render.py` 与 `film_encoder.py` 是唯一权威渲染实现；输出变化时递增渲染版本号。

## 菜谱与库存

- 菜谱主料只能来自库存，基础调料除外；库存不足时减少菜品。
- `prompts.py` 与 `agent.py` 的库存、规避和校验规则必须保持一致。

## 外部依赖

- AI 服务统一走 OpenAI 兼容协议，地址、模型和密钥均由环境变量配置。
- 条码等第三方数据源优先返回可验证数据，模型只作为兜底。

## 安全与部署

- 默认绑定 `127.0.0.1` 且关闭 debug；非回环绑定缺少 Token 或启用 debug 时必须拒绝启动。
- App 与设备使用独立凭据；CORS 采用显式白名单；生产部署使用 Gunicorn。

## 验证

- 运行 `python -m pytest` 和 `ruff check src tests scripts`。
- 部署配置自检使用 `scripts/verify_cloud_link.sh`。
