# 贡献指南

简体中文 | [English](CONTRIBUTING.en.md)

欢迎通过范围明确、改动集中并带有相应验证的方式参与项目。

## 开发规则

- 渲染实现只允许存在于 `backend/src/menu_render.py` 和 `film_encoder.py`；Android 与固件
  不得增加第二套 `.film` 编码器。
- 三端必须保持 `792 x 528`、六色、`209120` 字节格式一致。
- 禁止加入密钥、本地数据库、日志、生成媒体、构建产物或私有部署信息。
- 修改源自 FrameFilm 的文件时，必须保留上游许可证和署名。

## 验证

先运行覆盖改动的最小命令；修改共享契约时再扩大验证范围：

```bash
cd backend && .venv/bin/python -m pytest && .venv/bin/ruff check src
cd android && ./gradlew testDebugUnitTest
cd embedded/smart_fridge_magnet && bash test/host/run_host_tests.sh
```

固件改动涉及 BLE、Wi-Fi、电子纸、电源或唤醒行为时，还必须完成 ESP-IDF 构建和对应的
真机验证。

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，例如
`fix:`、`feat:`、`docs:` 或 `refactor:`。Pull Request 应保持范围集中，说明可观察行为，并
提供不含凭据、私有地址或内部信息的验证结果。
