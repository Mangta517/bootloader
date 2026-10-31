"""Compile the actual F103 receiver with a circular DMA mock."""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef TEST_USART_H
#define TEST_USART_H
#include <stdint.h>
typedef struct
{
    uint32_t remaining, tc_pending;
} DMA_HandleTypeDef;
typedef struct
{
    void *Instance;
    DMA_HandleTypeDef *hdmarx;
} UART_HandleTypeDef;
extern UART_HandleTypeDef huart1;
#define USART1 ((void *)1)
#define HAL_OK 0
#define RESET 0
#define __HAL_DMA_GET_COUNTER(dma) ((dma)->remaining)
#define __HAL_DMA_GET_TC_FLAG_INDEX(dma) 1
#define __HAL_DMA_GET_FLAG(dma, flag) ((dma)->tc_pending)
#define __DMB() __asm__ __volatile__("" ::: "memory")
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __set_PRIMASK(uint32_t value);
uint32_t HAL_GetTick(void);
uint32_t HAL_UART_Receive_DMA(UART_HandleTypeDef *uart, uint8_t *data, uint16_t size);
void HAL_UART_RxHalfCpltCallback(UART_HandleTypeDef *uart);
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *uart);
void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart);
#endif
'''
harness = r'''
#include "int_uart.h"
#include "usart.h"
#include <assert.h>
#include <stdio.h>

static DMA_HandleTypeDef dma;
UART_HandleTypeDef huart1 = {USART1, &dma};
static uint8_t *dma_buffer;
static uint32_t tick, primask, produced, consumed, start_error;
static uint8_t output[UART1_RX_BUFFER_SIZE + 1];

uint32_t __get_PRIMASK(void)
{
    return primask;
}
void __disable_irq(void)
{
    primask = 1;
}
void __set_PRIMASK(uint32_t value)
{
    primask = value;
}
uint32_t HAL_GetTick(void)
{
    return tick;
}
uint32_t HAL_UART_Receive_DMA(UART_HandleTypeDef *uart, uint8_t *data, uint16_t size)
{
    assert(uart == &huart1 && size == UART1_RX_RING_SIZE);
    dma_buffer = data;
    dma.remaining = size;
    dma.tc_pending = 0;
    return start_error;
}
static void reset(void)
{
    start_error = 0;
    tick = 0;
    primask = 0;
    produced = 0;
    consumed = 0;
    output[UART1_RX_BUFFER_SIZE] = 0xA5;
    assert(int_uart_init() == 0);
}
static void feed(uint32_t count, uint8_t defer_tc)
{
    uint32_t index;
    for (index = 0; index < count; index++)
    {
        dma_buffer[produced % UART1_RX_RING_SIZE] = (uint8_t)produced;
        produced++;
        dma.remaining--;
        if (dma.remaining == UART1_RX_DMA_HALF_SIZE)
        {
            HAL_UART_RxHalfCpltCallback(&huart1);
        }
        if (dma.remaining == 0)
        {
            dma.remaining = UART1_RX_RING_SIZE;
            if (defer_tc != 0)
            {
                dma.tc_pending = 1;
            }
            else
            {
                HAL_UART_RxCpltCallback(&huart1);
            }
        }
    }
}
static uint16_t receive(void)
{
    uint16_t length = int_uart_receive(output);
    uint16_t index;
    assert(length <= UART1_RX_BUFFER_SIZE);
    assert(output[UART1_RX_BUFFER_SIZE] == 0xA5);
    for (index = 0; index < length; index++)
    {
        assert(output[index] == (uint8_t)(consumed + index));
    }
    consumed += length;
    return length;
}
static void drain(void)
{
    uint32_t poll;
    /* Crossing a DMA half boundary can stage a partial block and return zero. */
    for (poll = 0; poll < UART1_RX_RING_SIZE / UART1_RX_BUFFER_SIZE + 2; poll++)
    {
        (void)receive();
    }
}
int main(void)
{
    uint32_t index;
    uint32_t completed_tick;
    UART_HandleTypeDef other = {0};

    reset();
    assert(receive() == 0);
    feed(3, 0);
    assert(receive() == 0);
    tick = 9;
    assert(receive() == 0);
    tick = 10;
    assert(receive() == 3 && uart1_rx_full_len == 3);
    completed_tick = uart1_rx_complete_tick;
    tick = 20;
    assert(receive() == 0 && uart1_rx_complete_tick == completed_tick);

    reset();
    feed(512, 0);
    assert(receive() == 0);
    tick = 10;
    assert(receive() == 512 && receive() == 0);
    assert(uart1_rx_complete_tick == 10);

    reset();
    for (index = 0; index < 24; index++)
    {
        feed(UART1_RX_DMA_HALF_SIZE, 0);
        drain();
        assert(consumed == produced);
        tick++;
    }
    feed(777, 0);
    assert(receive() == 0);
    tick += UART1_RX_TIMEOUT_MS;
    drain();
    assert(consumed == produced && uart1_rx_full_len == produced);
    assert(uart1_rx_overflow_count == 0 && uart1_rx_error_count == 0);
    assert(uart1_rx_complete_tick == tick);

    reset();
    feed(UART1_RX_RING_SIZE, 1);
    primask = 1;
    assert(receive() == 512 && primask == 1);
    assert(uart1_rx_full_len == UART1_RX_RING_SIZE);
    dma.tc_pending = 0;
    HAL_UART_RxCpltCallback(&huart1);
    primask = 0;
    drain();
    assert(consumed == produced && uart1_rx_full_len == produced);

    reset();
    feed(UART1_RX_RING_SIZE, 1);
    dma.remaining = 0;
    assert(receive() == 512 && uart1_rx_full_len == UART1_RX_RING_SIZE);
    dma.remaining = UART1_RX_RING_SIZE;
    dma.tc_pending = 0;
    HAL_UART_RxCpltCallback(&huart1);
    drain();
    assert(consumed == produced);

    reset();
    feed(UART1_RX_RING_SIZE + 808, 0);
    consumed = 808;
    (void)receive();
    tick = UART1_RX_TIMEOUT_MS;
    drain();
    assert(consumed == produced && uart1_rx_overflow_count == 808);

    reset();
    tick = UINT32_MAX - 5;
    feed(1, 0);
    assert(receive() == 0);
    tick = 4;
    assert(receive() == 1);

    reset();
    HAL_UART_ErrorCallback(&other);
    HAL_UART_RxHalfCpltCallback(&other);
    HAL_UART_RxCpltCallback(&other);
    assert(uart1_rx_error_count == 0 && receive() == 0);
    HAL_UART_ErrorCallback(&huart1);
    feed(3, 0);
    tick = 10;
    assert(uart1_rx_error_count == 1 && receive() == 0);
    reset();
    start_error = 1;
    assert(int_uart_init() == 1 && uart1_rx_error_count == 1);
    assert(receive() == 0);
    puts("PASS: idle tail, 512-byte block, continuous binary stream, circular/pending DMA wrap,");
    puts("      overflow count, tick wrap, IRQ mask restoration and UART/start errors");
    return 0;
}
'''

compiler = shutil.which("gcc") or r"C:\msys64\mingw64\bin\gcc.exe"
with tempfile.TemporaryDirectory(prefix="f103-uart-rx-") as folder:
    temporary = Path(folder)
    (temporary / "usart.h").write_text(mock_header, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_uart_receive.exe"
    subprocess.run([
        compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
        f"-I{temporary}", f"-I{root / 'interface'}",
        str(root / "interface/int_uart.c"), str(temporary / "test.c"),
        "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
