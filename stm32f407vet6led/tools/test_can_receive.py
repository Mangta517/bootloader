"""Compile the real CAN modules and check UART commands, receive byte counts and the FIFO driver."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef CAN_TEST_HAL_H
#define CAN_TEST_HAL_H
#include <stdint.h>
typedef enum
{
    HAL_OK,
    HAL_ERROR
} HAL_StatusTypeDef;
typedef struct
{
    uint32_t unused;
} CAN_HandleTypeDef;
typedef struct
{
    uint32_t unused;
} UART_HandleTypeDef;
typedef struct
{
    uint32_t StdId, ExtId, IDE, RTR, DLC, Timestamp, FilterMatchIndex;
} CAN_RxHeaderTypeDef;
typedef struct
{
    uint32_t StdId, ExtId, IDE, RTR, DLC, TransmitGlobalTime;
} CAN_TxHeaderTypeDef;
typedef struct
{
    uint32_t FilterIdHigh, FilterIdLow, FilterMaskIdHigh, FilterMaskIdLow;
    uint32_t FilterFIFOAssignment, FilterBank, FilterMode, FilterScale;
    uint32_t FilterActivation, SlaveStartFilterBank;
} CAN_FilterTypeDef;
#define CAN_ID_STD 0
#define CAN_ID_EXT 4
#define CAN_RTR_DATA 0
#define CAN_RTR_REMOTE 2
#define CAN_FILTERMODE_IDMASK 0
#define CAN_FILTERSCALE_32BIT 1
#define CAN_RX_FIFO0 0
#define ENABLE 1
extern CAN_HandleTypeDef hcan1;
extern UART_HandleTypeDef huart1;
#define led0_GPIO_Port ((void *)1)
#define led1_GPIO_Port ((void *)1)
#define led0_Pin 1
#define led1_Pin 2
uint32_t HAL_GetTick(void);
void HAL_GPIO_TogglePin(void *, uint16_t);
HAL_StatusTypeDef HAL_UARTEx_ReceiveToIdle_IT(UART_HandleTypeDef *, uint8_t *, uint16_t);
HAL_StatusTypeDef HAL_UART_AbortReceive(UART_HandleTypeDef *);
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *, uint16_t);
void HAL_UART_ErrorCallback(UART_HandleTypeDef *);
void Error_Handler(void);
HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef *, CAN_FilterTypeDef *);
HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef *);
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *);
HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef *, CAN_TxHeaderTypeDef *, uint8_t *, uint32_t *);
uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *, uint32_t);
HAL_StatusTypeDef HAL_CAN_GetRxMessage(CAN_HandleTypeDef *, uint32_t, CAN_RxHeaderTypeDef *, uint8_t *);
HAL_StatusTypeDef HAL_UART_Transmit(UART_HandleTypeDef *, const uint8_t *, uint16_t, uint32_t);
#endif
'''
harness = r'''
#include "int_can.h"
#include "app_update.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

CAN_HandleTypeDef hcan1;
UART_HandleTypeDef huart1;
static CAN_MSG_RECEIVE incoming;
static uint8_t pending;
static uint8_t receive_error;
static uint32_t start_calls;
static uint32_t transmit_calls;
static HAL_StatusTypeDef filter_result = HAL_OK;
static HAL_StatusTypeDef start_result = HAL_OK;
static jmp_buf error_jump;
static uint8_t *uart_receive_buffer;
static uint32_t idle_enabled;
static uint16_t uart_received;
static uint16_t uart_capacity;
static uint32_t tick_ms;
static uint32_t led_toggles;
static char uart_output[128];
static uint32_t uart_transmit_calls;

HAL_StatusTypeDef HAL_UARTEx_ReceiveToIdle_IT(UART_HandleTypeDef *handle, uint8_t *data, uint16_t length)
{
    assert(handle == &huart1 && length == 31);
    uart_receive_buffer = data;
    uart_capacity = length;
    uart_received = 0;
    idle_enabled++;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_UART_AbortReceive(UART_HandleTypeDef *handle)
{
    assert(handle == &huart1);
    uart_received = 0;
    return HAL_OK;
}

uint32_t HAL_GetTick(void)
{
    return tick_ms;
}

void HAL_GPIO_TogglePin(void *port, uint16_t pin)
{
    assert(port == led0_GPIO_Port);
    assert(pin == led0_Pin || pin == led1_Pin);
    assert(app_update_state == APP_UPDATE_IDLE);
    led_toggles++;
}

static void send_uart(const char *text)
{
    while (*text != 0)
    {
        uart_receive_buffer[uart_received++] = (uint8_t)*text;
        text++;
        if (uart_received == uart_capacity)
        {
            HAL_UARTEx_RxEventCallback(&huart1, uart_received);
        }
    }
}

static void uart_idle(void)
{
    if (uart_received > 0)
    {
        HAL_UARTEx_RxEventCallback(&huart1, uart_received);
    }
}

void Error_Handler(void)
{
    longjmp(error_jump, 1);
}

HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef *handle, CAN_FilterTypeDef *filter)
{
    assert(handle == &hcan1);
    assert(filter->FilterBank == 0 && filter->SlaveStartFilterBank == 14);
    assert(filter->FilterMode == CAN_FILTERMODE_IDMASK);
    assert(filter->FilterScale == CAN_FILTERSCALE_32BIT);
    assert(filter->FilterFIFOAssignment == CAN_RX_FIFO0 && filter->FilterActivation == ENABLE);
    assert((filter->FilterIdHigh | filter->FilterIdLow | filter->FilterMaskIdHigh | filter->FilterMaskIdLow) == 0);
    return filter_result;
}

HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef *handle)
{
    assert(handle == &hcan1);
    start_calls++;
    return start_result;
}

uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *handle)
{
    assert(handle == &hcan1);
    return 3;
}

HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef *handle, CAN_TxHeaderTypeDef *header,
                                    uint8_t *data, uint32_t *mailbox)
{
    assert(handle == &hcan1);
    assert(header->StdId == 0 && header->IDE == CAN_ID_STD);
    assert(header->RTR == CAN_RTR_DATA && header->DLC == 3);
    assert(memcmp(data, "hyf", 3) == 0);
    *mailbox = 1;
    transmit_calls++;
    return HAL_OK;
}

uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *handle, uint32_t fifo)
{
    assert(handle == &hcan1 && fifo == CAN_RX_FIFO0);
    return pending;
}

HAL_StatusTypeDef HAL_CAN_GetRxMessage(CAN_HandleTypeDef *handle, uint32_t fifo,
                                    CAN_RxHeaderTypeDef *header, uint8_t *data)
{
    assert(handle == &hcan1 && fifo == CAN_RX_FIFO0 && pending > 0);
    if (receive_error != 0)
    {
        return HAL_ERROR;
    }
    *header = incoming.RxHeader;
    memcpy(data, incoming.data, sizeof(incoming.data));
    pending--;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_UART_Transmit(UART_HandleTypeDef *handle, const uint8_t *data, uint16_t length, uint32_t timeout)
{
    assert(handle == &huart1 && timeout == 100 && length < sizeof(uart_output));
    memcpy(uart_output, data, length);
    uart_output[length] = 0;
    uart_transmit_calls++;
    return HAL_OK;
}

int main(void)
{
    CAN_MSG_RECEIVE message = {0};
    uint8_t count;
    uint8_t data[8] = {0x00, 0x55, 0xAA, 0xFF, 0, 0, 0, 0};

    int_can_init();
    assert(start_calls == 1);
    assert(transmit_calls == 0);
    app_update_init();
    assert(idle_enabled == 1 && uart_receive_buffer != NULL);
    assert(app_update_state == APP_UPDATE_IDLE && app_update_size_bytes == 0);
    app_update_work();
    assert(transmit_calls == 0 && led_toggles == 0);
    tick_ms = 500;
    app_update_work();
    assert(led_toggles == 2);

    const char *invalid_commands[] = {
        "cm", "CMD", "xcmd", "cmdx", "cmd:4480", "start:4480",
        "cmdxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    };
    for (uint32_t i = 0; i < sizeof(invalid_commands) / sizeof(invalid_commands[0]); i++)
    {
        send_uart(invalid_commands[i]);
        uart_idle();
        app_update_work();
        assert(app_update_state == APP_UPDATE_IDLE && transmit_calls == 0);
        assert(app_update_size_bytes == 0);
    }

    send_uart("cmd");
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && transmit_calls == 0);
    uart_idle();
    assert(app_update_state == APP_UPDATE_SEND_COMMAND && app_update_size_bytes == 0);
    assert(transmit_calls == 0);
    tick_ms = 1000;
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && transmit_calls == 1 && led_toggles == 2);
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && transmit_calls == 1);
    app_update_state = APP_UPDATE_VERIFY;
    app_update_work();
    assert(app_update_state == APP_UPDATE_VERIFY && led_toggles == 2);
    app_update_state = APP_UPDATE_DONE;
    app_update_work();
    assert(app_update_state == APP_UPDATE_DONE && led_toggles == 2);

    app_update_state = APP_UPDATE_IDLE;
    send_uart("cmd\r\n");
    uart_idle();
    assert(app_update_state == APP_UPDATE_SEND_COMMAND && app_update_size_bytes == 0);
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && transmit_calls == 2);

    app_update_state = APP_UPDATE_IDLE;
    send_uart("cmd\n");
    uart_idle();
    assert(app_update_state == APP_UPDATE_SEND_COMMAND && app_update_size_bytes == 0);
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && transmit_calls == 3);

    app_update_state = APP_UPDATE_IDLE;
    send_uart("cmd");
    HAL_UART_ErrorCallback(&huart1);
    uart_idle();
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && transmit_calls == 3);

    int_can_receive_msg(&message, &count);
    assert(count == 0);

    incoming.RxHeader.StdId = 0x123;
    incoming.RxHeader.IDE = CAN_ID_STD;
    incoming.RxHeader.RTR = CAN_RTR_DATA;
    incoming.RxHeader.DLC = 8;
    memcpy(incoming.data, data, sizeof(data));
    pending = 1;
    int_can_receive_msg(&message, &count);
    assert(count == 1 && pending == 0);
    assert(message.RxHeader.StdId == 0x123 && message.RxHeader.DLC == 8);
    assert(memcmp(message.data, data, sizeof(data)) == 0);

    pending = 1;
    receive_error = 1;
    int_can_receive_msg(&message, &count);
    assert(count == 0 && pending == 1);
    receive_error = 0;
    int_can_receive_msg(&message, &count);
    assert(count == 1 && pending == 0);

    /* 接收期间不逐帧打印，按 DLC 累计 ID 1 标准数据帧。 */
    pending = 1;
    app_update_work();
    assert(pending == 1 && uart_transmit_calls == 0);
    send_uart("cmd");
    uart_idle();
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && app_update_size_bytes == 0);
    assert(transmit_calls == 4);
    app_update_work();
    assert(pending == 0 && uart_transmit_calls == 0 && app_update_size_bytes == 0);

    incoming.RxHeader.StdId = 1;
    pending = 3;
    for (uint32_t i = 0; i < 3; i++)
    {
        app_update_work();
    }
    assert(pending == 0 && app_update_size_bytes == 24 && uart_transmit_calls == 0);
    tick_ms += 10;
    pending = 1;
    receive_error = 1;
    app_update_work();
    assert(pending == 1 && app_update_size_bytes == 24 && uart_transmit_calls == 0);
    receive_error = 0;

    incoming.RxHeader.DLC = 3;
    app_update_work();
    assert(pending == 0 && app_update_size_bytes == 27 && uart_transmit_calls == 0);
    /* 无关帧、远程帧和非法 DLC 不计数，也不延长接收等待。 */
    tick_ms += 999;
    incoming.RxHeader.IDE = CAN_ID_EXT;
    incoming.RxHeader.ExtId = 1;
    pending = 1;
    app_update_work();
    incoming.RxHeader.IDE = CAN_ID_STD;
    incoming.RxHeader.RTR = CAN_RTR_REMOTE;
    pending = 1;
    app_update_work();
    incoming.RxHeader.RTR = CAN_RTR_DATA;
    incoming.RxHeader.DLC = 0;
    pending = 1;
    app_update_work();
    incoming.RxHeader.DLC = 9;
    pending = 1;
    app_update_work();
    assert(app_update_size_bytes == 27 && uart_transmit_calls == 0);
    assert(app_update_state == APP_UPDATE_RECEIVE);
    tick_ms++;
    pending = 1;
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && app_update_size_bytes == 27);
    assert(uart_transmit_calls == 1);
    assert(strcmp(uart_output, "CAN receive done,len:27\r\n") == 0);
    app_update_work();
    assert(uart_transmit_calls == 1);

    /* 新一轮清零；完全没有数据时也只报告一次 0 字节，验证 tick 回绕。 */
    tick_ms = UINT32_MAX - 500;
    send_uart("cmd");
    uart_idle();
    app_update_work();
    assert(transmit_calls == 5 && app_update_size_bytes == 0);
    tick_ms += 999;
    app_update_work();
    assert(app_update_state == APP_UPDATE_RECEIVE && uart_transmit_calls == 1);
    tick_ms++;
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && uart_transmit_calls == 2);
    assert(strcmp(uart_output, "CAN receive done,len:0\r\n") == 0);
    app_update_work();
    assert(uart_transmit_calls == 2);

    /* 当前 F103 的 4480 字节 BIN：560 帧应累计为 4480 字节。 */
    send_uart("cmd\r\n");
    uart_idle();
    app_update_work();
    assert(transmit_calls == 6 && app_update_size_bytes == 0);
    incoming.RxHeader.DLC = 8;
    for (uint32_t i = 0; i < 560; i++)
    {
        tick_ms += 10;
        pending = 1;
        app_update_work();
    }
    assert(app_update_state == APP_UPDATE_RECEIVE && app_update_size_bytes == 4480);
    assert(uart_transmit_calls == 2);
    tick_ms += 1000;
    app_update_work();
    assert(app_update_state == APP_UPDATE_IDLE && uart_transmit_calls == 3);
    assert(strcmp(uart_output, "CAN receive done,len:4480\r\n") == 0);

    filter_result = HAL_ERROR;
    if (setjmp(error_jump) == 0)
    {
        int_can_init();
        assert(0 && "Filter initialization failure must stop startup");
    }
    assert(start_calls == 1);
    filter_result = HAL_OK;
    start_result = HAL_ERROR;
    if (setjmp(error_jump) == 0)
    {
        int_can_init();
        assert(0 && "CAN startup failure must stop startup");
    }
    assert(start_calls == 2);
    puts("PASS: UART commands, idle LEDs, one hyf per round, byte totals, short tail, ignored frames, idle timeout, zero bytes, tick wrap, 4480 bytes, FIFO and init errors");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="stm32-can-rx-") as folder:
    temporary = Path(folder)
    for name in ["can.h", "usart.h"]:
        (temporary / name).write_text(mock_header, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_can_receive.exe"
    subprocess.run([
        "gcc", "-std=c99", "-Wall", "-Wextra", "-Werror",
        f"-I{temporary}", f"-I{root / 'interface'}", f"-I{root / 'application'}",
        str(root / "interface/int_can.c"), str(root / "application/app_update.c"),
        str(temporary / "test.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
