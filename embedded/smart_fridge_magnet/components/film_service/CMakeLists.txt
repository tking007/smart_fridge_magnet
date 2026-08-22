set(srcs "src/service_init.c"
         "src/service_ble.c"
         "src/service_ble_parser.c"
         "src/service_ble_gatts.c"
         "src/service_ota.c"
         "src/service_param.c"
         "src/service_monitor.c"
         "src/service_file.c"
         "src/file_txn.c"
         "src/service_film.c"
         "src/service_wifi.c"
         "src/service_wifi_ota.c"
         "src/film_validate.c"
         "src/manifest_parse.c"
         "src/param_serialize.c"
         "src/factory_reset.c"
         "src/sync_decision.c"
         "src/sync_url.c"
         "src/wifi_sync_gate.c"
         "src/wifi_sync_yield.c"
         "src/wifi_link_watch.c"
         "src/poll_interval.c"
         "src/sleep_decision.c"
         "src/wake_schedule.c"
         "src/film_pin.c"
         "src/film_set_txn.c"
         "src/sfm_crypto.c"
         )

set(public_include "inc")

set(requires film_hal film_sys  bt nvs_flash app_update esp_wifi esp_netif esp_http_client
              esp_https_ota esp_app_format esp-tls mbedtls)

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${public_include}
                       REQUIRES ${requires}
                      )
