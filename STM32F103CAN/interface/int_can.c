#include "int_can.h"

#define CAN_SEND_TIMEOUT_MS 100

void can_init(void)
{
    CAN_FilterTypeDef filter = {0};

    /* 只接收标准 ID 0 的数据帧，同时匹配全部 11 位 ID、IDE 和 RTR。 */
    filter.FilterBank = 0;
    filter.FilterMode = CAN_FILTERMODE_IDMASK;
    filter.FilterScale = CAN_FILTERSCALE_32BIT;
    filter.FilterIdHigh = 0;
    filter.FilterIdLow = 0;
    filter.FilterMaskIdHigh = 0xFFE0;
    filter.FilterMaskIdLow = 0x0006;
    filter.FilterFIFOAssignment = CAN_RX_FIFO0;
    filter.FilterActivation = ENABLE;
    if (HAL_CAN_ConfigFilter(&hcan, &filter) != HAL_OK)
    {
        Error_Handler();
    }

    if (HAL_CAN_Start(&hcan) != HAL_OK)
    {
        Error_Handler();
    }
}

uint8_t can_send(uint16_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef header = {0};
    uint32_t mailbox;
    uint32_t start_tick;
    uint32_t tx_ok_flag;
    uint32_t complete_flag;
    uint8_t result = 1;

    if (HAL_CAN_GetTxMailboxesFreeLevel(&hcan) == 0)
    {
        return 1;
    }

    header.StdId = id;
    header.IDE = CAN_ID_STD;
    header.RTR = CAN_RTR_DATA;
    header.DLC = len;
    if (HAL_CAN_AddTxMessage(&hcan, &header, data, &mailbox) != HAL_OK)
    {
        return 1;
    }

    /* 当前关闭自动重传；邮箱释放也可能是发送失败，必须检查 TXOK。 */
    start_tick = HAL_GetTick();
    while (HAL_CAN_IsTxMessagePending(&hcan, mailbox) != 0)
    {
        if ((uint32_t)(HAL_GetTick() - start_tick) >= CAN_SEND_TIMEOUT_MS)
        {
            HAL_CAN_AbortTxRequest(&hcan, mailbox);
            return 1;
        }
    }

    if (mailbox == CAN_TX_MAILBOX0)
    {
        tx_ok_flag = CAN_FLAG_TXOK0;
        complete_flag = CAN_FLAG_RQCP0;
    }
    else if (mailbox == CAN_TX_MAILBOX1)
    {
        tx_ok_flag = CAN_FLAG_TXOK1;
        complete_flag = CAN_FLAG_RQCP1;
    }
    else
    {
        tx_ok_flag = CAN_FLAG_TXOK2;
        complete_flag = CAN_FLAG_RQCP2;
    }
    if (__HAL_CAN_GET_FLAG(&hcan, tx_ok_flag) != RESET)
    {
        result = 0;
    }
    __HAL_CAN_CLEAR_FLAG(&hcan, complete_flag);
    return result;
}

uint8_t can_receive(CAN_RxHeaderTypeDef *header, uint8_t *data)
{
    if (HAL_CAN_GetRxFifoFillLevel(&hcan, CAN_RX_FIFO0) == 0)
    {
        return 1;
    }

    if (HAL_CAN_GetRxMessage(&hcan, CAN_RX_FIFO0, header, data) != HAL_OK)
    {
        return 1;
    }

    return 0;
}
