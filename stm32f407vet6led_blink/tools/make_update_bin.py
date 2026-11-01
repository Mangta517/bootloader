"""Package the LED APP to fit the Bootloader and F407 CAN receive limits."""
from pathlib import Path
import struct
import sys

APP_START_ADDR = 0x08008000
UPDATE_SIZE_BYTES = 4480

raw_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
image = raw_path.read_bytes()

if len(image) > UPDATE_SIZE_BYTES:
    raise SystemExit(f"APP is {len(image)} bytes; the update slot allows {UPDATE_SIZE_BYTES} bytes.")

stack_pointer, reset_handler = struct.unpack_from("<II", image)
if not (0x20000000 < stack_pointer <= 0x20020000 and stack_pointer % 8 == 0):
    raise SystemExit("Invalid initial MSP.")
if not (reset_handler & 1 and APP_START_ADDR + 8 <= reset_handler < APP_START_ADDR + len(image)):
    raise SystemExit("The APP must be linked at 0x08008000 with a valid Thumb reset vector.")

# 测试镜像用 FF 补齐到 4480 字节，满足 Bootloader 的长度下限；不提前附加 CRC。
output_path.write_bytes(image.ljust(UPDATE_SIZE_BYTES, b"\xFF"))
print(f"Update BIN: {output_path} ({UPDATE_SIZE_BYTES} bytes; code image {len(image)} bytes)")
