#pragma once
// Copy to config.local.h. Never commit the actual Wi-Fi password / device token.
#define WIFI_SSID "CHANGE_ME"
#define WIFI_PASSWORD "CHANGE_ME"
#define SERVER_URL "http://192.168.1.100:8787"
#define DEVICE_TOKEN "PASTE_ONE_TIME_TOKEN_FROM_WORKBENCH"
// HTTP is only for a trusted local commissioning network. Never use a public URL.
#define ALLOW_PLAINTEXT_LAN 1
// HTTPS uses verified certificates. Supply the appropriate CA; no setInsecure().
#define TLS_ROOT_CA ""
#define NTC_SUPPLY_MV 3300.0f
#define MAIN_PWM_MAX_PERCENT 60
#define PROJECTION_PWM_MAX_PERCENT 35
#define PROJECTION_TIMEOUT_MS (15UL * 60UL * 1000UL)
