#include "int_eeprom.h"
#include "i2c.h"

#define EEPROM_DEVICE_ADDR         (EEPROM_DEVICE_ADDR_7BIT << 1)
#define EEPROM_TRANSFER_TIMEOUT_MS 100
#define EEPROM_WRITE_CYCLE_MS      10

uint8_t int_eeprom_read_byte(uint16_t address)
{
    uint8_t data = 0;

    HAL_I2C_Mem_Read(&hi2c1, EEPROM_DEVICE_ADDR, address, I2C_MEMADD_SIZE_8BIT, &data, 1, EEPROM_TRANSFER_TIMEOUT_MS);
    return data;
}

void int_eeprom_write_byte(uint16_t address, uint8_t data)
{
    int_eeprom_write(address, &data, 1);
}

void int_eeprom_read(uint16_t address, uint8_t *data, uint16_t length)
{
    HAL_I2C_Mem_Read(&hi2c1, EEPROM_DEVICE_ADDR, address, I2C_MEMADD_SIZE_8BIT, data, length, EEPROM_TRANSFER_TIMEOUT_MS);
}

void int_eeprom_write(uint16_t address, const uint8_t *data, uint16_t length)
{
    uint16_t page_remain_length;

    if (address >= EEPROM_SIZE_BYTES)
    {
        return;
    }

    /* 超出最后一页的数据截断，不回卷到 EEPROM 起始地址。 */
    if (length > EEPROM_SIZE_BYTES - address)
    {
        length = EEPROM_SIZE_BYTES - address;
    }

    while (length > 0)
    {
        /* 页写不能跨越页边界，否则会回卷覆盖本页数据。 */
        page_remain_length = EEPROM_PAGE_SIZE_BYTES - (address % EEPROM_PAGE_SIZE_BYTES);
        if (page_remain_length > length)
        {
            page_remain_length = length;
        }

        HAL_I2C_Mem_Write(&hi2c1, EEPROM_DEVICE_ADDR, address, I2C_MEMADD_SIZE_8BIT, (uint8_t *)data, page_remain_length,
                          EEPROM_TRANSFER_TIMEOUT_MS);
        /* 等待内部写周期结束，再访问下一页。 */
        HAL_Delay(EEPROM_WRITE_CYCLE_MS);

        address += page_remain_length;
        data += page_remain_length;
        length -= page_remain_length;
    }
}
