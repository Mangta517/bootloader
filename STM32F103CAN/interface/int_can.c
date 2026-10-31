#include "int_can.h"

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

    return 0;
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
