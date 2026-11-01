#include "app_update.h"
#include "int_can.h"
#include "usart.h"
#include <stdio.h>
#include <string.h>
#include "crc.h"
#include "int_w25q16.h"
#include "int_eeprom.h"
#define APP_UPDATE_COMMAND_SIZE_BYTES 32
#define APP_UPDATE_LED_INTERVAL_MS    500
#define APP_UPDATE_DATA_CAN_ID        1
#define APP_UPDATE_RECEIVE_IDLE_MS    1000
#define APP_UPDATE_CRC_TIMEOUT_MS     2000
#define APP_UPDATE_W25_SECTOR_SIZE_BYTES 4096
/* 与主 Bootloader 的 CHECK_UPDATE_ADDR、BOOT_UPDATE、BOOT_KEY 保持一致。 */
#define APP_UPDATE_EEPROM_STATE_ADDR  0x10
#define APP_UPDATE_BOOT_UPDATE        0x01
#define APP_UPDATE_BOOT_KEY           0x5A6B

volatile app_update_state_enum app_update_state = APP_UPDATE_IDLE;

static char uart_rx_buffer[APP_UPDATE_COMMAND_SIZE_BYTES];
/* 主循环接收，缓冲区和累计长度同时供后续 CRC 校验使用。 */
/* CRC 外设按 32 位字读取，BIN 缓冲区保持 4 字节对齐，CRC 帧不写入此缓冲区。 */
__ALIGN_BEGIN uint8_t can_rx_buffer[5120] __ALIGN_END = {0};
uint32_t can_rx_len = 0;
/* 保存帧头、数据和本次读取前 FIFO0 的报文数量，由主循环更新。 */
CAN_MSG_RECEIVE can_rx_msg = {0};
static uint32_t last_receive_tick;

void app_update_init(void)
{
    HAL_UARTEx_ReceiveToIdle_IT(&huart1, (uint8_t *)uart_rx_buffer, sizeof(uart_rx_buffer) - 1);
}

void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t size)
{
    if (huart != &huart1)
    {
        return;
    }
    uart_rx_buffer[size] = '\0';
    uart_rx_buffer[strcspn(uart_rx_buffer, "\r\n")] = '\0';
    if ((app_update_state == APP_UPDATE_IDLE) && (size < sizeof(uart_rx_buffer) - 1) &&
        (strcmp(uart_rx_buffer, "cmd") == 0))
    {
        app_update_state = APP_UPDATE_SEND_COMMAND;
    }
    app_update_init();
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart == &huart1)
    {
        HAL_UART_AbortReceive(huart);
        app_update_init();
    }
}

static void app_update_led_blink(void)
{
    static uint32_t last_led_tick;
    uint32_t now_ms = HAL_GetTick();

    if ((uint32_t)(now_ms - last_led_tick) >= APP_UPDATE_LED_INTERVAL_MS)
    {
        last_led_tick = now_ms;
        HAL_GPIO_TogglePin(led0_GPIO_Port, led0_Pin);
        HAL_GPIO_TogglePin(led1_GPIO_Port, led1_Pin);
    }
}
void app_update_receive(void)
{
    /* 先判断 BIN 是否结束，避免把已到达 FIFO 的末尾 CRC 拼入 BIN。 */
    if ((uint32_t)(HAL_GetTick() - last_receive_tick) >= APP_UPDATE_RECEIVE_IDLE_MS)
    {
        if (can_rx_len > 0)
        {
            char receive_message[48];
            uint16_t message_length;

            app_update_state = APP_UPDATE_VERIFY;
            message_length = (uint16_t)snprintf(receive_message, sizeof(receive_message), "CAN receive done,len:%lu\r\n", (unsigned long)can_rx_len);
            HAL_UART_Transmit(&huart1, (uint8_t *)receive_message, message_length, 100);
        }
        else
        {
            app_update_state = APP_UPDATE_ERROR;
        }
        return;
    }

    int_can_receive_msg(&can_rx_msg, &can_rx_msg.count);
    if ((can_rx_msg.count > 0) && (can_rx_msg.RxHeader.IDE == CAN_ID_STD) && (can_rx_msg.RxHeader.StdId == APP_UPDATE_DATA_CAN_ID) &&
        (can_rx_msg.RxHeader.RTR == CAN_RTR_DATA) && (can_rx_msg.RxHeader.DLC > 0) && (can_rx_msg.RxHeader.DLC <= sizeof(can_rx_msg.data)))
    {
        /* 超出 5 KiB 时停止本轮，保留已经收到的数据和长度。 */
        if (can_rx_msg.RxHeader.DLC > sizeof(can_rx_buffer) - can_rx_len)
        {
            app_update_state = APP_UPDATE_ERROR;
            return;
        }
        for (uint8_t i = 0; i < can_rx_msg.RxHeader.DLC; i++)
        {
            can_rx_buffer[can_rx_len + i] = can_rx_msg.data[i];
        }
        can_rx_len += can_rx_msg.RxHeader.DLC;
        last_receive_tick = HAL_GetTick();
    }
}

static uint32_t app_crc_cal(uint8_t *data, uint32_t len)
{
    uint32_t *p_data = (uint32_t *)data;
    uint32_t word_count = (len + 3) / 4;
    uint32_t crc_val;

    /* 最后不足 4 字节时补 0，只写接收数据之后的缓冲区空余位置。 */
    if ((len % 4) != 0)
    {
        memset(data + len, 0, 4 - (len % 4));
    }
    /* 复位 CRC 外设后按 32 位字计算。 */
    __HAL_CRC_DR_RESET(&hcrc);
    crc_val = HAL_CRC_Calculate(&hcrc, p_data, word_count);
    return crc_val;
}

/* CRC 通过后调用；底层写接口自动按 256 字节页边界拆分并等待编程完成。 */
static uint8_t app_update_save_image(void)
{
    uint8_t metadata[8];
    uint8_t boot_state[3] = {APP_UPDATE_BOOT_UPDATE, (uint8_t)(APP_UPDATE_BOOT_KEY >> 8), (uint8_t)APP_UPDATE_BOOT_KEY};
    uint8_t stored_state[3] = {0};
    uint8_t i;
    uint32_t address;

    /* 主 Bootloader 的元数据要求 BIN 大于 2048 字节，短测试数据不能提交为待更新固件。 */
    if (can_rx_len <= 0x0800)
    {
        return 1;
    }

    /* 先擦除旧元数据，正文写入失败时不留下指向半成品的有效长度。 */
    if (int_w25q16_erase_sector(0, 0) != 0)
    {
        return 1;
    }
    for (address = APP_UPDATE_W25_START_ADDR; address < APP_UPDATE_W25_START_ADDR + can_rx_len;
         address += APP_UPDATE_W25_SECTOR_SIZE_BYTES)
    {
        if (int_w25q16_erase_sector(address >> 16, (address >> 12) & 0x0F) != 0)
        {
            return 1;
        }
    }

    if (int_w25q16_write(APP_UPDATE_W25_START_ADDR >> 16, (APP_UPDATE_W25_START_ADDR >> 12) & 0x0F,
                         (APP_UPDATE_W25_START_ADDR >> 8) & 0x0F, APP_UPDATE_W25_START_ADDR & 0xFF,
                         can_rx_buffer, (uint16_t)can_rx_len) != 0)
    {
        return 1;
    }

    /* 前 4 字节为 W25Q16 片内起点，后 4 字节为 BIN 实际长度，均为小端；不保存 CRC 和补齐字节。 */
    for (i = 0; i < 4; i++)
    {
        metadata[i] = (uint8_t)(APP_UPDATE_W25_START_ADDR >> (i * 8));
        metadata[i + 4] = (uint8_t)(can_rx_len >> (i * 8));
    }
    if (int_w25q16_write(0, 0, 0, 0, metadata, sizeof(metadata)) != 0)
    {
        return 1;
    }

    /* 镜像完整后再提交更新状态和密钥；EEPROM 接口不返回状态，通过读回确认。 */
    int_eeprom_write(APP_UPDATE_EEPROM_STATE_ADDR, boot_state, sizeof(boot_state));
    int_eeprom_read(APP_UPDATE_EEPROM_STATE_ADDR, stored_state, sizeof(stored_state));
    for (i = 0; i < sizeof(boot_state); i++)
    {
        if (stored_state[i] != boot_state[i])
        {
            return 1;
        }
    }
    return 0;
}

void app_update_check_crc(void)
{
    uint32_t received_crc;
    uint32_t crc_value;

    int_can_receive_msg(&can_rx_msg, &can_rx_msg.count);
    if ((can_rx_msg.count > 0) && (can_rx_msg.RxHeader.IDE == CAN_ID_STD) && (can_rx_msg.RxHeader.StdId == APP_UPDATE_DATA_CAN_ID) &&
        (can_rx_msg.RxHeader.RTR == CAN_RTR_DATA) && (can_rx_msg.RxHeader.DLC == 4))
    {
        /* 末尾单独一帧发送小端 CRC，只对已接收的 BIN 计算校验值。 */
        received_crc = (uint32_t)can_rx_msg.data[0] | ((uint32_t)can_rx_msg.data[1] << 8) |
                       ((uint32_t)can_rx_msg.data[2] << 16) | ((uint32_t)can_rx_msg.data[3] << 24);
        crc_value = app_crc_cal(can_rx_buffer, can_rx_len);
        if (crc_value == received_crc)
        {
            uint8_t success_message[] = "CRC OK\r\n";
            uint8_t store_message[] = "Update save OK\r\n";

            HAL_UART_Transmit(&huart1, success_message, sizeof(success_message) - 1, 100);
            if (app_update_save_image() != 0)
            {
                uint8_t store_error_message[] = "Update save FAIL\r\n";

                app_update_state = APP_UPDATE_ERROR;
                HAL_UART_Transmit(&huart1, store_error_message, sizeof(store_error_message) - 1, 100);
                return;
            }
            app_update_state = APP_UPDATE_DONE;
            HAL_UART_Transmit(&huart1, store_message, sizeof(store_message) - 1, 100);
            return;
        }
    }
    else if ((uint32_t)(HAL_GetTick() - last_receive_tick) < APP_UPDATE_CRC_TIMEOUT_MS)
    {
        return;
    }

    /* CRC 不匹配或最后一帧 BIN 后 2 秒仍无 CRC，清理后允许下一轮命令。 */
    uint8_t error_message[] = "CRC FAIL\r\n";

    HAL_UART_Transmit(&huart1, error_message, sizeof(error_message) - 1, 100);
    memset(can_rx_buffer, 0, sizeof(can_rx_buffer));
    can_rx_len = 0;
    app_update_state = APP_UPDATE_IDLE;
}

void app_update_work(void)
{
    /* HAL 装载邮箱会读取 8 字节，DLC 仍只发送有效的 3 字节。 */
    uint8_t command[8] = {'h', 'y', 'f'};

    switch (app_update_state)
    {
        case APP_UPDATE_IDLE:
            app_update_led_blink();
            break;

        case APP_UPDATE_SEND_COMMAND:
        {
            uint8_t start_message[] = "Update receive start\r\n";

            can_rx_len = 0;
            memset(can_rx_buffer, 0, sizeof(can_rx_buffer));
            int_can_send(0, command, 3);
            last_receive_tick = HAL_GetTick();
            app_update_state = APP_UPDATE_RECEIVE;
            HAL_UART_Transmit(&huart1, start_message, sizeof(start_message) - 1, 100);
            break;
        }

        case APP_UPDATE_RECEIVE:
            app_update_receive();
            break;

        case APP_UPDATE_VERIFY:
            app_update_check_crc();
            break;

        case APP_UPDATE_DONE:
            /* BIN 和元数据已保存，完成状态不重复擦写。 */
            break;

        case APP_UPDATE_ERROR:
            /* 接收异常，停止本轮，复位后重新开始。 */
            break;
    }
}
