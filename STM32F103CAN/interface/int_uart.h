#ifndef INT_UART_H
#define INT_UART_H

#include <stdint.h>

#define UART1_RX_BUFFER_SIZE 512
#define UART1_RX_RING_SIZE 8192
#define UART1_RX_DMA_HALF_SIZE (UART1_RX_RING_SIZE / 2)
#define UART1_RX_TIMEOUT_MS 10

/* int_uart.c 定义；主循环取数时统计，错误计数也由 UART/DMA 中断更新。 */
extern volatile uint32_t uart1_rx_full_len;
extern volatile uint32_t uart1_rx_overflow_count;
extern volatile uint32_t uart1_rx_error_count;
extern volatile uint32_t uart1_rx_complete_tick;

/* MX_USART1_UART_Init 后调用一次，0 成功、1 失败。 */
uint8_t int_uart_init(void);
/* 主循环反复调用；data 至少 512 字节，返回本次有效长度，0 表示暂无数据或接收出错。
 * 数据是二进制，不补字符串结束符；出错后停止取数，通过 uart1_rx_error_count 判断。
 */
uint16_t int_uart_receive(uint8_t *data);

#endif
