# GENERATED FILE - DO NOT EDIT.
# Source: contracts/vision.yaml
# Regenerate: python3 tools/gen_contracts.py

import struct

COMMAND_PHOTO = "chup_anh"
COMMAND_DETECT = "nhan_dien"
CHUNK_MAGIC_U32 = 0x4d495253
CHUNK_VERSION = 1
CHUNK_HEADER_BYTES = 28
CHUNK_PAYLOAD_BYTES = 16384
CHUNK_HEADER = struct.Struct("<IHHHHIIII")
CHUNK_HEADER_FIELDS = ('magic', 'version', 'index', 'count', 'reserved', 'boot', 'seq', 'total_bytes', 'crc32')
