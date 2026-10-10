# Model lstm-ae-20261010-185148-be7522

Trained on 5995 normal sequences; tested on 6102 normal and 1079 attack windows from an unseen simulated population.

| Metric | lstm_ae | IsolationForest |
|---|---|---|
| fpr | 0.00492 | 0.00279 |
| precision | 0.9689 | 0.9682 |
| recall | 0.8656 | 0.4801 |
| f1 | 0.9143 | 0.6419 |
| roc_auc | 0.9719 | 0.9448 |

Rules alone: false-positive rate 0.0 on normal windows.

| Attack | windows | rules recall | AE recall | rules+AE recall | caught only by AE |
|---|---|---|---|---|---|
| bruteforce | 120 | 1.0 | 1.0 | 1.0 | 0 |
| enumeration | 120 | 0.9917 | 1.0 | 1.0 | 1 |
| exfiltration | 120 | 1.0 | 1.0 | 1.0 | 0 |
| odd-client | 120 | 0.0 | 1.0 | 1.0 | 120 |
| scanner | 120 | 1.0 | 1.0 | 1.0 | 0 |
| slow-bruteforce | 120 | 0.0 | 1.0 | 1.0 | 120 |
| stealth-bruteforce | 120 | 0.0083 | 0.9333 | 0.9333 | 111 |
| stealth-enumeration | 120 | 0.0 | 0.8167 | 0.8167 | 98 |
| stealth-exfiltration | 119 | 0.0504 | 0.0336 | 0.084 | 4 |

| Attack | episodes | detected (rules+AE) | median windows to detect |
|---|---|---|---|
| enumeration | 6 | 6 | 1.0 |
| odd-client | 6 | 6 | 1.0 |
| exfiltration | 6 | 6 | 1.0 |
| bruteforce | 6 | 6 | 1.0 |
| stealth-enumeration | 6 | 6 | 2.0 |
| scanner | 6 | 6 | 1.0 |
| slow-bruteforce | 6 | 6 | 1.0 |
| stealth-exfiltration | 6 | 6 | 9.5 |
| stealth-bruteforce | 6 | 6 | 1.0 |
