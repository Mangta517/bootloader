#include "stm32f4xx.h"

#define APP_START_ADDR 0x08008000
#define LED0_PIN       5
#define LED0_PIN_MASK  (1 << LED0_PIN)

static volatile uint32_t app_tick_ms;

void SysTick_Handler(void)
{
    app_tick_ms++;
}

static void app_delay_ms(uint32_t delay_ms)
{
    uint32_t start_ms = app_tick_ms;

    while ((uint32_t)(app_tick_ms - start_ms) < delay_ms)
    {
    }
}

int main(void)
{
    /* 由 Bootloader 跳转进入，先设置 APP 向量表，再将系统时钟切回 HSI 16 MHz。 */
    SCB->VTOR = APP_START_ADDR;
    __DSB();
    __ISB();
    RCC->CR |= RCC_CR_HSION;
    while ((RCC->CR & RCC_CR_HSIRDY) == 0)
    {
    }
    RCC->CFGR &= ~(RCC_CFGR_SW | RCC_CFGR_HPRE);
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_HSI)
    {
    }
    SystemCoreClockUpdate();

    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOEEN;
    (void)RCC->AHB1ENR;

    /* 板载 LED0 接 PE5，低电平点亮；仅配置这一只 LED。 */
    GPIOE->BSRR = LED0_PIN_MASK;
    GPIOE->MODER &= ~(3 << (LED0_PIN * 2));
    GPIOE->MODER |= 1 << (LED0_PIN * 2);
    GPIOE->OTYPER &= ~LED0_PIN_MASK;
    GPIOE->OSPEEDR &= ~(3 << (LED0_PIN * 2));
    GPIOE->PUPDR &= ~(3 << (LED0_PIN * 2));
    GPIOE->PUPDR |= 1 << (LED0_PIN * 2);

    SysTick_Config(SystemCoreClock / 1000);
    __enable_irq();

    while (1)
    {
        GPIOE->ODR ^= LED0_PIN_MASK;
        app_delay_ms(500);
    }
}
