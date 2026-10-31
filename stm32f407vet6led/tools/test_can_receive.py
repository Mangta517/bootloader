"""Compile the real CAN modules and check periodic hyf frames and the FIFO driver."""
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
void Error_Handler(void);
uint32_t HAL_GetTick(void);
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
#include "app_can.h"
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
static uint32_t now_ms;
static HAL_StatusTypeDef filter_result = HAL_OK;
static HAL_StatusTypeDef start_result = HAL_OK;
static jmp_buf error_jump;

void Error_Handler(void)
{
    longjmp(error_jump, 1);
}

uint32_t HAL_GetTick(void)
{
    return now_ms;
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
    (void)handle;
    (void)data;
    (void)length;
    (void)timeout;
    assert(0 && "CAN application must not print received frames");
    return HAL_OK;
}

int main(void)
{
    CAN_MSG_RECEIVE message = {0};
    uint8_t count;
    uint8_t data[8] = {0x00, 0x55, 0xAA, 0xFF, 0, 0, 0, 0};

    int_can_init();
    assert(start_calls == 1);
    app_can_process();
    assert(transmit_calls == 0);
    now_ms = 999;
    app_can_process();
    assert(transmit_calls == 0);
    now_ms = 1000;
    app_can_process();
    assert(transmit_calls == 1);
    app_can_process();
    now_ms = 1999;
    app_can_process();
    assert(transmit_calls == 1);
    now_ms = 2000;
    app_can_process();
    assert(transmit_calls == 2);
    now_ms = 10000;
    app_can_process();
    assert(transmit_calls == 3);

    /* 跨过 32 位毫秒计数回绕后，发送间隔仍为 1000 ms。 */
    now_ms = UINT32_MAX - 500;
    app_can_process();
    assert(transmit_calls == 4);
    now_ms = 498;
    app_can_process();
    assert(transmit_calls == 4);
    now_ms = 499;
    app_can_process();
    assert(transmit_calls == 5);

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
    puts("PASS: CAN1 ID 0 hyf payload, 1000 ms interval, tick wrap, no UART output, FIFO and init errors");
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
        str(root / "interface/int_can.c"), str(root / "application/app_can.c"),
        str(temporary / "test.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
