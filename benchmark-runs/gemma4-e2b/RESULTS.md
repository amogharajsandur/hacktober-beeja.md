# Benchmark results

| metric | value |
|---|---|
| cases | 3 |
| ground-truth rejected/deferred items | 8 |
| recovered | 1 |
| missed | 7 |
| recall | 0.125 |
| recall (items actually raised in the discussion) | 0.143 |
| precision | 1.0 |
| F1 | 0.222 |
| negative claims matching no ground truth | 0 |
| correct rejection reasons recovered | 0 |
| ...of which backed by verified evidence | 0 |
| evidence verification rate | 0.586 |
| accepted/open status checks passed | 2/3 |

## pep-0758

model: `ollama:gemma4:e2b`, alternatives extracted: 5

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| 758-r1: Allow mixing parenthesized and unparenthesized exception types in one except clause | no | no | - | - | no | no |
| 758-d1: Allow unparenthesized exception lists when the 'as' clause is used | yes | no | - | - | no | no |

## pep-0768

model: `ollama:gemma4:e2b`, alternatives extracted: 8

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| 768-r1: Debugger writes Python source code (instead of a path to a script file) into the remote buffer | yes | yes | direct | rejected | no | no |
| 768-r2: Use a single shared runtime-level buffer for all debugger communication instead of per-thread buffers | yes | no | - | - | no | no |

## synthetic-config-format

model: `ollama:gemma4:e2b`, alternatives extracted: 7

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| syn-r1: Dedicated YAML configuration file | yes | no | - | - | no | no |
| syn-r2: INI file via configparser | yes | no | - | - | no | no |
| syn-w1: Python configuration file (quillfmt_config.py) | yes | no | - | - | no | no |
| syn-d1: Standalone .quillfmt.toml file | yes | no | - | - | no | no |

Status checks:
- syn-a1: expected ['accepted'], found ['proposed'] -> FAIL
- syn-u1: expected ['unresolved', 'proposed'], found ['unresolved'] -> ok
- syn-u2: expected ['unresolved', 'proposed'], found ['proposed'] -> ok
