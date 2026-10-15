#include <Arduino.h>
#include "driver/twai.h"

// ESP32 pins
static constexpr gpio_num_t CAN_TX_PIN = GPIO_NUM_17;
static constexpr gpio_num_t CAN_RX_PIN = GPIO_NUM_18;

// PQ25 powertrain CAN is normally 500 kbit/s
static constexpr uint32_t SERIAL_BAUD = 115200;

void setupCan()
{
    twai_general_config_t generalConfig =
        TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_LISTEN_ONLY);

    // 500 kbit/s powertrain CAN
    twai_timing_config_t timingConfig = TWAI_TIMING_CONFIG_500KBITS();

    // Accept every standard/extended CAN frame
    twai_filter_config_t filterConfig = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t result = twai_driver_install(
        &generalConfig,
        &timingConfig,
        &filterConfig
    );

    if (result != ESP_OK)
    {
        Serial.print("# TWAI driver install failed: ");
        Serial.println(esp_err_to_name(result));
        return;
    }

    result = twai_start();

    if (result != ESP_OK)
    {
        Serial.print("# TWAI start failed: ");
        Serial.println(esp_err_to_name(result));
        return;
    }

    Serial.println("# CAN started in LISTEN ONLY mode");
    Serial.println("# 500 kbit/s");
    Serial.println("# timestamp_ms,can_id,extended,dlc,data");
}

void setup()
{
    Serial.begin(SERIAL_BAUD);
    delay(1000);

    Serial.println();
    Serial.println("# VAGbit PQ25 Powertrain Logger");
    Serial.println("# ESP32 TWAI");
    Serial.println("# RX ONLY / LISTEN ONLY / NO ACK");

    setupCan();
}

void loop()
{
    twai_message_t message;

    if (twai_receive(&message, pdMS_TO_TICKS(100)) == ESP_OK)
    {
        const uint32_t timestamp = millis();

        Serial.print(timestamp);
        Serial.print(",");

        if (message.extd)
        {
            Serial.printf("%08lX", static_cast<unsigned long>(message.identifier));
        }
        else
        {
            Serial.printf("%03lX", static_cast<unsigned long>(message.identifier));
        }

        Serial.print(",");
        Serial.print(message.extd ? 1 : 0);
        Serial.print(",");
        Serial.print(message.data_length_code);
        Serial.print(",");

        for (uint8_t i = 0; i < message.data_length_code; i++)
        {
            if (message.data[i] < 0x10)
            {
                Serial.print("0");
            }

            Serial.print(message.data[i], HEX);

            if (i + 1 < message.data_length_code)
            {
                Serial.print(" ");
            }
        }

        Serial.println();
    }
}