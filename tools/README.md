# Small automations

## `capture_to_initiative.py`

Converts freeform capture (chat paste, voice dump, memo) into a structured initiative markdown file under `initiatives/`.

```bash
python3 tools/capture_to_initiative.py "Ship weekly market memo"
python3 tools/capture_to_initiative.py --title "X" --body-file notes.txt
echo "notes..." | python3 tools/capture_to_initiative.py --stdin --title "Y"
python3 -m unittest discover -s tools/tests -v
```
