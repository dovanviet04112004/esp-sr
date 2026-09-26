# GENERATED FILE - DO NOT EDIT.
# Source: contracts/stream/frame.yaml
# Regenerate: python3 tools/gen_contracts.py

import struct

MAGIC = b'SRST'
MAGIC_U32 = 0x54535253
VERSION = 1
HEADER_BYTES = 24
HEADER = struct.Struct("<IHHQIBBH")
HEADER_FIELDS = ('magic', 'version', 'mode', 't_us', 'seq', 'channels', 'format', 'samples')
MODES = {0: ('off', 0, ()), 1: ('clean', 1, ('clean',)), 2: ('raw', 2, ('ch0', 'ch1')), 3: ('raw_ref', 3, ('ch0', 'ch1', 'ref')), 4: ('raw_ref_clean', 4, ('ch0', 'ch1', 'ref', 'clean'))}
FORMATS = {0: ('s16le', 2)}
