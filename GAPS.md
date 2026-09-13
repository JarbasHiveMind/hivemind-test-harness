# Known red cells

This file lists each Tests workflow job that is known to be red, why, and who
owns the fix. Remove a row when its job goes green.

| Job | Test | Cause | Owner | Unblocked by |
| --- | --- | --- | --- | --- |
| none | | | | |

## Consumer defects seen in the e2e shards that do not turn a test red

| Where | Log line | Owner | Task |
| --- | --- | --- | --- |
| ovos-e2e (4), `TestEdgeCases::test_empty_utterance_list` | `persona.openvoiceos - ERROR - list index out of range` from `ovos_persona/__init__.py:621` (`message.data.get("utterances")[0]` on `[]`) | persona lane (OpenVoiceOS/ovos-persona) | T-1340 |
| ovos-e2e (6), `tests/test_e2e_scheduler.py` before this change | `ovos.scheduler.schedule refused: in.seconds must be a number > 0` for `schedule_event(handler, 0)`. ovos-workshop accepts `when=0` and fails only at runtime | backcompat lane (OpenVoiceOS/ovos-workshop) | T-1341 |
