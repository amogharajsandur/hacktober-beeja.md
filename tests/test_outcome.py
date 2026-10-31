from beeja.extract import extract
from beeja.ingest import from_text
from beeja.outcome import related_messages
from tests.test_extract import alt

THREAD = from_text(
    "# Debugger buffer design\n"
    "[p1] pablo (author): We propose a per-thread buffer for debugger scripts.\n"
    "[p2] sam: Why not one global scratch buffer shared by all threads? It saves memory.\n"
    "[p3] eve: Unrelated note about docs.\n"
    "[p4] pablo (author): A single shared buffer would force serialization of all debugging operations, "
    "so we will keep per-thread buffers.\n",
    "t",
)
# reply links as Discourse would provide them
THREAD.messages[3].reply_to = "p2"


def test_related_messages_follow_replies_and_skip_unrelated():
    ids = [m.message_id for m in related_messages(THREAD, {"p2"}, "Single global scratch buffer")]
    assert ids == ["p2", "p4"]


def test_outcome_pass_recovers_rejection_from_reply(scripted):
    chunk = {"question": "q", "alternatives": [
        alt("Single global scratch buffer", "unresolved", proposed_in="p2"),
        alt("Per-thread buffer", "accepted", "p4", "we will keep per-thread buffers", proposed_in="p1"),
    ]}
    outcome = {"explanation": "author rejects it", "status": "rejected", "status_message_id": "p4",
               "status_quote": "A single shared buffer would force serialization of all debugging operations",
               "reasons": [{"text": "forces serialization", "message_id": "p4",
                            "quote": "would force serialization of all debugging operations"}]}
    prov = scripted([chunk, outcome])
    rec = extract(THREAD, prov, support_check=False)
    a = rec.alternatives[0]
    assert a.status == "rejected" and a.reasons and a.reasons[0].supported
    assert rec.metadata["outcome_pass_calls"] == 1  # the accepted one was not re-examined
    assert "p3" not in prov.prompts[1]


def test_outcome_pass_cannot_invent_outcome(scripted):
    chunk = {"question": "q", "alternatives": [alt("Single global scratch buffer", "unresolved", proposed_in="p2")]}
    outcome = {"explanation": "", "status": "rejected", "status_message_id": "p4",
               "status_quote": "The steering council vetoed global buffers forever", "reasons": []}
    rec = extract(THREAD, scripted([chunk, outcome]), support_check=False)
    assert rec.alternatives[0].status == "unresolved"


def test_message_order_independent_of_hash_seed():
    """Regression: ordering must follow the discussion, not set iteration (PYTHONHASHSEED)."""
    import os
    import subprocess
    import sys

    code = ("from tests.test_outcome import THREAD; from beeja.outcome import related_messages; "
            "print([m.message_id for m in related_messages(THREAD, {'p2', 'p4'}, 'Single global scratch buffer')])")
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": str(seed)}).stdout.strip()
        for seed in range(8)
    }
    assert outs == {"['p2', 'p4']"}
