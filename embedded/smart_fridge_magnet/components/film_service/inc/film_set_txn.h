#ifndef __FILM_SET_TXN_H__
#define __FILM_SET_TXN_H__

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FILM_SET_MAX_ITEMS       (4u)
#define FILM_SET_NAME_MAX        (31u)

typedef enum {
    FILM_SET_RECOVERY_KEEP = 0,
    FILM_SET_RECOVERY_RESTORE_BACKUP,
    FILM_SET_RECOVERY_REMOVE_TARGET,
} film_set_recovery_action_t;

/* 与 manifest 条目名使用同一落盘白名单：1..31 个 ASCII 字母、数字、'-'、'_'、'.'，
 * 禁止路径片段且必须以 .film 结尾。 */
bool film_set_name_is_safe(const char *name);

/* 准备阶段掉电时恢复旧套；完成标记落盘后则保留新套。 */
film_set_recovery_action_t film_set_recovery_action(bool commit_complete,
                                                    bool had_original,
                                                    bool backup_exists);

#ifdef __cplusplus
}
#endif

#endif /* __FILM_SET_TXN_H__ */
