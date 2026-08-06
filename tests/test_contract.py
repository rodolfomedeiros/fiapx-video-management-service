import json
from pathlib import Path

def test_video_event_contract_is_valid_json():
    contract = Path(__file__).parents[2] / "fiapx-platform" / "contracts" / "video-event.schema.json"
    data = json.loads(contract.read_text())
    assert "video.received" in data["properties"]["event_type"]["enum"]
