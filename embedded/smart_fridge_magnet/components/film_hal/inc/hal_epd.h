#ifndef __HAL_EPD_H__
#define __HAL_EPD_H__

/*********************************************************************
 * INCLUDES
 */
#include "esp_system.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
#define EPD_TAG                        "HAL_EPD"

// IO settings - SE0368-C smart fridge magnet
#define EPD_SCK_PIN                    GPIO_NUM_48  // SCK
#define EPD_SDIN_PIN                   GPIO_NUM_47  // SDIN (half-duplex: MOSI+MISO)
#define EPD_BUSY_PIN                   GPIO_NUM_11  // BUSY
#define EPD_RST_PIN                    GPIO_NUM_12  // RES
#define EPD_DC_PIN                     GPIO_NUM_13  // DC
#define EPD_CS_PIN                     GPIO_NUM_14  // CS

#define EPD_SPI_HOST                   SPI2_HOST

// GPIO control macros (CS/DC/RST/BUSY managed manually)
#define isEPD_W21_BUSY                 gpio_get_level(EPD_BUSY_PIN)
#define EPD_W21_RST_0                  gpio_set_level(EPD_RST_PIN, 0)
#define EPD_W21_RST_1                  gpio_set_level(EPD_RST_PIN, 1)
#define EPD_W21_DC_0                   gpio_set_level(EPD_DC_PIN, 0)
#define EPD_W21_DC_1                   gpio_set_level(EPD_DC_PIN, 1)
#define EPD_W21_CS_0                   gpio_set_level(EPD_CS_PIN, 0)
#define EPD_W21_CS_1                   gpio_set_level(EPD_CS_PIN, 1)

// SE0368-C commands
#define PSR                            0x00  // Panel setting
#define PWR                            0x01  // Power setting
#define POF                            0x02  // Power off
#define POFS                           0x03  // Power off sequence
#define PON                            0x04  // Power on
#define BTST1                          0x05  // Booster soft start 1
#define BTST2                          0x06  // Booster soft start 2
#define DSLP                           0x07  // Deep sleep
#define BTST3                          0x08  // Booster soft start 3
#define DTM                            0x10  // Data start transmission
#define REF                            0x17  // Display refresh
#define PLL                            0x30  // PLL control
#define TSE                            0x40  // Temperature sensor read
#define TSD                            0x41  // Temperature sensor data
#define CDI                            0x50  // CDI
#define RES2                           0x62  // Resolution setting 2
#define RSET                           0x83  // Resolution extended setting
#define WFT                            0xE0  // Waveform temperature
#define VCOM2                          0xE1  // VCOM2
#define PWS                            0xE3  // Power saving
#define WFD                            0xE6  // Waveform temperature data
#define VCOM                           0xE7  // VCOM
#define BOD                            0xE9  // Border

// 4-bit pixel color index (input format, 2 pixels per byte)
#define EPD_COLOR_BLACK                0x00
#define EPD_COLOR_WHITE                0x11
#define EPD_COLOR_GREEN                0x66
#define EPD_COLOR_BLUE                 0x55
#define EPD_COLOR_RED                  0x33
#define EPD_COLOR_YELLOW               0x22

// Screen resolution - SE0368-C smart fridge magnet
// 792x528、4bit/px，整幅 .film 为 209120 字节（32 字节头 + 209088 像素）。
// 契约详情见 docs/contracts/README.md。
#define EPD_WIDTH                      792
#define EPD_HEIGHT                     528

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
 * @brief 初始化电子纸硬件：控制引脚方向与半双工 3-wire SPI 总线。
 *
 * 使用其它 hal_epd_* 接口前必须先调用。重复调用直接返回 ESP_OK。
 */
esp_err_t hal_epd_init(void);

/**
 * @brief 释放 SPI 设备并把面板引脚置为输入。深睡前调用。
 *
 * 刻意不释放 SPI 总线（见实现中的说明）。
 */
esp_err_t hal_epd_deinit(void);

/**
 * @brief 下发 SE0368-C 上电初始化序列（复位 + 面板/电源/分辨率等寄存器）。
 *
 * 每次刷屏前需调用一次；面板 DSLP 之后必须重新走本序列。
 */
esp_err_t hal_epd_display_init(void);

/** @brief 全屏刷成纯白。 */
esp_err_t hal_epd_display_white(void);

/** @brief 全屏刷成纯黑。 */
esp_err_t hal_epd_display_black(void);

/** @brief 全屏刷成纯黄。 */
esp_err_t hal_epd_display_yellow(void);

/** @brief 全屏刷成纯红。 */
esp_err_t hal_epd_display_red(void);

/** @brief 全屏刷成纯蓝。 */
esp_err_t hal_epd_display_blue(void);

/** @brief 全屏刷成纯绿。 */
esp_err_t hal_epd_display_green(void);

/**
 * @brief 显示裸像素数据（无文件头）。
 *
 * @param picData 4bit/px、每行 EPD_WIDTH/2 字节的整屏数据
 */
esp_err_t hal_epd_display_pic(const unsigned char* picData);

/**
 * @brief 显示 .film 数据（32 字节文件头 + 4bit/px 像素，见 docs/contracts/README.md）。
 *
 * 文件头的分辨率、颜色数与像素长度与本机规格不符时返回 ESP_ERR_INVALID_SIZE。
 *
 * @param filmData .film 文件数据指针（含文件头）
 */
esp_err_t hal_epd_display_film(const unsigned char* filmData);

/**
 * @brief 面板进入深度睡眠（DSLP）。唤醒后须重新 hal_epd_display_init()。
 */
esp_err_t hal_epd_sleep(void);

/**
 * @brief 面板断电：等价于 sleep 后再留出内部掉电时间。
 */
esp_err_t hal_epd_pwroff(void);

#ifdef __cplusplus
}
#endif

#endif /* __HAL_EPD_H__ */
