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
