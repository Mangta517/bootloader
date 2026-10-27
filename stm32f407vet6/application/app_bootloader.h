#ifndef __APP_BOOTLOADER_H
#define __APP_BOOTLOADER_H

#include "int_bootloader.h"
#include "int_eeprom.h"

//添加校验密钥
#define BOOT_KEY_ADDR 0x11
#define BOOT_KEY 0x5A6B

//更新状态的位置
#define CHECK_UPDATE_ADDR 0x10

//更新状态的值
#define BOOT_UPDATE 0x01
#define BOOT_NO_UPDATE 0x02
#define BOOT_RESET 0x03

/* W25Q16 元数据位置：块、块内扇区、扇区内页、页内字节地址，均从 0 编号。 */
#define APP_META_BLOCK      0x00  /* 第 0 个 64 KiB 块 */
#define APP_META_SECTOR     0x00  /* 块内第 0 个 4 KiB 扇区 */
#define APP_META_PAGE       0x00  /* 扇区内第 0 个 256 字节页 */
#define APP_META_BYTE_ADDR  0x00  /* 页内第 0 字节 */

/* 定义于 app_bootloader.c，依次接收 8 字节元数据和 8 字节 APP 向量。 */
extern uint8_t data[8];

/* 读取 APP 向量后使用：前 4 字节按小端拼成初始栈指针。 */
#define APP_STACK_PTR ((uint32_t)data[0] | ((uint32_t)data[1] << 8) | ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24))

/* 读取 APP 向量后使用：后 4 字节按小端拼成复位入口，包含 Thumb 标志。 */
#define APP_RESET_HANDLER ((uint32_t)data[4] | ((uint32_t)data[5] << 8) | ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24))

//判断是否需要升级
void App_bootloader_check_update(void);

//升级
void App_bootloader_update(void);

//跳转应用地址
void App_bootloader_jump_app(void);


//判断进入默认程序
void App_bootloader_check_default(void);

#endif
