"""`.film` 编码器验证 —— 结构合法性 + 与网页调试工具 JS 实现的逐字节交叉验证。

交叉验证的必要性：`.film` 有两个实现 —— 后端 src/film_encoder.py 与 tools/sfm-devtool 的
JS。两者产出不一致时的表现极隐蔽：固件拒收时**保留旧画面**，真机上就是「上传成功、屏幕
不动」，得接串口才能看出是 FILM_ERR_* 里的哪一类。

因此这里做两件事：
  1. 结构自检：逐条对齐固件 film_validate.c 的拒收判定；
  2. 交叉验证：同一批输入，Python 与 JS 两侧输出的 SHA-256 必须完全相同。
     JS 侧的 quantize/buildFilm 是纯函数，能从 index.html 里抽出来在 node 里跑，
     不需要浏览器和 canvas。node 不可用时该项自动跳过（不让它变成 CI 的硬依赖）。

用法：python tests/test_film_encoder.py
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import film_encoder as fe   # noqa: E402

DEVTOOL_HTML = Path(__file__).resolve().parents[2] / 'tools' / 'sfm-devtool' / 'index.html'


# --------------------------------------------------------------------- 输入样本
def build_samples() -> dict:
    """构造覆盖不同风险面的输入样本。

    每个样本都是扁平 RGB 字节流（792*528*3）。挑选依据：
      - six_solid  : 六个调色板色本身，量化必须原样映射回对应索引（最基本的正确性）
      - color_bars : 竖向色带，能暴露 row-major / 高低半字节写反（画面会错位）
      - gray_ramp  : 灰阶，专打「低饱和判黑白」与 Lab 中性回退这条分支
      - noise      : 随机噪声，覆盖大量边界色 —— 浮点实现的细微差异最容易在这里暴露
      - antialias  : 模拟文字抗锯齿产生的中间色（白底上的灰边），这是菜单渲染的真实主力场景
    """
    width, height = fe.FILM_WIDTH, fe.FILM_HEIGHT
    samples = {}

    # 六色横向分块
    data = bytearray()
    for y in range(height):
        data += bytes(fe.PALETTE_RGB[min(5, y * 6 // height)]) * width
    samples['six_solid'] = bytes(data)

    # 竖向色带
    row = bytearray()
    for x in range(width):
        row += bytes(fe.PALETTE_RGB[min(5, x * 6 // width)])
    samples['color_bars'] = bytes(row) * height

    # 灰阶
    data = bytearray()
    for y in range(height):
        level = y * 256 // height
        data += bytes((level, level, level)) * width
    samples['gray_ramp'] = bytes(data)

    # 随机噪声（固定种子，保证两侧输入完全一致）
    rng = random.Random(20260727)
    samples['noise'] = bytes(rng.randrange(256) for _ in range(width * height * 3))

    # 抗锯齿灰边：白底 + 不同深浅的灰，模拟 PIL 渲染中文文字的边缘像素
    data = bytearray()
    for y in range(height):
        edge = 255 - (y % 8) * 32
        data += bytes((edge, edge, edge)) * width
    samples['antialias'] = bytes(data)

    return samples


# ------------------------------------------------------------------- 结构自检
def check_structure():
    """逐条对齐固件 film_validate.c 的拒收判定。"""
    assert fe.FILM_PIXEL_BYTES == fe.FILM_WIDTH * fe.FILM_HEIGHT // 2
    assert fe.FILM_TOTAL_SIZE == fe.FILM_HEADER_SIZE + fe.FILM_PIXEL_BYTES

    header = fe.build_header()
    assert len(header) == 32
    assert int.from_bytes(header[0:4], 'little') == 209088, '偏移 0 必须是像素长度而非文件总长'
    assert int.from_bytes(header[4:6], 'little') == 792
    assert int.from_bytes(header[6:8], 'little') == 528
    assert header[8] == 6
    assert header[16:22] == bytes([0x00, 0xFF, 0xFC, 0xE0, 0x03, 0x1C]), \
        'colorTable 必须写真实设备色值，写索引会被 FILM_ERR_COLOR_TABLE 拒收'
    assert header[9:16] == bytes(7) and header[22:32] == bytes(10), '保留字节必须为 0'

    # 六色纯色必须原样映射
    for index, (_name, rgb, _code) in enumerate(fe.PALETTE):
        assert fe.rgb_to_index(*rgb) == index, f'{rgb} 应映射到索引 {index}'

    # 高低半字节顺序：第一个像素占高 4 位
    indices = bytearray(fe.FILM_WIDTH * fe.FILM_HEIGHT)
    indices[0], indices[1] = 3, 5
    film = fe.build_film(bytes(indices))
    assert film[32] == 0x35, '偶数像素必须写高 4 位，写反会让画面按 2 像素左右互换'

    # 所有产物的每个半字节都必须 < 6，否则固件 FILM_ERR_PALETTE_INDEX 拒收整份文件
    for name, rgb_bytes in build_samples().items():
        film = fe.encode_rgb_image(rgb_bytes)
        assert len(film) == fe.FILM_TOTAL_SIZE, f'{name}: 长度 {len(film)}'
        for byte_value in set(film[32:]):
            assert (byte_value >> 4) < 6 and (byte_value & 0x0F) < 6, \
                f'{name}: 字节 0x{byte_value:02X} 含越界索引'
    print('  结构自检通过（含 5 个样本的索引越界扫描）')


# ------------------------------------------------------------- JS 交叉验证
_JS_HARNESS = r'''
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');

// 从 index.html 里抽出量化与组包相关的纯函数。它们不依赖 DOM / canvas，
// 因此可以直接在 node 里求值，用来和 Python 实现比对。
const names = ['PALETTE', 'D', 'HEADER_SIZE', 'rgbToHsl', 'rgbToLab', 'labDist',
               'paletteHsl', 'findClosestIndex', 'buildFilm'];
const script = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/)[1];

// 只保留需要的顶层声明，避免整段脚本因引用 document 而抛错
let picked = '';
for (const name of names) {
  const re = new RegExp('(?:^|\\n)(?:const|let|var|function)\\s+' + name + '\\b');
  const m = re.exec(script);
  if (!m) throw new Error('在 index.html 中找不到 ' + name);
  let start = m.index;
  if (script[start] === '\n') start += 1;
  // 从声明处向前扫到配平的位置：靠括号与花括号计数找到该声明的结束点
  let i = start, depth = 0, inStr = null, done = -1;
  for (; i < script.length; i++) {
    const c = script[i];
    if (inStr) {
      if (c === '\\') { i++; continue; }
      if (c === inStr) inStr = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') { inStr = c; continue; }
    if (c === '(' || c === '{' || c === '[') depth++;
    else if (c === ')' || c === '}' || c === ']') depth--;
    else if ((c === ';' || c === '\n') && depth === 0 && i > start + name.length) {
      done = i + 1; break;
    }
  }
  picked += script.slice(start, done > 0 ? done : script.length) + '\n';
}

const factory = new Function(picked + `
  return function (rgbBuf) {
    const w = D.w, h = D.h;
    const idx = new Uint8Array(w * h);
    for (let i = 0, p = 0; i < w * h; i++, p += 3) {
      idx[i] = findClosestIndex(rgbBuf[p], rgbBuf[p + 1], rgbBuf[p + 2]);
    }
    return buildFilm(idx);
  };
`);
const encode = factory();

const crypto = require('crypto');
const out = {};
const manifest = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
for (const [name, file] of Object.entries(manifest)) {
  const rgb = fs.readFileSync(file);
  const film = encode(rgb);
  out[name] = {
    sha256: crypto.createHash('sha256').update(Buffer.from(film)).digest('hex'),
    size: film.length,
  };
}
process.stdout.write(JSON.stringify(out));
'''


def check_cross_validation(samples: dict) -> bool:
    """同一批输入，Python 与 JS 两侧的 .film 必须逐字节相同（比对 SHA-256）。"""
    if shutil.which('node') is None:
        print('  跳过 JS 交叉验证：未找到 node')
        return True
    if not DEVTOOL_HTML.exists():
        print(f'  跳过 JS 交叉验证：{DEVTOOL_HTML} 不存在')
        return True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manifest = {}
        for name, rgb_bytes in samples.items():
            sample_file = tmp_path / f'{name}.rgb'
            sample_file.write_bytes(rgb_bytes)
            manifest[name] = str(sample_file)
        manifest_file = tmp_path / 'manifest.json'
        manifest_file.write_text(json.dumps(manifest))

        harness = tmp_path / 'harness.js'
        harness.write_text(_JS_HARNESS)

        proc = subprocess.run(
            ['node', str(harness), str(DEVTOOL_HTML), str(manifest_file)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            print('  JS harness 执行失败：')
            print('   ', (proc.stderr or proc.stdout).strip().splitlines()[-3:])
            return False

        js_result = json.loads(proc.stdout)

    ok = True
    for name, rgb_bytes in samples.items():
        py_film = fe.encode_rgb_image(rgb_bytes)
        py_sha = fe.sha256_hex(py_film)
        js = js_result.get(name)
        if js is None:
            print(f'  {name}: JS 侧无结果')
            ok = False
            continue
        same = (js['sha256'] == py_sha and js['size'] == len(py_film))
        print(f"  {name:<10} py={py_sha[:16]}  js={js['sha256'][:16]}  "
              f"{'一致' if same else '不一致 !!'}")
        if not same:
            ok = False
    return ok


def main():
    print('=== 结构自检（对齐固件 film_validate.c）===')
    check_structure()

    print('=== 与网页调试工具 JS 实现逐字节交叉验证 ===')
    samples = build_samples()
    if not check_cross_validation(samples):
        raise SystemExit('交叉验证失败：后端与调试工具的 .film 产物不一致')


if __name__ == '__main__':
    main()
    print('film encoder self-check passed')


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。

    为什么需要它：本模块是**脚本式**的 —— 编排在 `main()` 里，断言靠 `check()`
    累积。pytest 只收集 `test_` 开头的函数，而本模块里没有一个零参数的 `test_`，
    于是 pytest **一个用例都收集不到，整个模块被静默跳过**：测试报告是绿的，
    这些覆盖却从未运行过。这比失败更糟，因为它不留痕迹。

    这里不复述各条断言，只把 `main()` 的成败翻译成 pytest 的成败：
    断言逻辑只有一份，脚本直跑（`python tests/test_film_encoder.py`）与 pytest 跑的是同一套。
    """
    # 这一族的 main() 不返回值，失败时直接 raise SystemExit ——
    # 让它自然抛出即可，pytest 会如实记成失败并带上原始信息。
    main()
