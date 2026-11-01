#include "app_update.h"
#include "app_bootloader.h"
#include "int_can.h"
#include "crc.h"
#include <stdio.h>
#include <string.h>

app_update_state_enum app_update_state = APP_UPDATE_WAIT;

void app_update_init(void)
{
    can_init();
    app_update_state = APP_UPDATE_WAIT;
    printf("Waiting update command: hyf\r\n");
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
        return;
    }

    if ((app_flash_state != APP_FLASH_DONE) || (app_size_bytes == 0) || (app_size_bytes > APP_FLASH_CAPACITY_BYTES))
    {
        printf("No valid update in Flash\r\n");
        return;
    }

    app_update_state = APP_UPDATE_SEND;
    printf("Update send start\r\n");
}

static uint32_t app_crc_cal(uint32_t flash_addr, uint32_t len)
{
    uint32_t *p_data = (uint32_t *)(uintptr_t)flash_addr;
    const uint8_t *flash_data = (const uint8_t *)(uintptr_t)flash_addr;
    uint32_t word_count = len / 4;
    uint32_t tail_word = 0;
    uint32_t index;
    uint32_t crc_val;

    /* Calculate 内部复位 CRC，Flash 中完整的 32 位字直接参与计算。 */
    crc_val = HAL_CRC_Calculate(&hcrc, p_data, word_count);
    if ((len % 4) != 0)
    {
        /* 尾部在 RAM 中按小端补 0，与接收端一致，不改写 Flash 或读取尾部 FF。 */
        for (index = 0; index < len % 4; index++)
        {
            tail_word |= (uint32_t)flash_data[word_count * 4 + index] << (index * 8);
        }
        crc_val = HAL_CRC_Accumulate(&hcrc, &tail_word, 1);
    }
    return crc_val;
}

void app_update_send(void)
{
    uint8_t data[8] = {0};
    const uint8_t *flash_data = (const uint8_t *)(uintptr_t)APP_FLASH_START_ADDR;
    uint32_t remaining_bytes = app_size_bytes;
    uint32_t send_offset = 0;
    uint32_t crc_val;
    uint32_t length;
    uint32_t index;

    if (app_update_state != APP_UPDATE_SEND)
    {
        return;
    }

    /* CRC 只覆盖原始 BIN，先计算，避免占用 BIN 结束后的等待时间。 */
    crc_val = app_crc_cal(APP_FLASH_START_ADDR, app_size_bytes);
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
        /* BIN 相邻帧保持 10 ms 间隔，最后一帧之后单独等待 1100 ms。 */
        if (remaining_bytes > 0)
        {
            HAL_Delay(APP_UPDATE_FRAME_DELAY_MS);
        }
    }

    HAL_Delay(APP_UPDATE_CRC_DELAY_MS);
    for (index = 0; index < 4; index++)
    {
        data[index] = (uint8_t)(crc_val >> (index * 8));
    }
    if (can_send(APP_UPDATE_DATA_CAN_ID, data, 4) != 0)
    {
        app_update_state = APP_UPDATE_ERROR;
        printf("CAN send CRC error\r\n");
        return;
    }

    app_update_state = APP_UPDATE_DONE;
    printf("CAN send done,len:%lu\r\n", (unsigned long)send_offset);
}
