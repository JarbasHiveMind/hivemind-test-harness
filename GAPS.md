# Known red cells

This file lists each Tests workflow job that is known to be red, why, and who
owns the fix. Remove a row when its job goes green.

| Job | Test | Cause | Owner | Unblocked by |
| --- | --- | --- | --- | --- |
| micropython-e2e | all 4 tests in `tests/test_micropython_e2e.py` | hivemind-micropython-client `dev` does not start the Noise handshake on a hivemind-core 5.x parameter message; the client stays in state 2 after HELLO | hivemind lane (JarbasHiveMind/hivemind-micropython-client) | hivemind-micropython-client#22 merged |

## Consumer defects seen in the e2e shards that do not turn a test red

| Where | Log line | Owner | Task |
| --- | --- | --- | --- |
| ovos-e2e (4), `TestEdgeCases::test_empty_utterance_list` | `persona.openvoiceos - ERROR - list index out of range` from `ovos_persona/__init__.py:621` (`message.data.get("utterances")[0]` on `[]`) | persona lane (OpenVoiceOS/ovos-persona) | T-1340 |
| ovos-e2e (6), `tests/test_e2e_scheduler.py` before this change | `ovos.scheduler.schedule refused: in.seconds must be a number > 0` for `schedule_event(handler, 0)`. ovos-workshop accepts `when=0` and fails only at runtime | backcompat lane (OpenVoiceOS/ovos-workshop) | T-1341 |
