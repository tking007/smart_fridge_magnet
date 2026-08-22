# SFM 浏览器调试工具

`sfm-devtool` 是零依赖的单文件 Web Bluetooth 工具，用于智能冰箱贴硬件 bring-up、BLE 命令、
文件直传、设备参数和 BLE OTA。它不实现菜单、库存或 AI；这些职责属于后端与 Android。

## 启动

Web Bluetooth 需要 localhost 或 HTTPS，不能直接用 `file://`：

```bash
python3 -m http.server 8000 --directory tools/sfm-devtool
```

使用桌面或 Android 的 Chrome/Edge 打开 `http://localhost:8000`。设备深睡时不广播，按 FN
打开 60 秒窗口，长按 FN 可延长到 180 秒。

## 功能

| 页签 | 用途 |
| --- | --- |
| 图片转 `.film` | 缩放、六色量化、预览、下载和生成后端上传命令 |
| 测试图案 | 色条、纯色、棋盘和方向图，用于屏幕 bring-up |
| 蓝牙传输 | 单张/多张直传、文件列表、显示、删除和清空 |
| 设备控制 | 状态、Wi-Fi 配网、同步、休眠、唤醒表、轮询和置顶 |
| 固件 OTA | 通过 BLE 把 `.bin` 写入备用分区并切换重启 |
| 任意命令 | 调试新 opcode 或查看原始响应 |

后端菜单主链路仍是 HTTPS manifest。完整菜谱必须通过后端发布为一个版本、四张画面；本工具
的单张上传命令只适合验证 TLS、鉴权、版本递增和显示驱动。

## `.film` 契约

- 尺寸 `792 x 528`，4 bit/像素，32 字节头，总长 `209120` 字节。
- 调色板索引为黑、白、黄、红、蓝、绿。
- 头部调色板字节为 `00 FF FC E0 03 1C`，不是连续索引值。
- Android 只解码预览，不自行生成文件。

工具的 JavaScript 编码器与 `backend/src/film_encoder.py` 通过
`backend/tests/test_film_encoder.py` 做逐字节交叉验证。修改 `index.html` 中第一个内联
`<script>` 的 `PALETTE`、`D`、`HEADER_SIZE`、`rgbToHsl`、`rgbToLab`、`labDist`、
`paletteHsl`、`findClosestIndex` 或 `buildFilm` 时，必须重跑该测试。

## BLE 要点

- Service `0x2000`，写特征 `0x2001`，通知特征 `0x2001`/`0x2002`/`0x2003`。
- 帧为 `0x55 | opcode | LEN | DATA | SUM`，一次 BLE 写入必须是一整帧。
- `LEN` 为 1 字节，数据最大 255 字节；文件数据默认按协商 MTU选择安全分片。
- `FILE_NAME` 不带结尾 NUL；`FILE_STOP` 成功后不要再猜测文件 id 补发显示命令。
- 多张直传使用静默接收，全部完成后再按时段显示，避免每张都触发慢速 EPD 刷新。

完整 opcode 和载荷见
[../../embedded/smart_fridge_magnet/docs/ble_protocol.md](../../embedded/smart_fridge_magnet/docs/ble_protocol.md)。

## Wi-Fi 联调

1. 在后端登记 `device_token`。
2. 通过“设备控制”写入 Wi-Fi、manifest URL、device ID 和相同的 device Token。
3. 触发 `SYNC_NOW`。
4. 从串口确认 HTTPS、长度、SHA-256、整套提交和 EPD 完成状态。

固件强制 HTTPS；不要为了局域网方便关闭证书校验。证书配置见
[../../embedded/smart_fridge_magnet/certs/README.md](../../embedded/smart_fridge_magnet/certs/README.md)。
