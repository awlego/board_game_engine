"""
Cross-language parity tests for the Shards of Creation port.

Each trace in tests/parity/traces/ was produced by gen_traces.mjs running a
full random-driver game against the ORIGINAL JavaScript engine. This test
replays the recorded actions through the Python port and requires the full
state (canonical JSON, sorted keys, exact log strings) to match after every
single action. It also verifies that enumerate_actions() produces the same
action list (count + chosen index) the JS driver saw, so the adapter's
get_valid_actions is parity-checked too.

Regenerate traces with: node tests/parity/gen_traces.mjs
"""

import glob
import gzip
import json
import os

import pytest

from server.shards.engine import apply_action, create_game, enumerate_actions, view_for

TRACE_DIR = os.path.join(os.path.dirname(__file__), "traces")
TRACES = sorted(glob.glob(os.path.join(TRACE_DIR, "*.json.gz")))


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def first_divergence(actual, expected, path="$"):
    """Return a human-readable path to the first difference between two
    JSON-like structures (dicts/lists/scalars)."""
    if type(actual) is not type(expected):
        return f"{path}: type {type(actual).__name__} != {type(expected).__name__} ({actual!r} vs {expected!r})"
    if isinstance(actual, dict):
        for k in sorted(set(actual) | set(expected)):
            if k not in actual:
                return f"{path}.{k}: missing in Python state (JS has {expected[k]!r})"
            if k not in expected:
                return f"{path}.{k}: extra in Python state ({actual[k]!r})"
            d = first_divergence(actual[k], expected[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(actual, list):
        for i in range(min(len(actual), len(expected))):
            d = first_divergence(actual[i], expected[i], f"{path}[{i}]")
            if d:
                return d
        if len(actual) != len(expected):
            return f"{path}: length {len(actual)} != {len(expected)}"
        return None
    if actual != expected:
        return f"{path}: {actual!r} != {expected!r}"
    return None


def assert_states_equal(py_state, js_state, label):
    if canon(py_state) != canon(js_state):
        pytest.fail(f"{label}: {first_divergence(py_state, js_state)}")


@pytest.mark.parametrize("trace_path", TRACES, ids=[os.path.basename(p) for p in TRACES])
def test_trace_replays_identically(trace_path):
    with gzip.open(trace_path, "rt") as f:
        trace = json.load(f)

    state = create_game(
        player_ids=trace["playerIds"],
        player_names=trace["playerNames"],
        shard_ids=trace["shardIds"],
        seed=trace["seed"],
    )
    assert_states_equal(state, trace["initial"], "initial state")

    for i, step in enumerate(trace["steps"]):
        actions = enumerate_actions(state, step["actor"])
        assert len(actions) == step["nActions"], (
            f"step {i}: Python enumerates {len(actions)} actions, JS driver saw {step['nActions']}"
        )
        assert canon(actions[step["idx"]]) == canon(step["action"]), (
            f"step {i}: action at index {step['idx']} differs: "
            f"{actions[step['idx']]!r} vs {step['action']!r}"
        )
        state = apply_action(state, step["actor"], step["action"])
        assert_states_equal(state, step["state"], f"step {i} ({step['action']['type']})")
        # Some traces also snapshot the per-player redacted views (viewFor
        # parity — the React client is built against this contract).
        if "views" in step:
            for seat, js_view in enumerate(step["views"]["players"]):
                assert_states_equal(view_for(state, seat), js_view, f"step {i} view_for seat {seat}")
            assert_states_equal(view_for(state, None), step["views"]["spectator"], f"step {i} spectator view")

    assert state["phase"] == "gameOver"


def test_trace_coverage():
    """The trace set must cover every player count and all 8 shards."""
    assert len(TRACES) >= 30, f"expected at least 30 traces, found {len(TRACES)}"
    player_counts = set()
    shards = set()
    for p in TRACES:
        with gzip.open(p, "rt") as f:
            trace = json.load(f)
        player_counts.add(trace["playerCount"])
        shards.update(trace["shardIds"])
    assert player_counts == {2, 3, 4}
    assert shards == {"autonomy", "cultivation", "devotion", "dominion",
                      "honor", "odium", "preservation", "ruin"}
