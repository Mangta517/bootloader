"""Run the real circular DMA receiver and Flash application with a mock F103 Flash."""
from pathlib import Path
import runpy
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
support = runpy.run_path(str(root / "tools/test_uart_receive.py"))
mock_main = r'''
#ifndef TEST_MAIN_H
#define TEST_MAIN_H
#include "usart.h"
typedef uint32_t HAL_StatusTypeDef;
typedef struct
{
    uint32_t TypeErase, Banks, PageAddress, NbPages;
} FLASH_EraseInitTypeDef;
#define FLASH_PAGE_SIZE 1024
#define FLASH_TYPEERASE_PAGES 0
#define FLASH_TYPEPROGRAM_HALFWORD 1
#define UART_FLAG_RXNE 1
#define UART_FLAG_IDLE 2
#define __HAL_UART_GET_FLAG(uart, flag) mock_uart_flag(flag)
#define __HAL_UART_CLEAR_IDLEFLAG(uart) mock_uart_clear_idle()
#define READ_REG(reg) mock_uart_read()
uint32_t mock_uart_flag(uint32_t flag);
uint32_t mock_uart_read(void);
void mock_uart_clear_idle(void);
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *uart);
void NVIC_SystemReset(void);
uint32_t HAL_FLASH_Unlock(void);
uint32_t HAL_FLASH_Lock(void);
uint32_t HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *page_error);
uint32_t HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data);
#endif
'''
prefix = support["harness"].split("static uint16_t receive(void)", 1)[0]
prefix = prefix.replace("static uint8_t *dma_buffer;", "static uint32_t dma_start_calls;\nstatic uint8_t *dma_buffer;")
prefix = prefix.replace("    dma_buffer = data;", "    dma_start_calls++;\n    dma_buffer = data;")
harness = prefix + r'''
#include "app_bootloader.h"
#include "main.h"
#include <string.h>
#include <stdarg.h>

static uint8_t flash_memory[65536];
static uint32_t erase_calls, program_calls, locked, stop_calls;
static uint32_t fail_unlock, fail_erase, fail_program, fail_lock;
static char serial_output[2048];
static uint32_t serial_length, reset_calls;
static char command_input[128];
static uint32_t command_index, command_size, command_idle;

int test_printf(const char *format, ...)
{
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(serial_output + serial_length, sizeof(serial_output) - serial_length, format, arguments);
    va_end(arguments);
    assert(length >= 0 && (uint32_t)length < sizeof(serial_output) - serial_length);
    serial_length += (uint32_t)length;
    return length;
}
void NVIC_SystemReset(void)
{
    reset_calls++;
}

uint32_t mock_uart_flag(uint32_t flag)
{
    if (flag == UART_FLAG_RXNE)
    {
        return command_index < command_size;
    }
    return command_idle;
}
uint32_t mock_uart_read(void)
{
    assert(command_index < command_size);
    return (uint8_t)command_input[command_index++];
}
void mock_uart_clear_idle(void)
{
    command_idle = 0;
}
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *uart)
{
    assert(uart == &huart1);
    stop_calls++;
    return HAL_OK;
}
uint32_t HAL_FLASH_Unlock(void)
{
    if (fail_unlock != 0)
    {
        return 1;
    }
    locked = 0;
    return HAL_OK;
}
uint32_t HAL_FLASH_Lock(void)
{
    if (fail_lock != 0)
    {
        return 1;
    }
    locked = 1;
    return HAL_OK;
}
uint32_t HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *page_error)
{
    uint32_t offset = erase->PageAddress - 0x08000000;
    assert(locked == 0 && erase->TypeErase == FLASH_TYPEERASE_PAGES && erase->NbPages > 0);
    assert(erase->PageAddress == APP_FLASH_START_ADDR && offset + erase->NbPages * FLASH_PAGE_SIZE <= sizeof(flash_memory));
    assert(dma_start_calls == 0);
    erase_calls += erase->NbPages;
    if (fail_erase != 0)
    {
        return 1;
    }
    memset(flash_memory + offset, 0xFF, erase->NbPages * FLASH_PAGE_SIZE);
    *page_error = UINT32_MAX;
    return HAL_OK;
}
uint32_t HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data)
{
    uint32_t offset = address - 0x08000000;
    assert(locked == 0 && type == FLASH_TYPEPROGRAM_HALFWORD && address % 2 == 0);
    assert(address >= APP_FLASH_START_ADDR && address + 2 <= APP_FLASH_END_ADDR);
    assert(flash_memory[offset] == 0xFF && flash_memory[offset + 1] == 0xFF);
    program_calls++;
    if (fail_program != 0)
    {
        return 1;
    }
    flash_memory[offset] = (uint8_t)data;
    flash_memory[offset + 1] = (uint8_t)(data >> 8);
    return HAL_OK;
}
static void send_command(const char *text, uint8_t idle)
{
    assert(strlen(text) < sizeof(command_input));
    strcpy(command_input, text);
    command_index = 0;
    command_size = (uint32_t)strlen(text);
    command_idle = idle;
    App_bootloader_update();
}
static void begin(uint32_t expected)
{
    char command[32];
    reset();
    memset(flash_memory, 0xA5, sizeof(flash_memory));
    erase_calls = 0;
    program_calls = 0;
    stop_calls = 0;
    dma_start_calls = 0;
    locked = 1;
    fail_unlock = 0;
    fail_erase = 0;
    fail_program = 0;
    fail_lock = 0;
    command_index = 0;
    command_size = 0;
    command_idle = 0;
    serial_length = 0;
    serial_output[0] = 0;
    reset_calls = 0;
    App_bootloader_init();
    assert(strcmp(serial_output, "reset start\r\nwait user send \nsend 'start:len'to start\n") == 0);
    assert(app_flash_state == APP_FLASH_WAIT && app_size_bytes == 0);
    if (expected != 0)
    {
        snprintf(command, sizeof(command), "start:%lu\r\n", (unsigned long)expected);
        send_command(command, 1);
        assert(app_flash_state == APP_FLASH_RECEIVING && uart1_rx_app_len == expected);
        assert(dma_start_calls == 1 && locked == 1);
    }
}
static void poll(uint32_t count)
{
    uint32_t index;
    for (index = 0; index < count; index++)
    {
        App_bootloader_update();
    }
}
static void tail(void)
{
    poll(1);
    tick += UART1_RX_TIMEOUT_MS;
    poll(20);
}
static void check_data(uint32_t length)
{
    uint32_t index;
    for (index = 0; index < 32768; index++)
    {
        assert(flash_memory[index] == 0xA5);
    }
    for (index = 0; index < length; index++)
    {
        assert(flash_memory[32768 + index] == (uint8_t)index);
    }
}
int main(void)
{
    uint32_t index;
    uint32_t before_program;
    const char *invalid[] = {"abc", "start:0", "start:-1", "start:32769", "start:999999999999999999999999"};

    begin(0);
    tick = 3000;
    poll(1);
    assert(app_flash_state == APP_FLASH_WAIT && erase_calls == 0 && dma_start_calls == 0);
    for (index = 0; index < sizeof(invalid) / sizeof(invalid[0]); index++)
    {
        send_command(invalid[index], 1);
        assert(app_flash_state == APP_FLASH_WAIT && erase_calls == 0 && dma_start_calls == 0);
    }
    send_command("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", 1);
    assert(app_flash_state == APP_FLASH_WAIT && erase_calls == 0);
    send_command("sta", 0);
    assert(app_flash_state == APP_FLASH_WAIT);
    send_command("rt:3\r\n", 1);
    assert(app_flash_state == APP_FLASH_RECEIVING && uart1_rx_app_len == 3 && dma_start_calls == 1);

    begin(3);
    feed(3, 0);
    tail();
    assert(app_size_bytes == 3 && app_flash_state == APP_FLASH_RECEIVING);
    assert(program_calls == 1 && erase_calls == 1 && locked == 1);
    assert(flash_memory[32770] == 0xFF);
    tick += APP_FLASH_FINISH_DELAY_MS - 1;
    poll(1);
    assert(app_flash_state == APP_FLASH_RECEIVING && program_calls == 1);
    tick++;
    poll(1);
    assert(app_flash_state == APP_FLASH_DONE && program_calls == 2 && app_size_bytes == 3 && stop_calls == 1);
    assert(strstr(serial_output, "Length=3\r\nFlash erased; send data\r\nok,len:3\n") != NULL);
    check_data(3);
    assert(flash_memory[32771] == 0xFF && flash_memory[33792] == 0xA5);
    feed(10, 0);
    poll(1);
    assert(program_calls == 2 && app_size_bytes == 3);

    begin(4);
    feed(3, 0);
    tail();
    tick += 1999;
    feed(1, 0);
    poll(1);
    assert(uart1_rx_complete_tick == 0 && app_flash_state == APP_FLASH_RECEIVING);
    tick += UART1_RX_TIMEOUT_MS;
    poll(1);
    assert(app_size_bytes == 4 && program_calls == 2);
    tick += 1999;
    poll(1);
    assert(app_flash_state == APP_FLASH_RECEIVING);
    tick++;
    poll(1);
    assert(app_flash_state == APP_FLASH_DONE && program_calls == 2);
    check_data(4);

    begin(1);
    feed(1, 0);
    tail();
    assert(app_size_bytes == 1 && program_calls == 0 && erase_calls == 1);
    tick += APP_FLASH_FINISH_DELAY_MS;
    poll(1);
    assert(app_flash_state == APP_FLASH_DONE && program_calls == 1);
    check_data(1);
    assert(flash_memory[32769] == 0xFF);

    begin(APP_FLASH_CAPACITY_BYTES);
    for (index = 0; index < APP_FLASH_CAPACITY_BYTES / UART1_RX_DMA_HALF_SIZE; index++)
    {
        feed(UART1_RX_DMA_HALF_SIZE, 0);
        poll(20);
        tick++;
    }
    assert(app_size_bytes == APP_FLASH_CAPACITY_BYTES && erase_calls == 32);
    tail();
    tick += APP_FLASH_FINISH_DELAY_MS;
    poll(1);
    assert(app_flash_state == APP_FLASH_DONE && locked == 1);
    check_data(APP_FLASH_CAPACITY_BYTES);

    begin(4);
    feed(3, 0);
    tail();
    tick += APP_FLASH_FINISH_DELAY_MS;
    poll(1);
    assert(app_flash_state == APP_FLASH_ERROR && program_calls == 1 && stop_calls == 0 && reset_calls == 1);
    assert(strstr(serial_output, "RX error; reset\n") != NULL);
    assert(flash_memory[32770] == 0xFF);
    begin(2);
    feed(3, 0);
    tail();
    assert(app_flash_state == APP_FLASH_ERROR && program_calls == 0);

    begin(APP_FLASH_CAPACITY_BYTES);
    feed(UART1_RX_RING_SIZE + 1, 0);
    poll(1);
    assert(uart1_rx_overflow_count == 1 && app_flash_state == APP_FLASH_ERROR && program_calls == 0);
    begin(3);
    HAL_UART_ErrorCallback(&huart1);
    poll(1);
    assert(app_flash_state == APP_FLASH_ERROR && program_calls == 0);

    for (index = 0; index < 3; index++)
    {
        begin(0);
        switch (index)
        {
            case 0:
                fail_unlock = 1;
                break;
            case 1:
                fail_erase = 1;
                break;
            case 2:
                fail_lock = 1;
                break;
        }
        send_command("start:2", 1);
        assert(app_flash_state == APP_FLASH_ERROR && dma_start_calls == 0 && reset_calls == 1);
        assert(strstr(serial_output, "Flash erase error; reset\r\n") != NULL);
    }
    begin(0);
    start_error = 1;
    send_command("start:2", 1);
    assert(app_flash_state == APP_FLASH_ERROR && uart1_rx_error_count == 1 && reset_calls == 1);
    assert(strstr(serial_output, "RX start error; reset\r\n") != NULL);

    for (index = 0; index < 3; index++)
    {
        begin(2);
        switch (index)
        {
            case 0:
                fail_unlock = 1;
                break;
            case 1:
                fail_program = 1;
                break;
            case 2:
                fail_lock = 1;
                break;
        }
        feed(2, 0);
        tail();
        assert(app_flash_state == APP_FLASH_ERROR);
        before_program = program_calls;
        poll(1);
        assert(program_calls == before_program);
    }

    begin(3);
    tick = UINT32_MAX - 1000;
    feed(3, 0);
    tail();
    tick += APP_FLASH_FINISH_DELAY_MS;
    poll(1);
    assert(app_flash_state == APP_FLASH_DONE);
    check_data(3);
    puts("PASS: start:length parsing, pre-erase before DMA, 2-second idle and exact length comparison,");
    puts("      original UART feedback/reset behavior, binary stream, odd tails, full 32 KiB and tick wrap");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="f103-app-flash-") as folder:
    temporary = Path(folder)
    (temporary / "usart.h").write_text(support["mock_header"], encoding="utf-8")
    (temporary / "main.h").write_text(mock_main, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_app_flash.exe"
    subprocess.run([
        support["compiler"], "-std=c99", "-Wall", "-Wextra", "-Werror", "-Dprintf=test_printf",
        f"-I{temporary}", f"-I{root / 'interface'}", f"-I{root / 'appliaction'}",
        str(root / "interface/int_uart.c"), str(root / "appliaction/app_bootloader.c"),
        str(temporary / "test.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
