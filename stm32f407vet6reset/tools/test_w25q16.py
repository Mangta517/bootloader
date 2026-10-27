"""Check chip select and single-byte SPI transfers against a HAL mock."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef W25Q16_MOCK_H
#define W25Q16_MOCK_H
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
    uint8_t unused;
} SPI_HandleTypeDef;
extern SPI_HandleTypeDef hspi1;
#define F_CS_GPIO_Port ((void *)1)
#define F_CS_Pin 1U
void HAL_GPIO_WritePin(void *port, uint16_t pin, GPIO_PinState state);
HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *handle, uint8_t *data, uint16_t length, uint32_t timeout);
HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *handle, uint8_t *tx_data, uint8_t *rx_data, uint16_t length, uint32_t timeout);
#endif
'''
harness = r'''
#include "int_w25q16.h"
#include "spi.h"
#include <assert.h>
#include <stddef.h>

SPI_HandleTypeDef hspi1;
static uint8_t selected, expected_tx, received_byte;
static uint8_t id_mode, id_tx_index, id_rx_index;
static const uint8_t id_command[] = {0x9F};
static uint8_t id_data[] = {0xEF, 0x40, 0x15};
static uint32_t calls;
static HAL_StatusTypeDef transfer_status = HAL_OK;
static HAL_StatusTypeDef receive_status = HAL_OK;
static uint8_t read_mode, read_tx_index;
static uint32_t stream_address, expected_address, read_rx_count;
static uint16_t expected_length;
static uint8_t write_mode, write_enabled, busy_remaining;
static uint8_t write_frame[260];
static uint16_t write_frame_length;
static uint32_t written_count, programmed_pages;
static uint8_t erase_mode;
static uint32_t erased_sectors;


void HAL_GPIO_WritePin(void *port, uint16_t pin, GPIO_PinState state)
{
    assert(port == F_CS_GPIO_Port && pin == F_CS_Pin);
    if (state == GPIO_PIN_RESET)
    {
        assert(!selected);
        selected = 1;
        id_tx_index = 0;
        id_rx_index = 0;
        read_tx_index = 0;
        stream_address = 0;
        read_rx_count = 0;
        write_frame_length = 0;
    }
    else
    {
        if (write_mode || erase_mode)
        {
            assert(selected && write_frame_length > 0);
            if (write_frame[0] == 0x06)
            {
                assert(write_frame_length == 1 && !busy_remaining && !write_enabled);
                write_enabled = 1;
            }
            else if (erase_mode && write_frame[0] == 0x20)
            {
                uint32_t address = ((uint32_t)write_frame[1] << 16) |
                                   ((uint32_t)write_frame[2] << 8) | write_frame[3];
                assert(write_frame_length == 4 && write_enabled && !busy_remaining);
                assert(address == expected_address && (address & 0xFFF) == 0);
                erased_sectors++;
                write_enabled = 0;
                busy_remaining = 2;
            }
            else if (write_frame[0] == 0x02)
            {
                uint32_t address = ((uint32_t)write_frame[1] << 16) |
                                   ((uint32_t)write_frame[2] << 8) | write_frame[3];
                uint16_t count = write_frame_length - 4;
                assert(write_enabled && !busy_remaining);
                assert(count > 0 && count <= 256 - (address & 0xFF));
                assert(address == expected_address + written_count);
                assert(written_count + count <= expected_length);
                for (uint16_t i = 0; i < count; i++)
                {
                    assert(write_frame[4 + i] == (uint8_t)((written_count + i) ^ 0xA5));
                }
                written_count += count;
                programmed_pages++;
                write_enabled = 0;
                busy_remaining = 2;
            }
            else
            {
                assert(write_frame[0] == 0x05 && write_frame_length == 1);
            }
        }
        if (id_mode)
        {
            assert(id_tx_index == 1 && id_rx_index == 3);
        }
        if (read_mode)
        {
            assert(read_tx_index == 4);
            assert(stream_address == expected_address + expected_length);
            assert(read_rx_count == expected_length);
        }
        selected = 0;
    }
}

HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *handle, uint8_t *data, uint16_t length, uint32_t timeout)
{
    assert(handle == &hspi1 && selected && data != NULL);
    assert(length == 1 && timeout == 100);
    if (write_mode || erase_mode)
    {
        assert(write_frame_length < sizeof(write_frame));
        write_frame[write_frame_length++] = *data;
    }
    else if (read_mode)
    {
        assert(read_tx_index < 4 && read_rx_count == 0);
        if (read_tx_index == 0)
        {
            assert(*data == 0x03);
        }
        else
        {
            stream_address = (stream_address << 8) | *data;
        }
        read_tx_index++;
    }
    else if (id_mode)
    {
        assert(id_tx_index < sizeof(id_command) && id_rx_index == 0);
        assert(*data == id_command[id_tx_index++]);
    }
    else
    {
        assert(*data == expected_tx);
    }
    calls++;
    return transfer_status;
}

HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *handle, uint8_t *tx_data, uint8_t *rx_data, uint16_t length, uint32_t timeout)
{
    assert(handle == &hspi1 && selected && tx_data != NULL && rx_data != NULL);
    assert(length == 1 && timeout == 100 && *tx_data == 0xFF);
    calls++;
    if (receive_status != HAL_OK)
    {
        return receive_status;
    }
    if (transfer_status == HAL_OK)
    {
        if (write_mode || erase_mode)
        {
            assert(write_frame_length == 1 && write_frame[0] == 0x05);
            *rx_data = 0xFE;
            if (busy_remaining > 0)
            {
                *rx_data = 0xFF;
                busy_remaining--;
            }
        }
        else if (read_mode)
        {
            assert(read_tx_index == 4);
            assert(stream_address == expected_address + read_rx_count);
            *rx_data = (uint8_t)(stream_address ^ (stream_address >> 8) ^ (stream_address >> 16));
            stream_address++;
            read_rx_count++;
        }
        else if (id_mode)
        {
            assert(id_tx_index == 1 && id_rx_index < sizeof(id_data));
            *rx_data = id_data[id_rx_index++];
        }
        else
        {
            *rx_data = received_byte;
        }
    }
    return transfer_status;
}

int main(void)
{
    uint8_t rx_data;
    uint8_t manufacturer_id;
    uint16_t device_id;

    int_w25q16_stop();
    assert(!selected && calls == 0);
    int_w25q16_start();
    assert(selected && calls == 0);
    for (uint16_t value = 0; value < 256; value++)
    {
        expected_tx = (uint8_t)value;
        received_byte = (uint8_t)(value ^ 0xFF);
        assert(int_w25q16_write_byte(expected_tx) == 0 && selected);
        rx_data = int_w25q16_read_byte();
        assert(rx_data == received_byte && selected);
    }
    assert(calls == 512);
    int_w25q16_stop();
    assert(!selected && calls == 512);
    for (uint8_t error = HAL_ERROR; error <= HAL_TIMEOUT; error++)
    {
        transfer_status = (HAL_StatusTypeDef)error;
        int_w25q16_start();
        assert(int_w25q16_write_byte(expected_tx) == 1 && selected);
        assert(int_w25q16_read_byte() == 0xFF && selected);
        int_w25q16_stop();
        assert(!selected);
    }
    assert(calls == 518);
    transfer_status = HAL_OK;
    id_mode = 1;
    int_w25q16_read_id(&manufacturer_id, &device_id);
    assert(manufacturer_id == 0xEF && device_id == 0x4015 && !selected);
    id_data[0] = 0x12;
    id_data[1] = 0x34;
    id_data[2] = 0x56;
    int_w25q16_read_id(&manufacturer_id, &device_id);
    assert(manufacturer_id == 0x12 && device_id == 0x3456 && !selected);
    assert(calls == 526);
    id_mode = 0;
    write_mode = 1;
    for (uint16_t value = 0; value < 256; value++)
    {
        busy_remaining = (uint8_t)value;
        uint32_t previous_calls = calls;
        int_w25q16_is_busy();
        assert(!selected && !busy_remaining);
        assert(calls == previous_calls + 2 * (value + 1));
    }
    write_mode = 0;
    read_mode = 1;
    static const struct
    {
        uint8_t block, sector, page, address;
        uint32_t absolute_address;
        uint16_t length;
    } read_cases[] =
    {
        {0, 0, 0, 0, 0x000000, 0},
        {0, 0, 0, 0, 0x000000, 1},
        {18, 10, 11, 205, 0x12ABCD, 300},
        {0, 0, 0, 250, 0x0000FA, 300},
        {0, 0, 15, 250, 0x000FFA, 300},
        {0, 15, 15, 250, 0x00FFFA, 300},
        {31, 15, 15, 255, 0x1FFFFF, 1},
        {30, 15, 15, 255, 0x1EFFFF, 65535}
    };
    static uint8_t buffer[65537];
    for (uint16_t test = 0; test < sizeof(read_cases) / sizeof(read_cases[0]); test++)
    {
        expected_address = read_cases[test].absolute_address;
        expected_length = read_cases[test].length;
        buffer[0] = 0xA5;
        buffer[expected_length + 1] = 0x5A;
        uint32_t previous_calls = calls;
        int_w25q16_read(read_cases[test].block, read_cases[test].sector, read_cases[test].page,
                        read_cases[test].address, &buffer[1], expected_length);
        assert(!selected && calls == previous_calls + 4 + expected_length);
        assert(buffer[0] == 0xA5 && buffer[expected_length + 1] == 0x5A);
        for (uint32_t i = 0; i < expected_length; i++)
        {
            uint32_t address = expected_address + i;
            assert(buffer[i + 1] == (uint8_t)(address ^ (address >> 8) ^ (address >> 16)));
        }
    }
    read_mode = 0;
    write_mode = 1;
    for (uint32_t i = 0; i < sizeof(buffer); i++)
    {
        buffer[i] = (uint8_t)(i ^ 0xA5);
    }
    for (uint16_t test = 0; test < sizeof(read_cases) / sizeof(read_cases[0]); test++)
    {
        expected_address = read_cases[test].absolute_address;
        expected_length = read_cases[test].length;
        written_count = 0;
        programmed_pages = 0;
        busy_remaining = 2;
        uint32_t previous_calls = calls;
        int_w25q16_write(read_cases[test].block, read_cases[test].sector, read_cases[test].page,
                         read_cases[test].address, buffer, expected_length);
        assert(!selected && !write_enabled && written_count == expected_length);
        if (expected_length == 0)
        {
            assert(calls == previous_calls && programmed_pages == 0);
        }
        else
        {
            assert(!busy_remaining);
            assert(programmed_pages == ((expected_address & 0xFF) + expected_length + 255) / 256);
        }
    }
    write_mode = 0;
    erase_mode = 1;
    for (uint8_t block = 0; block < 32; block++)
    {
        for (uint8_t sector = 0; sector < 16; sector++)
        {
            expected_address = (uint32_t)block * 65536 + (uint32_t)sector * 4096;
            busy_remaining = 2;
            uint32_t previous_erases = erased_sectors;
            int_w25q16_erase_sector(block, sector);
            assert(!selected && !write_enabled && !busy_remaining);
            assert(erased_sectors == previous_erases + 1);
        }
    }
    assert(erased_sectors == 512);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="w25q16_test_") as temporary:
    path = Path(temporary)
    (path / "stm32f4xx_hal.h").write_text(mock_header, encoding="utf-8")
    (path / "spi.h").write_text('#include "stm32f4xx_hal.h"\n', encoding="utf-8")
    (path / "test.c").write_text(harness, encoding="utf-8")
    executable = path / "test.exe"
    subprocess.run([
        "gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(path),
        "-I", str(root / "interface"), str(root / "interface" / "int_w25q16.c"),
        str(path / "test.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
print("PASS: chip select, byte values, HAL errors, IDs, blocking BUSY waits, reads, page writes, and all 512 sector erase addresses with WREN/BUSY")
