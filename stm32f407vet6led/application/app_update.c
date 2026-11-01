#include "app_update.h"
#include "int_can.h"
#include "usart.h"
#include <string.h>
#include "crc.h"
#define APP_UPDATE_COMMAND_SIZE_BYTES 32
#define APP_UPDATE_LED_INTERVAL_MS    500
#define APP_UPDATE_DATA_CAN_ID        1
#define APP_UPDATE_RECEIVE_IDLE_MS    1000

volatile app_update_state_enum app_update_state = APP_UPDATE_IDLE;

static char uart_rx_buffer[APP_UPDATE_COMMAND_SIZE_BYTES];
/* 主循环接收，缓冲区和累计长度同时供后续 CRC 校验使用。 */
/* CRC 外设按 32 位字读取，缓冲区及跳过 4 字节包头后的数据均保持 4 字节对齐。 */
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
        for ( uint8_t i=0; i < can_rx_msg.RxHeader.DLC; i++)
        {
            can_rx_buffer[can_rx_len + i] = can_rx_msg.data[i];
        }
        can_rx_len += can_rx_msg.RxHeader.DLC;
        last_receive_tick = HAL_GetTick();
    }

    /* 发送端没有结束帧，连续 1 秒无有效数据后结束本轮接收。 */
    if ((uint32_t)(HAL_GetTick() - last_receive_tick) >= APP_UPDATE_RECEIVE_IDLE_MS)
    {
        if (can_rx_len > 0)
        {
            app_update_state = APP_UPDATE_VERIFY;
        }
        else
        {
            app_update_state = APP_UPDATE_ERROR;
        }
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

void app_update_check_crc(void)
{
    uint32_t received_crc;
    uint32_t crc_value;

    /* 前 4 字节为 CRC，至少还需要 1 字节有效载荷。 */
    if (can_rx_len > 4)
    {
        /* 发送端按小端发送 CRC，包头不参与有效载荷的 CRC 计算。 */
        received_crc = (uint32_t)can_rx_buffer[0] | ((uint32_t)can_rx_buffer[1] << 8) |
                       ((uint32_t)can_rx_buffer[2] << 16) | ((uint32_t)can_rx_buffer[3] << 24);
        crc_value = app_crc_cal(can_rx_buffer + 4, can_rx_len - 4);
        if (crc_value == received_crc)
        {
            uint8_t success_message[] = "CRC校验通过\r\n";

            HAL_UART_Transmit(&huart1, success_message, sizeof(success_message) - 1, 100);
            app_update_state = APP_UPDATE_DONE;
            return;
        }
    }

    /* 校验失败先清理本轮数据，最后回到空闲，允许串口启动下一轮。 */
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
            can_rx_len = 0;
            memset(can_rx_buffer, 0, sizeof(can_rx_buffer));
            int_can_send(0, command, 3);
            last_receive_tick = HAL_GetTick();
            app_update_state = APP_UPDATE_RECEIVE;
            break;

        case APP_UPDATE_RECEIVE:
            app_update_receive();
            break;

        case APP_UPDATE_VERIFY:
            app_update_check_crc();
            break;

        case APP_UPDATE_DONE:
            /* CRC 匹配，后续更新处理待实现。 */
            break;

        case APP_UPDATE_ERROR:
            /* 接收异常，停止本轮，复位后重新开始。 */
            break;
    }
}
