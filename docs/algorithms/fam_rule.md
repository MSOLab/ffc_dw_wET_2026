# FAM rule

Let us suppose we have a solution represented by a permutation of jobs $\pi= ( \pi_1 , \pi_2 , . . . , \pi_n )$,
where $\pi_j$ denotes the job occupying position $j$ in the permutation.
At the first stage we have to decide to which machine we assign job $\pi_1$.
Since all machines are free, we assign it to machine 1 at the first stage.
Then jobs $\pi_2$ through $\pi_n$ are assigned to the machine that is available at the earliest time.
When all jobs have been completed at stage 1 we proceed with stage 2 through stage $m$.
Note that jobs can finish at different times so instead of using permutation $\pi$
to decide the launch order of jobs for the remaining stages
we order the jobs according to their completion times at the previous stage.
Therefore, $\pi^{(i)} = (\pi^{(i)}_{1}, \pi^{(i)}_{2}, ... ,\pi^{(i)}_{n} )$ is the permutation
that indicates the order in which the jobs will be processed at stage i.
This order is obtained by sorting the jobs in ascending completion times at stage $i−1$, where $\pi^{(i)} = \pi$.
After the jobs are sorted, the FAM rule is applied again for machine assignment.
It is important to note that during sorting, ties might occur with jobs having the same completion time at a given stage.
To break ties, we favor the job with the smallest slack in the due date: $d^{+}_{j} − C_{i −1 ,j}$.
