#!/usr/bin/env python3
"""Does the turn cap degrade into a shallower review, or lose it?

Exercised with a fake client: the interesting part is the plumbing — mirroring
the conversation, noticing no findings were written, and asking once more
without tools. None of that needs a real model, so none of it needs paying for.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review  # noqa: E402
from models import ReviewResult  # noqa: E402


class FakeUsage:
    input_tokens = 100
    output_tokens = 10
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class FakeBlock:
    def __init__(self, type_):
        self.type = type_


class FakeMessage:
    def __init__(self, parsed=None, tool_use=True):
        self.content = [FakeBlock("tool_use" if tool_use else "text")]
        self.usage = FakeUsage()
        self.stop_reason = "tool_use" if tool_use else "end_turn"
        self.stop_details = None
        self.parsed_output = parsed


class FakeTurn:
    def __init__(self, message):
        self._m = message

    def get_final_message(self):
        return self._m


class FakeRunner:
    """Two turns of tool use and then nothing — the cap landing mid-exploration."""
    def __init__(self, turns):
        self._turns = turns
        self.responses = 0

    def __iter__(self):
        for m in self._turns:
            yield FakeTurn(m)

    def generate_tool_call_response(self):
        self.responses += 1
        return {"role": "user", "content": [{"type": "tool_result",
                                             "tool_use_id": "t", "content": "..."}]}


class FakeClient:
    def __init__(self, parsed_from_wrapup):
        self.parsed_from_wrapup = parsed_from_wrapup
        self.wrapup_messages = None
        self.beta = types.SimpleNamespace(
            messages=types.SimpleNamespace(tool_runner=self._runner))
        self.messages = types.SimpleNamespace(parse=self._parse)

    def _runner(self, **kw):
        return FakeRunner([FakeMessage(), FakeMessage()])

    def _parse(self, **kw):
        self.wrapup_messages = kw["messages"]
        return types.SimpleNamespace(parsed_output=self.parsed_from_wrapup,
                                     usage=FakeUsage())


def test_cap_falls_back_to_a_wrap_up_call():
    rescued = ReviewResult(
        summary="partial", findings=[],
        files_reviewed=[{"path": "a.ts", "verdict": "not-reviewed"}])
    client = FakeClient(rescued)
    out = review.call_claude("the diff", client=client)
    # The review survived the cap.
    assert out["summary"] == "partial"
    assert out["files_reviewed"][0]["verdict"] == "not-reviewed"
    # And the wrap-up saw the whole conversation, not just the last turn.
    msgs = client.wrapup_messages
    assert msgs[0]["content"] == "the diff", "the original prompt must be first"
    assert sum(1 for m in msgs if m["role"] == "assistant") == 2
    assert "Stop reading and report now" in msgs[-1]["content"]


def test_no_output_even_after_wrap_up_is_an_error_not_a_clean_review():
    client = FakeClient(None)
    try:
        review.call_claude("the diff", client=client)
    except SystemExit as e:
        assert "wrap-up" in str(e)
    else:
        raise AssertionError("a missing review must fail loudly, not read as clean")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
