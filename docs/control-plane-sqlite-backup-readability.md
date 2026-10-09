# SQLite backup readability probe

The source/config LKG deliberately excludes persistent history.db. This separate
diagnostic checks that SQLite's online backup can produce a readable, consistent
in-memory copy without replacing a database or changing application rows.

Run only against an explicitly selected authorized database:
`python3 scripts/zcloud_sqlite_backup_probe.py --db /absolute/path/history.db`

The source opens with SQLite mode=ro and query_only. The copy is temporary and
exists only in memory; it is closed after quick_check, foreign_key_check, schema
fingerprinting and allowlisted table counts. No row values, arbitrary table names,
database paths, evidence, tokens, owner identities or SQL errors are published.
SQLite may use normal reader locking/WAL sidecars; this is not a promise that
every filesystem metadata byte remains unchanged.

Defaults bound the copy to 10,000 pages, 40 MiB and five seconds. The page callback
also bounds growth during the backup. A busy, oversized, corrupt or incomplete
database fails closed, without a destination file or a partial success report.
The time limit is cooperative between SQLite steps, not a hard process deadline.

A readable copy proves database readability only. It does not authorize a restore,
show that queued commands can safely be replayed, certify live worker ownership,
or release the serialized #580/#1089 production window. Do not replace history.db.
Production restore design, encrypted durable retention, consistency with external
state and command replay reconciliation remain separate review gates.

The dedicated VPS proof uses only temporary databases and real project_runtime
receipt/resource producers. It covers committed WAL data, uncommitted writer
isolation, expired-lease preservation, read-only behavior, bounded failure, schema
and foreign-key rejection, encoded filenames and output privacy. It never opens
the live database, changes queue state or calls a running service.

## VPS test-fixture dependency

Unmodified full-suite discovery can fail under real VPS memory pressure in two
existing autonomy dispatch tests. Their canonical isolation owner is PR #646,
head b734ab0216c2147eab056f5800606afd9cf7155b; this probe does not edit that owner.

The proof records the raw outcome and refuses any other failure or any error.
It then composes only the pinned owner's setup/teardown memory fixture into a
disposable copy of the candidate. AST comparison requires every current test
method to remain unchanged, including tests added after the owner branch.
The entire composed suite and dedicated memory-guard suite must pass.
The receipt distinguishes raw success from known host-memory failure and pins
both candidate and fixture-owner identities. This is composition evidence, not
an assertion that unmodified discovery passed. Integration still depends on
#646 and the serialized writer window; no production memory guard is weakened.

An exclusive rollback-journal writer is also covered: the probe returns a generic
failure while locked, then succeeds after the producer rolls back. Failed proof
steps emit an incomplete receipt when the runner remains available; process or
runner shutdown before artifact upload supplies no acceptance evidence. The raw
suite summary preserves only test IDs and counts, never exception payloads.
