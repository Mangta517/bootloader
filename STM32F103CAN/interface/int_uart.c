#include "int_uart.h"
#include "usart.h"
#include <string.h>

/* 移植自 F407 int_bootloader.c，仅保留接收，不在此层写 Flash。 */
static uint8_t uart1_rx_dma_buf[2][UART1_RX_DMA_HALF_SIZE];
static uint8_t uart1_rx_buf[UART1_RX_BUFFER_SIZE];
static uint16_t uart1_rx_len;
static volatile uint32_t uart1_dma_wrap_count;
static volatile uint32_t uart1_dma_ready_end[2];
static uint32_t uart1_dma_read_total;
static uint32_t uart1_dma_last_total;
static uint32_t uart1_rx_last_tick;
static uint8_t uart1_rx_active;

volatile uint32_t uart1_rx_full_len;
volatile uint32_t uart1_rx_overflow_count;
volatile uint32_t uart1_rx_error_count;
volatile uint32_t uart1_rx_complete_tick;

void HAL_UART_RxHalfCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_dma_ready_end[0] = uart1_dma_wrap_count * UART1_RX_RING_SIZE + UART1_RX_DMA_HALF_SIZE;
    }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_dma_wrap_count++;
        uart1_dma_ready_end[1] = uart1_dma_wrap_count * UART1_RX_RING_SIZE;
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_rx_error_count++;
    }
}

uint8_t int_uart_init(void)
{
    uart1_rx_len = 0;
    uart1_dma_wrap_count = 0;
    uart1_dma_ready_end[0] = 0;
    uart1_dma_ready_end[1] = 0;
    uart1_dma_read_total = 0;
    uart1_dma_last_total = 0;
    uart1_rx_last_tick = HAL_GetTick();
    uart1_rx_active = 0;
    uart1_rx_full_len = 0;
    uart1_rx_overflow_count = 0;
    uart1_rx_error_count = 0;
    uart1_rx_complete_tick = 0;

    if (HAL_UART_Receive_DMA(&huart1, &uart1_rx_dma_buf[0][0], UART1_RX_RING_SIZE) != HAL_OK)
    {
        uart1_rx_error_count++;
        return 1;
    }
    return 0;
}

static uint32_t uart1_dma_received_total(void)
{
    uint32_t primask = __get_PRIMASK();
    uint32_t wraps;
    uint32_t remaining;

    /* 同时快照回绕计数和 CNDTR，补上 DMA 已回绕但 IRQ 尚未执行的情况。 */
    __disable_irq();
    wraps = uart1_dma_wrap_count;
    remaining = __HAL_DMA_GET_COUNTER(huart1.hdmarx);
    if (__HAL_DMA_GET_FLAG(huart1.hdmarx, __HAL_DMA_GET_TC_FLAG_INDEX(huart1.hdmarx)) != RESET)
    {
        wraps++;
        remaining = __HAL_DMA_GET_COUNTER(huart1.hdmarx);
        if (remaining == 0)
        {
            remaining = UART1_RX_RING_SIZE;
        }
    }
    __set_PRIMASK(primask);
    __DMB();
    return wraps * UART1_RX_RING_SIZE + UART1_RX_RING_SIZE - remaining;
}

uint16_t int_uart_receive(uint8_t *data)
{
    uint32_t received;
    uint32_t available;
    uint32_t position;
    uint32_t half;
    uint32_t half_end;
    uint32_t copy_end;
    uint32_t now;
    uint16_t length = 0;

    if (uart1_rx_error_count != 0)
    {
        return 0;
    }

    received = uart1_dma_received_total();
    now = HAL_GetTick();
    if (received != uart1_dma_last_total)
    {
        uart1_rx_full_len += received - uart1_dma_last_total;
        uart1_dma_last_total = received;
        uart1_rx_last_tick = now;
        uart1_rx_active = 1;
        uart1_rx_complete_tick = 0;
    }
    available = received - uart1_dma_read_total;
    if (available > UART1_RX_RING_SIZE)
    {
        uart1_rx_overflow_count += available - UART1_RX_RING_SIZE;
        uart1_dma_read_total = received - UART1_RX_RING_SIZE;
    }

    copy_end = uart1_dma_read_total;
    if (uart1_dma_read_total != received)
    {
        half = (uart1_dma_read_total / UART1_RX_DMA_HALF_SIZE) % 2;
        half_end = uart1_dma_read_total + UART1_RX_DMA_HALF_SIZE - uart1_dma_read_total % UART1_RX_DMA_HALF_SIZE;
        if (uart1_dma_ready_end[half] == half_end)
        {
            copy_end = half_end;
        }
        else if ((uint32_t)(now - uart1_rx_last_tick) >= UART1_RX_TIMEOUT_MS)
        {
            /* 不足半区的尾部在 DMA 写入位置稳定 10 ms 后读取。 */
            copy_end = received;
        }
    }
    while ((uart1_rx_len < UART1_RX_BUFFER_SIZE) && (uart1_dma_read_total != copy_end))
    {
        position = uart1_dma_read_total % UART1_RX_RING_SIZE;
        uart1_rx_buf[uart1_rx_len++] = uart1_rx_dma_buf[position / UART1_RX_DMA_HALF_SIZE][position % UART1_RX_DMA_HALF_SIZE];
        uart1_dma_read_total++;
    }

    if ((uart1_rx_len == UART1_RX_BUFFER_SIZE) ||
        ((uart1_rx_len > 0) && (uart1_dma_read_total == received) &&
         ((uint32_t)(now - uart1_rx_last_tick) >= UART1_RX_TIMEOUT_MS)))
    {
        length = uart1_rx_len;
        memcpy(data, uart1_rx_buf, length);
        uart1_rx_len = 0;
    }

    if ((uart1_rx_active != 0) && (uart1_rx_len == 0) && (uart1_dma_read_total == received) &&
        ((uint32_t)(now - uart1_rx_last_tick) >= UART1_RX_TIMEOUT_MS) && (uart1_dma_received_total() == received))
    {
        uart1_rx_complete_tick = HAL_GetTick();
        uart1_rx_active = 0;
    }
    return length;
}
