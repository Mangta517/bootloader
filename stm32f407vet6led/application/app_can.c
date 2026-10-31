#include "app_can.h"
#include "int_can.h"

#define APP_CAN_SEND_INTERVAL_MS  1000

void app_can_process(void)
{
    static uint32_t last_send_tick;
    uint32_t now_ms = HAL_GetTick();
    uint8_t command[] = "hyf";

    if ((uint32_t)(now_ms - last_send_tick) >= APP_CAN_SEND_INTERVAL_MS)
    {
        last_send_tick = now_ms;
        /* F103 更新命令：标准 ID 0，仅发送 hyf 三字节，不含字符串结束符。 */
        int_can_send(0, command, sizeof(command) - 1);
    }
}
