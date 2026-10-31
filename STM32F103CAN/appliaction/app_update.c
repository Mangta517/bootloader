#include "app_update.h"
#include "app_bootloader.h"
#include "int_can.h"
#include <stdio.h>
#include <string.h>

app_update_state_enum app_update_state = APP_UPDATE_WAIT;

void app_update_init(void)
{
    can_init();
    app_update_state = APP_UPDATE_WAIT;
    printf("CAN init OK, 200000 bit/s\r\nWaiting update command: hyf\r\n");
}

void app_update_work(void)
{
    switch (app_update_state)
    {
        case APP_UPDATE_WAIT:
            app_update_wait();
            break;

        case APP_UPDATE_SEND:
            app_update_send();
            break;

        case APP_UPDATE_DONE:
        case APP_UPDATE_ERROR:
            break;
    }
}

void app_update_wait(void)
{
    CAN_RxHeaderTypeDef header = {0};
    uint8_t data[8] = {0};

    if (can_receive(&header, data) != 0)
    {
        return;
    }

    /* 命令必须是 ID 0 的标准数据帧，长度为 3，内容完整匹配 hyf。 */
    if ((header.IDE != CAN_ID_STD) || (header.StdId != 0) || (header.RTR != CAN_RTR_DATA) ||
        (header.DLC != 3) || (memcmp(data, "hyf", 3) != 0))
    {
        printf("Invalid update command; waiting for hyf\r\n");
        return;
    }

    if ((app_flash_state != APP_FLASH_DONE) || (app_size_bytes == 0) || (app_size_bytes > APP_FLASH_CAPACITY_BYTES))
    {
        printf("No valid update in Flash\r\n");
        return;
    }

    app_update_state = APP_UPDATE_SEND;
    printf("hyf OK, enter send update\r\n");
}

void app_update_send(void)
{
    uint8_t data[8] = {0};
    const uint8_t *flash_data = (const uint8_t *)(uintptr_t)APP_FLASH_START_ADDR;
    uint32_t remaining_bytes = app_size_bytes;
    uint32_t send_offset = 0;
    uint32_t length;
    uint32_t index;

    if (app_update_state != APP_UPDATE_SEND)
    {
        return;
    }
    while (remaining_bytes > 0)
    {
        if (remaining_bytes >= 8)
        {
            length = 8;
        }
        else
        {
            length = remaining_bytes;
        }

        /* 只发送有效 BIN，半字写入时补齐的 FF 不进入 CAN 数据。 */
        for (index = 0; index < length; index++)
        {
            data[index] = flash_data[send_offset + index];
        }
        if (can_send(APP_UPDATE_DATA_CAN_ID, data, (uint8_t)length) != 0)
        {
            app_update_state = APP_UPDATE_ERROR;
            printf("CAN send error, offset:%lu\r\n", (unsigned long)send_offset);
            return;
        }

        remaining_bytes -= length;
        send_offset += length;
        /* 给 F407 留出一帧串口打印的时间。 */
        HAL_Delay(APP_UPDATE_FRAME_DELAY_MS);
    }

    app_update_state = APP_UPDATE_DONE;
    printf("CAN send done,len:%lu\r\n", (unsigned long)send_offset);
}
