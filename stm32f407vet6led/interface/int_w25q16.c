#include "int_w25q16.h"
#include "spi.h"

#define W25Q16_TRANSFER_TIMEOUT_MS 100
#define W25Q16_BUSY_TIMEOUT_MS 5000
//拉低片选
void int_w25q16_start(void)
{
    HAL_GPIO_WritePin(F_CS_GPIO_Port, F_CS_Pin, GPIO_PIN_RESET);
}
//拉高片选
void int_w25q16_stop(void)
{
    HAL_GPIO_WritePin(F_CS_GPIO_Port, F_CS_Pin, GPIO_PIN_SET);
}

uint8_t int_w25q16_write_byte(uint8_t data)
{
    HAL_StatusTypeDef status;

    status = HAL_SPI_Transmit(&hspi1, &data, 1, W25Q16_TRANSFER_TIMEOUT_MS);
    if (status != HAL_OK)
    {
        return 1;
    }
    return 0;
}

static uint8_t int_w25q16_read_byte_checked(uint8_t *data)
{
    uint8_t tx_data = 0xFF;
    HAL_StatusTypeDef status;

    status = HAL_SPI_TransmitReceive(&hspi1, &tx_data, data, 1, W25Q16_TRANSFER_TIMEOUT_MS);
    if (status != HAL_OK)
    {
        return 1;
    }
    return 0;
}

uint8_t int_w25q16_read_byte(void)
{
    uint8_t data = 0xFF;

    if (int_w25q16_read_byte_checked(&data) != 0)
    {
        return 0xFF;
    }
    return data;
}

void int_w25q16_read_id(uint8_t *manufacturer_id, uint16_t *device_id)
{
    int_w25q16_start();
    /* 9Fh 依次返回品牌 ID、存储器类型和容量，后两字节组成 16 位设备 ID。 */
    int_w25q16_write_byte(W25Q16_CMD_READ_JEDEC_ID);
    *manufacturer_id = int_w25q16_read_byte();
    *device_id = int_w25q16_read_byte();
    *device_id = (*device_id << 8) | int_w25q16_read_byte();
    int_w25q16_stop();
}

uint8_t int_w25q16_is_busy(void)
{
    uint8_t status;
    uint32_t start_tick = HAL_GetTick();

    while (1)
    {
        int_w25q16_start();
        /* 05h 读取状态寄存器 1，bit0 为 BUSY。 */
        if ((int_w25q16_write_byte(W25Q16_CMD_READ_STATUS_REG1) != 0) ||
            (int_w25q16_read_byte_checked(&status) != 0))
        {
            int_w25q16_stop();
            return 1;
        }
        int_w25q16_stop();

        if ((status & 0x01) == 0)
        {
            return 0;
        }
        /* 无符号差值允许 HAL tick 回绕，整个 BUSY 等待最多 5 秒。 */
        if ((uint32_t)(HAL_GetTick() - start_tick) >= W25Q16_BUSY_TIMEOUT_MS)
        {
            return 1;
        }
    }
}

uint8_t int_w25q16_write_enable(void)
{
    uint8_t result;

    if (int_w25q16_is_busy() != 0)
    {
        return 1;
    }
    int_w25q16_start();
    result = int_w25q16_write_byte(W25Q16_CMD_WRITE_ENABLE);
    int_w25q16_stop();
    return result;
}

static uint8_t int_w25q16_read_data(uint32_t read_address, uint8_t *data, uint16_t length)
{
    uint16_t i;

    int_w25q16_start();
    /* 指令、地址或正文任一传输失败都结束本次读取，不能把错误字节当成固件。 */
    if ((int_w25q16_write_byte(W25Q16_CMD_READ_DATA) != 0) ||
        (int_w25q16_write_byte((uint8_t)(read_address >> 16)) != 0) ||
        (int_w25q16_write_byte((uint8_t)(read_address >> 8)) != 0) ||
        (int_w25q16_write_byte((uint8_t)read_address) != 0))
    {
        int_w25q16_stop();
        return 1;
    }
    for (i = 0; i < length; i++)
    {
        if (int_w25q16_read_byte_checked(&data[i]) != 0)
        {
            int_w25q16_stop();
            return 1;
        }
    }
    int_w25q16_stop();
    return 0;
}

uint8_t int_w25q16_read(uint8_t block, uint8_t sector, uint8_t page, uint8_t address, uint8_t *data, uint16_t length)
{
    uint32_t read_address;

    /* 块号、块内扇区号、扇区内页号、页内字节位置依次拼接。 */
    read_address = ((uint32_t)block << 16) | ((uint32_t)sector << 12) | ((uint32_t)page << 8) | address;
    return int_w25q16_read_data(read_address, data, length);
}

uint8_t int_w25q16_read_address(uint32_t read_address, uint8_t *data, uint16_t length)
{
    if (int_w25q16_is_busy() != 0)
    {
        return 1;
    }
    return int_w25q16_read_data(read_address, data, length);
}

uint8_t int_w25q16_write(uint8_t block, uint8_t sector, uint8_t page, uint8_t address, const uint8_t *data, uint16_t length)
{
    uint32_t write_address;
    uint16_t page_length;
    uint16_t i;

    /* 块号、块内扇区号、扇区内页号、页内字节位置依次拼接。 */
    write_address = ((uint32_t)block << 16) | ((uint32_t)sector << 12) | ((uint32_t)page << 8) | address;

    while (length > 0)
    {
        /* 页编程不能跨越 256 字节边界，否则会回卷覆盖本页数据。 */
        page_length = 256 - (write_address & 0xFF);
        if (page_length > length)
        {
            page_length = length;
        }

        /* 每次页编程前都需要单独发送写使能指令。 */
        if (int_w25q16_write_enable() != 0)
        {
            return 1;
        }

        int_w25q16_start();
        if ((int_w25q16_write_byte(W25Q16_CMD_PAGE_PROGRAM) != 0) ||
            (int_w25q16_write_byte((uint8_t)(write_address >> 16)) != 0) ||
            (int_w25q16_write_byte((uint8_t)(write_address >> 8)) != 0) ||
            (int_w25q16_write_byte((uint8_t)write_address) != 0))
        {
            int_w25q16_stop();
            return 1;
        }
        for (i = 0; i < page_length; i++)
        {
            if (int_w25q16_write_byte(data[i]) != 0)
            {
                int_w25q16_stop();
                return 1;
            }
        }
        int_w25q16_stop();

        /* 拉高片选后才开始内部编程，等待完成再访问下一页。 */
        if (int_w25q16_is_busy() != 0)
        {
            return 1;
        }

        write_address += page_length;
        data += page_length;
        length -= page_length;
    }
    return 0;
}

uint8_t int_w25q16_erase_sector(uint8_t block, uint8_t sector)
{
    uint32_t erase_address;

    erase_address = ((uint32_t)block << 16) | ((uint32_t)sector << 12);

    /* 擦除前单独发送写使能，拉高片选后生效。 */
    if (int_w25q16_write_enable() != 0)
    {
        return 1;
    }

    int_w25q16_start();
    if ((int_w25q16_write_byte(W25Q16_CMD_SECTOR_ERASE_4KB) != 0) ||
        (int_w25q16_write_byte((uint8_t)(erase_address >> 16)) != 0) ||
        (int_w25q16_write_byte((uint8_t)(erase_address >> 8)) != 0) ||
        (int_w25q16_write_byte((uint8_t)erase_address) != 0))
    {
        int_w25q16_stop();
        return 1;
    }
    int_w25q16_stop();

    /* 拉高片选启动内部擦除，等待完成后返回。 */
    return int_w25q16_is_busy();
}
