#ifndef __INT_BOOTLOADER_H
#define __INT_BOOTLOADER_H

#include "stdlib.h"
#include "usart.h"
#include "string.h"

#define bootloader_uart_rx_buff_len   512
#define BOOTLOADER_UART_RING_SIZE     8192
#define BOOTLOADER_UART_DMA_HALF_SIZE (BOOTLOADER_UART_RING_SIZE / 2)
#define BOOTLOADER_UART_RX_TIMEOUT_MS 10
#define stack_addr_start              0x20000000
#define app_addr_end                  0x08080000
/* 保底 Bootloader 位于扇区 1（0x08004000–0x08007FFF），占 16 KiB；APP 从扇区 2（0x08008000）开始，结束地址不包含在可写范围内。 */
#define APP_START_ADDR 0x08008000
#define APP_SIZE_BYTES (app_addr_end - APP_START_ADDR)

/* 升级收尾有独立的待处理状态，不能当作二值成功/失败结果使用。 */
typedef enum
{
    BOOTLOADER_RESULT_OK = 0,
    BOOTLOADER_RESULT_ERROR = 1,
    BOOTLOADER_RESULT_PENDING = 2
} bootloader_result_enum;

// 主循环统计 DMA 已接收字节；缓冲覆盖字节及串口/DMA 错误分别计数。
extern volatile uint32_t uart1_rx_full_len;
extern volatile uint32_t uart1_rx_overflow_count;
extern volatile uint32_t uart1_rx_error_count;
// 最近一次空闲接收结束的确认时间（HAL_GetTick，ms），由 int 层更新，供 app 层读取。
extern volatile uint32_t uart1_rx_complete_tick;

//串口接收
void Int_bootloader_Init(void);
uint8_t bootloader_start_dma(void);
void Int_bootloader_Process(void);
//1失败 0成功
uint8_t Int_bootloader_jump_to_app(void);

/* 准备阶段整段擦除；收尾停止接收、分批处理尾部并补写遗留字节。
 * 二值接口：0 成功、1 失败；finish 用业务枚举区分完成、失败和尾部待处理。
 */
uint8_t bootloader_prepare_image(uint32_t image_length);
bootloader_result_enum bootloader_finish_image(void);
uint8_t Int_early_flash_erase(uint32_t early_sector_addr, uint16_t sectors);
#endif
