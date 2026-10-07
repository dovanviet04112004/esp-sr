# GENERATED FILE - DO NOT EDIT.
# Source: contracts/schema/*.schema.json
# Regenerate: python3 tools/gen_contracts.py

from typing import Literal, NotRequired, TypedDict


class CommandSetCommandsItem(TypedDict):
    id: str
    text: str
    response: NotRequired[str]



class CommandSet(TypedDict):
    version: int
    commands: list[CommandSetCommandsItem]

DeviceCmdOp = Literal['SET_CONFIG', 'SET_STREAM', 'SPEAK', 'CALIBRATE', 'REBOOT']


class DeviceCmdStream(TypedDict):
    mode: int
    host: NotRequired[str]
    port: NotRequired[int]
    durationS: NotRequired[int]



class DeviceCmd(TypedDict):
    op: DeviceCmdOp
    requestId: NotRequired[str]
    key: NotRequired[str]
    value: NotRequired[int]
    stream: NotRequired[DeviceCmdStream]
    text: NotRequired[str]

EventKind = Literal['WAKE', 'COMMAND', 'REJECT', 'ERROR']


class Event(TypedDict):
    deviceId: str
    seq: int
    kind: EventKind
    scorePermille: NotRequired[int]
    marginPermille: NotRequired[int]
    commandId: NotRequired[str]
    code: NotRequired[str]
    doaDeg: NotRequired[int]
    ts: NotRequired[int]



class Heartbeat(TypedDict):
    deviceId: str
    fw: str
    models: NotRequired[str]
    uptimeS: int
    heapInternalFree: int
    heapInternalMin: int
    heapPsramFree: NotRequired[int]
    heapPsramMin: NotRequired[int]
    dmaOverflows: int
    framesDropped: int
    cleanDropped: int
    eventsDropped: int
    streamDropped: NotRequired[int]
    qCleanPeak: NotRequired[int]
    jsonArenaPeak: NotRequired[int]
    rssiDbm: NotRequired[int]
    coreLoadPct: NotRequired[list[int]]
    ts: NotRequired[int]

OtaManifestKind = Literal['FIRMWARE', 'MODELS']


class OtaManifest(TypedDict):
    kind: OtaManifestKind
    version: str
    url: str
    sha256: str
    sizeBytes: int



class ResponsesResponsesItem(TypedDict):
    id: str
    text: str



class Responses(TypedDict):
    version: int
    responses: list[ResponsesResponsesItem]

StatusState = Literal['ONLINE', 'OFFLINE']


class Status(TypedDict):
    deviceId: str
    state: StatusState
    fw: NotRequired[str]

TelemetryState = Literal['LISTEN', 'COMMAND', 'REPLY']


class TelemetrySamplesItem(TypedDict):
    doaDeg: int
    doaConf: int
    vad: bool
    levelDbfs: int
    gainDb: int



class Telemetry(TypedDict):
    deviceId: str
    seq: int
    state: TelemetryState
    samples: list[TelemetrySamplesItem]


# Every schema as parsed JSON, for host/ to validate what boards send (KEHOACH 7.7).
SCHEMAS: dict[str, dict] = {
    'command_set': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/command_set',
     'title': 'command_set',
     'description': 'The closed set of commands a board recognises; one line of text per command.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['version', 'commands'],
     'properties': {'version': {'type': 'integer', 'minimum': 1, 'maximum': 2147483647},
                    'commands': {'type': 'array',
                                 'minItems': 1,
                                 'maxItems': 301,
                                 'items': {'type': 'object',
                                           'additionalProperties': False,
                                           'required': ['id', 'text'],
                                           'properties': {'id': {'type': 'string',
                                                                 'pattern': '^[a-z0-9_]{1,32}$',
                                                                 'maxLength': 32},
                                                          'text': {'type': 'string',
                                                                   'minLength': 1,
                                                                   'maxLength': 64},
                                                          'response': {'type': 'string',
                                                                       'pattern': '^[a-z0-9_]{1,32}$',
                                                                       'maxLength': 32}}}}}},
    'device_cmd': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/device_cmd',
     'title': 'device_cmd',
     'description': 'One instruction from the host to a board.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['op'],
     'properties': {'op': {'enum': ['SET_CONFIG', 'SET_STREAM', 'SPEAK', 'CALIBRATE', 'REBOOT']},
                    'requestId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,16}$', 'maxLength': 16},
                    'key': {'type': 'string', 'pattern': '^[a-z0-9_]{1,15}/[a-z0-9_]{1,15}$', 'maxLength': 31},
                    'value': {'type': 'integer', 'minimum': -2147483648, 'maximum': 2147483647},
                    'stream': {'type': 'object',
                               'additionalProperties': False,
                               'required': ['mode'],
                               'properties': {'mode': {'type': 'integer', 'minimum': 0, 'maximum': 4},
                                              'host': {'type': 'string',
                                                       'pattern': '^[A-Za-z0-9.-]{1,64}$',
                                                       'maxLength': 64},
                                              'port': {'type': 'integer', 'minimum': 1, 'maximum': 65535},
                                              'durationS': {'type': 'integer', 'minimum': 1, 'maximum': 3600}}},
                    'text': {'type': 'string', 'minLength': 1, 'maxLength': 128}},
     'allOf': [{'if': {'properties': {'op': {'const': 'SET_CONFIG'}}}, 'then': {'required': ['key', 'value']}},
               {'if': {'properties': {'op': {'const': 'SET_STREAM'}}}, 'then': {'required': ['stream']}},
               {'if': {'properties': {'op': {'const': 'SPEAK'}}}, 'then': {'required': ['text']}}]},
    'event': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/event',
     'title': 'event',
     'description': 'Something the listener decided: a wake word, a command, a rejection, or an error code.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['deviceId', 'seq', 'kind'],
     'properties': {'deviceId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{4,32}$', 'maxLength': 32},
                    'seq': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'kind': {'enum': ['WAKE', 'COMMAND', 'REJECT', 'ERROR']},
                    'scorePermille': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
                    'marginPermille': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
                    'commandId': {'type': 'string', 'pattern': '^[a-z0-9_]{1,32}$', 'maxLength': 32},
                    'code': {'type': 'string', 'pattern': '^[A-Z0-9_]{1,32}$', 'maxLength': 32},
                    'doaDeg': {'type': 'integer', 'minimum': -1, 'maximum': 180},
                    'ts': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}},
     'allOf': [{'if': {'properties': {'kind': {'const': 'WAKE'}}}, 'then': {'required': ['scorePermille']}},
               {'if': {'properties': {'kind': {'const': 'COMMAND'}}},
                'then': {'required': ['commandId', 'scorePermille', 'marginPermille']}},
               {'if': {'properties': {'kind': {'const': 'REJECT'}}}, 'then': {'required': ['code']}},
               {'if': {'properties': {'kind': {'const': 'ERROR'}}}, 'then': {'required': ['code']}}]},
    'heartbeat': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/heartbeat',
     'title': 'heartbeat',
     'description': 'Health of a board every heartbeat_interval_s: memory, dropped frames, queue peaks.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['deviceId',
                  'fw',
                  'uptimeS',
                  'heapInternalFree',
                  'heapInternalMin',
                  'dmaOverflows',
                  'framesDropped',
                  'cleanDropped',
                  'eventsDropped'],
     'properties': {'deviceId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{4,32}$', 'maxLength': 32},
                    'fw': {'type': 'string', 'pattern': '^[0-9A-Za-z._+-]{1,32}$', 'maxLength': 32},
                    'models': {'type': 'string', 'pattern': '^[0-9A-Za-z._+-]{1,32}$', 'maxLength': 32},
                    'uptimeS': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'heapInternalFree': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'heapInternalMin': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'heapPsramFree': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'heapPsramMin': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'dmaOverflows': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'framesDropped': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'cleanDropped': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'eventsDropped': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'streamDropped': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'qCleanPeak': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                    'jsonArenaPeak': {'type': 'integer', 'minimum': 0, 'maximum': 262144},
                    'rssiDbm': {'type': 'integer', 'minimum': -127, 'maximum': 0},
                    'coreLoadPct': {'type': 'array',
                                    'maxItems': 2,
                                    'items': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
                    'ts': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}}},
    'ota_manifest': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/ota_manifest',
     'title': 'ota_manifest',
     'description': 'Where to fetch a firmware or model image and how to check it.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['kind', 'version', 'url', 'sha256', 'sizeBytes'],
     'properties': {'kind': {'enum': ['FIRMWARE', 'MODELS']},
                    'version': {'type': 'string', 'pattern': '^[0-9A-Za-z._+-]{1,32}$', 'maxLength': 32},
                    'url': {'type': 'string', 'pattern': '^https?://[!-~]{1,248}$', 'maxLength': 256},
                    'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$', 'maxLength': 64},
                    'sizeBytes': {'type': 'integer', 'minimum': 1, 'maximum': 4294967295}}},
    'responses': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/responses',
     'title': 'responses',
     'description': 'Spoken replies by id; baked into LittleFS, not an MQTT payload.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['version', 'responses'],
     'properties': {'version': {'type': 'integer', 'minimum': 1, 'maximum': 2147483647},
                    'responses': {'type': 'array',
                                  'minItems': 1,
                                  'maxItems': 64,
                                  'items': {'type': 'object',
                                            'additionalProperties': False,
                                            'required': ['id', 'text'],
                                            'properties': {'id': {'type': 'string',
                                                                  'pattern': '^[a-z0-9_]{1,32}$',
                                                                  'maxLength': 32},
                                                           'text': {'type': 'string',
                                                                    'minLength': 1,
                                                                    'maxLength': 128}}}}}},
    'status': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/status',
     'title': 'status',
     'description': 'Retained online state of a board; the broker publishes the OFFLINE copy as its will.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['deviceId', 'state'],
     'properties': {'deviceId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{4,32}$', 'maxLength': 32},
                    'state': {'enum': ['ONLINE', 'OFFLINE']},
                    'fw': {'type': 'string', 'pattern': '^[0-9A-Za-z._+-]{1,32}$', 'maxLength': 32}}},
    'telemetry': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
     '$id': 'sr/telemetry',
     'title': 'telemetry',
     'description': 'Front-end figures sampled every 100 ms and sent in batches of telemetry_samples.',
     'type': 'object',
     'additionalProperties': False,
     'required': ['deviceId', 'seq', 'state', 'samples'],
     'properties': {'deviceId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{4,32}$', 'maxLength': 32},
                    'seq': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                    'state': {'enum': ['LISTEN', 'COMMAND', 'REPLY']},
                    'samples': {'type': 'array',
                                'minItems': 1,
                                'maxItems': 10,
                                'items': {'type': 'object',
                                          'additionalProperties': False,
                                          'required': ['doaDeg', 'doaConf', 'vad', 'levelDbfs', 'gainDb'],
                                          'properties': {'doaDeg': {'type': 'integer',
                                                                    'minimum': -1,
                                                                    'maximum': 180},
                                                         'doaConf': {'type': 'integer',
                                                                     'minimum': 0,
                                                                     'maximum': 255},
                                                         'vad': {'type': 'boolean'},
                                                         'levelDbfs': {'type': 'integer',
                                                                       'minimum': -128,
                                                                       'maximum': 0},
                                                         'gainDb': {'type': 'integer',
                                                                    'minimum': -40,
                                                                    'maximum': 40}}}}}},
}
