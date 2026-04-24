# Eval Fixture Folders

Save your manual evaluation source files here before running `pytest`.

## Meeting exports

Put this app's exported session JSON files in:

`eval/fixtures/meeting_exports/`

Expected filename pattern:

`meeting-*.json`

## TwinMind transcripts

Put the `.txt` files you collected from TwinMind in:

`eval/fixtures/twinmind_txt/`

Any `.txt` filename is accepted.

The automated tests currently validate that:
- meeting export files can be converted into eval cases
- TwinMind transcript files exist and are non-empty

The side-by-side semantic comparison is still manual for now.
