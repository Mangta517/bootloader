#include "int_bootloader.h"
#include "stm32f407xx.h"
#include "usart.h"
#include <stdint.h>
#include <stdio.h> // printf 声明; 缺它会产生隐式声明告警

// 失败返回 1；成功直接进入目标程序，不返回。
uint8_t Int_bootloader_jump_to_app(uint32_t app_address)
{
    uint32_t app_stack_ptr;
    uint32_t app_reset_handle;
    uint32_t app_end;
    uint32_t index;
    static void (*app_entry)(void);

    if (app_address == RESET_START_ADDR)
    {
        app_end = APP_START_ADDR;
    }
    else if (app_address == APP_START_ADDR)
    {
        app_end = app_addr_end;
    }
    else
    {
        return 1;
    }

    app_stack_ptr = *(volatile uint32_t *)app_address;
    app_reset_handle = *(volatile uint32_t *)(app_address + APP_RESET_VECTOR_OFFSET);
    // F407 的 SRAM 为 128 KiB，CCM 为 64 KiB；栈顶允许等于区域末端。
    if (((app_stack_ptr & APP_STACK_ALIGN_MASK) != 0) ||
        !(((app_stack_ptr > stack_addr_start) && (app_stack_ptr <= SRAM_END_ADDR)) ||
          ((app_stack_ptr > CCM_START_ADDR) && (app_stack_ptr <= CCM_END_ADDR))))
    {
        printf("Error: Invalid stack pointer in application!\r\n");
        return 1;
    }
    // 入口必须在所选分区的向量字之后，并使用 Thumb 状态。
    if (((app_reset_handle & APP_RESET_THUMB_MASK) == 0) ||
        (app_reset_handle < app_address + APP_VECTOR_HEADER_SIZE_BYTES) || (app_reset_handle >= app_end))
    {
        printf("Error: Invalid reset handler in application!\r\n");
        return 1;
    }
    __disable_irq();
    // HAL 复位外设，同时停止 DMA；清除旧中断，交给目标程序重新初始化。
    HAL_DeInit();
    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL = 0;

    for (index = 0; index < 8; index++)
    {
        NVIC->ICER[index] = 0xFFFFFFFF;
        NVIC->ICPR[index] = 0xFFFFFFFF;
    }
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
    SCB->VTOR = app_address;
    __DSB();
    __ISB();

    // 静态函数指针不放在当前栈中，切换 MSP 后直接调用目标入口。
    app_entry = (void (*)(void))app_reset_handle;
    // 现有 APP 和恢复程序均在入口处重新开启全局中断。
    __set_MSP(app_stack_ptr);
    app_entry();

    // 目标入口不应返回，避免使用新栈退出当前函数。
    while (1)
    {
    }
}
