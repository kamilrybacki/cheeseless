"""The demo recorder writes a valid asciicast v2 stream with real timestamps."""

import json

from demo.run_demo import Recorder, parse_cast


def test_cast_header_and_events_use_recorded_clock() -> None:
    ticks = iter([10.0, 10.0, 10.5])
    recorder = Recorder(clock=lambda: next(ticks), wall=lambda: 1_700_000_000)
    recorder.write("hello\n")
    recorder.write("world\n")

    data = recorder.to_asciicast()
    lines = data.splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] == 100
    assert header["height"] == 30
    assert header["timestamp"] == 1_700_000_000

    first = json.loads(lines[1])
    second = json.loads(lines[2])
    assert first == [0.0, "o", "hello\n"]
    assert second == [0.5, "o", "world\n"]


def test_parse_cast_round_trips_events() -> None:
    ticks = iter([0.0, 0.0])
    recorder = Recorder(clock=lambda: next(ticks), wall=lambda: 0)
    recorder.write("one\n")
    cast = parse_cast(recorder.to_asciicast())
    assert cast["width"] == 100
    assert cast["events"] == [[0.0, "o", "one\n"]]


def test_timestamps_are_non_decreasing() -> None:
    values = iter([5.0, 5.2, 5.2, 5.9])
    recorder = Recorder(clock=lambda: next(values), wall=lambda: 0)
    recorder.write("a\n")
    recorder.write("b\n")
    recorder.write("c\n")
    times = [event[0] for event in recorder.events]
    assert times == sorted(times)
