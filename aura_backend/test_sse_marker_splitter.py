import unittest
from services.sse_marker_splitter import MarkerStreamSplitter


def run(chunks):
    """Feeds chunks through a fresh splitter and returns the full event list."""
    splitter = MarkerStreamSplitter()
    events = []
    for c in chunks:
        events.extend(splitter.feed(c))
    events.extend(splitter.finish())
    return events


class TestMarkerStreamSplitter(unittest.TestCase):
    def test_plain_text_no_marker(self):
        events = run(["Hello ", "world"])
        self.assertEqual(events, [("token", {"text": "Hello "}), ("token", {"text": "world"})])

    def test_marker_in_single_chunk(self):
        events = run(['Hi there[ACTIONS][{"type": "add_task"}]'])
        self.assertEqual(events[0], ("token", {"text": "Hi there"}))
        self.assertEqual(events[1], ("tool_calls", {"actions": [{"type": "add_task"}]}))

    def test_marker_split_across_two_chunks(self):
        # The exact failure mode this class exists to fix: a chunk boundary
        # lands in the middle of the literal "[ACTIONS]" string.
        events = run(["Done. [ACTI", 'ONS][{"type": "add_task"}]'])
        self.assertEqual(events[0], ("token", {"text": "Done. "}))
        self.assertEqual(events[1], ("tool_calls", {"actions": [{"type": "add_task"}]}))

    def test_marker_split_one_char_at_a_time(self):
        text = 'Done.[ACTIONS][{"type": "x"}]'
        events = run(list(text))  # feed one character per chunk
        token_text = "".join(e[1]["text"] for e in events if e[0] == "token")
        self.assertEqual(token_text, "Done.")
        tool_calls = [e for e in events if e[0] == "tool_calls"]
        self.assertEqual(tool_calls, [("tool_calls", {"actions": [{"type": "x"}]})])

    def test_actions_json_split_across_chunks(self):
        events = run(["Done.[ACTIONS][{\"type\":", ' "add_task"}]'])
        self.assertEqual(events[0], ("token", {"text": "Done."}))
        self.assertEqual(events[1], ("tool_calls", {"actions": [{"type": "add_task"}]}))

    def test_no_actions_marker_ever_appears(self):
        events = run(["Just a plain ", "conversational reply."])
        self.assertEqual(
            events,
            [("token", {"text": "Just a plain "}), ("token", {"text": "conversational reply."})],
        )

    def test_invalid_actions_json_yields_error_not_crash(self):
        events = run(["Done.[ACTIONS]not valid json"])
        self.assertEqual(events[0], ("token", {"text": "Done."}))
        self.assertEqual(events[1][0], "error")
        self.assertIn("Failed to parse", events[1][1]["message"])

    def test_marker_at_very_start(self):
        events = run(['[ACTIONS][{"type": "add_task"}]'])
        # No visible text before the marker at all.
        self.assertEqual(events, [("tool_calls", {"actions": [{"type": "add_task"}]})])

    def test_text_ending_in_bracket_that_is_not_a_marker(self):
        # "text[" alone must not be held back forever if the stream ends
        # right there with no marker ever completing. May arrive as more
        # than one token event (the trailing "[" is held back pending a
        # possible marker start, then flushed at finish()), but every
        # event must be a "token" and the concatenated text must round-trip.
        events = run(["Some text["])
        self.assertTrue(all(e[0] == "token" for e in events))
        self.assertEqual("".join(e[1]["text"] for e in events), "Some text[")

    def test_empty_stream(self):
        self.assertEqual(run([]), [])
