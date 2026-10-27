"""Check the four EEPROM functions against a page-write model."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver_source = (root / "interface" / "int_eeprom.c").read_text(encoding="utf-8")
functions = re.findall(r"^(?:static\s+)?(?:uint8_t|void|HAL_StatusTypeDef)\s+(\w+)\([^;]*?\)\s*\{", driver_source, re.M)
assert sorted(functions) == sorted([
    "int_eeprom_read_byte", "int_eeprom_write_byte", "int_eeprom_read", "int_eeprom_write",
])
mock_header = r'''
#ifndef EEPROM_MOCK_H
#define EEPROM_MOCK_H
#include <stdint.h>
typedef enum
{
    HAL_OK, HAL_ERROR, HAL_BUSY, HAL_TIMEOUT
} HAL_StatusTypeDef;
typedef struct
{
    uint8_t unused;
} I2C_HandleTypeDef;
extern I2C_HandleTypeDef hi2c1;
#define I2C_MEMADD_SIZE_8BIT 1U
void HAL_Delay(uint32_t delay);
HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *handle, uint16_t device, uint16_t address, uint16_t address_size,
                                uint8_t *data, uint16_t length, uint32_t timeout);
HAL_StatusTypeDef HAL_I2C_Mem_Write(I2C_HandleTypeDef *handle, uint16_t device, uint16_t address, uint16_t address_size,
                                 uint8_t *data, uint16_t length, uint32_t timeout);
#endif
'''
harness = r'''
#include "int_eeprom.h"
#include "i2c.h"
#include <assert.h>
#include <string.h>

I2C_HandleTypeDef hi2c1;
static uint8_t memory[256];
static uint32_t busy_ms;

void HAL_Delay(uint32_t delay)
{
    if (busy_ms > delay)
    {
        busy_ms -= delay;
    }
    else
    {
        busy_ms = 0;
    }
}

HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *handle, uint16_t device, uint16_t address, uint16_t address_size,
                                uint8_t *data, uint16_t length, uint32_t timeout)
{
    assert(handle == &hi2c1 && device == 0xA0U && address_size == I2C_MEMADD_SIZE_8BIT && timeout > 0U);
    assert(busy_ms == 0U && address + length <= sizeof(memory));
    memcpy(data, memory + address, length);
    return HAL_OK;
}

HAL_StatusTypeDef HAL_I2C_Mem_Write(I2C_HandleTypeDef *handle, uint16_t device, uint16_t address, uint16_t address_size,
                                 uint8_t *data, uint16_t length, uint32_t timeout)
{
    assert(handle == &hi2c1 && device == 0xA0U && address_size == I2C_MEMADD_SIZE_8BIT && timeout > 0U);
    assert(busy_ms == 0U && length > 0U && length <= 8U);
    assert(address < sizeof(memory) && length <= sizeof(memory) - address);
    for (uint16_t i = 0; i < length; i++)
    {
        memory[(address & ~7U) | ((address + i) & 7U)] = data[i];
    }
    busy_ms = 5U;
    return HAL_OK;
}

int main(void)
{
    uint8_t source[256], result[256];

    for (uint16_t i = 0; i < sizeof(source); i++)
    {
        source[i] = (uint8_t)(i ^ 0x5AU);
    }
    memset(memory, 0xFF, sizeof(memory));
    int_eeprom_write_byte(255U, 0xA5U);
    assert(busy_ms == 0U && memory[254] == 0xFFU && memory[255] == 0xA5U);
    assert(int_eeprom_read_byte(255U) == 0xA5U);
    assert(int_eeprom_read_byte(254U) == 0xFFU);
    for (uint16_t address = 0; address < sizeof(memory); address++)
    {
        uint16_t length = sizeof(memory) - address;
        memset(memory, 0xFF, sizeof(memory));
        int_eeprom_write(address, source, length);
        assert(busy_ms == 0U);
        assert(memcmp(memory + address, source, length) == 0);
        for (uint16_t i = 0; i < address; i++)
        {
            assert(memory[i] == 0xFFU);
        }
        int_eeprom_read(address, result, length);
        assert(memcmp(source, result, length) == 0);
    }

    /* 从每个有效起点写入超长数据，只允许写到容量末尾。 */
    for (uint16_t address = 0; address < sizeof(memory); address++)
    {
        uint16_t expected_length = sizeof(memory) - address;
        memset(memory, 0xFF, sizeof(memory));
        int_eeprom_write(address, source, UINT16_MAX);
        assert(busy_ms == 0);
        assert(memcmp(memory + address, source, expected_length) == 0);
        for (uint16_t i = 0; i < address; i++)
        {
            assert(memory[i] == 0xFF);
        }
    }

    /* 越界起点和零长度请求均不应发起写入。 */
    memset(memory, 0xFF, sizeof(memory));
    int_eeprom_write(256, source, 1);
    int_eeprom_write(UINT16_MAX, source, UINT16_MAX);
    int_eeprom_write_byte(256, 0xA5);
    int_eeprom_write(0, source, 0);
    int_eeprom_write(255, source, 0);
    assert(busy_ms == 0);
    for (uint16_t i = 0; i < sizeof(memory); i++)
    {
        assert(memory[i] == 0xFF);
    }

    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="eeprom_test_") as temporary:
    path = Path(temporary)
    (path / "stm32f4xx_hal.h").write_text(mock_header, encoding="utf-8")
    (path / "i2c.h").write_text('#include "stm32f4xx_hal.h"\n', encoding="utf-8")
    (path / "test.c").write_text(harness, encoding="utf-8")
    executable = path / "test.exe"
    subprocess.run([
        "gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(path),
        "-I", str(root / "interface"), str(root / "interface" / "int_eeprom.c"),
        str(path / "test.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
print("PASS: four EEPROM functions, byte reads/writes, all valid offsets, page boundaries, capacity truncation, invalid starts, zero length and write-cycle delay")
