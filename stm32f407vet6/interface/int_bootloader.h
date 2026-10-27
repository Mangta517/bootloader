#ifndef __INT_BOOTLOADER_H
#define __INT_BOOTLOADER_H

#include "stdlib.h"
#include "usart.h"
#include "string.h"

#define stack_addr_start              0x20000000
#define app_addr_end                  0x08080000
/* 扇区 0 放 Bootloader，扇区 1 放恢复程序；APP 从扇区 2 开始，结束地址不包含在可写范围内。 */
#define RESET_START_ADDR 0x08004000
#define APP_START_ADDR 0x08008000

/* SRAM 栈范围：(stack_addr_start, SRAM_END_ADDR]；CCM 栈范围：(CCM_START_ADDR, CCM_END_ADDR]。
 * 起点不作为有效栈顶，末端允许作为初始栈顶。
 */
#define SRAM_END_ADDR                  0x20020000  /* 128 KiB SRAM 的末端 */
#define CCM_START_ADDR                 0x10000000  /* 64 KiB CCM 的起点 */
#define CCM_END_ADDR                   0x10010000  /* 64 KiB CCM 的末端 */
#define APP_STACK_ALIGN_MASK           0x07        /* 栈顶按 8 字节对齐，低 3 位应为 0 */

/* APP 向量表开头：前 4 字节是初始栈指针，后 4 字节是复位入口。 */
#define APP_RESET_VECTOR_OFFSET        4           /* 复位向量相对向量表起点的字节偏移 */
#define APP_VECTOR_HEADER_SIZE_BYTES   8           /* 仅栈指针和复位向量这两个字，共 8 字节 */
#define APP_RESET_THUMB_MASK           0x01        /* 复位入口最低位须为 1，表示 Thumb 状态 */

// 失败返回 1；成功直接跳转，不返回。
uint8_t Int_bootloader_jump_to_app(uint32_t app_address);

#endif
