# GENERATED FILE - DO NOT EDIT.
# Source: contracts/mqtt_topics.yaml
# Regenerate: python3 tools/gen_contracts.py

from dataclasses import dataclass

DEVICE_ID_MAX = 32
HEARTBEAT_INTERVAL_S = 30
TELEMETRY_PERIOD_MS = 1000
TELEMETRY_SAMPLES = 10


@dataclass(frozen=True)
class Topic:
    id: str
    path: str
    direction: str
    qos: int
    retain: bool
    schema: str
    will: bool

    def build(self, device_id: str) -> str:
        return self.path.replace("{deviceId}", device_id)


TOPICS = (
    Topic("status", "sr/{deviceId}/up/status", "up", 1, True, "status", True),
    Topic("heartbeat", "sr/{deviceId}/up/heartbeat", "up", 0, False, "heartbeat", False),
    Topic("telemetry", "sr/{deviceId}/up/telemetry", "up", 0, False, "telemetry", False),
    Topic("event", "sr/{deviceId}/up/event", "up", 1, False, "event", False),
    Topic("cmd", "sr/{deviceId}/down/cmd", "down", 1, False, "device_cmd", False),
    Topic("commands", "sr/{deviceId}/down/commands", "down", 1, True, "command_set", False),
    Topic("ota", "sr/{deviceId}/down/ota", "down", 1, False, "ota_manifest", False),
)
BY_ID = {t.id: t for t in TOPICS}


def match(topic: str) -> tuple[Topic, str] | None:
    for t in TOPICS:
        prefix, suffix = t.path.split("{deviceId}")
        if topic.startswith(prefix) and topic.endswith(suffix) and len(topic) > len(prefix) + len(suffix):
            return t, topic[len(prefix) : len(topic) - len(suffix)]
    return None
