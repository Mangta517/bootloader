#include "int_bootloader.h"
#include "stm32f407xx.h"
#include "usart.h"
#include <stdint.h>
#include <stdio.h> // printf 声明; 缺它会产生隐式声明告警

/* DMA 数据由主循环复制到工作缓冲，再按半字写入 APP 区域。 */
static uint8_t uart1_rx_buf[bootloader_uart_rx_buff_len] = {0};
static uint16_t uart1_rx_len = 0;
volatile uint32_t uart1_rx_full_len = 0;
// printf 重定向在 usart.c 的 USER CODE 1 区(Keil/ARMCC 走 fputc, 无 GCC 的 __io_putchar 桥接), 此处不重复定义

/* 接收期间保持偶数偏移；收尾补写后按有效固件字节数计数。 */
static uint32_t flash_write_offset = 0;
static uint8_t last_btye_flag = 0;
static uint8_t last_byte = 0;

static uint32_t bootloader_image_length;
static uint8_t bootloader_flash_status = 1;

static uint8_t Int_flash_halfword_write(void)
{
    uint16_t position = 0;
    uint16_t data;
    HAL_StatusTypeDef status;

    if (last_btye_flag != 0)
    {
        data = (uint16_t)last_byte | ((uint16_t)uart1_rx_buf[0] << 8);
        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, APP_START_ADDR + flash_write_offset, data);
        if (status != HAL_OK)
        {
            return 1;
        }
        flash_write_offset += 2;
        last_btye_flag = 0;
        position = 1;
    }

    for (; position + 1 < uart1_rx_len; position += 2)
    {
        data = (uint16_t)uart1_rx_buf[position] | ((uint16_t)uart1_rx_buf[position + 1] << 8);
        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, APP_START_ADDR + flash_write_offset, data);
        if (status != HAL_OK)
        {
            return 1;
        }
        flash_write_offset += 2;
    }

    if (position < uart1_rx_len)
    {
        last_byte = uart1_rx_buf[position];
        last_btye_flag = 1;
    }
    return 0;
}
/* Circular DMA 的前后两个半区交替接收，主循环直接消费。 */
static uint8_t uart1_rx_dma_buf[2][BOOTLOADER_UART_DMA_HALF_SIZE];
static volatile uint32_t uart1_dma_wrap_count;
static volatile uint32_t uart1_dma_ready_end[2];
static uint32_t uart1_dma_read_total;
static uint32_t uart1_dma_last_total;
static uint32_t uart1_rx_last_tick;
static uint8_t uart1_rx_active;
static uint8_t bootloader_rx_stopped;
static uint32_t bootloader_rx_end_total;
// 主循环确认空闲且当前数据处理结束时更新，单位 ms；启动 DMA 时清零。
volatile uint32_t uart1_rx_complete_tick = 0;
volatile uint32_t uart1_rx_overflow_count = 0;
volatile uint32_t uart1_rx_error_count = 0;

/* 就绪标记记录半区的累计结束位置，避免布尔标记在回绕后混淆新旧数据。 */
void HAL_UART_RxHalfCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_dma_ready_end[0] = uart1_dma_wrap_count * BOOTLOADER_UART_RING_SIZE + BOOTLOADER_UART_DMA_HALF_SIZE;
    }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_dma_wrap_count++;
        uart1_dma_ready_end[1] = uart1_dma_wrap_count * BOOTLOADER_UART_RING_SIZE;
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart1_rx_error_count++;
    }
}

uint8_t bootloader_start_dma(void)
{
    HAL_StatusTypeDef status;

    uart1_dma_wrap_count = 0;
    uart1_dma_ready_end[0] = 0;
    uart1_dma_ready_end[1] = 0;
    uart1_dma_read_total = 0;
    uart1_dma_last_total = 0;
    uart1_rx_last_tick = HAL_GetTick();
    uart1_rx_active = 0;
    uart1_rx_complete_tick = 0;
    bootloader_rx_stopped = 0;
    status = HAL_UART_Receive_DMA(&huart1, &uart1_rx_dma_buf[0][0], BOOTLOADER_UART_RING_SIZE);
    if (status != HAL_OK)
    {
        uart1_rx_error_count++;
        return 1;
    }
    /* HAL 默认开启 HT/TC：前后半区完成后分别通知主循环。 */
    return 0;
}

static uint32_t bootloader_dma_received_total(void)
{
    uint32_t primask = __get_PRIMASK();
    uint32_t wraps;
    uint32_t remaining;

    /* 同时快照回绕计数和 NDTR，补上已回绕但尚未进入 DMA IRQ 的情况。 */
    __disable_irq();
    wraps = uart1_dma_wrap_count;
    remaining = __HAL_DMA_GET_COUNTER(huart1.hdmarx);
    if (__HAL_DMA_GET_FLAG(huart1.hdmarx, __HAL_DMA_GET_TC_FLAG_INDEX(huart1.hdmarx)) != RESET)
    {
        wraps++;
        remaining = __HAL_DMA_GET_COUNTER(huart1.hdmarx);
        if (remaining == 0)
        {
            remaining = BOOTLOADER_UART_RING_SIZE;
        }
    }
    __set_PRIMASK(primask);
    __DMB();
    return wraps * BOOTLOADER_UART_RING_SIZE + BOOTLOADER_UART_RING_SIZE - remaining;
}

static void bootloader_process_packet(void)
{
    HAL_StatusTypeDef lock_status;

    if (bootloader_flash_status != 0)
    {
        uart1_rx_len = 0;
        return;
    }
    /* 限制在本次声明的固件长度内，遗留字节也占有效数据空间。 */
    if ((flash_write_offset >= bootloader_image_length) ||
        ((uint32_t)uart1_rx_len + last_btye_flag > bootloader_image_length - flash_write_offset))
    {
        bootloader_flash_status = 1;
        uart1_rx_len = 0;
        return;
    }

    if (HAL_FLASH_Unlock() == HAL_OK)
    {
        /* 接收前已整段擦除，写入过程中绝不再擦除扇区。 */
        bootloader_flash_status = Int_flash_halfword_write();
        lock_status = HAL_FLASH_Lock();
        if (lock_status != HAL_OK)
        {
            bootloader_flash_status = 1;
        }
    }
    else
    {
        bootloader_flash_status = 1;
    }
    uart1_rx_len = 0;
}

/* DMA 持续生产；主循环复制到工作缓冲并顺序写入已经擦除的 APP 区域。 */
void Int_bootloader_Process(void)
{
    uint32_t received;
    uint32_t available;
    uint32_t position;
    uint32_t half;
    uint32_t half_end;
    uint32_t copy_end;
    uint32_t now;

    /* UART 已出错时本次升级会失败，不再重启 DMA 或继续写入。 */
    if (uart1_rx_error_count != 0)
    {
        return;
    }

    if (bootloader_rx_stopped != 0)
    {
        received = bootloader_rx_end_total;
    }
    else
    {
        received = bootloader_dma_received_total();
    }
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
    if (available > BOOTLOADER_UART_RING_SIZE)
    {
        uart1_rx_overflow_count += available - BOOTLOADER_UART_RING_SIZE;
        uart1_dma_read_total = received - BOOTLOADER_UART_RING_SIZE;
    }
    copy_end = uart1_dma_read_total;
    if (uart1_dma_read_total != received)
    {
        half = (uart1_dma_read_total / BOOTLOADER_UART_DMA_HALF_SIZE) % 2;
        half_end = uart1_dma_read_total + BOOTLOADER_UART_DMA_HALF_SIZE - uart1_dma_read_total % BOOTLOADER_UART_DMA_HALF_SIZE;
        if (bootloader_rx_stopped != 0)
        {
            copy_end = received;
        }
        else if (uart1_dma_ready_end[half] == half_end)
        {
            copy_end = half_end;
        }
        else if ((uint32_t)(now - uart1_rx_last_tick) >= BOOTLOADER_UART_RX_TIMEOUT_MS)
        {
            /* 不足半区的尾部：只在 DMA 写入位置稳定 10 ms 后交给消费者。 */
            copy_end = received;
        }
    }
    while ((uart1_rx_len < bootloader_uart_rx_buff_len) && (uart1_dma_read_total != copy_end))
    {
        position = uart1_dma_read_total % BOOTLOADER_UART_RING_SIZE;
        uart1_rx_buf[uart1_rx_len++] = uart1_rx_dma_buf[position / BOOTLOADER_UART_DMA_HALF_SIZE][position % BOOTLOADER_UART_DMA_HALF_SIZE];
        uart1_dma_read_total++;
    }

    if (uart1_rx_len == bootloader_uart_rx_buff_len)
    {
        bootloader_process_packet();
    }
    else if ((uart1_rx_len > 0) && (uart1_dma_read_total == received) &&
             ((bootloader_rx_stopped != 0) || ((uint32_t)(now - uart1_rx_last_tick) >= BOOTLOADER_UART_RX_TIMEOUT_MS)))
    {
        bootloader_process_packet();
    }

    // 同时覆盖整包和不足一包的结束情况，每轮接收只记录一次。
    if ((uart1_rx_active != 0) && (uart1_rx_len == 0) && (uart1_dma_read_total == received) &&
        ((uint32_t)(now - uart1_rx_last_tick) >= BOOTLOADER_UART_RX_TIMEOUT_MS) &&
        ((bootloader_rx_stopped != 0) || (bootloader_dma_received_total() == received)))
    {
        uart1_rx_complete_tick = HAL_GetTick();
        uart1_rx_active = 0;
    }
}

void Int_bootloader_Init(void)
{
    bootloader_start_dma();
}
//1失败 0成功
uint8_t Int_bootloader_jump_to_app(void)
{
    typedef void (*pFunc)(void);

    //(1)校验
    uint32_t app_stack_ptr = *(volatile uint32_t *)(APP_START_ADDR);
    uint32_t app_reset_handle = *(volatile uint32_t *)(APP_START_ADDR + 4);
    // 1.校验栈顶地址
    if ((app_stack_ptr & 0xFFFF0000) != stack_addr_start)
    {
        printf("Error: Invalid stack pointer in application!\r\n");
        return 1;
    }
    // 2.校验复位地址
    if (app_reset_handle <= APP_START_ADDR || app_reset_handle >= app_addr_end)
    {
        printf("Error: Invalid reset handler in application!\r\n");
        return 1;
    }
    // （2）注销bootloader程序

    NVIC_DisableIRQ(USART1_IRQn);
    NVIC_DisableIRQ(DMA2_Stream2_IRQn);
    NVIC_DisableIRQ(EXTI0_IRQn);

    //关闭系统滴答定时器
    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL = 0;

    // 注销hal库
    HAL_DeInit();

    // 关中断
    __disable_irq();
    // 2.设置堆栈指针
    __set_MSP(app_stack_ptr);
    // 3.跳转到应用程序复位地址
    SCB->VTOR = APP_START_ADDR;
    // 4.跳转到app复位中断
    pFunc jump_to_app = (pFunc)app_reset_handle;
    jump_to_app();
    //虽然跳转不到
    return 0;
}

static uint32_t bootloader_flash_sector(uint32_t address)
{
    if (address < 0x08010000)
    {
        return (address - 0x08000000) / 0x4000;
    }
    if (address < 0x08020000)
    {
        return FLASH_SECTOR_4;
    }
    return FLASH_SECTOR_5 + (address - 0x08020000) / 0x20000;
}

uint8_t Int_early_flash_erase(uint32_t early_sector_addr, uint16_t sectors)
{
    FLASH_EraseInitTypeDef erase;
    uint32_t sector_error = 0xFFFFFFFFU;
    uint32_t first_sector;
    HAL_StatusTypeDef status;
    HAL_StatusTypeDef lock_status;

    if ((early_sector_addr < APP_START_ADDR) || (early_sector_addr >= app_addr_end) || (sectors == 0))
    {
        return 1;
    }
    first_sector = bootloader_flash_sector(early_sector_addr);
    if ((uint32_t)sectors > FLASH_SECTOR_7 + 1 - first_sector)
    {
        return 1;
    }

    status = HAL_FLASH_Unlock();
    if (status != HAL_OK)
    {
        return 1;
    }
    erase.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase.Sector = first_sector;
    erase.NbSectors = sectors;
    erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
    status = HAL_FLASHEx_Erase(&erase, &sector_error);
    lock_status = HAL_FLASH_Lock();
    if (status != HAL_OK)
    {
        return 1;
    }
    if (sector_error != 0xFFFFFFFFU)
    {
        return 1;
    }
    if (lock_status != HAL_OK)
    {
        return 1;
    }
    return 0;
}

uint8_t bootloader_prepare_image(uint32_t image_length)
{
    uint32_t last_sector;

    bootloader_flash_status = 1;
    bootloader_image_length = 0;
    if ((image_length == 0) || (image_length > APP_SIZE_BYTES))
    {
        return 1;
    }

    /* 擦除在 DMA 接收开始前完成；长度减一用于准确处理扇区边界。 */
    last_sector = bootloader_flash_sector(APP_START_ADDR + image_length - 1);
    bootloader_flash_status = Int_early_flash_erase(APP_START_ADDR, (uint16_t)(last_sector - bootloader_flash_sector(APP_START_ADDR) + 1));
    if (bootloader_flash_status != 0)
    {
        return bootloader_flash_status;
    }
    bootloader_image_length = image_length;
    flash_write_offset = 0;
    last_btye_flag = 0;
    last_byte = 0;
    uart1_rx_len = 0;
    uart1_rx_full_len = 0;
    uart1_rx_overflow_count = 0;
    uart1_rx_error_count = 0;
    return 0;
}

bootloader_result_enum bootloader_finish_image(void)
{
    uint32_t stopped_position;
    HAL_StatusTypeDef status;
    HAL_StatusTypeDef lock_status;

    if (bootloader_rx_stopped == 0)
    {
        status = HAL_UART_DMAPause(&huart1);
        if (status != HAL_OK)
        {
            return BOOTLOADER_RESULT_ERROR;
        }
        /* 先记录真实接收量；HAL Abort 会先关闭完成中断，避免停止事件被当成回绕。 */
        bootloader_rx_end_total = bootloader_dma_received_total();
        /* DMAPause 已关闭 DMAR，UART Abort 不会再替我们终止 DMA 句柄。 */
        status = HAL_DMA_Abort(huart1.hdmarx);
        if (status != HAL_OK)
        {
            return BOOTLOADER_RESULT_ERROR;
        }
        /* DMAR 已暂停，按停止前后的位置差补上最后一次总线传输，不读取停止产生的 TC。 */
        stopped_position = (BOOTLOADER_UART_RING_SIZE - __HAL_DMA_GET_COUNTER(huart1.hdmarx)) % BOOTLOADER_UART_RING_SIZE;
        bootloader_rx_end_total += (stopped_position + BOOTLOADER_UART_RING_SIZE -
                                   bootloader_rx_end_total % BOOTLOADER_UART_RING_SIZE) % BOOTLOADER_UART_RING_SIZE;
        status = HAL_UART_AbortReceive(&huart1);
        if (status != HAL_OK)
        {
            return BOOTLOADER_RESULT_ERROR;
        }
        bootloader_rx_stopped = 1;
    }

    /* 每轮最多处理一包，保留主循环处理 CAN 的机会。 */
    Int_bootloader_Process();
    if ((bootloader_flash_status != 0) || (uart1_rx_error_count != 0) || (uart1_rx_overflow_count != 0) ||
        (uart1_rx_full_len != bootloader_image_length))
    {
        return BOOTLOADER_RESULT_ERROR;
    }
    if ((uart1_dma_read_total != bootloader_rx_end_total) || (uart1_rx_len != 0))
    {
        return BOOTLOADER_RESULT_PENDING;
    }
    if (flash_write_offset + last_btye_flag != bootloader_image_length)
    {
        return BOOTLOADER_RESULT_ERROR;
    }

    if (last_btye_flag != 0)
    {
        status = HAL_FLASH_Unlock();
        if (status != HAL_OK)
        {
            bootloader_flash_status = 1;
            return BOOTLOADER_RESULT_ERROR;
        }
        /* 末字节放低位，高位补 FF；保留无符号表达式传入 64 位接口，有效长度只增加一字节。 */
        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, APP_START_ADDR + flash_write_offset, (uint16_t)last_byte | 0xFF00U);
        lock_status = HAL_FLASH_Lock();
        if (status == HAL_OK)
        {
            status = lock_status;
        }
        if (status != HAL_OK)
        {
            bootloader_flash_status = 1;
            return BOOTLOADER_RESULT_ERROR;
        }
        flash_write_offset++;
        last_btye_flag = 0;
    }
    return BOOTLOADER_RESULT_OK;
}
