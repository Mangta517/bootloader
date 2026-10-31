#include "app_update.h"
#include "int_can.h"
#include "usart.h"
#include <string.h>
#include <stdio.h>

#define APP_UPDATE_COMMAND_SIZE_BYTES 32
#define APP_UPDATE_LED_INTERVAL_MS    500
#define APP_UPDATE_DATA_CAN_ID        1
#define APP_UPDATE_RECEIVE_IDLE_MS    1000

volatile app_update_state_enum app_update_state = APP_UPDATE_IDLE;
volatile uint32_t app_update_size_bytes;

static char uart_rx_buffer[APP_UPDATE_COMMAND_SIZE_BYTES];
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

static void app_update_receive(void)
{
    CAN_MSG_RECEIVE message;
    uint8_t count;
    char output[48];
    int length;

    int_can_receive_msg(&message, &count);
    if ((count > 0) && (message.RxHeader.IDE == CAN_ID_STD) && (message.RxHeader.StdId == APP_UPDATE_DATA_CAN_ID) &&
        (message.RxHeader.RTR == CAN_RTR_DATA) && (message.RxHeader.DLC > 0) && (message.RxHeader.DLC <= sizeof(message.data)))
    {
        app_update_size_bytes += message.RxHeader.DLC;
        last_receive_tick = HAL_GetTick();
    }

    /* 发送端没有结束帧，以连续 1 秒未收到有效数据作为本轮测试结束。 */
    if ((uint32_t)(HAL_GetTick() - last_receive_tick) >= APP_UPDATE_RECEIVE_IDLE_MS)
    {
        length = sprintf(output, "CAN receive done,len:%lu\r\n", (unsigned long)app_update_size_bytes);
        HAL_UART_Transmit(&huart1, (uint8_t *)output, (uint16_t)length, 100);
        app_update_state = APP_UPDATE_IDLE;
    }
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
            app_update_size_bytes = 0;
            int_can_send(0, command, 3);
            last_receive_tick = HAL_GetTick();
            app_update_state = APP_UPDATE_RECEIVE;
            break;

        case APP_UPDATE_RECEIVE:
            app_update_receive();
            break;

        case APP_UPDATE_VERIFY:
            /* 暂不实现固件校验。 */
            break;

        case APP_UPDATE_DONE:
            /* 暂不修改 EEPROM 更新状态。 */
            break;
    }
}
