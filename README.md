# RegressLab Java demonstration

A public, intentionally constructed event-aggregation workload for investigating
real executed Java performance. This is not production traffic. The baseline
precomputes category counts; the demonstration PR replaces cached queries with
scans. Both implementations return the same results on the correctness checks.

Two JMH 1.37 cases measure count-by-category queries and distinct-category queries
on 10,000 deterministic events across 128 categories. Java 21 is used. Trial setup
and dataset construction are excluded from measured queries; returned results are
consumed by JMH. Measurements do not include the memory/construction tradeoff.

The `pull_request` workflow checks out exact base/head SHAs, builds both versions,
and alternates arm order across three fork blocks. Each case/fork gets three
one-second warmups and five one-second measured iterations. A fresh JVM executes
each case. Both arms use the same runner/JDK/configuration, one thread and fixed
256 MiB heap. Shared hosted-runner noise is not eliminated by this protocol.

Every arm artifact contains `run.json` (RegressLab v2 measurements) and
`execution.json` (scheduled order, elapsed time, statuses and raw block values).
Failures and subprocess timeouts are explicit. Partial observations remain in the
execution ledger; failed runs are not presented as complete comparisons. Hard
runner termination/cancellation may prevent upload, and is visible on GitHub.

The workflow has only `contents: read`, pinned official actions, no persisted Git
credentials or caches, and no RegressLab/GitHub administration/database secrets.
Untrusted application builds/JVMs receive a sanitized environment. There is no
`pull_request_target` or privileged artifact-execution job.

A separate trusted administrator imports the downloaded archive into RegressLab.
The server checks GitHub metadata, archive digest and reviewed pipeline identities
and only parses bounded JSON; it never executes an artifact. Three forks give only
descriptive/insufficient evidence, not significance or a production regression claim.

Local correctness check: `mvn -B -ntp package` then
`java -cp target/test-classes:target/classes dev.regresslab.demo.EventStatisticsTest`.
The orchestrator requires genuine checked-out commit pairs and a PR event document.
