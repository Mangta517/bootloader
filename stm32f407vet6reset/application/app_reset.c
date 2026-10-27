#include "app_reset.h"
#include <stdio.h>

Bootloader_state boot_state = BOOTLOADER_STATE_DEFAULT;

uint8_t uart1_rx_start_buff[64] = {0};
uint16_t uart1_rx_start_len = 0;
uint32_t uart1_rx_app_len = 0;

void App_bootloader_Init(void)
{
    printf("reset start\r\n");
    printf("wait user send \n");
    printf("send 'start:len'to start\n");
    boot_state = BOOTLOADER_STATE_INIT;
}

// 等待串口升级命令。
void App_bootloader_run(void)
{
    uint8_t received_byte;
    static uint8_t command_overflow = 0;
    char *start_str;
    unsigned long app_length;

    /* 通过串口空闲标志识别命令结束，收到合法命令后再启动 DMA。 */
    while (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_RXNE) != RESET)
    {
        /* 前面的状态读取与此 DATA 读取同时清除 RXNE 和旧 IDLE。 */
        received_byte = (uint8_t)huart1.Instance->DR;

        if (uart1_rx_start_len < (sizeof(uart1_rx_start_buff) - 1))
        {
            uart1_rx_start_buff[uart1_rx_start_len] = received_byte;
            uart1_rx_start_len++;
        }
        else
        {
            command_overflow = 1;
        }
    }

    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_IDLE) == RESET)
    {
        return;
    }

    __HAL_UART_CLEAR_IDLEFLAG(&huart1);

    if (command_overflow != 0)
    {
        printf("Bad command\r\n");
        command_overflow = 0;
        uart1_rx_start_len = 0;
        return;
    }

    uart1_rx_start_buff[uart1_rx_start_len] = 0;
    start_str = strstr((char *)uart1_rx_start_buff, "start:");

    if (uart1_rx_start_len > 0)
    {
        if (start_str != NULL)
        {
            /* 最大 480 KiB；转换结果先检查范围，避免截断后绕过容量限制。 */
            app_length = strtoul(start_str + 6, NULL, 10);
            if ((start_str[6] >= '0') && (start_str[6] <= '9') &&
                (app_length > 0) && (app_length <= APP_SIZE_BYTES))
            {
                uart1_rx_app_len = (uint32_t)app_length;
                printf("Length=%lu\r\n", (unsigned long)uart1_rx_app_len);
                boot_state = BOOTLOADER_STATE_RUN;
            }
            else
            {
                printf("Bad length\r\n");
            }
        }
        else
        {
            printf("Bad command\r\n");
        }
    }

    uart1_rx_start_len = 0;
}

// 接收数据
void App_bootloader_rec_data(void)
{
    Int_bootloader_Process();

    // UART/DMA 错误不等待空闲时间戳，沿用检查阶段的停收和复位流程。
    if (uart1_rx_error_count != 0)
    {
        boot_state = BOOTLOADER_STATE_CHECK_DATA;
        return;
    }

    // 接收空闲确认后等待 2 秒；新数据会清除时间戳并重新计时。
    if ((uart1_rx_complete_tick != 0) && ((uint32_t)(HAL_GetTick() - uart1_rx_complete_tick) >= 2000))
    {
        boot_state = BOOTLOADER_STATE_CHECK_DATA;
    }
}

// 收尾停止 DMA、处理积压数据并补写末字节，PENDING 时下一轮继续。
bootloader_result_enum App_bootloader_check_data(void)
{
    bootloader_result_enum status = bootloader_finish_image();

    if (status != BOOTLOADER_RESULT_OK)
    {
        return status;
    }
    if (uart1_rx_app_len != uart1_rx_full_len)
    {
        return BOOTLOADER_RESULT_ERROR;
    }

    printf("ok,len:%lu\n", (unsigned long)uart1_rx_app_len);
    boot_state = BOOTLOADER_STATE_JUMP_TO_APP;
    return BOOTLOADER_RESULT_OK;
}

uint8_t App_bootloader_jump_to_app(void)
{
    return Int_bootloader_jump_to_app();
}

// 串口升级状态机，在主循环持续调用。
void App_bootloader_work(void)
{
    switch (boot_state)
    {
        case BOOTLOADER_STATE_DEFAULT:
            App_bootloader_Init();
            break;

        case BOOTLOADER_STATE_INIT:
            App_bootloader_run();
            break;

        case BOOTLOADER_STATE_RUN:
            // 根据本次 BIN 长度一次性擦除全部目标扇区。
            if (bootloader_prepare_image(uart1_rx_app_len) != 0)
            {
                printf("Flash erase error; reset\r\n");
                NVIC_SystemReset();
                break;
            }
            if (bootloader_start_dma() != 0)
            {
                printf("RX start error; reset\r\n");
                NVIC_SystemReset();
                break;
            }
            boot_state = BOOTLOADER_STATE_REC_DATA;
            printf("Flash erased; send data\r\n");
            break;

        case BOOTLOADER_STATE_REC_DATA:
            App_bootloader_rec_data();
            break;

        case BOOTLOADER_STATE_CHECK_DATA:
        {
            bootloader_result_enum check_status = App_bootloader_check_data();

            if (check_status == BOOTLOADER_RESULT_ERROR)
            {
                printf("RX error; reset\n");
                NVIC_SystemReset();
            }
            break;
        }

        case BOOTLOADER_STATE_JUMP_TO_APP:
            if (App_bootloader_jump_to_app() != 0)
            {
                printf("jump error\n");
                NVIC_SystemReset();
            }
            break;

        default:
            break;
    }
}
