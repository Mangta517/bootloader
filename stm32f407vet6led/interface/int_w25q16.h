#ifndef INT_W25Q16_H
#define INT_W25Q16_H

#include <stdint.h>

#define W25Q16_SIZE_BYTES 0x200000

/* W25Q16 常用 SPI 命令。 */
#define W25Q16_CMD_WRITE_ENABLE          0x06    /* 写使能 */
#define W25Q16_CMD_WRITE_DISABLE         0x04    /* 写禁止 */
#define W25Q16_CMD_READ_STATUS_REG1      0x05    /* 读状态寄存器 1 */
#define W25Q16_CMD_READ_STATUS_REG2      0x35    /* 读状态寄存器 2 */
#define W25Q16_CMD_READ_STATUS_REG3      0x15    /* 读状态寄存器 3 */
#define W25Q16_CMD_WRITE_STATUS_REG1     0x01    /* 写状态寄存器 1 */
#define W25Q16_CMD_WRITE_STATUS_REG2     0x31    /* 写状态寄存器 2 */
#define W25Q16_CMD_WRITE_STATUS_REG3     0x11    /* 写状态寄存器 3 */
#define W25Q16_CMD_READ_DATA             0x03    /* 普通读取 */
#define W25Q16_CMD_FAST_READ             0x0B    /* 快速读取 */
#define W25Q16_CMD_PAGE_PROGRAM          0x02    /* 页编程 */
#define W25Q16_CMD_SECTOR_ERASE_4KB      0x20    /* 擦除 4 KiB 扇区 */
#define W25Q16_CMD_BLOCK_ERASE_32KB      0x52    /* 擦除 32 KiB 块 */
#define W25Q16_CMD_BLOCK_ERASE_64KB      0xD8    /* 擦除 64 KiB 块 */
#define W25Q16_CMD_CHIP_ERASE            0xC7    /* 整片擦除 */
#define W25Q16_CMD_READ_MANUFACTURER_ID  0x90    /* 读取品牌 ID 和设备 ID */
#define W25Q16_CMD_READ_JEDEC_ID         0x9F    /* 读取 JEDEC ID */
#define W25Q16_CMD_POWER_DOWN            0xB9    /* 进入掉电模式 */
#define W25Q16_CMD_RELEASE_POWER_DOWN    0xAB    /* 退出掉电模式 */
#define W25Q16_CMD_ENABLE_RESET          0x66    /* 复位使能 */
#define W25Q16_CMD_RESET                 0x99    /* 复位芯片 */

/* 先初始化 GPIO 和 SPI1；PB0 为低有效片选。 */
void int_w25q16_start(void);
void int_w25q16_stop(void);

/* 读写前调用 start，整条指令完成或失败后调用 stop；写入返回 0 成功、1 失败。 */
uint8_t int_w25q16_write_byte(uint8_t data);
/* 发送 0xFF 产生读取时钟，直接返回读到的字节；通信失败返回 0xFF，无法与有效数据 0xFF 区分。 */
uint8_t int_w25q16_read_byte(void);

/* 9Fh 读取 JEDEC ID，品牌 ID 为 8 位、设备 ID 为 16 位，两个指针必须有效。
 * W25Q16JV-IQ/JQ 分别为 0xEF、0x4015；设备 ID 高字节为存储器类型，低字节为容量。
 */
void int_w25q16_read_id(uint8_t *manufacturer_id, uint16_t *device_id);

/* 内部循环查询 BUSY 位，空闲返回 0；SPI 失败或等待达到 5 秒返回 1。
 * 仅在主循环/任务中调用，等待期间须保持 HAL tick 运行。
 */
uint8_t int_w25q16_is_busy(void);

/* 等待芯片空闲后发送写使能指令，内部控制片选；返回 0 成功、1 失败。 */
uint8_t int_w25q16_write_enable(void);

/* 连续读取 length 字节，可跨页、扇区和块，函数内部控制片选。
 * block：64 KiB 块号 0~31；sector：块内 4 KiB 扇区号 0~15，与 write/erase_sector 相同。
 * page：扇区内页号 0~15；address：页内字节位置 0~255，编号均从 0 开始。
 * data：接收数据的缓冲区；length：读取字节数。
 * 返回 0 成功、1 失败；失败时已释放片选，接收缓冲区可能包含部分数据，调用方应丢弃本次结果。
 * 调用方保证芯片空闲、参数及缓冲区有效，读取范围不超出 0x1FFFFF。
 */
uint8_t int_w25q16_read(uint8_t block, uint8_t sector, uint8_t page, uint8_t address, uint8_t *data, uint16_t length);

/* 直接按 32 位参数中的片内地址读取：先等待 BUSY 清零，再发送 0x03 和低 24 位地址。
 * 调用方保证地址及缓冲区有效，读取范围不超出 0x1FFFFF。
 * 返回 0 成功、1 失败；BUSY 等待最多 5 秒，SPI 失败立即结束并释放片选。
 * 失败时接收缓冲区可能包含部分数据，调用方应丢弃本次结果；仅在主循环/任务中调用。
 */
uint8_t int_w25q16_read_address(uint32_t read_address, uint8_t *data, uint16_t length);

/* 从指定位置连续写入 length 字节，自动按 256 字节分页并等待编程完成。
 * block：64 KiB 块号 0~31；sector：块内 4 KiB 扇区号 0~15，与 erase_sector 相同。
 * page：扇区内页号 0~15；address：页内字节位置 0~255，编号均从 0 开始。
 * data：待写入的数据缓冲区；length：写入字节数，可跨页、扇区和块。
 * 写入前目标区域必须已擦除且未被写保护；本函数不执行擦除，返回 0 成功、1 失败。
 * 调用方保证参数及缓冲区有效，写入范围不超出 0x1FFFFF；length 为 0 时不操作。
 * 每次 BUSY 等待最多 5 秒，等待失败或写使能失败时停止后续写入；仅在主循环/任务中调用。
 */
uint8_t int_w25q16_write(uint8_t block, uint8_t sector, uint8_t page, uint8_t address, const uint8_t *data, uint16_t length);

/* 擦除一个 4 KiB 扇区：block 为 64 KiB 块号 0~31，sector 为块内扇区号 0~15。
 * 整个扇区的数据都会变为 0xFF；调用方保证编号有效且目标扇区未被写保护。
 * 内部执行写使能并阻塞等待擦除完成，每次 BUSY 等待最多 5 秒，返回 0 成功、1 失败。
 * 等待或写使能失败时停止后续操作；仅在主循环/任务中调用。
 */
uint8_t int_w25q16_erase_sector(uint8_t block, uint8_t sector);

#endif
