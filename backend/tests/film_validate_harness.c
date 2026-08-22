/*
 * 用【固件自己的校验器】验证后端生成的 .film —— 最权威的判定。
 *
 * 后端是第三个 .film 生产者（另两个是网页调试工具的 JS 与安卓 FilmEncoder.kt）。
 * 与 JS 交叉验证只能证明「三端彼此一致」；真正决定设备收不收的是固件里的
 * film_validate()，它有 7 类拒收分支，而拒收的表现是**保留旧画面** ——
 * 真机上看起来就是「上传成功、屏幕不动」，得接串口才能定位。
 *
 * film_validate.c 只 include 自己的头文件、只依赖标准 C，因此可以直接在宿主机编译，
 * 不需要 ESP-IDF、不需要烧录。这样「产物能否被设备接受」在提交前就有确定答案。
 *
 * 用法（由 tests/test_film_encoder_firmware.py 自动调用）：
 *   cc -I <film_service/inc> film_validate_harness.c <film_service/src/film_validate.c> -lm -o harness
 *   ./harness <file.film> [<file.film> ...]
 * 输出每个文件一行：`OK <path>` 或 `FAIL <path> <FILM_ERR_xxx>`；
 * 只要有一个文件未通过，退出码非 0。
 */
#include <stdio.h>
#include <stdlib.h>

#include "film_validate.h"

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "用法: %s <file.film> [...]\n", argv[0]);
        return 2;
    }

    int failures = 0;

    for (int i = 1; i < argc; i++) {
        const char *path = argv[i];

        FILE *fp = fopen(path, "rb");
        if (fp == NULL) {
            printf("FAIL %s 无法打开文件\n", path);
            failures++;
            continue;
        }

        if (fseek(fp, 0, SEEK_END) != 0) {
            printf("FAIL %s fseek 失败\n", path);
            fclose(fp);
            failures++;
            continue;
        }
        long size = ftell(fp);
        rewind(fp);

        if (size <= 0) {
            printf("FAIL %s 文件为空\n", path);
            fclose(fp);
            failures++;
            continue;
        }

        unsigned char *buf = (unsigned char *)malloc((size_t)size);
        if (buf == NULL) {
            printf("FAIL %s 内存不足\n", path);
            fclose(fp);
            failures++;
            continue;
        }

        size_t read = fread(buf, 1, (size_t)size, fp);
        fclose(fp);
        if (read != (size_t)size) {
            printf("FAIL %s 读取不完整\n", path);
            free(buf);
            failures++;
            continue;
        }

        /* 调用固件里那个一模一样的校验函数 */
        film_err_t err = film_validate(buf, read);
        if (err == FILM_OK) {
            printf("OK %s\n", path);
        } else {
            printf("FAIL %s %s\n", path, film_err_str(err));
            failures++;
        }

        free(buf);
    }

    return failures == 0 ? 0 : 1;
}
