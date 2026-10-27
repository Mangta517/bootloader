#include "app_bootloader.h"
#include "int_w25q16.h"
#include <stdio.h>

volatile uint8_t bootloader_check_update_status = BOOT_NO_UPDATE;

uint8_t data[8] = {0};         // W25Q16 元数据和 APP 向量接收缓冲区
//程序在w25q16当中的地址
uint32_t w25_app_start_addr;
//程序的大小
uint32_t app_size_bytes;
// W25Q16 前 8 字节依次存放 APP 在 W25Q16 内的起始地址和 BIN 字节长度，均为小端。
static uint8_t App_bootloader_check_meta(void)
{
    // 按块、扇区、页和页内字节地址读取元数据。
    if (int_w25q16_read(APP_META_BLOCK, APP_META_SECTOR, APP_META_PAGE, APP_META_BYTE_ADDR, data, sizeof(data)) != 0)
    {
        return 1;
    }
     w25_app_start_addr = (uint32_t)data[0] | ((uint32_t)data[1] << 8) | ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
     app_size_bytes = (uint32_t)data[4] | ((uint32_t)data[5] << 8) | ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24);

    // 起点在首扇区内并避开元数据；长度为 (0x0800, 0x78000]，整段 APP 不能超出 W25Q16。
    if ((w25_app_start_addr < 8) || (w25_app_start_addr >= 0x1000) ||
        (app_size_bytes <= 0x0800) || (app_size_bytes > 0x78000) ||
        (app_size_bytes > 0x200000 - w25_app_start_addr))
    {
        printf("APP metadata error: W25=0x%08lX length=%lu\r\n",
               (unsigned long)w25_app_start_addr, (unsigned long)app_size_bytes);
        return 1;
    }

    if (int_w25q16_read_address(w25_app_start_addr, data, sizeof(data)) != 0)
    {
        return 1;
    }

    // 栈顶地址必须位于 SRAM 或 CCM 内，允许指向 RAM 末端。
    if (!(((APP_STACK_PTR > 0x20000000) && (APP_STACK_PTR <= 0x20020000)) ||
          ((APP_STACK_PTR > 0x10000000) && (APP_STACK_PTR <= 0x10010000))))
    {
        printf("APP stack pointer error: 0x%08lX\r\n", (unsigned long)APP_STACK_PTR);
        return 1;
    }

    // 复位入口必须位于搬运后的 APP 范围内。
    if ((APP_RESET_HANDLER < APP_START_ADDR + 8) || (APP_RESET_HANDLER >= APP_START_ADDR + app_size_bytes))
    {
        printf("APP reset handler error: 0x%08lX\r\n", (unsigned long)APP_RESET_HANDLER);
        return 1;
    }

    printf("APP check ok: W25=0x%08lX length=%lu\r\n",
           (unsigned long)w25_app_start_addr, (unsigned long)app_size_bytes);
    return 0;
}

// 元数据校验成功后调用，按 BIN 长度擦除 APP 覆盖的全部扇区。
static uint8_t App_bootloader_erase_app_flash(void)
{
    FLASH_EraseInitTypeDef erase;
    uint32_t sector_error;
    uint32_t last_addr = APP_START_ADDR + app_size_bytes - 1;
    uint32_t last_sector;
    HAL_StatusTypeDef status;

    // F407VE 共 512 KiB：扇区 0~3 各 16 KiB，扇区 4 为 64 KiB，扇区 5~7 各 128 KiB。
    if (last_addr < 0x08010000)
    {
        last_sector = (last_addr - 0x08000000) / 0x4000;
    }
    else if (last_addr < 0x08020000)
    {
        last_sector = FLASH_SECTOR_4;
    }
    else
    {
        last_sector = FLASH_SECTOR_5 + (last_addr - 0x08020000) / 0x20000;
    }

    if (HAL_FLASH_Unlock() != HAL_OK)
    {
        return 1;
    }
    erase.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase.Sector = FLASH_SECTOR_2;
    erase.NbSectors = last_sector - FLASH_SECTOR_2 + 1;
    erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
    status = HAL_FLASHEx_Erase(&erase, &sector_error);
    if ((HAL_FLASH_Lock() != HAL_OK) || (status != HAL_OK))
    {
        return 1;
    }
    return 0;
}

static uint8_t App_bootloader_write_app_flash(void)
{
    static uint8_t buffer[512];
    uint32_t offset = 0;
    uint32_t length;
    uint16_t i;
    uint16_t value;

    // 读取元数据，校验数据。
    if (App_bootloader_check_meta() != 0)
    {
        return 1;
    }

    // 3.1 提前擦除足够的 Flash 空间。
    if (App_bootloader_erase_app_flash() != 0)
    {
        return 1;
    }

    if (HAL_FLASH_Unlock() != HAL_OK)
    {
        return 1;
    }
    // 512 字节可整分所有 Flash 扇区，逐块搬运，不占用整扇区 RAM。
    while (offset < app_size_bytes)
    {
        length = app_size_bytes - offset;
        if (length > sizeof(buffer))
        {
            length = sizeof(buffer);
        }
        if (int_w25q16_read_address(w25_app_start_addr + offset, buffer, (uint16_t)length) != 0)
        {
            // SPI 失败或 BUSY 超时后不写入本块，保留升级标记并交给恢复程序。
            HAL_FLASH_Lock();
            return 1;
        }

        // 最后一块若为奇数长度，补 FF 后按半字写入，不多读 W25Q16。
        if ((length % 2) != 0)
        {
            buffer[length] = 0xFF;
        }
        for (i = 0; i < length; i += 2)
        {
            value = (uint16_t)buffer[i] | ((uint16_t)buffer[i + 1] << 8);
            if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, APP_START_ADDR + offset + i, value) != HAL_OK)
            {
                HAL_FLASH_Lock();
                return 1;
            }
        }
        offset += length;
    }
    if (HAL_FLASH_Lock() != HAL_OK)
    {
        return 1;
    }
    return 0;
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    if (GPIO_Pin == key1_Pin)
    {
        bootloader_check_update_status = BOOT_RESET;
    }
}
void App_bootloader_check_default(void)
{
    HAL_Delay(3000);
}
void App_bootloader_check_update(void)
{
    printf("bootloader satrt\n");
    printf("check update\n");
    // 读取更新状态和两个密钥字节。
    uint8_t data[3] = {0};
    int_eeprom_read(CHECK_UPDATE_ADDR, data, 3);
    
    // 首次只初始化 EEPROM，保留本次启动的默认状态。
    uint16_t key = data[1] << 8 | data[2];
    if (key != BOOT_KEY)
    {
        data[0] = BOOT_UPDATE;
        data[1] = (uint8_t)(BOOT_KEY >> 8);
        data[2] = (uint8_t)BOOT_KEY;
        int_eeprom_write(CHECK_UPDATE_ADDR, data, 3);
        HAL_Delay(10);
    }
    else
    {
        uint32_t primask = __get_PRIMASK();

        // 读取有效状态，同时保留 KEY1 恢复请求。
        __disable_irq();
        if (bootloader_check_update_status != BOOT_RESET)
        {
            bootloader_check_update_status = data[0];
        }
        __set_PRIMASK(primask);
    }
}

void App_bootloader_update(void)
{
    if (bootloader_check_update_status == BOOT_UPDATE)
    {
        uint8_t update_status = BOOT_NO_UPDATE;
        uint32_t primask;

        printf("update\n");
        if (App_bootloader_write_app_flash() != 0)
        {
            printf("APP update error; enter recovery\r\n");
            bootloader_check_update_status = BOOT_RESET;
            return;
        }

        // 全部写入后清除升级标记，保留两个密钥字节和 KEY1 恢复请求。
        int_eeprom_write(CHECK_UPDATE_ADDR, &update_status, 1);
        primask = __get_PRIMASK();
        __disable_irq();
        if (bootloader_check_update_status != BOOT_RESET)
        {
            bootloader_check_update_status = BOOT_NO_UPDATE;
        }
        __set_PRIMASK(primask);
        printf("APP update ok: length=%lu\r\n", (unsigned long)app_size_bytes);
    }
    else if (bootloader_check_update_status == BOOT_NO_UPDATE)
    {
        printf("no update\n");
    }
    else if (bootloader_check_update_status == BOOT_RESET)
    {
        printf("reset\n");
    }
}

void App_bootloader_jump_app(void)
{
    uint32_t app_address;

    if (bootloader_check_update_status == BOOT_RESET)
    {
        // KEY1 或 EEPROM 恢复标志：进入串口恢复程序。
        app_address = RESET_START_ADDR;
    }
    else
    {
        app_address = APP_START_ADDR;
    }

    if (Int_bootloader_jump_to_app(app_address) != 0)
    {
        printf("Jump failed:0x%08lX\r\n", (unsigned long)app_address);
    }
}
