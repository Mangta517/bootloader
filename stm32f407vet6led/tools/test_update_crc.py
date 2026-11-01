"""Exercise the real F407 CAN receiver and W25Q16 page writes with mocked HAL."""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef UPDATE_CRC_TEST_HAL_H
#define UPDATE_CRC_TEST_HAL_H
#include <stdint.h>
typedef enum
{
    HAL_OK, HAL_ERROR, HAL_BUSY, HAL_TIMEOUT
} HAL_StatusTypeDef;
typedef enum
{
    GPIO_PIN_RESET, GPIO_PIN_SET
} GPIO_PinState;
typedef struct
{
    uint32_t unused;
} SPI_HandleTypeDef;
typedef struct
{
    uint32_t StdId, IDE, RTR, DLC;
} CAN_RxHeaderTypeDef;
typedef struct
{
    uint32_t unused;
} UART_HandleTypeDef;
typedef struct
{
    uint32_t value;
} CRC_HandleTypeDef;
extern UART_HandleTypeDef huart1;
extern CRC_HandleTypeDef hcrc;
extern SPI_HandleTypeDef hspi1;
#define CAN_ID_STD 0
#define CAN_ID_EXT 4
#define CAN_RTR_DATA 0
#define CAN_RTR_REMOTE 2
#define __ALIGN_BEGIN
#define __ALIGN_END __attribute__((aligned(4)))
#define __HAL_CRC_DR_RESET(handle) ((handle)->value = 0xFFFFFFFF)
#define led0_GPIO_Port ((void *)1)
#define led1_GPIO_Port ((void *)2)
#define led0_Pin 1
#define led1_Pin 2
#define F_CS_GPIO_Port ((void *)3)
#define F_CS_Pin 4
uint32_t HAL_GetTick(void);
void HAL_GPIO_TogglePin(void *port, uint16_t pin);
void HAL_GPIO_WritePin(void *port, uint16_t pin, GPIO_PinState state);
HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *handle, uint8_t *data, uint16_t length, uint32_t timeout);
HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *handle, uint8_t *tx_data, uint8_t *rx_data, uint16_t length, uint32_t timeout);
uint32_t HAL_UARTEx_ReceiveToIdle_IT(UART_HandleTypeDef *handle, uint8_t *data, uint16_t size);
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *handle);
uint32_t HAL_UART_Transmit(UART_HandleTypeDef *handle, uint8_t *data, uint16_t size, uint32_t timeout);
uint32_t HAL_CRC_Calculate(CRC_HandleTypeDef *handle, uint32_t *buffer, uint32_t word_count);
#endif
'''
harness = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "app_update.c"

UART_HandleTypeDef huart1;
CRC_HandleTypeDef hcrc;
SPI_HandleTypeDef hspi1;
static uint32_t tick, success_messages, crc_calls;
static uint32_t start_messages, receive_messages, error_messages;
static uint32_t flash_success_messages, flash_error_messages;
static uint8_t flash_data[W25Q16_SIZE_BYTES];
static uint8_t selected, write_enabled, flash_busy;
static uint8_t spi_frame[260];
static uint16_t spi_frame_length;
static uint32_t flash_actions, flash_fail_action, erased_sectors, programmed_pages, metadata_writes, bin_written;
static uint8_t eeprom_data[256], eeprom_fail_write, eeprom_fail_read;
static uint32_t eeprom_write_calls, eeprom_read_calls;
static CAN_MSG_RECEIVE incoming;
static uint8_t pending;

uint32_t HAL_GetTick(void)
{
    return tick;
}
void HAL_GPIO_TogglePin(void *port, uint16_t pin)
{
    (void)port;
    (void)pin;
}
void HAL_GPIO_WritePin(void *port, uint16_t pin, GPIO_PinState state)
{
    uint32_t address, length, index;

    assert(port == F_CS_GPIO_Port && pin == F_CS_Pin);
    if (state == GPIO_PIN_RESET)
    {
        assert(selected == 0);
        selected = 1;
        spi_frame_length = 0;
        return;
    }
    assert(selected == 1);
    selected = 0;
    if (spi_frame_length == 0)
    {
        return;
    }
    if (spi_frame[0] == W25Q16_CMD_WRITE_ENABLE)
    {
        assert(spi_frame_length == 1 && flash_busy == 0 && write_enabled == 0);
        write_enabled = 1;
        return;
    }
    if (spi_frame[0] == W25Q16_CMD_READ_STATUS_REG1)
    {
        assert(spi_frame_length == 1);
        return;
    }
    assert(write_enabled == 1 && flash_busy == 0 && spi_frame_length >= 4);
    address = ((uint32_t)spi_frame[1] << 16) | ((uint32_t)spi_frame[2] << 8) | spi_frame[3];
    if (spi_frame[0] == W25Q16_CMD_SECTOR_ERASE_4KB)
    {
        assert(spi_frame_length == 4 && address == erased_sectors * 4096);
        assert(address + 4096 <= sizeof(flash_data));
        memset(flash_data + address, 0xFF, 4096);
        erased_sectors++;
    }
    else
    {
        assert(spi_frame[0] == W25Q16_CMD_PAGE_PROGRAM && spi_frame_length > 4);
        length = spi_frame_length - 4;
        assert(length <= 256 - (address % 256));
        if (address == 0)
        {
            assert(bin_written == can_rx_len && length == 8 && metadata_writes == 0);
            for (index = 0; index < 4; index++)
            {
                assert(spi_frame[4 + index] == (uint8_t)(0x1000 >> (index * 8)));
                assert(spi_frame[8 + index] == (uint8_t)(can_rx_len >> (index * 8)));
            }
            metadata_writes++;
        }
        else
        {
            assert(address == 0x1000 + bin_written && length <= can_rx_len - bin_written);
            assert(memcmp(spi_frame + 4, can_rx_buffer + bin_written, length) == 0);
            bin_written += length;
        }
        for (index = 0; index < length; index++)
        {
            assert(flash_data[address + index] == 0xFF);
            flash_data[address + index] = spi_frame[4 + index];
        }
        programmed_pages++;
    }
    write_enabled = 0;
    flash_busy = 1;
}
HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *handle, uint8_t *data, uint16_t length, uint32_t timeout)
{
    assert(handle == &hspi1 && selected == 1 && length == 1 && timeout == 100);
    if ((spi_frame_length == 0) && ((*data == W25Q16_CMD_SECTOR_ERASE_4KB) || (*data == W25Q16_CMD_PAGE_PROGRAM)))
    {
        flash_actions++;
        if (flash_actions == flash_fail_action)
        {
            return HAL_ERROR;
        }
    }
    assert(spi_frame_length < sizeof(spi_frame));
    spi_frame[spi_frame_length++] = *data;
    return HAL_OK;
}
HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *handle, uint8_t *tx_data, uint8_t *rx_data,
                                       uint16_t length, uint32_t timeout)
{
    assert(handle == &hspi1 && selected == 1 && length == 1 && timeout == 100 && *tx_data == 0xFF);
    assert(spi_frame_length == 1 && spi_frame[0] == W25Q16_CMD_READ_STATUS_REG1);
    *rx_data = flash_busy;
    flash_busy = 0;
    return HAL_OK;
}
uint32_t HAL_UARTEx_ReceiveToIdle_IT(UART_HandleTypeDef *handle, uint8_t *data, uint16_t size)
{
    assert(handle == &huart1 && data == (uint8_t *)uart_rx_buffer && size == 31);
    return 0;
}
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *handle)
{
    assert(handle == &huart1);
    return 0;
}
uint32_t HAL_UART_Transmit(UART_HandleTypeDef *handle, uint8_t *data, uint16_t size, uint32_t timeout)
{
    const char message[] = "CRC OK\r\n";
    const char start_message[] = "Update receive start\r\n";
    const char error_message[] = "CRC FAIL\r\n";
    const char flash_message[] = "Update save OK\r\n";
    const char flash_error_message[] = "Update save FAIL\r\n";
    char receive_message[48];
    int length;

    assert(handle == &huart1 && timeout == 100);
    if ((size == sizeof(flash_message) - 1) && (memcmp(data, flash_message, size) == 0))
    {
        assert(metadata_writes == 1 && bin_written == can_rx_len && eeprom_write_calls == 1 && eeprom_read_calls == 1);
        assert(eeprom_data[0x10] == 0x01 && eeprom_data[0x11] == 0x5A && eeprom_data[0x12] == 0x6B);
        flash_success_messages++;
        return 0;
    }
    if ((size == sizeof(flash_error_message) - 1) && (memcmp(data, flash_error_message, size) == 0))
    {
        flash_error_messages++;
        return 0;
    }
    if ((size == sizeof(start_message) - 1) && (memcmp(data, start_message, size) == 0))
    {
        start_messages++;
        return 0;
    }
    if ((size == sizeof(error_message) - 1) && (memcmp(data, error_message, size) == 0))
    {
        error_messages++;
        return 0;
    }
    length = snprintf(receive_message, sizeof(receive_message), "CAN receive done,len:%lu\r\n", (unsigned long)can_rx_len);
    if ((size == length) && (memcmp(data, receive_message, size) == 0))
    {
        receive_messages++;
        return 0;
    }
    assert(size == sizeof(message) - 1 && memcmp(data, message, size) == 0);
    success_messages++;
    return 0;
}
uint32_t HAL_CRC_Calculate(CRC_HandleTypeDef *handle, uint32_t *buffer, uint32_t word_count)
{
    uint32_t index, bit;
    assert(handle == &hcrc && buffer == (uint32_t *)can_rx_buffer && (uintptr_t)buffer % 4 == 0);
    assert(word_count == (can_rx_len + 3) / 4);
    crc_calls++;
    handle->value = 0xFFFFFFFF;
    for (index = 0; index < word_count; index++)
    {
        handle->value ^= buffer[index];
        for (bit = 0; bit < 32; bit++)
        {
            if ((handle->value & 0x80000000) != 0)
            {
                handle->value = (handle->value << 1) ^ 0x04C11DB7;
            }
            else
            {
                handle->value <<= 1;
            }
        }
    }
    return handle->value;
}
void int_can_send(uint16_t id, uint8_t *data, uint8_t len)
{
    assert(id == 0 && len == 3 && memcmp(data, "hyf", 3) == 0);
}
void int_eeprom_write(uint16_t address, const uint8_t *data, uint16_t length)
{
    uint16_t index;

    assert(address == 0x10 && length == 3 && metadata_writes == 1 && bin_written == can_rx_len);
    assert(data[0] == 0x01 && data[1] == 0x5A && data[2] == 0x6B);
    assert(eeprom_write_calls == 0);
    eeprom_write_calls++;
    if (eeprom_fail_write != 0)
    {
        return;
    }
    for (index = 0; index < length; index++)
    {
        eeprom_data[address + index] = data[index];
    }
}
void int_eeprom_read(uint16_t address, uint8_t *data, uint16_t length)
{
    uint16_t index;

    assert(address == 0x10 && length == 3 && eeprom_write_calls == 1 && eeprom_read_calls == 0);
    eeprom_read_calls++;
    if (eeprom_fail_read != 0)
    {
        return;
    }
    for (index = 0; index < length; index++)
    {
        data[index] = eeprom_data[address + index];
    }
}
void int_can_receive_msg(CAN_MSG_RECEIVE *msg, uint8_t *count)
{
    if (pending == 0)
    {
        *count = 0;
        return;
    }
    *msg = incoming;
    *count = 1;
    pending = 0;
}
static void begin(void)
{
    pending = 0;
    crc_calls = 0;
    success_messages = 0;
    start_messages = 0;
    receive_messages = 0;
    error_messages = 0;
    flash_success_messages = 0;
    flash_error_messages = 0;
    flash_actions = 0;
    flash_fail_action = 0;
    erased_sectors = 0;
    programmed_pages = 0;
    metadata_writes = 0;
    bin_written = 0;
    selected = 0;
    write_enabled = 0;
    flash_busy = 0;
    memset(flash_data, 0xA5, sizeof(flash_data));
    eeprom_write_calls = 0;
    eeprom_read_calls = 0;
    eeprom_fail_write = 0;
    eeprom_fail_read = 0;
    memset(eeprom_data, 0xA5, sizeof(eeprom_data));
    app_update_state = APP_UPDATE_IDLE;
    app_update_init();
    strcpy(uart_rx_buffer, "cmd");
    HAL_UARTEx_RxEventCallback(&huart1, 3);
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && can_rx_len == 0 && start_messages == 1);
    assert(flash_actions == 0);
}
static void queue(const uint8_t *data, uint8_t len)
{
    memset(&incoming, 0, sizeof(incoming));
    incoming.RxHeader.StdId = 1;
    incoming.RxHeader.DLC = len;
    memcpy(incoming.data, data, len);
    pending = 1;
}
static void receive_bin(uint32_t len)
{
    uint32_t offset, index;
    uint8_t data[8], size;
    for (offset = 0; offset < len; offset += size)
    {
        size = 8;
        if (len - offset < size)
        {
            size = (uint8_t)(len - offset);
        }
        for (index = 0; index < size; index++)
        {
            data[index] = (uint8_t)((offset + index) * 17 + (offset + index) / 256);
        }
        tick += 10;
        queue(data, size);
        app_update_work();
        assert(app_update_state == APP_UPDATE_RECEIVE);
    }
    assert(can_rx_len == len);
}
static void queue_crc(uint32_t value)
{
    uint8_t data[4];
    uint32_t index;
    for (index = 0; index < 4; index++)
    {
        data[index] = (uint8_t)(value >> (index * 8));
    }
    queue(data, 4);
}
int main(void)
{
    const uint32_t lengths[] = {TEST_LENGTHS};
    const uint32_t expected_crcs[] = {EXPECTED_CRCS};
    uint32_t index, offset, crc_4480 = 0;
    uint8_t extra = 1;
    for (index = 0; index < sizeof(lengths) / sizeof(lengths[0]); index++)
    {
        tick = UINT32_MAX - 500;
        begin();
        receive_bin(lengths[index]);
        tick += 999;
        app_update_work();
        assert(app_update_state == APP_UPDATE_RECEIVE && crc_calls == 0 && flash_actions == 0 && eeprom_write_calls == 0);
        tick++;
        app_update_work();
        assert(app_update_state == APP_UPDATE_VERIFY && crc_calls == 0 && receive_messages == 1);
        tick += 99;
        app_update_work();
        assert(app_update_state == APP_UPDATE_VERIFY && can_rx_len == lengths[index]);
        tick++;
        queue_crc(expected_crcs[index]);
        app_update_work();
        if (lengths[index] <= 2048)
        {
            assert(app_update_state == APP_UPDATE_ERROR && success_messages == 1 && crc_calls == 1);
            assert(flash_error_messages == 1 && flash_actions == 0 && eeprom_write_calls == 0);
            assert(can_rx_len == lengths[index]);
            continue;
        }
        assert(app_update_state == APP_UPDATE_DONE && success_messages == 1 && crc_calls == 1);
        assert(flash_success_messages == 1 && flash_error_messages == 0 && metadata_writes == 1);
        assert(erased_sectors == 1 + (lengths[index] + 4095) / 4096);
        assert(programmed_pages == 1 + (lengths[index] + 255) / 256 && bin_written == lengths[index]);
        assert(can_rx_len == lengths[index]);
        for (offset = 0; offset < can_rx_len; offset++)
        {
            assert(can_rx_buffer[offset] == (uint8_t)(offset * 17 + offset / 256));
            assert(flash_data[0x1000 + offset] == can_rx_buffer[offset]);
        }
        for (offset = 8; offset < 0x1000; offset++)
        {
            assert(flash_data[offset] == 0xFF);
        }
        for (offset = 0x1000 + can_rx_len; offset < erased_sectors * 4096; offset++)
        {
            assert(flash_data[offset] == 0xFF);
        }
        assert(flash_data[erased_sectors * 4096] == 0xA5);
        assert(eeprom_data[0x0F] == 0xA5 && eeprom_data[0x13] == 0xA5);
        app_update_work();
        assert(success_messages == 1 && start_messages == 1 && receive_messages == 1 && error_messages == 0);
        assert(metadata_writes == 1 && flash_success_messages == 1 && eeprom_write_calls == 1);
        if (lengths[index] == 4480)
        {
            crc_4480 = expected_crcs[index];
        }
    }

    begin();
    receive_bin(4);
    tick += 1100;
    queue_crc(expected_crcs[3]);
    app_update_work();
    assert(app_update_state == APP_UPDATE_VERIFY && pending == 1 && can_rx_len == 4);
    app_update_work();
    assert(app_update_state == APP_UPDATE_ERROR && success_messages == 1 && flash_actions == 0 && eeprom_write_calls == 0);

    begin();
    receive_bin(1);
    tick += 1000;
    app_update_work();
    for (index = 0; index < 4; index++)
    {
        queue_crc(expected_crcs[0]);
        switch (index)
        {
            case 0:
                incoming.RxHeader.StdId = 2;
                break;
            case 1:
                incoming.RxHeader.IDE = CAN_ID_EXT;
                break;
            case 2:
                incoming.RxHeader.RTR = CAN_RTR_REMOTE;
                break;
            case 3:
                incoming.RxHeader.DLC = 3;
                break;
        }
        app_update_work();
        assert(app_update_state == APP_UPDATE_VERIFY && crc_calls == 0 && can_rx_len == 1);
    }
    tick += 999;
    app_update_work();
    assert(app_update_state == APP_UPDATE_VERIFY);
    tick++;
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && can_rx_len == 0 && success_messages == 0 && error_messages == 1);
    assert(flash_actions == 0 && eeprom_write_calls == 0);

    begin();
    receive_bin(3);
    tick += 1000;
    app_update_work();
    tick += 100;
    queue_crc(expected_crcs[2] ^ 1);
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && can_rx_len == 0 && success_messages == 0 && error_messages == 1);
    assert(flash_actions == 0 && eeprom_write_calls == 0);
    for (offset = 0; offset < sizeof(can_rx_buffer); offset++)
    {
        assert(can_rx_buffer[offset] == 0);
    }
    begin();
    tick += 1000;
    app_update_work();
    assert(app_update_state == APP_UPDATE_ERROR);
    assert(flash_actions == 0 && eeprom_write_calls == 0);
    begin();
    receive_bin(sizeof(can_rx_buffer));
    queue(&extra, 1);
    app_update_work();
    assert(app_update_state == APP_UPDATE_ERROR && can_rx_len == sizeof(can_rx_buffer));
    assert(flash_actions == 0 && eeprom_write_calls == 0);

    /* 4480 字节：3 次扇区擦除、18 页 BIN、1 页元数据；每一步失败都必须停止。 */
    for (index = 1; index <= 22; index++)
    {
        begin();
        receive_bin(4480);
        tick += 1100;
        app_update_work();
        flash_fail_action = index;
        queue_crc(crc_4480);
        app_update_work();
        assert(app_update_state == APP_UPDATE_ERROR && success_messages == 1);
        assert(flash_error_messages == 1 && flash_success_messages == 0 && flash_actions == index && selected == 0);
        assert(metadata_writes == 0 && eeprom_write_calls == 0 && eeprom_read_calls == 0);
        if (index > 1)
        {
            for (offset = 0; offset < 8; offset++)
            {
                assert(flash_data[offset] == 0xFF);
            }
        }
        app_update_work();
        assert(flash_actions == index && eeprom_write_calls == 0);
    }
    for (index = 0; index < 2; index++)
    {
        begin();
        receive_bin(4480);
        tick += 1100;
        app_update_work();
        eeprom_fail_write = (index == 0);
        eeprom_fail_read = (index == 1);
        queue_crc(crc_4480);
        app_update_work();
        assert(app_update_state == APP_UPDATE_ERROR && metadata_writes == 1);
        assert(eeprom_write_calls == 1 && eeprom_read_calls == 1 && flash_success_messages == 0 && flash_error_messages == 1);
    }
    puts("PASS: BIN-only buffer, 1000 ms boundary, separate CRC at 1100 ms, queued CRC, padding, 5 KiB and tick wrap,");
    puts("      invalid frames, missing/wrong CRC, clean retry, no-data and overflow errors;");
    puts("      real W25 page programming, sector erases, BIN/metadata contents, EEPROM 01 5A 6B and injected storage failures");
    return 0;
}
'''

test_lengths = [1, 2, 3, 4, 7, 8, 9, 255, 256, 257, 2048, 2049, 2303, 2304, 2305, 4095, 4096, 4097, 4480, 5117, 5118, 5119, 5120]
expected_crcs = []
for length in test_lengths:
    payload = bytes((index * 17 + index // 256) & 0xFF for index in range(length))
    crc = 0xFFFFFFFF
    for offset in range(0, length, 4):
        word = int.from_bytes(payload[offset:offset + 4].ljust(4, b"\0"), "little")
        remainder = (crc ^ word) << 32
        while remainder.bit_length() > 32:
            remainder ^= 0x104C11DB7 << (remainder.bit_length() - 33)
        crc = remainder
    expected_crcs.append(f"0x{crc:08X}")
harness = harness.replace("TEST_LENGTHS", ", ".join(map(str, test_lengths)))
harness = harness.replace("EXPECTED_CRCS", ", ".join(expected_crcs))
compiler = shutil.which("gcc") or r"C:\msys64\mingw64\bin\gcc.exe"
with tempfile.TemporaryDirectory(prefix="f407-update-crc-") as folder:
    temporary = Path(folder)
    for name in ("can.h", "usart.h", "crc.h", "spi.h"):
        (temporary / name).write_text(mock_header, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_update_crc.exe"
    subprocess.run([
        compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
        f"-I{temporary}", f"-I{root / 'application'}", f"-I{root / 'interface'}",
        str(temporary / "test.c"), str(root / "interface/int_w25q16.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
