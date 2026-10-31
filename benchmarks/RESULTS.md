# Benchmark results

| metric | value |
|---|---|
| cases | 3 |
| ground-truth rejected/deferred items | 8 |
| recovered | 5 |
| missed | 3 |
| recall | 0.625 |
| recall (items actually raised in the discussion) | 0.714 |
| precision | 0.8 |
| F1 | 0.702 |
| negative claims matching no ground truth | 1 |
| correct rejection reasons recovered | 3 |
| ...of which backed by verified evidence | 3 |
| evidence verification rate | 0.86 |
| accepted/open status checks passed | 3/3 |

## pep-0758

model: `ollama:qwen3:4b`, alternatives extracted: 6

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| 758-r1: Allow mixing parenthesized and unparenthesized exception types in one except clause | no | no | - | - | no | no |
| 758-d1: Allow unparenthesized exception lists when the 'as' clause is used | yes | no | - | - | no | no |

Negative claims with no ground-truth match (review manually; may be real ideas the PEP omits):
- alt-3 [rejected] Backport to Python 3.12/13

## pep-0768

model: `ollama:qwen3:4b`, alternatives extracted: 8

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| 768-r1: Debugger writes Python source code (instead of a path to a script file) into the remote buffer | yes | yes | complement | accepted | yes | yes |
| 768-r2: Use a single shared runtime-level buffer for all debugger communication instead of per-thread buffers | yes | no | - | - | no | no |

## synthetic-config-format

model: `ollama:qwen3:4b`, alternatives extracted: 7

| item | in discussion | recovered | via | status | reason | reason verified |
|---|---|---|---|---|---|---|
| syn-r1: Dedicated YAML configuration file | yes | yes | direct | rejected | yes | yes |
| syn-r2: INI file via configparser | yes | yes | direct | rejected | yes | yes |
| syn-w1: Python configuration file (quillfmt_config.py) | yes | yes | direct | withdrawn | no | no |
| syn-d1: Standalone .quillfmt.toml file | yes | yes | direct | deferred | no | no |

Status checks:
- syn-a1: expected ['accepted'], found ['accepted'] -> ok
- syn-u1: expected ['unresolved', 'proposed'], found ['unresolved'] -> ok
- syn-u2: expected ['unresolved', 'proposed'], found ['proposed'] -> ok

