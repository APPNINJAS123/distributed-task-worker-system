# Benchmark results

Made by running python3 -m sdn.benchmark

## Experiment 1: scheduling (30 normal tasks)

| condition | scheduler | total time (s) | avg time per task (s) | tasks per worker |
|---|---|---|---|---|
| uneven loads 70/10/40 | round_robin | 10.14 | 5.21 | w1=10, w2=10, w3=10 |
| uneven loads 70/10/40 | least_loaded | 6.08 | 3.41 | w1=6, w2=16, w3=8 |
| equal loads 30/30/30 | round_robin | 6.1 | 3.42 | w1=10, w2=10, w3=10 |
| equal loads 30/30/30 | least_loaded | 6.09 | 3.4 | w1=10, w2=10, w3=10 |

## Experiment 2: routing (big sort tasks, 10 Mbit/s core links)

| tasks | routing | total time (s) | avg time per task (s) | packets via s2 | packets via s3 |
|---|---|---|---|---|---|
| 6 | static | 6.64 | 3.9 | 233 | 0 |
| 6 | dynamic | 4.76 | 2.71 | 131 | 140 |
| 12 | static | 10.71 | 5.13 | 375 | 0 |
| 12 | dynamic | 6.22 | 2.91 | 174 | 202 |
