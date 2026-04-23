# Idle Time Insertion

(코드에 쓰인 기호와 paper pseudocode 기호 다름에 주의)

- For machine $l$ in the last stage:
  - $j$ := the number of jobs in the machine in the given schedule
  - While $j > 0$:
    - Construct a block of jobs $S_M$ starting from job $\gamma_{j, l}$
    - if there is a job to the right of $S_M$:
      - Calculate idle time $\Delta_2$
    - else:
      - $\Delta_2 := \infty$
    - Generate $S_E$, $S_D$ and $S_T$ from $S_M$
    - if $\sum_{j\in S_E} w'_j > \sum_{j\in S_T} w_j$:
      - Calculate $\Delta_1 = \min\{\min_{j\in S_E}\{E_j\}, min_{j\in S_D} \{d^{+}_j - C_j\}\}$
      - Insert $\Delta = \min\{\Delta_1, \Delta_2\}$ time units before $S_M$
      - Set $C_{m,k} = C_{m,k} + \Delta, \forall k\in S_M$
      - Set $E_k = E_k - \Delta, \forall k\in S_E$
      - Set $T_k = T_k + \Delta, \forall k\in S_T$
    - else:
      - $j := j-1$
  - EndWhile
- EndFor
