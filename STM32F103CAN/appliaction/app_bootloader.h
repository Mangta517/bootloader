#ifndef APP_BOOTLOADER_H
#define APP_BOOTLOADER_H

#include <stdint.h>

/* F103C8：前 32 KiB 为程序，后 32 KiB 存串口数据，结束地址不包含在内。 */
#define APP_FLASH_START_ADDR 0x08008000
#define APP_FLASH_END_ADDR 0x08010000
#define APP_FLASH_CAPACITY_BYTES (APP_FLASH_END_ADDR - APP_FLASH_START_ADDR)
#define APP_FLASH_FINISH_DELAY_MS 2000

typedef enum
{
    APP_FLASH_WAIT = 0,
    APP_FLASH_RECEIVING,
    APP_FLASH_DONE,
    APP_FLASH_ERROR
} app_flash_state_enum;

/* app_bootloader.c 定义，主循环更新；DONE 时 app_size_bytes 为 Flash 中的有效字节数。 */
extern app_flash_state_enum app_flash_state;
extern uint32_t app_size_bytes;
/* start:长度 中声明的 BIN 字节数，不包含命令和尾部 FF 填充。 */
extern uint32_t uart1_rx_app_len;

void App_bootloader_init(void);
void App_bootloader_update(void);

#endif
