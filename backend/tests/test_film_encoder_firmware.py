"""用固件自己的 film_validate() 验证后端生成的 `.film`（无需烧录）。

与 test_film_encoder.py 的分工：
  - test_film_encoder.py      证明「后端与调试工具逐字节一致」
  - 本文件                     证明「产物能被固件接受」——这是设备收不收的最终判据

固件 components/film_service/src/film_validate.c 只 include 自己的头文件、只依赖标准 C，
所以能直接在宿主机编译成 harness 调用，把「上传成功但屏幕不动」这类问题的答案提前到提交前。

同时做**反面验证**：逐条注入违规（改坏总长/尺寸/色数/colorTable/像素索引），确认校验器
确实会拒收对应项。只验证正例不够 —— harness 没跑起来或校验器被 stub 掉时正例一样会通过。

用法：python tests/test_film_encoder_firmware.py
（找不到 C 编译器时自动跳过，不作为 CI 硬依赖）
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import film_encoder as fe   # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FILM_SERVICE = REPO_ROOT / 'embedded' / 'smart_fridge_magnet' / 'components' / 'film_service'
VALIDATE_SRC = FILM_SERVICE / 'src' / 'film_validate.c'
VALIDATE_INC = FILM_SERVICE / 'inc'
HARNESS_SRC = Path(__file__).resolve().parent / 'film_validate_harness.c'


def find_compiler():
    for candidate in ('cc', 'gcc', 'clang'):
        if shutil.which(candidate):
            return candidate
    return None


def build_harness(compiler: str, workdir: Path) -> Path:
    binary = workdir / 'film_validate_harness'
    cmd = [
        compiler,
        '-std=c11', '-O1', '-Wall',
        f'-I{VALIDATE_INC}',
        str(HARNESS_SRC),
        str(VALIDATE_SRC),
        '-lm',
        '-o', str(binary),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit('harness 编译失败：\n' + (proc.stderr or proc.stdout))
    return binary


def run_harness(binary: Path, films: dict, workdir: Path) -> dict:
    """把若干 .film 写盘并交给 harness，返回 {name: (passed, detail)}。"""
    paths = {}
    for name, data in films.items():
        path = workdir / f'{name}.film'
        path.write_bytes(data)
        paths[name] = path

    proc = subprocess.run(
        [str(binary), *[str(p) for p in paths.values()]],
        capture_output=True, text=True,
    )

    results = {}
    for line in proc.stdout.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[1]
        detail = parts[2] if len(parts) > 2 else ''
        for name, candidate in paths.items():
            if str(candidate) == path:
                results[name] = (status == 'OK', detail)
    return results


def make_corruptions(valid_film: bytes) -> dict:
    """逐条注入违规，每项对应 film_validate.c 的一个拒收分支。"""
    def mutate(offset: int, value: int) -> bytes:
        data = bytearray(valid_film)
        data[offset] = value
        return bytes(data)

    return {
        # 总长 != 209120
        'bad_total_len': (valid_film[:-1], 'FILM_ERR_TOTAL_LEN'),
        # 头部声明的像素长度不对
        'bad_pixel_len': (mutate(0, 0x00), 'FILM_ERR_PIXEL_LEN'),
        # 宽度不是 792
        'bad_width': (mutate(4, 0x00), 'FILM_ERR_DIMS'),
        # 颜色数不是 6
        'bad_color_count': (mutate(8, 5), 'FILM_ERR_COLOR_COUNT'),
        # colorTable 写成索引而非真实设备色值 —— 安卓当年那个白屏就是这一类
        'bad_color_table': (mutate(0x10, 0x01), 'FILM_ERR_COLOR_TABLE'),
        # 像素半字节出现 >= 6 的越界索引
        'bad_palette_index': (mutate(32, 0x6A), 'FILM_ERR_PALETTE_INDEX'),
    }


def main():
    compiler = find_compiler()
    if compiler is None:
        print('跳过：未找到 C 编译器（cc/gcc/clang）')
        return
    if not VALIDATE_SRC.exists():
        print(f'跳过：{VALIDATE_SRC} 不存在')
        return

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        binary = build_harness(compiler, workdir)
        print(f'harness 已编译（{compiler}，直接链接固件的 film_validate.c）')

        # ---- 正面：后端产物必须全部被固件接受 ----
        print('=== 正面验证：后端生成的 .film 应被固件接受 ===')
        from test_film_encoder import build_samples
        films = {
            name: fe.encode_rgb_image(rgb)
            for name, rgb in build_samples().items()
        }
        results = run_harness(binary, films, workdir)
        for name in films:
            passed, detail = results.get(name, (False, '无结果'))
            print(f"  {name:<10} {'FILM_OK' if passed else 'FAIL ' + detail}")
            if not passed:
                raise SystemExit(f'{name} 未通过固件校验：{detail}')

        # ---- 反面：注入违规必须被逐条拒收 ----
        print('=== 反面验证：注入违规应被固件逐条拒收 ===')
        corruptions = make_corruptions(films['color_bars'])
        bad_films = {name: data for name, (data, _) in corruptions.items()}
        bad_results = run_harness(binary, bad_films, workdir)
        for name, (_data, expected) in corruptions.items():
            passed, detail = bad_results.get(name, (True, '无结果'))
            rejected = (not passed) and expected in detail
            print(f"  {name:<18} -> {detail or 'FILM_OK'}  "
                  f"{'符合预期' if rejected else '!! 期望 ' + expected}")
            if not rejected:
                raise SystemExit(
                    f'{name} 未被按预期拒收（期望 {expected}，实际 {detail or "FILM_OK"}）'
                )


if __name__ == '__main__':
    main()
    print('firmware validator cross-check passed')


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。

    为什么需要它：本模块是**脚本式**的 —— 编排在 `main()` 里，断言靠 `check()`
    累积。pytest 只收集 `test_` 开头的函数，而本模块里没有一个零参数的 `test_`，
    于是 pytest **一个用例都收集不到，整个模块被静默跳过**：测试报告是绿的，
    这些覆盖却从未运行过。这比失败更糟，因为它不留痕迹。

    这里不复述各条断言，只把 `main()` 的成败翻译成 pytest 的成败：
    断言逻辑只有一份，脚本直跑（`python tests/test_film_encoder_firmware.py`）与 pytest 跑的是同一套。
    """
    # 这一族的 main() 不返回值，失败时直接 raise SystemExit ——
    # 让它自然抛出即可，pytest 会如实记成失败并带上原始信息。
    main()
