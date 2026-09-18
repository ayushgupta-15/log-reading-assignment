# Log Reading Assignment

Investigation of a production incident where users could place orders that never
appeared in the system. Correlates `web.log` (HTTP requests) with `worker.log`
(async job processing) to find the root cause.

## Contents

- `ANSWERS.md` — answers to the assignment questions, with supporting evidence,
  and a description of the investigation process.
- `analyze.py` — script used to parse and correlate the two log files.
- `Log_Reading_Assignment_v2.pdf` — original assignment prompt.

Log files (`web.log`, `worker.log`) are not included, per the assignment
instructions.

## Usage

```
python3 analyze.py web.log worker.log
```
