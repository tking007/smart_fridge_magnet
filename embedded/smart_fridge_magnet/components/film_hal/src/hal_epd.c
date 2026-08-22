/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm frame_film_pro).
 *
 * FileName : /film_hal/src/hal_epd.c
 * Author: Kiritro  Version: v0.1  Date: 2026/3/31
 * Description: SE0368-C 6-color EPD driver (smart fridge magnet, hardware SPI half-duplex)
 *   792x528 六色面板；输入 4bit/px 的整屏或 .film 数据，按 color_map/color_map1
 *   拆成两个 2bpp pass 下发，每 pass 前按面板温度选波形档位。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include "esp_timer.h"

#include "hal_epd.h"
#include "sys_log.h"

/*********************************************************************
 * MACROS
 */
// 4bpp input → 2bpp output conversion
#define EPD_INPUT_LINE      (EPD_WIDTH / 2)   // 396 bytes per line
#define EPD_OUTPUT_LINE     (EPD_WIDTH / 4)   // 198 bytes per line

#define FILM_HEADER_SIZE                  (32)
#define FILM_COLOR_TABLE_SIZE             (16)
#define FILM_OFFSET_FILESIZE              (0x00)
#define FILM_OFFSET_SCREENWIDTH           (0x04)
#define FILM_OFFSET_SCREENHEIGHT          (0x06)
#define FILM_OFFSET_COLORCOUNT            (0x08)
#define FILM_OFFSET_RESERVED              (0x09)
#define FILM_OFFSET_COLORTABLE            (0x10)

// Pack 4 identical 2-bit values into one byte
#define PACK4(v)  (((v) << 6) | ((v) << 4) | ((v) << 2) | (v))
#define EPD_BUSY_TIMEOUT_MS             (40000)

/* 深睡前发 DSLP 时的 BUSY 等待上限，刻意远小于 EPD_BUSY_TIMEOUT_MS。
 *
 * 面板一旦进了 DSLP，BUSY 就恒为「忙」，再按 40 秒去等就是白等一整个超时，而这段时间
 * 是满功耗清醒（实测每次深睡前多醒 40 秒）。DSLP 本身只是双保险，真正保证面板不耗电的
 * 是 hal_pwr 的外设断电，所以面板状态异常时不值得为它多醒几十秒。 */
#define EPD_SHUTDOWN_BUSY_TIMEOUT_MS    (2000)
#define EPD_TRY(expr) do { esp_err_t epd_err_ = (expr); if(epd_err_ != ESP_OK) return epd_err_; } while(0)

/*********************************************************************
* TYPEDEFS
*/
typedef struct
{
    uint32_t fileSize;
    uint16_t screenWidth;
    uint16_t screenHeight;
    uint8_t  colorCount;
    uint8_t  reserved[7];
    uint8_t  colorTable[FILM_COLOR_TABLE_SIZE];
} __attribute__((packed)) FilmHeader;

/*********************************************************************
 * CONSTANTS
 */
// 4-bit color index → device 4-bit LUT (for film format playback)
static const unsigned char color_lut[256] =
{
    [0x00] = 0x00, // Black
    [0xFF] = 0x01, // White
    [0xFC] = 0x02, // Yellow
    [0xE0] = 0x03, // Red
    [0x03] = 0x05, // Blue
    [0x1C] = 0x06, // Green
};

// SE0368-C 2-bit color maps for dual-pass display (from reference driver)
static const uint8_t color_map[16]  = {1, 1, 2, 3, 0, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1};
static const uint8_t color_map1[16] = {0, 1, 1, 3, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};

/* 双 pass 的耗时日志阶段名，索引 0/1 对应 pass1/pass2。 */
static const struct
{
    const char *total;
    const char *ready;
    const char *refresh;
} pass_stage[2] = {
    {"pass1", "pass1_ready", "pass1_refresh"},
    {"pass2", "pass2_ready", "pass2_refresh"},
};

/*********************************************************************
 * LOCAL VARIABLES
 */
static spi_device_handle_t m_spi_device;
static bool m_initialized;
/* 面板是否已处于 DSLP。用来让 hal_epd_sleep() 幂等：DSLP 下 BUSY 恒为忙，重复调用会
 * 白等一整个 BUSY 超时。深睡路径每次唤醒都会调它，而多数唤醒并不刷屏、面板本来就还停在
 * 上一次的 DSLP 里。由 reset() 清零——硬件复位是唤醒面板的唯一途径。 */
static bool m_in_dslp;

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static esp_err_t spi_init(void);
static esp_err_t EPD_W21_WriteCMD(unsigned char command);
static esp_err_t EPD_W21_WriteDATA(unsigned char datas);
static esp_err_t EPD_W21_WriteDATA_Bulk(const unsigned char *data, uint32_t len);
static esp_err_t EPD_read(unsigned char *out);
static esp_err_t reset(void);
static esp_err_t lcd_chkstatus(const char *stage);
static esp_err_t lcd_chkstatus_to(const char *stage, int limit_ms);
static esp_err_t epd_read_temp(unsigned char *out);
static esp_err_t epd_do_pass(const unsigned char *input_data, const uint8_t *cmap,
                             uint8_t temp_val, uint8_t pass);
static esp_err_t epd_do_film_pass(const unsigned char *pixel_data, const FilmHeader *header,
                                  const uint8_t *cmap, uint8_t temp_val, uint8_t pass);
static esp_err_t epd_display_solid_pass(unsigned char fill_byte, unsigned char temp_val,
                                        uint8_t pass);
static esp_err_t epd_display_solid(unsigned char color_index);
static esp_err_t film_parse_header(const unsigned char *filmData, FilmHeader *header);

/*********************************************************************
 * HARDWARE SPI (half-duplex, 3-wire mode)
 *   面板只有一根数据线：SDIN 兼作 MOSI+MISO，故 miso_io_num = -1 且设备标志带
 *   SPI_DEVICE_HALFDUPLEX | SPI_DEVICE_3WIRE；CS/DC/RST/BUSY 由本文件手动控制。
 *********************************************************************/

static esp_err_t spi_init(void)
{
    esp_err_t ret;

    spi_bus_config_t buscfg =
    {
        .miso_io_num = -1,
        .mosi_io_num = EPD_SDIN_PIN,
        .sclk_io_num = EPD_SCK_PIN,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = EPD_OUTPUT_LINE * 4
    };

    ret = spi_bus_initialize(EPD_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if(ret != ESP_OK)
    {
        return ret;
    }

    spi_device_interface_config_t devcfg =
    {
        .clock_speed_hz = 10000000,
        .mode = 0,
        .spics_io_num = -1,
        .queue_size = 1,
        .flags = SPI_DEVICE_HALFDUPLEX | SPI_DEVICE_3WIRE,
    };

    ret = spi_bus_add_device(EPD_SPI_HOST, &devcfg, &m_spi_device);
    if(ret != ESP_OK)
    {
        (void)spi_bus_free(EPD_SPI_HOST);
        return ret;
    }
    return ESP_OK;
}

static esp_err_t EPD_W21_WriteCMD(unsigned char command)
{
    esp_err_t err = EPD_W21_CS_0;
    if(err != ESP_OK) return err;
    err = EPD_W21_DC_0;
    if(err != ESP_OK) goto done;
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8;
    t.tx_buffer = &command;
    err = spi_device_transmit(m_spi_device, &t);
done:
    (void)EPD_W21_CS_1;
    return err;
}

static esp_err_t EPD_W21_WriteDATA(unsigned char datas)
{
    esp_err_t err = EPD_W21_CS_0;
    if(err != ESP_OK) return err;
    err = EPD_W21_DC_1;
    if(err != ESP_OK) goto done;
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8;
    t.tx_buffer = &datas;
    err = spi_device_transmit(m_spi_device, &t);
done:
    (void)EPD_W21_CS_1;
    return err;
}

static esp_err_t EPD_W21_WriteDATA_Bulk(const unsigned char *data, uint32_t len)
{
    esp_err_t err = EPD_W21_CS_0;
    if(err != ESP_OK) return err;
    err = EPD_W21_DC_1;
    if(err != ESP_OK) goto done;
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = len * 8;
    t.tx_buffer = data;
    err = spi_device_transmit(m_spi_device, &t);
done:
    (void)EPD_W21_CS_1;
    return err;
}

static esp_err_t EPD_read(unsigned char *out)
{
    if(out == NULL) return ESP_ERR_INVALID_ARG;
    *out = 0;
    esp_err_t err = EPD_W21_CS_0;
    if(err != ESP_OK) return err;
    err = EPD_W21_DC_1;
    if(err != ESP_OK) goto done;
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.rxlength = 8;
    t.rx_buffer = out;
    err = spi_device_transmit(m_spi_device, &t);
done:
    (void)EPD_W21_CS_1;
    return err;
}

/*********************************************************************
 * HELPER FUNCTIONS
 *********************************************************************/

static esp_err_t reset(void)
{
    int64_t started = esp_timer_get_time();
    EPD_TRY(EPD_W21_RST_1);
    vTaskDelay(20 / portTICK_PERIOD_MS);
    EPD_TRY(EPD_W21_RST_0);
    vTaskDelay(100 / portTICK_PERIOD_MS);
    EPD_TRY(EPD_W21_RST_1);
    vTaskDelay(100 / portTICK_PERIOD_MS);
    /* 硬件复位把面板从 DSLP 拉了出来，状态跟踪必须同步清零，否则下一次
     * hal_epd_sleep() 会误判「已在 DSLP」而跳过，面板真的带电进深睡。
     * 放在 chkstatus 之前：那一步可能提前返回错误。 */
    m_in_dslp = false;
    EPD_TRY(lcd_chkstatus("reset"));
    sys_logi(EPD_TAG, "timing reset=%lldms", (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t lcd_chkstatus_to(const char *stage, int limit_ms)
{
    /* 六色面板刷新慢，BUSY 拉高才算空闲。加超时上限，防止面板未接/异常时死循环
     * 卡死任务或触发看门狗。 */
    int timeout_ms = limit_ms;
    int64_t started = esp_timer_get_time();
    while (isEPD_W21_BUSY == 0 && timeout_ms > 0)
    {
        vTaskDelay(10 / portTICK_PERIOD_MS);
        timeout_ms -= 10;
    }
    if (timeout_ms <= 0)
    {
        sys_loge(EPD_TAG, "%s BUSY timeout after %dms", stage, limit_ms);
        return ESP_ERR_TIMEOUT;
    }
    sys_logi(EPD_TAG, "timing %s_busy=%lldms", stage,
             (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t lcd_chkstatus(const char *stage)
{
    return lcd_chkstatus_to(stage, EPD_BUSY_TIMEOUT_MS);
}

static esp_err_t epd_read_temp(unsigned char *out)
{
    if(out == NULL) return ESP_ERR_INVALID_ARG;
    int64_t started = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(TSD));   // 0x41
    EPD_TRY(EPD_W21_WriteDATA(0x00));
    EPD_TRY(EPD_W21_WriteCMD(TSE));   // 0x40
    EPD_TRY(lcd_chkstatus("temperature"));
    EPD_TRY(EPD_read(out));
    sys_logi(EPD_TAG, "timing temperature=%lldms", (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t epd_do_pass(const unsigned char *input_data, const uint8_t *cmap,
                             uint8_t temp_val, uint8_t pass)
{
    int64_t started = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(WFT));
    EPD_TRY(EPD_W21_WriteDATA(0x02));
    EPD_TRY(EPD_W21_WriteCMD(WFD));
    EPD_TRY(EPD_W21_WriteDATA(temp_val));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].ready));

    EPD_TRY(EPD_W21_WriteCMD(DTM));

    unsigned char out_line[EPD_OUTPUT_LINE];
    for (int row = 0; row < EPD_HEIGHT; row++)
    {
        const unsigned char *in_ptr = input_data + row * EPD_INPUT_LINE;
        for (int col = 0, out_idx = 0; col < EPD_INPUT_LINE; col += 2, out_idx++)
        {
            uint8_t in1 = in_ptr[col];
            uint8_t in2 = in_ptr[col + 1];
            uint8_t p1 = (in1 >> 4) & 0x0F;
            uint8_t p2 = in1 & 0x0F;
            uint8_t p3 = (in2 >> 4) & 0x0F;
            uint8_t p4 = in2 & 0x0F;
            out_line[out_idx] = (cmap[p1] << 6) | (cmap[p2] << 4)
                                | (cmap[p3] << 2) | cmap[p4];
        }
        EPD_TRY(EPD_W21_WriteDATA_Bulk(out_line, EPD_OUTPUT_LINE));
    }

    int64_t transfer_done = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(REF));
    EPD_TRY(EPD_W21_WriteDATA(0xA5));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].refresh));
    sys_logi(EPD_TAG, "timing %s_transfer=%lldms %s_total=%lldms",
             pass_stage[pass].total, (transfer_done - started) / 1000,
             pass_stage[pass].total, (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t epd_do_film_pass(const unsigned char *pixel_data, const FilmHeader *header,
                                  const uint8_t *cmap, uint8_t temp_val, uint8_t pass)
{
    int64_t started = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(WFT));
    EPD_TRY(EPD_W21_WriteDATA(0x02));
    EPD_TRY(EPD_W21_WriteCMD(WFD));
    EPD_TRY(EPD_W21_WriteDATA(temp_val));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].ready));
    EPD_TRY(EPD_W21_WriteCMD(DTM));

    unsigned char line_4bpp[EPD_INPUT_LINE];
    unsigned char out_line[EPD_OUTPUT_LINE];
    for(uint32_t row = 0; row < EPD_HEIGHT; row++)
    {
        for(uint32_t col = 0; col < EPD_INPUT_LINE; col++)
        {
            uint8_t byte = pixel_data[row * EPD_INPUT_LINE + col];
            uint8_t cc1 = (byte >> 4) & 0x0F;
            uint8_t cc2 = byte & 0x0F;
            line_4bpp[col] = (color_lut[header->colorTable[cc1]] << 4)
                                | color_lut[header->colorTable[cc2]];
        }
        for(int col = 0; col < EPD_INPUT_LINE; col += 2)
        {
            uint8_t in1 = line_4bpp[col];
            uint8_t in2 = line_4bpp[col + 1];
            out_line[col / 2] = (cmap[(in1 >> 4) & 0x0F] << 6)
                                | (cmap[in1 & 0x0F] << 4)
                                | (cmap[(in2 >> 4) & 0x0F] << 2)
                                | cmap[in2 & 0x0F];
        }
        EPD_TRY(EPD_W21_WriteDATA_Bulk(out_line, EPD_OUTPUT_LINE));
    }

    int64_t transfer_done = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(REF));
    EPD_TRY(EPD_W21_WriteDATA(0xA5));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].refresh));
    sys_logi(EPD_TAG, "timing %s_transfer=%lldms %s_total=%lldms",
             pass_stage[pass].total, (transfer_done - started) / 1000,
             pass_stage[pass].total, (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t epd_display_solid_pass(unsigned char fill_byte, unsigned char temp_val,
                                        uint8_t pass)
{
    int64_t started = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(WFT));
    EPD_TRY(EPD_W21_WriteDATA(0x02));
    EPD_TRY(EPD_W21_WriteCMD(WFD));
    EPD_TRY(EPD_W21_WriteDATA(temp_val));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].ready));

    EPD_TRY(EPD_W21_WriteCMD(DTM));

    unsigned char line[EPD_OUTPUT_LINE];
    memset(line, fill_byte, EPD_OUTPUT_LINE);
    for (int row = 0; row < EPD_HEIGHT; row++)
    {
        EPD_TRY(EPD_W21_WriteDATA_Bulk(line, EPD_OUTPUT_LINE));
    }

    int64_t transfer_done = esp_timer_get_time();
    EPD_TRY(EPD_W21_WriteCMD(REF));
    EPD_TRY(EPD_W21_WriteDATA(0xA5));
    EPD_TRY(lcd_chkstatus(pass_stage[pass].refresh));
    sys_logi(EPD_TAG, "timing %s_transfer=%lldms %s_total=%lldms",
             pass_stage[pass].total, (transfer_done - started) / 1000,
             pass_stage[pass].total, (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

static esp_err_t epd_display_solid(unsigned char color_index)
{
    unsigned char var_temp, temp1, temp2;

    EPD_TRY(epd_read_temp(&var_temp));

    if (var_temp < 5)
    {
        temp1 = 2;
        temp2 = 7;
    }
    else if (var_temp <= 10)
    {
        temp1 = 12;
        temp2 = 17;
    }
    else if (var_temp <= 20)
    {
        temp1 = 22;
        temp2 = 27;
    }
    else if (var_temp <= 30)
    {
        temp1 = 32;
        temp2 = 37;
    }
    else /* var_temp > 30 && <= 127*/
    {
        temp1 = 42;
        temp2 = 47;
    }

    uint8_t pass1 = PACK4(color_map[color_index]);
    uint8_t pass2 = PACK4(color_map1[color_index]);

    sys_logi(EPD_TAG, "Solid idx=%d temp=%d t1=%d t2=%d p1=0x%02X p2=0x%02X",
             color_index, var_temp, temp1, temp2, pass1, pass2);

    EPD_TRY(epd_display_solid_pass(pass1, temp1, 0));
    EPD_TRY(epd_display_solid_pass(pass2, temp2, 1));
    return ESP_OK;
}

static esp_err_t film_parse_header(const unsigned char *filmData, FilmHeader *header)
{
    if (filmData == NULL || header == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    memcpy(&header->fileSize, &filmData[FILM_OFFSET_FILESIZE], sizeof(uint32_t));
    memcpy(&header->screenWidth, &filmData[FILM_OFFSET_SCREENWIDTH], sizeof(uint16_t));
    memcpy(&header->screenHeight, &filmData[FILM_OFFSET_SCREENHEIGHT], sizeof(uint16_t));
    memcpy(&header->colorCount, &filmData[FILM_OFFSET_COLORCOUNT], sizeof(uint8_t));
    memcpy(header->reserved, &filmData[FILM_OFFSET_RESERVED], 7);
    memcpy(header->colorTable, &filmData[FILM_OFFSET_COLORTABLE], FILM_COLOR_TABLE_SIZE);

    if (header->colorCount != 6)
    {
        sys_loge(EPD_TAG, "Invalid color count: %d (expected 6)", header->colorCount);
        return ESP_ERR_INVALID_SIZE;
    }

    if(header->screenWidth != EPD_WIDTH || header->screenHeight != EPD_HEIGHT)
    {
        sys_loge(EPD_TAG, "Resolution mismatch: %ux%u", header->screenWidth,
                 header->screenHeight);
        return ESP_ERR_INVALID_SIZE;
    }

    uint32_t expectedSize = (EPD_WIDTH * EPD_HEIGHT) / 2;
    if (header->fileSize != expectedSize)
    {
        sys_loge(EPD_TAG, "File size mismatch: %lu (expected %lu)",
                 (unsigned long)header->fileSize, (unsigned long)expectedSize);
        return ESP_ERR_INVALID_SIZE;
    }

    return ESP_OK;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 *********************************************************************/

esp_err_t hal_epd_init(void)
{
    if(m_initialized) return ESP_OK;
    gpio_config_t io_conf;

    // BUSY: input
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << EPD_BUSY_PIN);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
    EPD_TRY(gpio_config(&io_conf));

    // RST, DC, CS: output
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_OUTPUT;
    io_conf.pin_bit_mask = (1ULL << EPD_RST_PIN) | (1ULL << EPD_DC_PIN) |
                           (1ULL << EPD_CS_PIN);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
    EPD_TRY(gpio_config(&io_conf));

    // Initial control pin states
    EPD_TRY(EPD_W21_CS_1);
    EPD_TRY(EPD_W21_DC_1);

    // Init hardware SPI (half-duplex, SCK + SDIN controlled by peripheral)
    EPD_TRY(spi_init());

    m_initialized = true;
    /* 本函数只在冷启动/深睡唤醒后跑，此时面板刚随外设电源上电，处于复位态而非 DSLP。 */
    m_in_dslp = false;
    sys_logi(EPD_TAG, "EPD initialized (hardware SPI half-duplex)");
    return ESP_OK;
}

esp_err_t hal_epd_display_init(void)
{
    if(!m_initialized) return ESP_ERR_INVALID_STATE;
    int64_t started = esp_timer_get_time();
    EPD_TRY(reset());

    // SE0368-C init sequence
    EPD_TRY(EPD_W21_WriteCMD(PSR));   // 0x00
    EPD_TRY(EPD_W21_WriteDATA(0x27));
    EPD_TRY(EPD_W21_WriteDATA(0x29));

    // 另一种扫描方向（PSR 首字节 0x2B/0x2F）需改用 0x61 序列，见 SE0368-C 手册。
    // 以下为 PSR 首字节 0x23/0x27 时的扫描方向设置。
    EPD_TRY(EPD_W21_WriteCMD(RSET));  // 0x83
    EPD_TRY(EPD_W21_WriteDATA(0x00));
    EPD_TRY(EPD_W21_WriteDATA(0x00));
    EPD_TRY(EPD_W21_WriteDATA(0x03));
    EPD_TRY(EPD_W21_WriteDATA(0x17));
    EPD_TRY(EPD_W21_WriteDATA(0x00));
    EPD_TRY(EPD_W21_WriteDATA(0x48));
    EPD_TRY(EPD_W21_WriteDATA(0x02));
    EPD_TRY(EPD_W21_WriteDATA(0x57));
    EPD_TRY(EPD_W21_WriteDATA(0x01));
    EPD_TRY(lcd_chkstatus("init_rset"));

    EPD_TRY(EPD_W21_WriteCMD(PWR));   // 0x01
    EPD_TRY(EPD_W21_WriteDATA(0x07));
    EPD_TRY(EPD_W21_WriteDATA(0x00));

    EPD_TRY(EPD_W21_WriteCMD(POFS));  // 0x03
    EPD_TRY(EPD_W21_WriteDATA(0x10));
    EPD_TRY(EPD_W21_WriteDATA(0x54));
    EPD_TRY(EPD_W21_WriteDATA(0x44));

    EPD_TRY(EPD_W21_WriteCMD(BTST2)); // 0x06
    EPD_TRY(EPD_W21_WriteDATA(0x25));
    EPD_TRY(EPD_W21_WriteDATA(0x25));
    EPD_TRY(EPD_W21_WriteDATA(0x3C));
    EPD_TRY(EPD_W21_WriteDATA(0x17));

    EPD_TRY(EPD_W21_WriteCMD(PLL));   // 0x30
    EPD_TRY(EPD_W21_WriteDATA(0x08));
    EPD_TRY(lcd_chkstatus("init_pll"));

    EPD_TRY(EPD_W21_WriteCMD(TSD));   // 0x41
    EPD_TRY(EPD_W21_WriteDATA(0x00));

    EPD_TRY(EPD_W21_WriteCMD(CDI));   // 0x50
    EPD_TRY(EPD_W21_WriteDATA(0x37));

    EPD_TRY(EPD_W21_WriteCMD(RES2));  // 0x62
    EPD_TRY(EPD_W21_WriteDATA(0x02));
    EPD_TRY(EPD_W21_WriteDATA(0x02));

    EPD_TRY(EPD_W21_WriteCMD(VCOM));  // 0xE7
    EPD_TRY(EPD_W21_WriteDATA(0x1C));

    EPD_TRY(EPD_W21_WriteCMD(PWS));   // 0xE3
    EPD_TRY(EPD_W21_WriteDATA(0x22));

    EPD_TRY(EPD_W21_WriteCMD(BOD));   // 0xE9
    EPD_TRY(EPD_W21_WriteDATA(0x01));

    EPD_TRY(EPD_W21_WriteCMD(VCOM2)); // 0xE1
    EPD_TRY(EPD_W21_WriteDATA(0x02));

    sys_logi(EPD_TAG, "EPD display initialized, timing init_total=%lldms",
             (esp_timer_get_time() - started) / 1000);
    return ESP_OK;
}

esp_err_t hal_epd_display_white(void)
{
    return epd_display_solid(0x01);
}
esp_err_t hal_epd_display_black(void)
{
    return epd_display_solid(0x00);
}
esp_err_t hal_epd_display_yellow(void)
{
    return epd_display_solid(0x02);
}
esp_err_t hal_epd_display_red(void)
{
    return epd_display_solid(0x03);
}
esp_err_t hal_epd_display_blue(void)
{
    return epd_display_solid(0x05);
}
esp_err_t hal_epd_display_green(void)
{
    return epd_display_solid(0x06);
}

esp_err_t hal_epd_display_pic(const unsigned char *picData)
{
    if (picData == NULL)
    {
        sys_loge(EPD_TAG, "picData is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    unsigned char var_temp, temp1, temp2;

    EPD_TRY(epd_read_temp(&var_temp));

    if (var_temp < 5)
    {
        temp1 = 2;
        temp2 = 7;
    }
    else if (var_temp <= 10)
    {
        temp1 = 12;
        temp2 = 17;
    }
    else if (var_temp <= 20)
    {
        temp1 = 22;
        temp2 = 27;
    }
    else if (var_temp <= 30)
    {
        temp1 = 32;
        temp2 = 37;
    }
    else /* var_temp > 30 && <= 127*/
    {
        temp1 = 42;
        temp2 = 47;
    }

    sys_logi(EPD_TAG, "Display pic: temp=%d t1=%d t2=%d", var_temp, temp1, temp2);

    EPD_TRY(epd_do_pass(picData, color_map,  temp1, 0));
    EPD_TRY(epd_do_pass(picData, color_map1, temp2, 1));

    sys_logi(EPD_TAG, "Display pic completed");
    return ESP_OK;
}

esp_err_t hal_epd_display_film(const unsigned char *filmData)
{
    FilmHeader header;

    if (filmData == NULL)
    {
        sys_loge(EPD_TAG, "filmData is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = film_parse_header(filmData, &header);
    if (err != ESP_OK)
    {
        sys_loge(EPD_TAG, "Failed to parse film header");
        return err;
    }

    sys_logi(EPD_TAG, "Film: %ux%u, %d colors, %lu bytes",
             header.screenWidth, header.screenHeight,
             header.colorCount, (unsigned long)header.fileSize);

    unsigned char var_temp, temp1, temp2;
    EPD_TRY(epd_read_temp(&var_temp));

    if (var_temp < 5)
    {
        temp1 = 2;
        temp2 = 7;
    }
    else if (var_temp <= 10)
    {
        temp1 = 12;
        temp2 = 17;
    }
    else if (var_temp <= 20)
    {
        temp1 = 22;
        temp2 = 27;
    }
    else if (var_temp <= 30)
    {
        temp1 = 32;
        temp2 = 37;
    }
    else /* var_temp > 30 && <= 127*/
    {
        temp1 = 42;
        temp2 = 47;
    }

    sys_logi(EPD_TAG, "Film temp=%d t1=%d t2=%d", var_temp, temp1, temp2);
    const unsigned char *pixel_data = filmData + FILM_HEADER_SIZE;
    EPD_TRY(epd_do_film_pass(pixel_data, &header, color_map, temp1, 0));
    EPD_TRY(epd_do_film_pass(pixel_data, &header, color_map1, temp2, 1));

    sys_logi(EPD_TAG, "Film display completed");
    return ESP_OK;
}

/* BUSY 等待失败也必须把 DSLP 发出去：跳过它会让面板带电进深睡，
 * 代价远大于一次刷屏失败。返回第一个错误码供调用方判定刷屏成败。 */
esp_err_t hal_epd_sleep(void)
{
    if(!m_initialized) return ESP_ERR_INVALID_STATE;

    /* 已在 DSLP 就直接返回，不再等 BUSY、不再发命令。DSLP 状态下 BUSY 恒为忙，
     * 重复调用会白等一整个超时并全部计入清醒时长。 */
    if(m_in_dslp)
    {
        sys_logi(EPD_TAG, "EPD already in deep sleep, skip DSLP");
        return ESP_OK;
    }

    esp_err_t err = lcd_chkstatus_to("shutdown", EPD_SHUTDOWN_BUSY_TIMEOUT_MS);

    esp_err_t cmd_err = EPD_W21_WriteCMD(DSLP);
    if(cmd_err == ESP_OK) cmd_err = EPD_W21_WriteDATA(0xA5);
    if(err == ESP_OK) err = cmd_err;

    /* 命令发出去就算进了 DSLP，与 BUSY 等待成败无关：面板收到 DSLP 即进深睡，
     * 而 BUSY 超时只说明它之前忙。据此避免下一次再白等。 */
    if(cmd_err == ESP_OK) m_in_dslp = true;

    if(err != ESP_OK)
    {
        sys_logw(EPD_TAG, "EPD sleep issued with error: %s", esp_err_to_name(err));
    }
    else
    {
        sys_logi(EPD_TAG, "EPD sleep");
    }
    return err;
}

esp_err_t hal_epd_pwroff(void)
{
    int64_t started = esp_timer_get_time();
    esp_err_t err = hal_epd_sleep();
    vTaskDelay(100 / portTICK_PERIOD_MS);

    sys_logi(EPD_TAG, "EPD power off, timing shutdown=%lldms",
             (esp_timer_get_time() - started) / 1000);
    return err;
}

esp_err_t hal_epd_deinit(void)
{
    if (m_spi_device != NULL)
    {
        esp_err_t err = spi_bus_remove_device(m_spi_device);
        if(err != ESP_OK) return err;
        m_spi_device = NULL;
    }
    /* 不调用 spi_bus_free()：IDF 6.0 上它经 IPC 到另一核释放 SPI 总线中断，会触发
     * esp_intr_free 的 use-after-free 崩溃。本函数只在深睡前调用，而深睡唤醒等价于
     * 重启、hal_epd_init() 会重新 spi_bus_initialize，因此不释放总线不会造成泄漏。 */

    gpio_config_t io_conf =
    {
        .pin_bit_mask = (1ULL << EPD_BUSY_PIN) | (1ULL << EPD_RST_PIN) |
        (1ULL << EPD_DC_PIN)   | (1ULL << EPD_CS_PIN) |
        (1ULL << EPD_SCK_PIN)  | (1ULL << EPD_SDIN_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    EPD_TRY(gpio_config(&io_conf));

    m_initialized = false;
    sys_logi(EPD_TAG, "EPD deinit, SPI and GPIOs released");
    return ESP_OK;
}
