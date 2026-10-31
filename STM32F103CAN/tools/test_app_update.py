"""Exercise the real Flash-to-CAN application and CAN driver on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef UPDATE_TEST_HAL_H
#define UPDATE_TEST_HAL_H
#include <stdint.h>
typedef struct
{
    uint32_t StdId, ExtId, IDE, RTR, DLC, TransmitGlobalTime;
} CAN_RxHeaderTypeDef;
typedef CAN_RxHeaderTypeDef CAN_TxHeaderTypeDef;
typedef struct
{
    uint32_t FilterBank, FilterMode, FilterScale, FilterIdHigh, FilterIdLow;
    uint32_t FilterMaskIdHigh, FilterMaskIdLow, FilterFIFOAssignment, FilterActivation;
} CAN_FilterTypeDef;
typedef struct
{
    uint32_t unused;
} CAN_HandleTypeDef;
extern CAN_HandleTypeDef hcan;
#define CAN_ID_STD 0
#define CAN_ID_EXT 4
#define CAN_RTR_DATA 0
#define CAN_RTR_REMOTE 2
#define CAN_FILTERMODE_IDMASK 0
#define CAN_FILTERSCALE_32BIT 1
#define CAN_RX_FIFO0 0
#define CAN_TX_MAILBOX0 1
#define CAN_TX_MAILBOX1 2
#define CAN_TX_MAILBOX2 4
#define CAN_FLAG_TXOK0 0x501
#define CAN_FLAG_TXOK1 0x509
#define CAN_FLAG_TXOK2 0x511
#define CAN_FLAG_RQCP0 0x500
#define CAN_FLAG_RQCP1 0x508
#define CAN_FLAG_RQCP2 0x510
#define HAL_OK 0
#define RESET 0
#define ENABLE 1
#define __HAL_CAN_GET_FLAG(handle, flag) mock_get_flag(flag)
#define __HAL_CAN_CLEAR_FLAG(handle, flag) mock_clear_flag(flag)
uint32_t mock_get_flag(uint32_t flag);
void mock_clear_flag(uint32_t flag);
uint32_t HAL_GetTick(void);
void HAL_Delay(uint32_t milliseconds);
uint32_t HAL_CAN_ConfigFilter(CAN_HandleTypeDef *handle, CAN_FilterTypeDef *filter);
uint32_t HAL_CAN_Start(CAN_HandleTypeDef *handle);
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *handle);
uint32_t HAL_CAN_AddTxMessage(CAN_HandleTypeDef *handle, CAN_TxHeaderTypeDef *header, const uint8_t *data, uint32_t *mailbox);
uint32_t HAL_CAN_IsTxMessagePending(const CAN_HandleTypeDef *handle, uint32_t mailbox);
uint32_t HAL_CAN_AbortTxRequest(CAN_HandleTypeDef *handle, uint32_t mailbox);
uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *handle, uint32_t fifo);
uint32_t HAL_CAN_GetRxMessage(CAN_HandleTypeDef *handle, uint32_t fifo, CAN_RxHeaderTypeDef *header, uint8_t *data);
void Error_Handler(void);
#endif
'''
harness = r'''
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif
#include "app_update.h"
#include "app_bootloader.h"
#include "int_can.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

CAN_HandleTypeDef hcan;
app_flash_state_enum app_flash_state;
uint32_t app_size_bytes;
static CAN_RxHeaderTypeDef incoming;
static uint8_t payload[8];
static uint8_t receive_result = 1;
static uint32_t init_count, bootloader_calls, tick, tick_step = 1;
static uint32_t mailbox_index, mailbox_free = 3, add_error, tx_error, tx_stuck;
static uint32_t pending, abort_count, tx_ok, sent_bytes, sent_frames, submitted_frames;
static uint32_t delay_calls, delayed_ms;
static uint8_t frame[8], frame_length;
static uint8_t captured[APP_FLASH_CAPACITY_BYTES];
static uint8_t *flash_data = (uint8_t *)(uintptr_t)APP_FLASH_START_ADDR;
static char output[4096];
static size_t output_length;

uint32_t HAL_GetTick(void)
{
    uint32_t value = tick;
    tick += tick_step;
    return value;
}
void Error_Handler(void)
{
    assert(0);
}
void HAL_Delay(uint32_t milliseconds)
{
    assert(milliseconds == APP_UPDATE_FRAME_DELAY_MS);
    assert(sent_frames == delay_calls + 1);
    delay_calls++;
    delayed_ms += milliseconds;
    tick += milliseconds;
}
uint32_t HAL_CAN_ConfigFilter(CAN_HandleTypeDef *handle, CAN_FilterTypeDef *filter)
{
    assert(handle == &hcan && app_flash_state == APP_FLASH_DONE);
    assert(filter->FilterMaskIdHigh == 0xFFE0 && filter->FilterMaskIdLow == 6);
    return HAL_OK;
}
uint32_t HAL_CAN_Start(CAN_HandleTypeDef *handle)
{
    assert(handle == &hcan);
    init_count++;
    return HAL_OK;
}
uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *handle, uint32_t fifo)
{
    assert(handle == &hcan && fifo == CAN_RX_FIFO0);
    return receive_result == 0;
}
uint32_t HAL_CAN_GetRxMessage(CAN_HandleTypeDef *handle, uint32_t fifo, CAN_RxHeaderTypeDef *header, uint8_t *data)
{
    assert(handle == &hcan && fifo == CAN_RX_FIFO0);
    *header = incoming;
    memcpy(data, payload, sizeof(payload));
    receive_result = 1;
    return HAL_OK;
}
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *handle)
{
    assert(handle == &hcan);
    if (sent_bytes >= 8)
    {
        return mailbox_free;
    }
    return 3;
}
uint32_t HAL_CAN_AddTxMessage(CAN_HandleTypeDef *handle, CAN_TxHeaderTypeDef *header, const uint8_t *data, uint32_t *mailbox)
{
    assert(handle == &hcan && pending == 0);
    assert(header->StdId == 1 && header->IDE == CAN_ID_STD && header->RTR == CAN_RTR_DATA);
    assert(header->DLC >= 1 && header->DLC <= 8);
    assert(delay_calls == sent_frames);
    if ((add_error != 0) && (sent_bytes >= 8))
    {
        return 1;
    }
    *mailbox = 1 << mailbox_index;
    frame_length = (uint8_t)header->DLC;
    memcpy(frame, data, 8);
    pending = 2;
    tx_ok = 0;
    submitted_frames++;
    return HAL_OK;
}
uint32_t HAL_CAN_IsTxMessagePending(const CAN_HandleTypeDef *handle, uint32_t mailbox)
{
    assert(handle == &hcan && mailbox == (uint32_t)(1 << mailbox_index));
    assert(pending != 0);
    if ((tx_stuck != 0) && (sent_bytes >= 8))
    {
        return 1;
    }
    pending--;
    if (pending != 0)
    {
        return 1;
    }
    if ((tx_error == 0) || (sent_bytes < 8))
    {
        assert(sent_bytes + frame_length <= sizeof(captured));
        memcpy(captured + sent_bytes, frame, frame_length);
        sent_bytes += frame_length;
        sent_frames++;
        tx_ok = 1;
    }
    return 0;
}
uint32_t HAL_CAN_AbortTxRequest(CAN_HandleTypeDef *handle, uint32_t mailbox)
{
    assert(handle == &hcan && mailbox == (uint32_t)(1 << mailbox_index));
    abort_count++;
    pending = 0;
    return HAL_OK;
}
uint32_t mock_get_flag(uint32_t flag)
{
    assert(flag == CAN_FLAG_TXOK0 + mailbox_index * 8);
    return tx_ok;
}
void mock_clear_flag(uint32_t flag)
{
    assert(flag == CAN_FLAG_RQCP0 + mailbox_index * 8);
    tx_ok = 0;
}
void App_bootloader_init(void)
{
    app_flash_state = APP_FLASH_WAIT;
    bootloader_calls = 0;
}
void App_bootloader_update(void)
{
    assert(init_count == 0);
    bootloader_calls++;
    if (bootloader_calls < 4)
    {
        app_flash_state = APP_FLASH_RECEIVING;
    }
    else
    {
        app_flash_state = APP_FLASH_DONE;
        app_size_bytes = 9;
    }
}
int test_printf(const char *format, ...)
{
    va_list arguments;
    int length;
    va_start(arguments, format);
    length = vsnprintf(output + output_length, sizeof(output) - output_length, format, arguments);
    va_end(arguments);
    assert(length >= 0 && (size_t)length < sizeof(output) - output_length);
    output_length += (size_t)length;
    return length;
}
static void run_startup(void)
{
STARTUP_CODE
}
static void command(void)
{
    incoming.StdId = 0;
    incoming.IDE = CAN_ID_STD;
    incoming.RTR = CAN_RTR_DATA;
    incoming.DLC = 3;
    memcpy(payload, "hyf", 3);
    receive_result = 0;
    app_update_work();
}
static void begin(uint32_t length)
{
    uint32_t index;
    app_flash_state = APP_FLASH_DONE;
    app_size_bytes = length;
    sent_bytes = 0;
    sent_frames = 0;
    submitted_frames = 0;
    delay_calls = 0;
    delayed_ms = 0;
    mailbox_free = 3;
    add_error = 0;
    tx_error = 0;
    tx_stuck = 0;
    abort_count = 0;
    pending = 0;
    tick_step = 1;
    receive_result = 1;
    memset(captured, 0xA5, sizeof(captured));
    for (index = 0; index < APP_FLASH_CAPACITY_BYTES; index++)
    {
        flash_data[index] = (uint8_t)(index * 17 + index / 256);
    }
    app_update_init();
    command();
}
int main(void)
{
    const char *invalid[] = {"", "h", "hy", "HYF", "abc", "hyfX"};
    const uint32_t lengths[] = {1, 7, 8, 9, 16, 255, 256, 257, 511, 512, 513, 4480, APP_FLASH_CAPACITY_BYTES};
    uint32_t index;
    size_t previous_length;
    void *mapped;
#ifdef _WIN32
    mapped = VirtualAlloc((void *)0x08000000, 65536, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
#else
    mapped = mmap((void *)0x08000000, 65536, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
#endif
    assert(mapped == (void *)0x08000000);
    run_startup();
    assert(bootloader_calls == 0 && init_count == 1 && app_update_state == APP_UPDATE_WAIT);
    assert(app_flash_state == APP_FLASH_DONE && app_size_bytes == APP_UPDATE_STORED_SIZE_BYTES);
    assert(strstr(output, "CAN init OK") != NULL);
    previous_length = output_length;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT && output_length == previous_length);
    for (index = 0; index < sizeof(invalid) / sizeof(invalid[0]); index++)
    {
        incoming.DLC = (uint32_t)strlen(invalid[index]);
        memset(payload, 0, sizeof(payload));
        memcpy(payload, invalid[index], incoming.DLC);
        receive_result = 0;
        app_update_work();
        assert(app_update_state == APP_UPDATE_WAIT);
    }
    command();
    app_update_init();
    incoming.DLC = 8;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.DLC = 3;
    incoming.StdId = 1;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.StdId = 0;
    incoming.IDE = CAN_ID_EXT;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.IDE = CAN_ID_STD;
    incoming.RTR = CAN_RTR_REMOTE;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);

    begin(0);
    assert(app_update_state == APP_UPDATE_WAIT && submitted_frames == 0);
    begin(APP_FLASH_CAPACITY_BYTES + 1);
    assert(app_update_state == APP_UPDATE_WAIT && submitted_frames == 0);
    begin(8);
    app_update_state = APP_UPDATE_WAIT;
    app_flash_state = APP_FLASH_RECEIVING;
    command();
    assert(app_update_state == APP_UPDATE_WAIT && submitted_frames == 0);

    for (index = 0; index < sizeof(lengths) / sizeof(lengths[0]); index++)
    {
        begin(lengths[index]);
        mailbox_index = index % 3;
        assert(app_update_state == APP_UPDATE_SEND);
        app_update_work();
        assert(app_update_state == APP_UPDATE_DONE);
        assert(sent_bytes == lengths[index] && sent_frames == (lengths[index] + 7) / 8);
        assert(memcmp(captured, flash_data, sent_bytes) == 0);
        assert(frame_length == lengths[index] - (sent_frames - 1) * 8);
        assert(delay_calls == sent_frames);
        assert(delayed_ms == delay_calls * APP_UPDATE_FRAME_DELAY_MS);
        if (lengths[index] == 4480)
        {
            assert(sent_frames == 560 && frame_length == 8 && delay_calls == 560 && delayed_ms == 5600);
        }
        assert(strstr(output, "CAN send done,len:") != NULL);
        previous_length = output_length;
        command();
        app_update_send();
        assert(app_update_state == APP_UPDATE_DONE && sent_bytes == lengths[index] && output_length == previous_length);
    }

    begin(257);
    tick = UINT32_MAX - 130;
    app_update_work();
    assert(app_update_state == APP_UPDATE_DONE && sent_bytes == 257 && delay_calls == 33);
    for (index = 0; index < 4; index++)
    {
        begin(16);
        switch (index)
        {
            case 0:
                mailbox_free = 0;
                break;
            case 1:
                add_error = 1;
                break;
            case 2:
                tx_error = 1;
                break;
            case 3:
                tx_stuck = 1;
                tick = UINT32_MAX - 40;
                break;
        }
        app_update_work();
        assert(app_update_state == APP_UPDATE_ERROR && sent_bytes == 8);
        assert(strstr(output, "CAN send error, offset:8") != NULL);
        assert(abort_count == (index == 3));
        previous_length = output_length;
        app_update_work();
        assert(output_length == previous_length && sent_bytes == 8);
    }
    puts("PASS: stored BIN startup without UART receive, strict hyf, Flash validity, byte-exact ID 1 frames, short tails,");
    puts("      full 32 KiB, all mailboxes, 10 ms per-frame delays, tick wrap, DONE and send errors");
    return 0;
}
'''

main_source = (root / "Core/Src/main.c").read_text(encoding="utf-8")
startup_code = main_source.split("/* USER CODE BEGIN 2 */", 1)[1].split("/* USER CODE END 2 */", 1)[0]
harness = harness.replace("STARTUP_CODE", startup_code)
compiler = shutil.which("gcc") or r"C:\msys64\mingw64\bin\gcc.exe"
with tempfile.TemporaryDirectory(prefix="f103-app-update-") as folder:
    temporary = Path(folder)
    (temporary / "can.h").write_text(mock_header, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_app_update.exe"
    subprocess.run([
        compiler, "-std=c99", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror", "-Dprintf=test_printf",
        f"-I{temporary}", f"-I{root / 'appliaction'}", f"-I{root / 'interface'}",
        str(root / "appliaction/app_update.c"), str(root / "interface/int_can.c"), str(temporary / "test.c"),
        "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
