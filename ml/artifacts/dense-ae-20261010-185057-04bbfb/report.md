# Model dense-ae-20261010-185057-04bbfb

Trained on 6608 normal windows; tested on 6732 normal and 1079 attack windows from an unseen simulated population.

| Metric | dense_ae | IsolationForest |
|---|---|---|
| fpr | 0.00416 | 0.00357 |
| precision | 0.9667 | 0.9557 |
| recall | 0.7535 | 0.4801 |
| f1 | 0.8469 | 0.6391 |
| roc_auc | 0.962 | 0.9399 |

Rules alone: false-positive rate 0.0 on normal windows.

| Attack | windows | rules recall | AE recall | rules+AE recall | caught only by AE |
|---|---|---|---|---|---|
| bruteforce | 120 | 1.0 | 1.0 | 1.0 | 0 |
| enumeration | 120 | 0.9917 | 1.0 | 1.0 | 1 |
| exfiltration | 120 | 1.0 | 1.0 | 1.0 | 0 |
| odd-client | 120 | 0.0 | 1.0 | 1.0 | 120 |
| scanner | 120 | 1.0 | 1.0 | 1.0 | 0 |
| slow-bruteforce | 120 | 0.0 | 1.0 | 1.0 | 120 |
| stealth-bruteforce | 120 | 0.0083 | 0.1833 | 0.1833 | 21 |
| stealth-enumeration | 120 | 0.0 | 0.55 | 0.55 | 66 |
| stealth-exfiltration | 119 | 0.0504 | 0.042 | 0.0924 | 5 |

| Attack | episodes | detected (rules+AE) | median windows to detect |
|---|---|---|---|
| enumeration | 6 | 6 | 1.0 |
| odd-client | 6 | 6 | 1.0 |
| exfiltration | 6 | 6 | 1.0 |
| bruteforce | 6 | 6 | 1.0 |
| stealth-enumeration | 6 | 6 | 1.0 |
| scanner | 6 | 6 | 1.0 |
| slow-bruteforce | 6 | 6 | 1.0 |
| stealth-exfiltration | 6 | 6 | 1.0 |
| stealth-bruteforce | 6 | 6 | 1.0 |
