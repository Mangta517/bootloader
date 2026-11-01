"""Exercise the real application routing and jump validation with mocked hardware."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "interface/int_bootloader.c").read_text(encoding="utf-8-sig")
start = source.index("uint8_t Int_bootloader_jump_to_app(")
end = len(source)
app_source = (root / "application/app_bootloader.c").read_text(encoding="utf-8-sig")

mock_header = r'''
#ifndef JUMP_MOCK_H
#define JUMP_MOCK_H
#include <stdint.h>
#define key1_Pin 1
#define FLASH_TYPEERASE_SECTORS 0
#define FLASH_VOLTAGE_RANGE_3 2
#define FLASH_TYPEPROGRAM_HALFWORD 1
#define FLASH_SECTOR_2 2
#define FLASH_SECTOR_4 4
#define FLASH_SECTOR_5 5
typedef enum { HAL_OK, HAL_ERROR, HAL_BUSY, HAL_TIMEOUT } HAL_StatusTypeDef;
typedef struct { uint32_t TypeErase, Banks, Sector, NbSectors, VoltageRange; } FLASH_EraseInitTypeDef;
HAL_StatusTypeDef HAL_FLASH_Unlock(void);
HAL_StatusTypeDef HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *sector_error);
HAL_StatusTypeDef HAL_FLASH_Lock(void);
HAL_StatusTypeDef HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t value);
#define SCB_ICSR_PENDSTCLR_Msk (1UL << 25)
#define SCB_ICSR_PENDSVCLR_Msk (1UL << 27)
typedef struct { uint32_t CTRL, LOAD, VAL; } systick_struct;
typedef struct { uint32_t ICER[8], ICPR[8]; } nvic_struct;
typedef struct { uint32_t ICSR, VTOR; } scb_struct;
extern systick_struct mock_systick;
extern nvic_struct mock_nvic;
extern scb_struct mock_scb;
#define SysTick (&mock_systick)
#define NVIC (&mock_nvic)
#define SCB (&mock_scb)
void __disable_irq(void);
uint32_t __get_PRIMASK(void);
void __set_PRIMASK(uint32_t value);
void __set_MSP(uint32_t stack_pointer);
void __DSB(void);
void __ISB(void);
void HAL_DeInit(void);
void HAL_Delay(uint32_t delay);
#endif
'''

harness = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "app_bootloader.h"
#include "int_w25q16.h"
systick_struct mock_systick;
nvic_struct mock_nvic;
scb_struct mock_scb;
static jmp_buf entry;
static uint32_t disabled, deinit_calls, dsb_calls, isb_calls, entered_stack, entered_handler;
static uint32_t delay_ms, key_during_read, key_during_wait;
static uint8_t eeprom_status;
static uint16_t eeprom_key = BOOT_KEY;
static uint32_t eeprom_write_calls;
static uint8_t w25_data[W25Q16_SIZE_BYTES];
static uint32_t w25_read_calls, w25_read_addresses[2], w25_copy_bytes;
static uint32_t w25_read_fail_call;
static uint32_t flash_unlock_calls, flash_erase_calls, flash_lock_calls;
static uint32_t flash_program_calls, flash_program_fail_call, flash_unlock_fail_call, flash_lock_fail_call;
static uint32_t key_during_program;
static const uint32_t flash_sector_ends[] = {0x0800C000, 0x08010000, 0x08020000, 0x08040000, 0x08060000, 0x08080000};
static FLASH_EraseInitTypeDef flash_erase;
static HAL_StatusTypeDef flash_unlock_status = HAL_OK, flash_erase_status = HAL_OK, flash_lock_status = HAL_OK;
void HAL_GPIO_EXTI_Callback(uint16_t pin);
static int test_printf(const char *format, ...)
{
    (void)format;
    return 0;
}
#define printf test_printf
void __disable_irq(void)
{
    disabled = 1;
}
uint32_t __get_PRIMASK(void)
{
    return disabled;
}
void __set_PRIMASK(uint32_t value)
{
    disabled = value;
}
void __DSB(void)
{
    dsb_calls++;
}
void __ISB(void)
{
    isb_calls++;
}
void HAL_DeInit(void)
{
    assert(disabled == 1);
    deinit_calls++;
}
void HAL_Delay(uint32_t delay)
{
    delay_ms += delay;
    if (key_during_wait != 0)
    {
        HAL_GPIO_EXTI_Callback(key1_Pin);
    }
}
void int_eeprom_read(uint16_t address, uint8_t *data, uint16_t length)
{
    assert(address == CHECK_UPDATE_ADDR && length == 3);
    data[0] = eeprom_status;
    data[1] = eeprom_key >> 8;
    data[2] = eeprom_key & 0xFF;
    if (key_during_read != 0)
    {
        HAL_GPIO_EXTI_Callback(key1_Pin);
    }
}
void int_eeprom_write(uint16_t address, const uint8_t *data, uint16_t length)
{
    assert(address == CHECK_UPDATE_ADDR);
    if (length == 1)
    {
        assert(data[0] == BOOT_NO_UPDATE && flash_program_calls > 0 && flash_lock_calls == 2);
        eeprom_status = data[0];
        eeprom_write_calls++;
        return;
    }
    assert(length == 3);
    assert(eeprom_key != BOOT_KEY && "Valid EEPROM key must not be rewritten");
    assert(data[0] == BOOT_UPDATE && data[1] == 0x5A && data[2] == 0x6B);
    eeprom_status = data[0];
    eeprom_key = ((uint16_t)data[1] << 8) | data[2];
    eeprom_write_calls++;
}
uint8_t int_w25q16_read_address(uint32_t address, uint8_t *data, uint16_t length)
{
    assert(address < W25Q16_SIZE_BYTES && length <= W25Q16_SIZE_BYTES - address);
    if (w25_read_calls < 2)
    {
        assert(length == 8);
        w25_read_addresses[w25_read_calls] = address;
    }
    else
    {
        uint32_t image_start, image_length;

        memcpy(&image_start, w25_data, 4);
        memcpy(&image_length, w25_data + 4, 4);
        assert(flash_erase_calls == 1 && flash_unlock_calls == 2);
        assert(length > 0 && length <= 512);
        assert(address == image_start + w25_copy_bytes && length <= image_length - w25_copy_bytes);
        w25_copy_bytes += length;
    }
    w25_read_calls++;
    memcpy(data, w25_data + address, length);
    if (w25_read_calls == w25_read_fail_call)
    {
        return 1;
    }
    return 0;
}
uint8_t int_w25q16_read(uint8_t block, uint8_t sector, uint8_t page, uint8_t address, uint8_t *data, uint16_t length)
{
    uint32_t read_address = ((uint32_t)block << 16) | ((uint32_t)sector << 12) | ((uint32_t)page << 8) | address;

    return int_w25q16_read_address(read_address, data, length);
}
HAL_StatusTypeDef HAL_FLASH_Unlock(void)
{
    assert(w25_read_calls == 2 && "Check metadata and vector before erasing");
    flash_unlock_calls++;
    if (flash_unlock_calls == flash_unlock_fail_call)
    {
        return HAL_ERROR;
    }
    return flash_unlock_status;
}
HAL_StatusTypeDef HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *sector_error)
{
    assert(flash_unlock_calls == 1 && flash_lock_calls == 0);
    assert(erase->TypeErase == FLASH_TYPEERASE_SECTORS && erase->VoltageRange == FLASH_VOLTAGE_RANGE_3);
    assert(erase->Sector == 2 && erase->NbSectors >= 1 && erase->NbSectors <= 6);
    flash_erase.Sector = erase->Sector;
    flash_erase.NbSectors = erase->NbSectors;
    flash_erase_calls++;
    *sector_error = UINT32_MAX;
    if (flash_erase_status == HAL_OK)
    {
        memset((void *)(uintptr_t)APP_START_ADDR, 0xFF, flash_sector_ends[erase->NbSectors - 1] - APP_START_ADDR);
    }
    return flash_erase_status;
}
HAL_StatusTypeDef HAL_FLASH_Lock(void)
{
    assert(flash_erase_calls == 1);
    flash_lock_calls++;
    if (flash_lock_calls == flash_lock_fail_call)
    {
        return HAL_ERROR;
    }
    return flash_lock_status;
}
HAL_StatusTypeDef HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t value)
{
    uint16_t *target = (uint16_t *)(uintptr_t)address;

    assert(type == FLASH_TYPEPROGRAM_HALFWORD && (address % 2) == 0);
    assert(address >= APP_START_ADDR && address < app_addr_end - 1);
    assert(flash_erase_calls == 1 && flash_unlock_calls == 2 && flash_lock_calls == 1);
    assert(address == APP_START_ADDR + flash_program_calls * 2 && *target == 0xFFFF);
    flash_program_calls++;
    if (flash_program_calls == flash_program_fail_call)
    {
        return HAL_ERROR;
    }
    *target = (uint16_t)value;
    if (key_during_program != 0)
    {
        HAL_GPIO_EXTI_Callback(key1_Pin);
        key_during_program = 0;
    }
    return HAL_OK;
}
void __set_MSP(uint32_t stack_pointer)
{
    assert(disabled == 1 && deinit_calls == 1);
    entered_stack = stack_pointer;
}
static void mock_app_entry(void)
{
    uint32_t index;

    assert(disabled == 1 && deinit_calls == 1);
    assert(SysTick->CTRL == 0 && SysTick->LOAD == 0 && SysTick->VAL == 0);
    assert(SCB->ICSR == (SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk));
    assert(dsb_calls == 1 && isb_calls == 1);
    for (index = 0; index < 8; index++)
    {
        assert(NVIC->ICER[index] == UINT32_MAX && NVIC->ICPR[index] == UINT32_MAX);
    }
    assert(entered_stack != 0);
    longjmp(entry, 1);
}
static void (*mock_reset_entry(uint32_t reset_handler))(void)
{
    assert(entered_stack == 0);
    entered_handler = reset_handler;
    return mock_app_entry;
}
'''
# Host pointers cannot execute Cortex-M Thumb addresses; map only the entry address.
harness += source[start:end].replace("(void (*)(void))app_reset_handle", "mock_reset_entry(app_reset_handle)")
harness += app_source
harness += r'''
static void reset_hardware(void)
{
    disabled = 0;
    deinit_calls = 0;
    dsb_calls = 0;
    isb_calls = 0;
    entered_stack = 0;
    entered_handler = 0;
    delay_ms = 0;
    key_during_read = 0;
    key_during_wait = 0;
    eeprom_write_calls = 0;
    memset(&mock_nvic, 0, sizeof(mock_nvic));
    memset(&mock_scb, 0, sizeof(mock_scb));
    memset(&mock_systick, 0xFF, sizeof(mock_systick));
}
static void set_vector(uint32_t address, uint32_t stack_pointer, uint32_t handler)
{
    uint32_t *vector = (uint32_t *)(uintptr_t)address;

    vector[0] = stack_pointer;
    vector[1] = handler;
}
static void expect_rejected(uint32_t address, uint32_t stack_pointer, uint32_t handler)
{
    reset_hardware();
    set_vector(APP_START_ADDR, stack_pointer, handler);
    set_vector(RESET_START_ADDR, stack_pointer, handler);
    assert(Int_bootloader_jump_to_app(address) == 1);
    assert(disabled == 0 && deinit_calls == 0 && entered_stack == 0 && SCB->VTOR == 0);
}
static void expect_jump(uint32_t address, uint32_t stack_pointer, uint32_t handler)
{
    reset_hardware();
    set_vector(address, stack_pointer, handler);
    if (setjmp(entry) == 0)
    {
        Int_bootloader_jump_to_app(address);
        assert(0 && "Successful jump must not return");
    }
    assert(SCB->VTOR == address && entered_stack == stack_pointer && entered_handler == handler);
}
static void set_w25_word(uint32_t address, uint32_t value)
{
    uint32_t index;

    for (index = 0; index < 4; index++)
    {
        w25_data[address + index] = (uint8_t)(value >> (index * 8));
    }
}
static void expect_w25_check(uint32_t start_address, uint32_t length, uint32_t stack_pointer,
                            uint32_t handler, uint8_t expected_result)
{
    set_w25_word(0, start_address);
    set_w25_word(4, length);
    if ((start_address >= 8) && (start_address <= W25Q16_SIZE_BYTES - 8))
    {
        set_w25_word(start_address, stack_pointer);
        set_w25_word(start_address + 4, handler);
    }
    w25_read_calls = 0;
    assert(App_bootloader_check_meta() == expected_result);
    assert(w25_read_addresses[0] == 0);
    if (w25_read_calls == 1)
    {
        assert(expected_result == 1);
    }
    else
    {
        assert(w25_read_calls == 2 && w25_read_addresses[1] == start_address);
        assert(APP_STACK_PTR == stack_pointer && APP_RESET_HANDLER == handler);
    }
    if (expected_result == 0)
    {
        assert(w25_read_calls == 2);
    }
}
static void prepare_w25_copy(uint32_t start_address, uint32_t length)
{
    uint32_t index;

    for (index = 0; index < length; index++)
    {
        w25_data[start_address + index] = (uint8_t)(index ^ (index >> 8) ^ 0x5A);
    }
    set_w25_word(0, start_address);
    set_w25_word(4, length);
    set_w25_word(start_address, 0x20001000);
    set_w25_word(start_address + 4, APP_START_ADDR + 0x101);
    memset((void *)(uintptr_t)0x08000000, 0xA5, 0x80000);
    w25_read_calls = w25_copy_bytes = 0;
    w25_read_fail_call = 0;
    flash_unlock_calls = flash_erase_calls = flash_lock_calls = flash_program_calls = 0;
    flash_program_fail_call = flash_unlock_fail_call = flash_lock_fail_call = 0;
    flash_unlock_status = flash_erase_status = flash_lock_status = HAL_OK;
    key_during_program = 0;
    eeprom_status = BOOT_UPDATE;
    eeprom_key = BOOT_KEY;
    eeprom_write_calls = 0;
    bootloader_check_update_status = BOOT_UPDATE;
}
int main(void)
{
    void *flash = VirtualAlloc((void *)(uintptr_t)0x08000000, 0x80000,
                              MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    const uint32_t stack_tops[] = {0x20000008, 0x20010000, 0x20020000, 0x10000008, 0x10010000};
    const uint32_t bad_stacks[] = {0, 0xFFFFFFFF, 0x20000000, 0x20020008, 0x10000000, 0x10010008};
    const uint8_t states[] = {BOOT_RESET, BOOT_NO_UPDATE, BOOT_UPDATE};
    const uint16_t invalid_keys[] = {0xFFFF, 0x006B};
    uint32_t index;
    uint32_t address;
    uint32_t key_mode;

    assert(flash == (void *)(uintptr_t)0x08000000);
    // 第 0 扇区只存元数据，BIN 起点从 0x1000 起，覆盖后续扇区和芯片末端。
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, 0x20001000, APP_START_ADDR + 0x101, 0);
    expect_w25_check(0x1001, 0x801, 0x20001000, APP_START_ADDR + 0x101, 0);
    expect_w25_check(0x1000, 0x801, 0x20001000, APP_START_ADDR + 9, 0);
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x802, 0x20001000, APP_START_ADDR + 0x801, 0);
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x78000, 0x20001000, app_addr_end - 1, 0);
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1001, 0x20001000, APP_START_ADDR + 0xFFF, 0);
    expect_w25_check(0x2000, 0x1000, 0x20001000, APP_START_ADDR + 0x101, 0);
    expect_w25_check(0x12ABCD, 0x1000, 0x20001000, APP_START_ADDR + 0x101, 0);
    expect_w25_check(W25Q16_SIZE_BYTES - 0x801, 0x801, 0x20001000, APP_START_ADDR + 0x101, 0);
    // W25 APP 预检只判断向量地址范围，不限制栈对齐或复位入口的 Thumb 位。
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, 0x20001004, APP_START_ADDR + 0x101, 0);
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, 0x20001000, APP_START_ADDR + 0x100, 0);
    for (index = 0; index < sizeof(stack_tops) / sizeof(stack_tops[0]); index++)
    {
        expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, stack_tops[index], APP_START_ADDR + 0x101, 0);
    }
    const uint32_t bad_meta[][2] =
    {
        {0, 0x1000}, {7, 0x1000}, {8, 0x1000}, {0x100, 0x1000}, {0xFFF, 0x1000},
        {W25Q16_SIZE_BYTES, 0x1000}, {UINT32_MAX, UINT32_MAX},
        {0x1000, 0}, {0x1000, 7}, {0x1000, 0x7FF}, {0x1000, 0x800}, {0x1000, 0x78001},
        {W25Q16_SIZE_BYTES - 0x801, 0x802}, {W25Q16_SIZE_BYTES - 4, 0x801}, {0x1000, UINT32_MAX}
    };
    for (index = 0; index < sizeof(bad_meta) / sizeof(bad_meta[0]); index++)
    {
        expect_w25_check(bad_meta[index][0], bad_meta[index][1], 0x20001000, APP_START_ADDR + 0x101, 1);
        assert(w25_read_calls == 1 && "Invalid metadata must not read the APP vector");
    }
    for (index = 0; index < sizeof(bad_stacks) / sizeof(bad_stacks[0]); index++)
    {
        expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, bad_stacks[index], APP_START_ADDR + 0x101, 1);
        assert(w25_read_calls == 2);
    }
    const uint32_t bad_handlers[] =
    {
        0, UINT32_MAX, APP_START_ADDR + 1, APP_START_ADDR + 7,
        RESET_START_ADDR + 0x101, APP_START_ADDR + 0x1001, app_addr_end - 1
    };
    for (index = 0; index < sizeof(bad_handlers) / sizeof(bad_handlers[0]); index++)
    {
        expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, 0x20001000, bad_handlers[index], 1);
        assert(w25_read_calls == 2);
    }
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1001, 0x20001000, APP_START_ADDR + 0x1001, 1);

    // 每个 APP 扇区末端及跨界一字节：只擦到包含 BIN 最后一个字节的扇区。
    const uint32_t erase_cases[][2] =
    {
        {0x801, 1}, {0x802, 1}, {0x9FF, 1}, {0xA00, 1}, {0xA01, 1},
        {0x3FFF, 1}, {0x4000, 1}, {0x4001, 2},
        {0x7FFF, 2}, {0x8000, 2}, {0x8001, 3},
        {0x17FFF, 3}, {0x18000, 3}, {0x18001, 4},
        {0x37FFF, 4}, {0x38000, 4}, {0x38001, 5},
        {0x57FFF, 5}, {0x58000, 5}, {0x58001, 6}, {0x78000, 6}
    };
    for (index = 0; index < sizeof(erase_cases) / sizeof(erase_cases[0]); index++)
    {
        uint32_t length = erase_cases[index][0];
        const uint32_t starts[] = {0x1000, 0x1001, 0x1FFF, W25Q16_SIZE_BYTES - length};
        uint32_t image_start = starts[index % (sizeof(starts) / sizeof(starts[0]))];
        uint32_t erase_end = flash_sector_ends[erase_cases[index][1] - 1];

        prepare_w25_copy(image_start, length);
        App_bootloader_update();
        assert(w25_copy_bytes == length && w25_read_calls == 2 + (length + 511) / 512);
        assert(flash_unlock_calls == 2 && flash_erase_calls == 1 && flash_lock_calls == 2);
        assert(flash_erase.NbSectors == erase_cases[index][1]);
        assert(flash_program_calls == (length + 1) / 2);
        assert(memcmp((void *)(uintptr_t)APP_START_ADDR, w25_data + image_start, length) == 0);
        for (address = 0x08000000; address < APP_START_ADDR; address++)
        {
            assert(*(uint8_t *)(uintptr_t)address == 0xA5);
        }
        for (address = APP_START_ADDR + length; address < app_addr_end; address++)
        {
            if (address < erase_end)
            {
                assert(*(uint8_t *)(uintptr_t)address == 0xFF);
            }
            else
            {
                assert(*(uint8_t *)(uintptr_t)address == 0xA5);
            }
        }
        assert(bootloader_check_update_status == BOOT_NO_UPDATE);
        assert(eeprom_status == BOOT_NO_UPDATE && eeprom_key == BOOT_KEY && eeprom_write_calls == 1);
    }

    bootloader_check_update_status = BOOT_UPDATE;
    flash_unlock_calls = flash_erase_calls = flash_lock_calls = 0;
    flash_program_calls = eeprom_write_calls = 0;
    expect_w25_check(UINT32_MAX, UINT32_MAX, 0, 0, 1);
    w25_read_calls = 0;
    App_bootloader_update();
    assert(w25_read_calls == 1 && bootloader_check_update_status == BOOT_RESET);
    assert(flash_unlock_calls == 0 && flash_erase_calls == 0 && flash_lock_calls == 0);
    for (index = 0; index < sizeof(bad_meta) / sizeof(bad_meta[0]); index++)
    {
        expect_w25_check(bad_meta[index][0], bad_meta[index][1], 0x20001000, APP_START_ADDR + 0x101, 1);
        w25_read_calls = 0;
        bootloader_check_update_status = BOOT_UPDATE;
        App_bootloader_update();
        assert(bootloader_check_update_status == BOOT_RESET && eeprom_write_calls == 0 && flash_program_calls == 0);
        assert(flash_unlock_calls == 0 && flash_erase_calls == 0 && flash_lock_calls == 0);
    }
    expect_w25_check(APP_W25_START_ADDR_MIN, 0x1000, 0, APP_START_ADDR + 0x101, 1);
    w25_read_calls = 0;
    bootloader_check_update_status = BOOT_UPDATE;
    App_bootloader_update();
    assert(flash_unlock_calls == 0 && flash_erase_calls == 0 && flash_lock_calls == 0);

    // 解锁失败不擦除；擦除失败仍上锁；HAL 非成功状态统一返回失败。
    w25_read_calls = 2;
    app_size_bytes = 0x1000;
    flash_unlock_status = HAL_ERROR;
    assert(App_bootloader_erase_app_flash() == 1);
    assert(flash_erase_calls == 0 && flash_lock_calls == 0);
    flash_unlock_calls = 0;
    flash_unlock_status = HAL_OK;
    flash_erase_status = HAL_TIMEOUT;
    assert(App_bootloader_erase_app_flash() == 1 && flash_lock_calls == 1);
    flash_unlock_calls = flash_erase_calls = flash_lock_calls = 0;
    flash_erase_status = HAL_OK;
    flash_lock_status = HAL_ERROR;
    assert(App_bootloader_erase_app_flash() == 1);
    flash_lock_status = HAL_OK;

    // 擦除、写入中途或上锁失败，不清升级标记，也不进入未完成的 APP。
    for (index = 0; index < 5; index++)
    {
        prepare_w25_copy(0x1FFF, 0xA01);
        if (index == 0)
        {
            flash_unlock_status = HAL_BUSY;
        }
        else if (index == 1)
        {
            flash_erase_status = HAL_TIMEOUT;
        }
        else if (index == 2)
        {
            flash_unlock_fail_call = 2;
        }
        else if (index == 3)
        {
            flash_program_fail_call = 257;
        }
        else
        {
            flash_lock_fail_call = 2;
        }
        App_bootloader_update();
        assert(bootloader_check_update_status == BOOT_RESET);
        assert(eeprom_status == BOOT_UPDATE && eeprom_write_calls == 0);
        if (index >= 3)
        {
            assert(flash_lock_calls == 2);
        }
    }
    // 元数据、向量、每个正文块和奇数尾块读取失败，均不能清标记或写入失败块。
    for (index = 1; index <= 8; index++)
    {
        uint32_t expected_program_calls = 0;

        reset_hardware();
        prepare_w25_copy(APP_W25_START_ADDR_MIN, 0xA01);
        w25_read_fail_call = index;
        App_bootloader_update();
        assert(w25_read_calls == index && bootloader_check_update_status == BOOT_RESET);
        assert(eeprom_status == BOOT_UPDATE && eeprom_write_calls == 0);
        if (index <= 2)
        {
            assert(flash_erase_calls == 0 && flash_unlock_calls == 0 && flash_lock_calls == 0);
        }
        else
        {
            expected_program_calls = (index - 3) * 256;
            assert(flash_erase_calls == 1 && flash_unlock_calls == 2 && flash_lock_calls == 2);
            assert(*(uint16_t *)(uintptr_t)(APP_START_ADDR + expected_program_calls * 2) == 0xFFFF);
        }
        assert(flash_program_calls == expected_program_calls);
        set_vector(RESET_START_ADDR, 0x20002000, RESET_START_ADDR + 0x101);
        if (setjmp(entry) == 0)
        {
            App_bootloader_jump_app();
            assert(0 && "Read failure must enter recovery");
        }
        assert(entered_handler == RESET_START_ADDR + 0x101 && SCB->VTOR == RESET_START_ADDR);
    }
    reset_hardware();
    prepare_w25_copy(APP_W25_START_ADDR_MIN, 0xA01);
    key_during_program = 1;
    App_bootloader_update();
    assert(bootloader_check_update_status == BOOT_RESET && disabled == 0);
    assert(eeprom_status == BOOT_NO_UPDATE && eeprom_write_calls == 1);
    assert(memcmp((void *)(uintptr_t)APP_START_ADDR, w25_data + APP_W25_START_ADDR_MIN, 0xA01) == 0);
    w25_read_calls = 0;
    bootloader_check_update_status = BOOT_NO_UPDATE;
    App_bootloader_update();
    assert(w25_read_calls == 0);
    for (index = 0; index < sizeof(stack_tops) / sizeof(stack_tops[0]); index++)
    {
        expect_jump(APP_START_ADDR, stack_tops[index], APP_START_ADDR + 0x101);
        expect_jump(RESET_START_ADDR, stack_tops[index], RESET_START_ADDR + 0x101);
    }
    for (index = 0; index < sizeof(bad_stacks) / sizeof(bad_stacks[0]); index++)
    {
        expect_rejected(APP_START_ADDR, bad_stacks[index], APP_START_ADDR + 0x101);
    }
    expect_rejected(0x08000000, 0x20001000, APP_START_ADDR + 0x101);
    expect_rejected(APP_START_ADDR + 4, 0x20001000, APP_START_ADDR + 0x101);
    expect_rejected(APP_START_ADDR, 0x20001004, APP_START_ADDR + 0x101);
    expect_rejected(APP_START_ADDR, 0x20001000, APP_START_ADDR + 0x100);
    expect_rejected(APP_START_ADDR, 0x20001000, APP_START_ADDR + 1);
    expect_rejected(APP_START_ADDR, 0x20001000, RESET_START_ADDR + 0x101);
    expect_rejected(APP_START_ADDR, 0x20001000, app_addr_end + 1);
    expect_rejected(RESET_START_ADDR, 0x20001000, APP_START_ADDR + 1);
    expect_jump(APP_START_ADDR, 0x20001000, app_addr_end - 1);
    expect_jump(RESET_START_ADDR, 0x20001000, APP_START_ADDR - 1);

    set_vector(APP_START_ADDR, 0x20001000, APP_START_ADDR + 0x101);
    set_vector(RESET_START_ADDR, 0x20002000, RESET_START_ADDR + 0x101);
    for (index = 0; index < sizeof(invalid_keys) / sizeof(invalid_keys[0]); index++)
    {
        for (key_mode = 0; key_mode < 3; key_mode++)
        {
            reset_hardware();
            bootloader_check_update_status = BOOT_NO_UPDATE;
            eeprom_key = invalid_keys[index];
            eeprom_status = 0xFF;
            if (key_mode == 1)
            {
                key_during_read = 1;
            }
            else if (key_mode == 2)
            {
                key_during_wait = 1;
            }
            App_bootloader_check_update();
            assert(eeprom_key == BOOT_KEY && eeprom_status == BOOT_UPDATE && eeprom_write_calls == 1);
            assert(disabled == 0);
            if (key_mode == 0)
            {
                assert(bootloader_check_update_status == BOOT_NO_UPDATE);
            }
            else
            {
                assert(bootloader_check_update_status == BOOT_RESET);
            }

            // 模拟下次启动读取持久状态，不应重复初始化 EEPROM。
            bootloader_check_update_status = BOOT_NO_UPDATE;
            key_during_read = 0;
            key_during_wait = 0;
            App_bootloader_check_update();
            assert(eeprom_write_calls == 1 && bootloader_check_update_status == BOOT_UPDATE);
        }
    }
    for (index = 0; index < sizeof(states) / sizeof(states[0]); index++)
    {
        reset_hardware();
        bootloader_check_update_status = BOOT_NO_UPDATE;
        eeprom_status = states[index];
        App_bootloader_check_update();
        App_bootloader_check_default();
        assert(delay_ms == 3000);
        address = APP_START_ADDR;
        if (states[index] == BOOT_RESET)
        {
            address = RESET_START_ADDR;
        }
        if (setjmp(entry) == 0)
        {
            App_bootloader_jump_app();
            assert(0);
        }
        assert(SCB->VTOR == address);
    }
    reset_hardware();
    bootloader_check_update_status = BOOT_NO_UPDATE;
    eeprom_status = BOOT_NO_UPDATE;
    key_during_read = 1;
    App_bootloader_check_update();
    assert(bootloader_check_update_status == BOOT_RESET);
    bootloader_check_update_status = BOOT_NO_UPDATE;
    HAL_GPIO_EXTI_Callback(key1_Pin + 1);
    assert(bootloader_check_update_status == BOOT_NO_UPDATE);
    key_during_wait = 1;
    App_bootloader_check_default();
    assert(bootloader_check_update_status == BOOT_RESET);
    if (setjmp(entry) == 0)
    {
        App_bootloader_jump_app();
        assert(0);
    }
    assert(SCB->VTOR == RESET_START_ADDR);

    reset_hardware();
    set_vector(RESET_START_ADDR, UINT32_MAX, UINT32_MAX);
    bootloader_check_update_status = BOOT_RESET;
    App_bootloader_jump_app();
    assert(disabled == 0 && deinit_calls == 0);
    assert(VirtualFree(flash, 0, MEM_RELEASE) != 0);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="bootloader_jump_test_") as temporary:
    path = Path(temporary)
    (path / "usart.h").write_text(mock_header, encoding="utf-8")
    (path / "app_bootloader.h").write_bytes((root / "application/app_bootloader.h").read_bytes())
    (path / "test.c").write_text(harness, encoding="utf-8")
    executable = path / "test.exe"
    subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-int-to-pointer-cast",
                    "-I", str(path), "-I", str(root / "interface"), str(path / "test.c"),
                    "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("PASS: W25 metadata/vector bounds; complete F407VE APP copy, sector/buffer boundaries, odd tail, flags and HAL failures; SPI read failure preserves flags and enters recovery; EEPROM/KEY1; routing and IRQ handoff")
