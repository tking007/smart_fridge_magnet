set(srcs "src/hal_init.c"
         "src/hal_key.c"
         "src/hal_bat.c"
         "src/hal_led.c"
         "src/hal_sd.c"
         "src/hal_pwr.c"
         "src/hal_epd.c"
         )

set(public_include "inc")

# 已移除 esp-idf-lib 的 encoder 依赖：硬件是三个独立按键，改用自带的 hal_key 驱动
# （rotary_encoder 的正交 A/B 相解码对独立按键不成立）。
set(requires film_sys led_strip driver fatfs esp_adc)

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${public_include}
                       REQUIRES ${requires}
                      )