"""Run the actual bootloader C with mocked circular DMA and mapped host Flash memory."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef UART_MOCK_H
#define UART_MOCK_H
#include <stdint.h>
typedef uint32_t HAL_StatusTypeDef;
#define HAL_OK 0U
#define HAL_ERROR 1U
#define HAL_BUSY 2U
#define HAL_TIMEOUT 3U
#define DMA_SxCR_EN 1U
#define RESET 0U
#define DMA_IT_HT 1U
#define HAL_UART_RXEVENT_TC 0U
#define HAL_UART_RXEVENT_HT 1U
#define HAL_UART_RXEVENT_IDLE 2U
#define HAL_UART_STATE_READY 0U
#define HAL_UART_STATE_BUSY_RX 1U
#define FLASH_SECTOR_4 4U
#define FLASH_SECTOR_5 5U
#define FLASH_SECTOR_7 7U
#define FLASH_VOLTAGE_RANGE_3 3U
#define FLASH_TYPEERASE_SECTORS 0U
#define FLASH_TYPEPROGRAM_HALFWORD 1U
typedef struct
{
    uint32_t SR;
} mock_uart_struct;
typedef struct
{
    uint32_t CR;
} mock_dma_stream_struct;
typedef struct
{
    mock_dma_stream_struct *Instance;
    uint32_t NDTR, tc_flag, ht_flag, ht_enabled, tc_enabled;
} DMA_HandleTypeDef;
typedef struct
{
    DMA_HandleTypeDef *hdmarx;
    mock_uart_struct *Instance;
    uint32_t RxState;
    uint32_t RxEventType;
} UART_HandleTypeDef;
typedef struct
{
    uint32_t TypeErase, Sector, NbSectors, VoltageRange;
} FLASH_EraseInitTypeDef;
extern mock_uart_struct mock_uart;
extern UART_HandleTypeDef huart1;
#define USART1 (&mock_uart)
#define __DMB() __asm__ __volatile__("" ::: "memory")
#define __HAL_UART_CLEAR_OREFLAG(uart) ((uart)->Instance->SR = 0U)
uint32_t HAL_GetTick(void);
void mock_dma_disable(DMA_HandleTypeDef *dma);
#define __HAL_DMA_DISABLE(dma) mock_dma_disable(dma)
#define __HAL_DMA_GET_COUNTER(dma) ((dma)->NDTR)
#define __HAL_DMA_GET_TC_FLAG_INDEX(dma) 1U
#define __HAL_DMA_GET_FLAG(dma, flag) ((dma)->tc_flag)
#define __HAL_DMA_DISABLE_IT(dma, interrupt) ((dma)->ht_enabled = 0U)
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __set_PRIMASK(uint32_t value);
uint32_t HAL_UART_Receive_DMA(UART_HandleTypeDef *uart, uint8_t *buffer, uint16_t size);
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *uart);
uint32_t HAL_UART_DMAPause(UART_HandleTypeDef *uart);
uint32_t HAL_DMA_Abort(DMA_HandleTypeDef *dma);
uint32_t HAL_UARTEx_GetRxEventType(UART_HandleTypeDef *uart);
uint32_t HAL_FLASH_Unlock(void);
uint32_t HAL_FLASH_Lock(void);
uint32_t HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *error);
uint32_t HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data);
#endif
'''

harness = r'''
#include <assert.h>
#include <windows.h>
#include "usart.h"
#include "int_bootloader.h"
mock_uart_struct mock_uart;
UART_HandleTypeDef huart1;
static uint32_t tick, program_calls, erase_calls;
static uint8_t *receive_target;
static DMA_HandleTypeDef mock_dma;
static mock_dma_stream_struct mock_stream;
static uint32_t primask, service_irq, receive_calls;
static uint32_t inject_bytes;
static uint8_t abort_tail_pending, abort_tail_byte;
static uint8_t expected[APP_SIZE_BYTES];
static uint32_t fail_program_call, fail_erase, fail_unlock, fail_lock, fail_receive;
static uint32_t reset_calls, ready_calls, success_calls, jump_calls;
static int test_printf(const char *format, ...)
{
    if (strcmp(format, "Flash erased; send data\r\n") == 0)
    {
        assert(huart1.RxState == HAL_UART_STATE_BUSY_RX);
        ready_calls++;
    }
    else if (strcmp(format, "ok,len:%lu\n") == 0)
    {
        success_calls++;
    }
    return 0;
}
#define printf test_printf
'''
# Test the receive/Flash path through its Init function; unrelated app-jump code is outside this fixture.
bootloader_source = (root / "interface/int_bootloader.c").read_text(encoding="utf-8")
init_start = bootloader_source.index("void Int_bootloader_Init(void)")
init_brace = bootloader_source.index("{", init_start)
depth = 1
init_end = init_brace + 1
while depth > 0:
    if bootloader_source[init_end] == "{":
        depth += 1
    elif bootloader_source[init_end] == "}":
        depth -= 1
    init_end += 1
harness += "\n" + bootloader_source[:init_end] + "\n"
harness += bootloader_source[bootloader_source.index("static uint32_t bootloader_flash_sector("):] + "\n"
def extract_function(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    end = brace + 1
    depth = 1
    while depth > 0:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end]


app_source = (root / "application/app_reset.c").read_text(encoding="utf-8-sig")
app_header = (root / "application/app_reset.h").read_text(encoding="utf-8-sig")
start = app_header.index("typedef enum")
end = app_header.index("} Bootloader_state;", start) + len("} Bootloader_state;")
harness += app_header[start:end] + r'''
static Bootloader_state boot_state;
static uint32_t uart1_rx_app_len;
void NVIC_SystemReset(void)
{
    reset_calls++;
}
void App_bootloader_Init(void)
{
}
void App_bootloader_run(void)
{
}
uint8_t App_bootloader_jump_to_app(void)
{
    jump_calls++;
    return 0U;
}
'''
harness += extract_function(app_source, "void App_bootloader_rec_data(void)") + "\n"
harness += extract_function(app_source, "bootloader_result_enum App_bootloader_check_data(void)") + "\n"
harness += extract_function(app_source, "void App_bootloader_work(void)") + "\n"
harness += r'''
uint32_t HAL_GetTick(void)
{
    return tick;
}
uint32_t HAL_UARTEx_GetRxEventType(UART_HandleTypeDef *uart)
{
    return uart->RxEventType;
}
static void service_dma_irq(void)
{
    if ((mock_dma.ht_flag != 0U) && (mock_dma.ht_enabled != 0) && (primask == 0U) && (service_irq != 0U))
    {
        mock_dma.ht_flag = 0;
        HAL_UART_RxHalfCpltCallback(&huart1);
    }
    if ((mock_dma.tc_flag != 0U) && (mock_dma.tc_enabled != 0) && (primask == 0U) && (service_irq != 0U))
    {
        mock_dma.tc_flag = 0;
        HAL_UART_RxCpltCallback(&huart1);
    }
}
void mock_dma_disable(DMA_HandleTypeDef *dma)
{
    if ((dma->Instance->CR & DMA_SxCR_EN) != 0)
    {
        dma->Instance->CR &= ~DMA_SxCR_EN;
        /* STM32F4 主动关闭 DMA 流也会置 TC；中断仍启用时会执行完成回调。 */
        dma->tc_flag = 1;
        service_dma_irq();
    }
}
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
    service_dma_irq();
}
uint32_t HAL_UART_Receive_DMA(UART_HandleTypeDef *uart, uint8_t *buffer, uint16_t size)
{
    assert(uart->RxState == HAL_UART_STATE_READY && size == BOOTLOADER_UART_RING_SIZE);
    if (fail_receive != 0U)
    {
        return HAL_ERROR;
    }
    mock_stream.CR = DMA_SxCR_EN;
    receive_target = buffer;
    uart->RxState = HAL_UART_STATE_BUSY_RX;
    mock_dma.NDTR = size;
    mock_dma.tc_flag = 0;
    mock_dma.ht_flag = 0;
    mock_dma.ht_enabled = 1;
    mock_dma.tc_enabled = 1;
    receive_calls++;
    return HAL_OK;
}
uint32_t HAL_UART_AbortReceive(UART_HandleTypeDef *uart)
{
    uart->RxState = HAL_UART_STATE_READY;
    mock_dma.tc_flag = 0;
    return HAL_OK;
}
uint32_t HAL_UART_DMAPause(UART_HandleTypeDef *uart)
{
    (void)uart;
    return HAL_OK;
}
static void feed_byte(uint8_t data);
uint32_t HAL_DMA_Abort(DMA_HandleTypeDef *dma)
{
    /* 与实际 HAL 一致：先禁止完成中断，再停止 DMA 并清除标志。 */
    dma->tc_enabled = 0;
    dma->ht_enabled = 0;
    if (abort_tail_pending != 0)
    {
        abort_tail_pending = 0;
        feed_byte(abort_tail_byte);
    }
    __HAL_DMA_DISABLE(dma);
    dma->tc_flag = 0U;
    dma->ht_flag = 0U;
    return HAL_OK;
}
uint32_t HAL_FLASH_Unlock(void)
{
    if (fail_unlock != 0U)
    {
        return HAL_ERROR;
    }
    return HAL_OK;
}
uint32_t HAL_FLASH_Lock(void)
{
    if (fail_lock != 0U)
    {
        return HAL_ERROR;
    }
    return HAL_OK;
}
uint32_t HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *error)
{
    uint32_t base;
    uint32_t size;
    assert(erase->Sector >= 2U && erase->Sector + erase->NbSectors <= 8U);
    if (fail_erase != 0U)
    {
        return HAL_ERROR;
    }
    for (uint32_t sector = erase->Sector; sector < erase->Sector + erase->NbSectors; sector++)
    {
        if (sector < 4U)
        {
            base = 0x08000000U + sector * 0x4000U;
            size = 0x4000U;
        }
        else if (sector == 4U)
        {
            base = 0x08010000U;
            size = 0x10000U;
        }
        else
        {
            base = 0x08020000U + (sector - 5U) * 0x20000U;
            size = 0x20000U;
        }
        memset((void *)(uintptr_t)base, 0xff, size);
    }
    *error = UINT32_MAX;
    erase_calls++;
    return HAL_OK;
}
static void feed_byte(uint8_t data)
{
    uint32_t position = BOOTLOADER_UART_RING_SIZE - mock_dma.NDTR;
    assert(huart1.RxState == HAL_UART_STATE_BUSY_RX);
    receive_target[position] = data;
    mock_dma.NDTR--;
    if ((mock_dma.NDTR == BOOTLOADER_UART_DMA_HALF_SIZE) && (mock_dma.ht_enabled != 0U))
    {
        mock_dma.ht_flag = 1;
        service_dma_irq();
    }
    if (mock_dma.NDTR == 0U)
    {
        mock_dma.NDTR = BOOTLOADER_UART_RING_SIZE;
        mock_dma.tc_flag = 1;
        service_dma_irq();
    }
}
uint32_t HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data)
{
    uint16_t *target = (uint16_t *)(uintptr_t)address;
    uint32_t i;
    assert(type == FLASH_TYPEPROGRAM_HALFWORD && (address & 1U) == 0);
    assert(address >= APP_START_ADDR && address + 2U <= app_addr_end);
    program_calls++;
    if (program_calls == fail_program_call)
    {
        return HAL_ERROR;
    }
    *target &= (uint16_t)data;
    /* Simulate an ISR preempting the consumer while its Flash buffer is in use. */
    for (i = 0; i < inject_bytes; i++)
    {
        feed_byte(expected[512U + i]);
    }
    inject_bytes = 0;
    return HAL_OK;
}
static void reset_image(uint32_t length)
{
    memset((void *)(uintptr_t)0x08000000U, 0xff, 0x80000U);
    memset(uart1_rx_dma_buf, 0, sizeof(uart1_rx_dma_buf));
    memset(uart1_rx_buf, 0, sizeof(uart1_rx_buf));
    uart1_rx_len = 0;
    uart1_rx_full_len = 0;
    uart1_rx_overflow_count = 0;
    uart1_rx_error_count = 0;
    flash_write_offset = 0;
    last_btye_flag = 0;
    last_byte = 0;
    tick = 0;
    program_calls = 0;
    erase_calls = 0;
    inject_bytes = 0;
    uart1_rx_last_tick = 0;
    abort_tail_pending = 0;
    primask = 0;
    service_irq = 1;
    receive_calls = 0;
    memset(&mock_dma, 0, sizeof(mock_dma));
    mock_dma.Instance = &mock_stream;
    mock_stream.CR = 0U;
    fail_program_call = 0U;
    fail_erase = 0U;
    fail_unlock = 0U;
    fail_lock = 0U;
    fail_receive = 0U;
    reset_calls = 0U;
    ready_calls = 0U;
    success_calls = 0;
    jump_calls = 0;
    huart1.hdmarx = &mock_dma;
    huart1.Instance = USART1;
    huart1.RxState = HAL_UART_STATE_READY;
    uart1_rx_app_len = length;
    assert(bootloader_prepare_image(length) == 0U);
    erase_calls = 0U;
    assert(bootloader_start_dma() == 0U);
}
static void reset_receive(void)
{
    reset_image(APP_SIZE_BYTES);
}
static void drain(void)
{
    while (uart1_dma_read_total != bootloader_dma_received_total())
    {
        uint32_t previous = uart1_dma_read_total;
        Int_bootloader_Process();
        if (uart1_dma_read_total == previous)
        {
            tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
            Int_bootloader_Process();
        }
    }
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    Int_bootloader_Process();
}
static void check_flash(uint32_t size)
{
    assert(flash_write_offset == size);
    assert(memcmp((void *)(uintptr_t)APP_START_ADDR, expected, size) == 0);
    assert(uart1_rx_overflow_count == 0);
}
int main(void)
{
    uint32_t i;
    uint32_t position;
    const uint32_t odd_chunks[] = {1, 2, 3, 1, 5};
    void *flash = VirtualAlloc((void *)(uintptr_t)0x08000000U, 0x80000U, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    assert(flash == (void *)(uintptr_t)0x08000000U);
    for (i = 0; i < sizeof(expected); i++)
    {
        expected[i] = (uint8_t)(i * 37U + 11U);
    }

    reset_receive();
    for (i = 0; i < 256; i++)
    {
        feed_byte(expected[i]);
    }
    Int_bootloader_Process();
    assert(program_calls == 0 && uart1_dma_read_total == 0);
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    Int_bootloader_Process();
    check_flash(256);
    assert(receive_calls == 1 && huart1.RxState == HAL_UART_STATE_BUSY_RX);
    position = program_calls;
    Int_bootloader_Process();
    assert(program_calls == position && flash_write_offset == 256);

    reset_receive();
    assert(mock_dma.ht_enabled == 1);
    for (i = 0; i < BOOTLOADER_UART_DMA_HALF_SIZE - 1U; i++)
    {
        feed_byte(expected[i]);
    }
    Int_bootloader_Process();
    assert(uart1_dma_read_total == 0 && program_calls == 0);
    feed_byte(expected[BOOTLOADER_UART_DMA_HALF_SIZE - 1U]);
    assert(uart1_dma_ready_end[0] == BOOTLOADER_UART_DMA_HALF_SIZE);
    assert(uart1_dma_read_total == 0 && program_calls == 0);
    Int_bootloader_Process();
    assert(uart1_dma_read_total == 512 && flash_write_offset == 512);
    for (i = BOOTLOADER_UART_DMA_HALF_SIZE; i < BOOTLOADER_UART_RING_SIZE; i++)
    {
        feed_byte(expected[i]);
    }
    assert(uart1_dma_ready_end[1] == BOOTLOADER_UART_RING_SIZE);
    assert(uart1_dma_read_total == 512 && flash_write_offset == 512);
    drain();
    check_flash(BOOTLOADER_UART_RING_SIZE);

    reset_receive();
    drain();
    assert(program_calls == 0 && erase_calls == 0);
    for (i = 0; i < 4452; i++)
    {
        feed_byte(expected[i]);
    }
    drain();
    check_flash(4452);
    assert(uart1_rx_full_len == 4452);

    reset_receive();
    for (i = 0; i < 4452; i++)
    {
        feed_byte(expected[i]);
        if (((i + 1U) % 256U) == 0U)
        {
            drain();
        }
    }
    drain();
    check_flash(4452);

    reset_receive();
    for (i = 0; i < sizeof(expected); i++)
    {
        feed_byte(expected[i]);
        if (((i + 1U) % 300U) == 0U)
        {
            Int_bootloader_Process();
        }
    }
    drain();
    check_flash(sizeof(expected));

    reset_receive();
    for (i = 0; i < 512; i++)
    {
        feed_byte(expected[i]);
    }
    inject_bytes = 1000;
    Int_bootloader_Process();
    drain();
    check_flash(1512);
    assert(uart1_rx_full_len == 1512);

    reset_receive();
    service_irq = 0;
    for (i = 0; i < BOOTLOADER_UART_RING_SIZE; i++)
    {
        feed_byte(expected[i]);
    }
    assert(uart1_dma_wrap_count == 0 && mock_dma.tc_flag == 1);
    assert(bootloader_dma_received_total() == BOOTLOADER_UART_RING_SIZE);
    mock_dma.NDTR = 0;
    assert(bootloader_dma_received_total() == BOOTLOADER_UART_RING_SIZE);
    mock_dma.NDTR = BOOTLOADER_UART_RING_SIZE;
    service_irq = 1;
    service_dma_irq();
    assert(bootloader_dma_received_total() == BOOTLOADER_UART_RING_SIZE);
    drain();
    check_flash(BOOTLOADER_UART_RING_SIZE);
    assert(receive_calls == 1 && mock_dma.ht_enabled == 1);

    reset_receive();
    for (i = 0; i < BOOTLOADER_UART_RING_SIZE + 1000U; i++)
    {
        feed_byte(expected[i]);
    }
    drain();
    assert(uart1_rx_overflow_count == 1000);
    assert(uart1_rx_full_len == BOOTLOADER_UART_RING_SIZE + 1000U);
    assert(memcmp((void *)(uintptr_t)APP_START_ADDR, expected + 1000, BOOTLOADER_UART_RING_SIZE) == 0);

    reset_receive();
    position = 0;
    for (i = 0; i < sizeof(odd_chunks) / sizeof(odd_chunks[0]); i++)
    {
        uint32_t j;
        for (j = 0; j < odd_chunks[i]; j++)
        {
            feed_byte(expected[position++]);
        }
        drain();
    }
    check_flash(12);

    reset_receive();
    feed_byte(expected[0]);
    feed_byte(expected[1]);
    drain();
    feed_byte(expected[2]);
    feed_byte(expected[3]);
    huart1.RxState = HAL_UART_STATE_READY;
    HAL_UART_ErrorCallback(&huart1);
    assert(receive_calls == 1 && uart1_rx_error_count == 1);
    Int_bootloader_Process();
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    Int_bootloader_Process();
    assert(receive_calls == 1 && program_calls == 1);
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR);
    check_flash(2);

    /* UART/DMA 错误必须经过真实状态机立即收尾，不能依赖空闲时间戳。 */
    for (uint32_t idle_confirmed = 0; idle_confirmed < 2; idle_confirmed++)
    {
        reset_image(2);
        boot_state = BOOTLOADER_STATE_REC_DATA;
        feed_byte(expected[0]);
        feed_byte(expected[1]);
        App_bootloader_work();
        assert(uart1_rx_complete_tick == 0);
        if (idle_confirmed != 0)
        {
            tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
            App_bootloader_work();
            assert(uart1_rx_complete_tick != 0);
        }
        HAL_DMA_Abort(&mock_dma);
        HAL_UART_AbortReceive(&huart1);
        HAL_UART_ErrorCallback(&huart1);
        position = program_calls;
        App_bootloader_work();
        assert(boot_state == BOOTLOADER_STATE_CHECK_DATA && reset_calls == 0);
        App_bootloader_work();
        assert(reset_calls == 1 && bootloader_rx_stopped != 0);
        assert(program_calls == position && receive_calls == 1);
        assert(huart1.RxState == HAL_UART_STATE_READY && (mock_stream.CR & DMA_SxCR_EN) == 0);
        assert(success_calls == 0 && jump_calls == 0 && boot_state != BOOTLOADER_STATE_JUMP_TO_APP);
    }

    reset_receive();
    tick = UINT32_MAX - 5U;
    feed_byte(expected[0]);
    feed_byte(expected[1]);
    Int_bootloader_Process();
    assert(program_calls == 0);
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    Int_bootloader_Process();
    check_flash(2);

    /* 新布局预擦除只能涉及 APP；Bootloader 两个扇区保留哨兵数据。 */
    reset_receive();
    memset(flash, 0xa5, 0x10000U);
    Int_early_flash_erase(APP_START_ADDR, 2U);
    assert(erase_calls == 1U);
    for (i = 0; i < 0x10000U; i++)
    {
        if (i < 0x8000U)
        {
            assert(((uint8_t *)flash)[i] == 0xa5U);
        }
        else
        {
            assert(((uint8_t *)flash)[i] == 0xffU);
        }
    }
    Int_early_flash_erase(0x08004000U, 1U);
    Int_early_flash_erase(app_addr_end, 1U);
    Int_early_flash_erase(APP_START_ADDR, 0U);
    Int_early_flash_erase(0x08060000U, 2U);
    assert(erase_calls == 1U);
    Int_early_flash_erase(0x08060000U, 1U);
    assert(erase_calls == 2U);

    /* 最后一个半字可写；包含遗留字节的越界包不得擦除或部分编程。 */
    reset_receive();
    flash_write_offset = APP_SIZE_BYTES - 2U;
    uart1_rx_buf[0] = 0x12U;
    uart1_rx_buf[1] = 0x34U;
    uart1_rx_len = 2U;
    bootloader_process_packet();
    assert(flash_write_offset == APP_SIZE_BYTES && program_calls == 1U);
    assert(*(uint16_t *)(uintptr_t)(app_addr_end - 2U) == 0x3412U);
    uart1_rx_len = 2U;
    bootloader_process_packet();
    assert(program_calls == 1U && erase_calls == 0U && uart1_rx_len == 0U);

    reset_receive();
    flash_write_offset = APP_SIZE_BYTES - 2U;
    uart1_rx_len = 4U;
    bootloader_process_packet();
    assert(program_calls == 0U && erase_calls == 0U && uart1_rx_len == 0U);
    last_btye_flag = 1U;
    last_byte = 0x56U;
    uart1_rx_len = 2U;
    bootloader_process_packet();
    assert(program_calls == 0U && erase_calls == 0U && uart1_rx_len == 0U);
    reset_receive();
    flash_write_offset = APP_SIZE_BYTES - 2U;
    last_btye_flag = 1U;
    last_byte = 0x56U;
    uart1_rx_buf[0] = 0x78U;
    uart1_rx_len = 1U;
    bootloader_process_packet();
    assert(flash_write_offset == APP_SIZE_BYTES && program_calls == 1U);
    assert(*(uint16_t *)(uintptr_t)(app_addr_end - 2U) == 0x7856U);
    flash_write_offset = UINT32_MAX;
    uart1_rx_len = 1U;
    bootloader_process_packet();
    assert(program_calls == 1U && erase_calls == 0U && uart1_rx_len == 0U);

    /* 擦除范围按声明长度计算，覆盖全部 APP 边界并保留 Bootloader。 */
    const uint32_t lengths[] = {1U, 0x4000U, 0x4001U, 0x8000U, 0x8001U, 0x18000U, 0x18001U, APP_SIZE_BYTES};
    const uint32_t last_sectors[] = {2U, 2U, 3U, 3U, 4U, 4U, 5U, 7U};
    for (i = 0; i < sizeof(lengths) / sizeof(lengths[0]); i++)
    {
        reset_receive();
        HAL_UART_AbortReceive(&huart1);
        memset(flash, 0xa5, 0x80000U);
        assert(bootloader_prepare_image(lengths[i]) == 0U);
        assert(((uint8_t *)flash)[0] == 0xa5U && ((uint8_t *)flash)[0x7fffU] == 0xa5U);
        uint32_t end = 0x80000U;
        if (last_sectors[i] < 4U)
        {
            end = (last_sectors[i] + 1U) * 0x4000U;
        }
        else
        {
            end = 0x20000U + (last_sectors[i] - 4U) * 0x20000U;
        }
        assert(((uint8_t *)flash)[0x8000U] == 0xffU && ((uint8_t *)flash)[end - 1U] == 0xffU);
        if (end < 0x80000U)
        {
            assert(((uint8_t *)flash)[end] == 0xa5U);
        }
    }
    assert(bootloader_prepare_image(0U) == 1U);
    assert(bootloader_prepare_image(APP_SIZE_BYTES + 1U) == 1U);

    /* 复现板上 4484 字节收尾：停止产生的 TC 无论挂起还是回调，都不能多算 8192 字节。 */
    for (uint32_t irq_mode = 0; irq_mode < 2; irq_mode++)
    {
        reset_image(4484);
        for (i = 0; i < 4484; i++)
        {
            feed_byte(expected[i]);
            if ((i + 1) % 256 == 0)
            {
                drain();
            }
        }
        drain();
        service_irq = irq_mode;
        boot_state = BOOTLOADER_STATE_CHECK_DATA;
        App_bootloader_work();
        assert(reset_calls == 0 && boot_state == BOOTLOADER_STATE_JUMP_TO_APP);
        assert(uart1_rx_full_len == 4484 && bootloader_rx_end_total == 4484);
        check_flash(4484);
    }

    /* 暂停请求到 DMA 完全停止之间的最后一字节也要保留，覆盖普通位置与整圈边界。 */
    const uint32_t stop_lengths[] = {4484, BOOTLOADER_UART_RING_SIZE};
    for (i = 0; i < sizeof(stop_lengths) / sizeof(stop_lengths[0]); i++)
    {
        uint32_t length = stop_lengths[i];
        reset_image(length);
        for (uint32_t j = 0; j < length - 1; j++)
        {
            feed_byte(expected[j]);
            if ((j + 1) % 256 == 0)
            {
                drain();
            }
        }
        abort_tail_byte = expected[length - 1];
        abort_tail_pending = 1;
        bootloader_result_enum stop_result;
        do
        {
            stop_result = App_bootloader_check_data();
        }
        while (stop_result == BOOTLOADER_RESULT_PENDING);
        assert(stop_result == BOOTLOADER_RESULT_OK && uart1_rx_full_len == length);
        check_flash(length);
    }

    /* 旧扇区中 FF 空洞不能导致后续包擦掉前面的新数据。 */
    reset_receive();
    HAL_UART_AbortReceive(&huart1);
    memset((void *)(uintptr_t)0x08010200U, 0x00, 512U);
    assert(bootloader_prepare_image(0x8400U) == 0U);
    assert(bootloader_start_dma() == 0U);
    position = erase_calls;
    for (i = 0; i < 0x8400U; i++)
    {
        feed_byte(expected[i]);
        if ((i + 1U) % 256U == 0U)
        {
            drain();
        }
    }
    drain();
    check_flash(0x8400U);
    assert(erase_calls == position);
    assert(bootloader_finish_image() == BOOTLOADER_RESULT_OK);
    assert(memcmp((void *)(uintptr_t)0x08010000U, expected + 0x8000U, 1024U) == 0);

    const uint32_t odd_lengths[] = {1U, 3U, 511U, 513U, 4097U, APP_SIZE_BYTES - 1U, APP_SIZE_BYTES};
    for (i = 0; i < sizeof(odd_lengths) / sizeof(odd_lengths[0]); i++)
    {
        uint32_t length = odd_lengths[i];
        reset_image(length);
        for (uint32_t j = 0; j < length; j++)
        {
            feed_byte(expected[j]);
            if ((j + 1U) % 256U == 0U)
            {
                drain();
            }
        }
        boot_state = BOOTLOADER_STATE_CHECK_DATA;
        bootloader_result_enum result;
        do
        {
            result = App_bootloader_check_data();
        }
        while (result == BOOTLOADER_RESULT_PENDING);
        assert(result == BOOTLOADER_RESULT_OK && reset_calls == 0U);
        assert(boot_state == BOOTLOADER_STATE_JUMP_TO_APP);
        assert(flash_write_offset == length && last_btye_flag == 0U);
        assert(memcmp((void *)(uintptr_t)APP_START_ADDR, expected, length) == 0);
        if ((length & 1U) != 0U)
        {
            assert(*(uint8_t *)(uintptr_t)(APP_START_ADDR + length) == 0xffU);
        }
    }

    /* 没有数据时不收尾；空闲确认后 1999 ms 继续等待，新数据重新计时。 */
    reset_image(3);
    boot_state = BOOTLOADER_STATE_REC_DATA;
    tick += 3000;
    App_bootloader_work();
    assert(uart1_rx_complete_tick == 0 && boot_state == BOOTLOADER_STATE_REC_DATA);
    feed_byte(expected[0]);
    feed_byte(expected[1]);
    App_bootloader_work();
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    App_bootloader_work();
    assert(uart1_rx_complete_tick != 0);
    tick += 1999;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_REC_DATA);
    feed_byte(expected[2]);
    App_bootloader_work();
    assert(uart1_rx_complete_tick == 0 && boot_state == BOOTLOADER_STATE_REC_DATA);
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    App_bootloader_work();
    tick += 1999;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_REC_DATA);
    tick++;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_CHECK_DATA);
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_JUMP_TO_APP && reset_calls == 0);
    check_flash(3);

    /* HAL tick 回绕后，仍按无符号时间差在满 2000 ms 时收尾。 */
    reset_image(1);
    boot_state = BOOTLOADER_STATE_REC_DATA;
    tick = 0xFFFFFF00;
    feed_byte(expected[0]);
    App_bootloader_work();
    tick += BOOTLOADER_UART_RX_TIMEOUT_MS;
    App_bootloader_work();
    tick += 1999;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_REC_DATA);
    tick++;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_CHECK_DATA);
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_JUMP_TO_APP && reset_calls == 0);
    check_flash(1);

    /* 收尾时尾部尚有多包：CHECK_DATA 必须等待，不能误判为失败或直接跳转。 */
    reset_image(4097U);
    for (i = 0; i < 4097U; i++)
    {
        feed_byte(expected[i]);
    }
    boot_state = BOOTLOADER_STATE_CHECK_DATA;
    App_bootloader_work();
    assert(boot_state == BOOTLOADER_STATE_CHECK_DATA && reset_calls == 0U);
    while (boot_state == BOOTLOADER_STATE_CHECK_DATA)
    {
        App_bootloader_work();
    }
    assert(boot_state == BOOTLOADER_STATE_JUMP_TO_APP && reset_calls == 0U);
    check_flash(4097U);

    /* TC IRQ 尚未执行时停止 DMA，也必须保留最后一圈的接收计数。 */
    reset_image(BOOTLOADER_UART_RING_SIZE);
    service_irq = 0U;
    for (i = 0; i < BOOTLOADER_UART_RING_SIZE; i++)
    {
        feed_byte(expected[i]);
    }
    assert(mock_dma.tc_flag != 0U && uart1_dma_wrap_count == 0U);
    bootloader_result_enum result;
    do
    {
        result = App_bootloader_check_data();
    }
    while (result == BOOTLOADER_RESULT_PENDING);
    assert(result == BOOTLOADER_RESULT_OK && uart1_rx_full_len == BOOTLOADER_UART_RING_SIZE);
    check_flash(BOOTLOADER_UART_RING_SIZE);

    /* 少收、多收、DMA 丢数、UART 错误和 Flash 失败均不得报告成功。 */
    reset_image(3U);
    feed_byte(1U);
    feed_byte(2U);
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR);
    reset_image(2U);
    feed_byte(1U);
    feed_byte(2U);
    feed_byte(3U);
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && program_calls == 0U);
    reset_image(BOOTLOADER_UART_RING_SIZE + 1U);
    for (i = 0; i < BOOTLOADER_UART_RING_SIZE + 1U; i++)
    {
        feed_byte(expected[i]);
    }
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && uart1_rx_overflow_count != 0U);
    reset_image(2U);
    feed_byte(1U);
    feed_byte(2U);
    uart1_rx_error_count = 1U;
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR);
    reset_image(4U);
    fail_program_call = 2U;
    for (i = 0; i < 4U; i++)
    {
        feed_byte(expected[i]);
    }
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && flash_write_offset == 2U);
    reset_image(1U);
    feed_byte(0x33U);
    fail_program_call = 1U;
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && last_btye_flag == 1U);
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && program_calls == 1U);
    reset_image(2U);
    feed_byte(1U);
    feed_byte(2U);
    fail_lock = 1U;
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR);
    reset_image(2U);
    feed_byte(1U);
    feed_byte(2U);
    fail_unlock = 1U;
    assert(App_bootloader_check_data() == BOOTLOADER_RESULT_ERROR && program_calls == 0U);

    reset_receive();
    HAL_UART_AbortReceive(&huart1);
    boot_state = BOOTLOADER_STATE_RUN;
    fail_erase = 1U;
    position = receive_calls;
    App_bootloader_work();
    assert(reset_calls == 1U && ready_calls == 0U && receive_calls == position);
    reset_receive();
    HAL_UART_AbortReceive(&huart1);
    boot_state = BOOTLOADER_STATE_RUN;
    fail_receive = 1U;
    App_bootloader_work();
    assert(reset_calls == 1U && ready_calls == 0U);
    reset_receive();
    HAL_UART_AbortReceive(&huart1);
    boot_state = BOOTLOADER_STATE_RUN;
    App_bootloader_work();
    assert(reset_calls == 0U && ready_calls == 1U && boot_state == BOOTLOADER_STATE_REC_DATA);

    assert(VirtualFree(flash, 0, MEM_RELEASE) != 0);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="uart_ring_test_") as temporary:
    path = Path(temporary)
    (path / "usart.h").write_text(mock_header, encoding="utf-8")
    (path / "stm32f407xx.h").write_text("", encoding="utf-8")
    (path / "test.c").write_text(harness, encoding="utf-8")
    executable = path / "test.exe"
    subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-int-to-pointer-cast", "-I", str(path), "-I", str(root / "interface"), str(path / "test.c"), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("PASS: circular DMA HT/TC, tail timeout, wraparound, overrun, stitching, UART failure without restart, immediate state-machine reset before/after idle; full-image erase, odd tail/drain/commit, exact capacity, failures and Bootloader preservation")
