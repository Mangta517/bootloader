#include "app_bootloader.h"
#include "int_uart.h"
#include "main.h"
#include "usart.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

app_flash_state_enum app_flash_state = APP_FLASH_WAIT;
uint32_t app_size_bytes;
uint32_t uart1_rx_app_len;

static uint8_t command_buffer[64];
static uint16_t command_length;
static uint8_t command_overflow;

static uint32_t flash_write_offset;
static uint8_t last_byte;
static uint8_t last_byte_pending;

void App_bootloader_init(void)
{
    app_flash_state = APP_FLASH_WAIT;
    app_size_bytes = 0;
    uart1_rx_app_len = 0;
    command_length = 0;
    command_overflow = 0;
    flash_write_offset = 0;
    last_byte = 0;
    last_byte_pending = 0;
    printf("reset start\r\n");
    printf("wait user send \n");
    printf("send 'start:len'to start\n");
}

/* 沿用 F407 的 start:十进制长度协议，命令和 BIN 分开发送。 */
static void App_bootloader_wait_start(void)
{
    FLASH_EraseInitTypeDef erase = {0};
    uint32_t page_error;
    uint8_t received_byte;
    char *start_str;
    unsigned long app_length;
    HAL_StatusTypeDef erase_status;
    HAL_StatusTypeDef lock_status;

    while (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_RXNE) != RESET)
    {
        received_byte = (uint8_t)READ_REG(huart1.Instance->DR);
        if (command_length < sizeof(command_buffer) - 1)
        {
            command_buffer[command_length++] = received_byte;
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
        command_length = 0;
        return;
    }
    if (command_length == 0)
    {
        return;
    }
    command_buffer[command_length] = 0;
    command_length = 0;
    start_str = strstr((char *)command_buffer, "start:");
    if (start_str == NULL)
    {
        printf("Bad command\r\n");
        return;
    }
    app_length = strtoul(start_str + 6, NULL, 10);
    if ((start_str[6] < '0') || (start_str[6] > '9') || (app_length == 0) || (app_length > APP_FLASH_CAPACITY_BYTES))
    {
        printf("Bad length\r\n");
        return;
    }
    uart1_rx_app_len = (uint32_t)app_length;
    printf("Length=%lu\r\n", (unsigned long)uart1_rx_app_len);

    /* 接收前按声明长度擦除所有目标页，避免一边收 BIN 一边擦除。 */
    if (HAL_FLASH_Unlock() != HAL_OK)
    {
        app_flash_state = APP_FLASH_ERROR;
        printf("Flash erase error; reset\r\n");
        NVIC_SystemReset();
        return;
    }
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = APP_FLASH_START_ADDR;
    erase.NbPages = (uart1_rx_app_len + FLASH_PAGE_SIZE - 1) / FLASH_PAGE_SIZE;
    erase_status = HAL_FLASHEx_Erase(&erase, &page_error);
    lock_status = HAL_FLASH_Lock();
    if ((erase_status != HAL_OK) || (lock_status != HAL_OK))
    {
        app_flash_state = APP_FLASH_ERROR;
        printf("Flash erase error; reset\r\n");
        NVIC_SystemReset();
        return;
    }
    if (int_uart_init() != 0)
    {
        app_flash_state = APP_FLASH_ERROR;
        printf("RX start error; reset\r\n");
        NVIC_SystemReset();
        return;
    }
    app_flash_state = APP_FLASH_RECEIVING;
    printf("Flash erased; send data\r\n");
}

/* 分块数据按半字连续追加；奇数字节跨块保留，只有收尾才补 FF。 */
static uint8_t App_bootloader_write_app_flash(const uint8_t *data, uint16_t length, uint8_t finish)
{
    uint32_t address;
    uint16_t position = 0;
    uint16_t value;

    if (length > uart1_rx_app_len - app_size_bytes)
    {
        return 1;
    }
    if ((length == 0) && (last_byte_pending == 0))
    {
        return 0;
    }
    if (HAL_FLASH_Unlock() != HAL_OK)
    {
        return 1;
    }

    while ((position < length) || ((finish != 0) && (last_byte_pending != 0)))
    {
        if (last_byte_pending == 0)
        {
            last_byte = data[position++];
            last_byte_pending = 1;
        }
        if ((position == length) && (finish == 0))
        {
            break;
        }

        value = last_byte;
        if (position < length)
        {
            value |= (uint16_t)data[position++] << 8;
        }
        else
        {
            value |= 0xFF00;
        }

        address = APP_FLASH_START_ADDR + flash_write_offset;
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, address, value) != HAL_OK)
        {
            HAL_FLASH_Lock();
            return 1;
        }
        flash_write_offset += 2;
        last_byte_pending = 0;
    }

    if (HAL_FLASH_Lock() != HAL_OK)
    {
        return 1;
    }
    app_size_bytes += length;
    return 0;
}

void App_bootloader_update(void)
{
    static uint8_t buffer[UART1_RX_BUFFER_SIZE];
    uint16_t length;

    if ((app_flash_state == APP_FLASH_DONE) || (app_flash_state == APP_FLASH_ERROR))
    {
        return;
    }

    if (app_flash_state == APP_FLASH_WAIT)
    {
        App_bootloader_wait_start();
        return;
    }

    length = int_uart_receive(buffer);
    /* 已经丢字节的数据不能继续作为完整固件保存。 */
    if ((uart1_rx_error_count != 0) || (uart1_rx_overflow_count != 0))
    {
        app_flash_state = APP_FLASH_ERROR;
        printf("RX error; reset\n");
        NVIC_SystemReset();
        return;
    }
    if (length > 0)
    {
        if (App_bootloader_write_app_flash(buffer, length, 0) != 0)
        {
            app_flash_state = APP_FLASH_ERROR;
            printf("RX error; reset\n");
            NVIC_SystemReset();
            return;
        }
        app_flash_state = APP_FLASH_RECEIVING;
    }

    /* 接收层确认空闲且尾包已取完后，再等待 2 秒；新字节到来会清零此时间戳。 */
    if ((app_flash_state == APP_FLASH_RECEIVING) && (uart1_rx_complete_tick != 0) &&
        ((uint32_t)(HAL_GetTick() - uart1_rx_complete_tick) >= APP_FLASH_FINISH_DELAY_MS))
    {
        if ((app_size_bytes != uart1_rx_app_len) || (uart1_rx_full_len != uart1_rx_app_len))
        {
            app_flash_state = APP_FLASH_ERROR;
            printf("RX error; reset\n");
            NVIC_SystemReset();
            return;
        }
        if (App_bootloader_write_app_flash(0, 0, 1) != 0)
        {
            app_flash_state = APP_FLASH_ERROR;
            printf("RX error; reset\n");
            NVIC_SystemReset();
            return;
        }
        if (HAL_UART_AbortReceive(&huart1) != HAL_OK)
        {
            app_flash_state = APP_FLASH_ERROR;
            printf("RX error; reset\n");
            NVIC_SystemReset();
            return;
        }
        app_flash_state = APP_FLASH_DONE;
        printf("ok,len:%lu\n", (unsigned long)uart1_rx_app_len);
    }
}
