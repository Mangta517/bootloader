#include "app_update.h"
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

    app_update_state = APP_UPDATE_SEND;
    printf("hyf OK, enter send update\r\n");
}

void app_update_send(void)
{
    /* 后续在这里发送更新内容。 */
}
