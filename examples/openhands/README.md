# OpenHands Integration

OpenHands runs tools through its `BashSession` runtime. Install
snipp globally and create an event listener that compresses
`CmdOutputObservation` payloads before they hit context.

```python
# openhands_plugin/snipp_filter.py
from openhands.events.observation import CmdOutputObservation
from snipp import compress_output

def filter_cmd_output(obs: CmdOutputObservation) -> CmdOutputObservation:
    if len(obs.content) < 4000:
        return obs
    result = compress_output(
        obs.content,
        command=obs.command or "",
        model="gpt-4o",
    )
    obs.content = result.compressed
    obs.metadata = {**(obs.metadata or {}), "snipp": result.fidelity}
    return obs
```

Wire it in via OpenHands' `event_stream` listener registry.
