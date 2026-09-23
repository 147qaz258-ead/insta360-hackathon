from app.server import RuntimeStore
from tactile.v4_frame import COLS, ROWS, publish_tactile_frame


def _frame(frame_id: int = 7):
    heights = [[0] * COLS for _ in range(ROWS)]
    regions = [[0] * COLS for _ in range(ROWS)]
    heights[5][9] = 173
    regions[5][9] = 1
    return publish_tactile_frame(
        {
            "version": 2,
            "device_id": "rdk_hdmi",
            "cols": COLS,
            "rows": ROWS,
            "height_encoding": "uint8",
            "height_rows": heights,
            "region_rows": regions,
            "regions": [
                {
                    "id": 1,
                    "name": "测试区域",
                    "description": "用于验证权威帧恢复。",
                    "speech": "这里是测试区域。",
                }
            ],
            "scene_summary": "持久化测试",
            "audio_overview": "当前是一张持久化测试触觉图。",
            "self_review": {"decision": "accept"},
        },
        frame_id,
    )


def test_runtime_store_restores_last_published_authoritative_frame(tmp_path):
    state_path = tmp_path / "runtime" / "current_frame.json"
    first = RuntimeStore(state_path)
    frame = _frame()
    first.publish("run-7", "data:image/png;base64,AA==", frame)
    first.runtime_event(state="READY", source="agent:run-7", run_id="run-7")

    restored = RuntimeStore(state_path)

    assert restored.frame.frame_id == 7
    assert restored.frame.checksum == frame.checksum
    assert restored.frame.height_rows[5][9] == 173
    assert restored.frame.region_at(5, 9)["name"] == "测试区域"
    assert restored.current_run_id == "run-7"
    assert restored.current_image_data_url == "data:image/png;base64,AA=="
    assert restored.motion_state == "READY"
    assert restored.runtime_snapshot()["current_run_available"] is False


def test_runtime_snapshot_marks_live_run_as_available(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.json")
    run_id = store.create_run()
    store.publish(run_id, "data:image/png;base64,AA==", _frame())

    snapshot = store.runtime_snapshot()

    assert snapshot["current_run_id"] == run_id
    assert snapshot["current_run_available"] is True

