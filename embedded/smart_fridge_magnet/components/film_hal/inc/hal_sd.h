#ifndef __HAL_SD_H__
#define __HAL_SD_H__


/*********************************************************************
 * INCLUDES
 */


/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
 #define MOUNT_POINT             "/sdcard"
 #define SD_MOUNT                (1)
 #define SD_UNMOUNT              (0)

/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */


/*********************************************************************
 * GLOBAL FUNCTIONS
 */
/**
 * @brief 挂载 SDMMC 存储到 MOUNT_POINT（板载 SD NAND，挂载失败时自动格式化）。
 */
extern void hal_sd_init(void);

/**
 * @brief 返回挂载状态：SD_MOUNT / SD_UNMOUNT。
 */
extern int hal_sd_get_status(void);

/**
 * @brief 卸载文件系统并把 SDMMC 引脚置为低功耗输入。深睡前调用。
 */
extern void hal_sd_deinit(void);

/**
 * @brief 格式化SD卡
 *
 * 对SD卡执行FAT格式化操作。需在SD卡已挂载时调用。
 *
 * @return int 0:成功, -1:失败
 */
extern int hal_sd_format(void);

#ifdef __cplusplus
}
#endif

#endif /* __HAL_SD_H__ */
