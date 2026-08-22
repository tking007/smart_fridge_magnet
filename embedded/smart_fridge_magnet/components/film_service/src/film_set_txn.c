#include <stddef.h>
#include <string.h>

#include "film_set_txn.h"

bool film_set_name_is_safe(const char *name)
{
    if(name == NULL)
    {
        return false;
    }

    size_t len = 0;
    while(name[len] != '\0')
    {
        unsigned char ch = (unsigned char)name[len];
        if(!((ch >= 'a' && ch <= 'z') ||
             (ch >= 'A' && ch <= 'Z') ||
             (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.'))
        {
            return false;
        }
        len++;
        if(len > FILM_SET_NAME_MAX)
        {
            return false;
        }
    }

    static const char ext[] = ".film";
    return len > sizeof(ext) - 1u &&
           strstr(name, "..") == NULL &&
           strcmp(name + len - (sizeof(ext) - 1u), ext) == 0;
}

film_set_recovery_action_t film_set_recovery_action(bool commit_complete,
                                                    bool had_original,
                                                    bool backup_exists)
{
    if(commit_complete)
    {
        return FILM_SET_RECOVERY_KEEP;
    }
    if(had_original)
    {
        return backup_exists ? FILM_SET_RECOVERY_RESTORE_BACKUP
                             : FILM_SET_RECOVERY_KEEP;
    }
    return FILM_SET_RECOVERY_REMOVE_TARGET;
}
