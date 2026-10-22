# Remote shell ingestion: source-only no-new-debt gate (15 Oct 2026)

Scope: zCloud deploy-ops #633; proposed in [PR #1466](https://github.com/Zennay/zCloud/pull/1466). This is a **non-authorizing source tripwire**, not an enforcement mechanism for runtime or deployment.

## Protected pattern

A newly added or modified GitHub Actions workflow containing a literal `run:` command with `curl` or `wget` piped into `sh`, `bash`, `zsh`, `/bin/bash`, `/usr/bin/bash`, `sudo -E /usr/bin/bash` or `/usr/bin/env bash` is rejected. The same applies to `bash <(curl ...)`. The scanner reads committed Git objects, not a mutable working-tree file list. It requires exact immutable base/head commit SHA, valid ancestry, bounded blob sizes and no symlink/gitlink workflow entries. Changed previously risky workflows cannot reuse the historical exception merely by being relocated into a different job or privilege context.

An **unchanged** historical workflow with a matched risk remains counted but is not considered new debt. That exception is purely for gradual remediation; it does not establish safety.

## Run offline

```bash
python3 -m unittest discover -s tests -p 'test_zcloud_remote_shell_ratchet_20261015_w1.py' -v
python3 scripts/zcloud_remote_shell_ratchet_20261015_w1.py --repo . --base <exact-40-hex-main> --head <exact-40-hex-head>
```

Exit: **0** = complete source proof and no *new matched* risks; **1** = matched new risk; **2** = unknown or incomplete source proof. Output includes only numerical counts and all-false mutation/release/deployment flags. It deliberately never prints URLs, script bodies, environment variables or arbitrary Git errors.

## Limitations and gate

- **Not a full YAML parser or shell execution analyzer.** Escaped/aliased mapping keys, complex flow YAML, indirect `uses` actions, heredocs, variables, base64 payloads, script downloads to files, shell substitutions beyond the named pattern and alternate fetch tools may bypass this detector.
- A PR can change its own scanner or workflow. Running on a same-repository owner PR provides a test signal, **not tamperproof branch protection**. Independent diff review and protected merge conditions are required before reliance.
- GitHub-hosted checkout uses immutable PR head and does not persist credentials; the job has read-only repository permission. It neither contacts the VPS nor invokes any deploy/recovery/manual writer.
- A green source job is **not** proof that the VPS listener, worker generation, SQLite, Firefox, deploy or protected-main release gate works. Self-hosted runner #1326 and production writer locks #1447 have separate owners. Release decisions remain with #580 / PWQ-41 / #1089.
- No automatic merge or direct-main mutation. Keep draft pending independent review and exact-head full hosted regression.

Owners should review changed PR file inventory and current main before integration, and never treat these counters as permission to retry production.

## Extended admission coverage (15 October)

Source guard now inspects immutable Git objects for both `.github/workflows/*.yml` and repository-local `action.yml` / `action.yaml` composite entrypoints (including nested directories), so remote-shell pipelines introduced via `uses: ./local/action` are not silently omitted. Git symlinks under `.github/actions` and symlinked Action files are rejected. Source guard detects quoted `run` mapping keys, compact flow-map steps, folded YAML `run: >` blocks, direct remote scripts executed through absolute-path shell binaries, shell `-c "$(curl ...)"` and `sh -c '$(wget ...)'` forms. An opaque `run: *alias` or `run: !tag` is **not assumed safe**, even when no literal remote command appears at the use site. This is intentional conservative rejection of newly edited workflows, not proof that every alias is malicious.

GitHub-hosted admission additionally verifies the remote PR branch ref before **and after** the exact-Git-source comparison; a moved PR head invalidates the source result. The original SHA and immutable main are still checked. Those checks do not grant merge or deploy authority.

**Known limits:** full YAML key decoding (including escaped quoted keys and explicit `?` mappings), multiline flow-map parsing, templated scripts, downloaded intermediate files, third-party non-local actions, alternate download commands, unusual shell encodings and dynamically assembled code are not yet semantically modeled. Focused detection can produce false positives, and green scans must be reviewed alongside independent privileged-workflow checks. Separate #1464/#1465 owners handle interpolation and container-source trust; do not edit their files.

## Multiline quoted YAML admission

GitHub Actions `run: "..."` and `run: '...'` YAML scalars may continue on a later indented line; the command is folded with spaces. The scanner joins those quoted continuations before examining shell execution patterns. Unterminated or indentation-broken quoted `run` values cause fail-closed source-evidence rejection, not a clean result. Inline hashes inside a wholly quoted YAML scalar remain part of the command, while a whitespace-prefixed `#` in a plain (unquoted) YAML scalar starts a YAML comment and is excluded from the script source. This avoids false positives from commented-out download snippets without suppressing real quoted execution. This remains a **syntactic source check**, not a complete YAML parser or runtime verification.

## Bounded multi-stage shell ingestion and immutable directory integrity

A remote download can still be executed after a pass-through or decoding pipeline, for example `curl ... | tee payload.sh | bash` or `wget ... | base64 -d | /bin/sh`. The scanner now checks up to **two** explicit intermediates drawn from `tee`, `cat`, `sed`, `base64`, `tr`, `awk`, `gunzip` and `gzip`. A download saved to a local file without a shell consumer is not classified as direct ingestion. This is intentionally a bounded syntactic ratchet; more elaborate stages or execution from a downloaded file still require separate review.

Git-tree validation now refuses a symlink or gitlink replacing the exact `.github`, `.github/workflows`, or `.github/actions` directory, even when an unrelated composite Action exists and would otherwise make the source inventory nonempty. Existing per-file symlink checks remain intact. Nested Gitlinks (Git mode 160000) under `.github/actions/` and `.github/workflows/` are also denied: their child source cannot be authenticated from this repository's immutable Git objects. The scanner does not follow these links and never executes repository code during inventory.

**Coordination (15 October):** changed-filename checks of 12 active related deploy-ops PRs (56 paths total) found no direct collision with this PR's four own add-only paths. This is **not** a complete 450+-PR or PR-less-branch semantic audit and cannot grant independent review, protected-branch merge or production authorization.

## Dutch GitHub-hosted decision summary

The hosted-only PR job writes a **bounded Dutch operator summary** to the normal GitHub Actions step summary for both successful and rejected scans. The summary has only fixed wording and bounded counts. Its authoritative fields always remain `release_authorized=false`, `deployment_authorized=false`, and `mutation_performed=false`.

Before publishing the summary, source JSON is limited to **8 KiB**, parsed with duplicate JSON key rejection and rejection of `NaN` / infinities, and checked for complete/consistent schema, strict integer types, monotonic finding counts and scan exit status. Contradictory or forged data is **not** printed to the operator: the job emits a fixed warning and fails. Unavailable Git evidence is explicitly marked **Onvolledig bewijs**, never as approval. All report content stays local in GitHub-hosted temporary storage; no artifact upload, API write, personal information, workflow dispatch, runner execution or VPS mutation is added.

The scanner and summary are evidence only. A green result means no **new detected source risk within this narrow heuristic**, not verified shell safety, healthy zCloud workers, a recovered VPS listener or permission to merge/deploy.

## Additional remote interpreter execution exposure

Directly piped remote code is not restricted to shell interpreters. The same supply-chain risk applies to source fetched over a network and executed immediately by `python`/`python3` (including versioned Python binaries), `node`/`nodejs`, `ruby`, `perl`, `php` and `pypy`. The source ratchet now identifies these command consumers, including bounded `tee`/`base64` pipeline intermediates and absolute interpreter binaries under `/bin`, `/usr/bin` or `/usr/local/bin`. A read-only fetch piped into `jq` is not treated as interpreter execution.

This remains **heuristic detection only**. Encoded scripts, unsupported interpreters, shell aliases, downloaded source files executed later, Docker build steps and scripts generated from templates may escape detection. The extra coverage neither makes previous workflows safe nor authorizes live execution.

## Escaped and explicit YAML mapping-key admission

The narrowly parsed `run` mapping-key syntax is not a full YAML parser. YAML quoted mapping keys with `\\xNN`, `\\uNNNN` or `\\UNNNNNNNN` escapes may decode to `run` even when a source-text match cannot see it. YAML explicit (`? run`) keys pose a related ambiguity. Newly edited Actions source containing these unusual keys is conservatively classified as an **opaque-source finding** and denied; an unchanged legacy source is subject to the existing whole-file grandfathering rule. This may reject harmless unusual YAML as a false positive, but it avoids asserting safety without semantic evidence. It is *not* protection against all YAML encodings, aliases or scripted code generation.

Interpreter wrappers also include a bounded chain of `sudo`, `env`, `/usr/bin/env`, `timeout`, `nohup` or `command` around a source-consuming interpreter (including `env -S`, `sudo -E env -S`, and `timeout 30 bash`). An unrelated read-only command such as `curl ... | timeout 10 jq .` remains below the direct-code-execution threshold.

## Direct remote script standard-input redirection

Input redirection may feed downloaded source directly to an interpreter without a literal `curl | bash` pipe. The source check now flags shell here-strings such as `bash <<< "$(curl ...)"`, process substitution used as standard input such as `bash < <(wget ...)`, and Python standard-input execution such as `python3 - < <(curl ...)`. This only matches recognisable **direct** curl/wget-to-interpreter input forms, not every shell redirection, temporary file, asynchronous substitution or encoded source. Fetching remote text into a variable and only printing it is not classified as direct execution.

## YAML double-quoted script escapes: executable source is not raw Git text

The YAML scalar `run: "curl … \\u007c bash"` can decode the escaped `\\u007c` as `|` **before** the shell runs. Comparing raw source against only literal pipeline characters would silently miss that executable command. The gate now conservatively counts newly changed **double-quoted** `run` scalars containing control/Unicode/hex/backslash YAML escapes as opaque risk, including compact flow-map scripts. The ratchet does not decode or execute those values. The same backslash sequence inside a single-quoted YAML scalar is literal and does not trigger this particular rule.

This deliberately trades some false positives for no false-green claim on newly introduced escaped execution text. Unchanged legacy Git blobs remain grandfathered as before, but editing such a blob must clear the new risk. Exact Git fixture tests exercise `\\x7c`, `\\u007c`, `\\U0000007c`, quoted newlines, flow-map syntax, inert single-quoted text, an ordinary escaped quote, and legacy source identity. This **does not** prove YAML-wide or shell-wide equivalence: aliases, tags, alternate scalar encodings and runtime environment expansion still require independent review. No VPS or deploy authorization is inferred.

## Encoded flow-map run keys

A compact YAML step can encode a key such as `"r\u0075n"`, `"r\x75n"` or `"r\U00000075n"` inside a flow map. YAML then decodes that key to `run`, while a literal key search would miss the execution-bearing value. The source-only scanner now treats new/modified flow mappings with double-quoted escaped keys as opaque until independently reviewed. Single-quoted mappings preserve escape text literally and do not inherit this special risk. Three adversarial real-Git groups prove escaped variants, inert single-quoted controls and unchanged historical source identity. This is not a complete YAML parser; no release or runtime permission is inferred.

## Bounded direct execution after shell grouping or local download (15 October)

Two additional source-execution patterns are covered by the existing **same-file, read-only** gate:

- A fetched stream piped into a shell **subshell** (`curl ... | ( bash )`) or a shell **command group** (`wget ... | { sh; }`) is still immediate remote-code execution. The source matcher allows one literal opening group directly before the interpreter; `curl ... | ( jq . )` is a safe negative control for this narrow check.
- A fetched script saved using literal `curl -o file`, `curl --output=file`, `curl -fsSLo file`, `wget -O file`, `wget -qO file`, or `wget --output-document=file` and immediately passed to a script interpreter via `&&`, `;`, or the next line is also counted. This path uses an **identical literal filename backreference**; a different filename, a longer similarly prefixed name, or a download merely saved for later does not trigger it. Bounded file names are limited to a conservative ASCII shape; variable/quoted paths, chmod chains and indirect execution remain outside this heuristic.

Real-Git tests include positive and negative cases and verify unchanged risky Git blobs remain historical debt, whereas modifications reactivate admission failure. The reported counts are **source observations only**, not proof of complete shell coverage, pipeline safety, release eligibility or runtime deployment. Source CI stays GitHub-hosted without production credentials, self-hosted runner execution, manual dispatch, SQLite writes or VPS changes.

## Bounded interpreter wrappers with user and environment (15 October)

A pipe into `sudo -u root bash`, `sudo --user=root python3 -` or `env LANG=C sh` still executes downloaded bytes directly. The scanner now recognizes bounded literal `sudo` user names and a maximum of three static environment assignments before the existing interpreter allowlist, including the earlier subshell form `curl | ( sudo -u nobody sh )`. It **does not** expand environment values, chained commands, dynamically generated aliases, shell expressions or arbitrary option arrangements.

Independent real-Git positive and negative regression controls confirm that `curl | env LANG=C jq .` and `curl | sudo -u nobody jq .` are **not** execution matches, and unchanged legacy Git blobs remain unmodified historical risk instead of newly admitted debt. This is a syntactic review aid only; **not** evidence of authorization to merge, dispatch, deploy or restart the VPS listener.

## Remote code execution by `eval` and `source` (15 October follow-up)

The offline Git source ratchet now also flags literal shell **`eval "$(curl ...)"`** and **`eval $(wget ...)`**, plus **`source <(curl ...)`** and **`. <(wget ...)`**. These forms execute downloaded bytes without the previously recognized `curl | bash` shape. An immediately sourced local file is also recognized when the filename exactly matches an earlier literal `curl -o file` or `wget -O file` in the same bounded run scalar. This does not trace variables, arbitrary shell evaluation, aliases or changes in intervening files.

Ten adversarial **real Git** tests cover execution cases, harmless output-only `cat`/different filenames, non-authorizing results and unchanged legacy blob semantics. The source check cannot certify that all shell execution forms are safe. It detects *newly introduced known syntax* under a 5-minute GitHub-hosted read-only PR workflow; it neither approves a merge nor changes VPS/runner/SQLite/worker state.

## Bounded Git object capture (15 October follow-up)

The entire raw `git ls-tree` output can be much larger than the number of selected Action source files. A per-Action-file cap applied **after** collecting that output does not prevent memory exhaustion. The scanner therefore reads Git stdout in bounded 64 KiB chunks via a nonblocking readiness selector, with a **64 MiB total response cap** and **25-second monotonic wall deadline**, before decoding or parsing Git objects. It discards Git stderr rather than printing arbitrary source or path data, and kills/reaps a subprocess that exceeds the time or response limit. This applies to `ls-tree`, `cat-file`, commit resolution and ancestry checks.

Real Git tests force very small artificial read limits to prove oversized trees and blobs **fail closed** and cannot yield `source_complete=true` or release/deploy authority. Successful reads retain exact bytes (including NUL-separated Git tree entries). These bounds are a memory/availability control, not an independent proof that a GitHub PR or source-tree snapshot is authorized for deployment.

## Alternate shell stdin consumers (15 October follow-up)

The remote-script source scanner previously recognized common `bash`/`sh`/`zsh` and language interpreters, but a download piped to **`dash`**, **`ksh`**, **`mksh`**, **`ash`**, **`fish`** or BusyBox shell applets (`busybox sh`, `/bin/busybox ash`) still executes network-provided instructions. The explicit consumer allowlist now includes these bounded literal forms for stdin pipes, local-file interpreter execution and previously covered wrappers. Safe read-only filters such as `jq` remain excluded. Real-Git tests also prove that an exact unchanged historical shell workflow continues to be treated as legacy debt, without granting release or deployment authority.

This is **not a complete shell interpreter registry**. Arbitrary aliases, binaries renamed to custom paths, dynamically assembled interpreters and alternative download clients are outside this finite source check. The GitHub-hosted result is non-authorizing and does not remove the separate self-hosted listener, production writer lock or review requirements.

## Multiline plain YAML `run` scalars (15 October follow-up)

A valid YAML plain `run` value can continue on following more-indented lines; YAML folds the value before GitHub Actions passes it to the shell. For example, `- run: curl … |` followed by `          bash` is an executable download pipeline even though the raw source places the consumer on another line. The source-only gate now folds more-indented plain-scalar continuation text before its existing remote-execution checks, skips YAML comment lines and stops at the next sibling mapping key. Quoted, explicit `|` and `>` block scalars retain their separate already-tested paths.

Six new real-Git test groups verify blocked multiline `curl|bash` and `wget|busybox sh`, comments, ordinary read-only `jq`, safe sibling field boundaries and the unchanged historical-blob exception. This is a **bounded heuristic**, not a full YAML/Actions interpreter; its green result remains non-authorizing and does not support a production or VPS claim.
 
## Direct `exec` interpreter wrapper (15 October follow-up)

In a POSIX shell pipeline, `curl … | exec bash` executes downloaded stdin as shell commands even though the invoked interpreter is preceded by the `exec` built-in. The same applies to `wget … | ( exec /bin/sh )` and a downloaded file started via `exec bash local.sh`. The bounded wrapper list now includes this directly specified `exec` form; it does not infer arbitrary shell function aliases or dynamically constructed commands. Real-Git positive, inert `exec jq` and exact-unchanged-blob tests verify source-only admission without granting production authority.

## Download redirected to a directly executed local script (15 October)

A shell redirect can save remote bytes without the explicit download client's output option, e.g. `curl -fsSL URL > install.sh && bash install.sh`, `wget -qO- URL > ./install.sh; source ./install.sh` or a script file later executed by `python3` on the next literal shell line. The source-only ratchet now recognizes the bounded *exact same literal path* target for these cases in addition to `curl -o`/`wget -O`. A safe `jq` filter, merely saving bytes without running them or executing a **different** filename is not evidence of direct remote-code execution for this heuristic. Legacy risky Git blobs are grandfathered **only when unchanged**; any modification to a risky workflow reactivates the block.

Six real-Git cases confirm the contract. This does **not** trace dynamically constructed paths, aliases, expanded variables, file rewrites, relative path normalization, redirection via file descriptors, or downloads through unrelated commands. The read-only GitHub-hosted result does not permit merge, workflow dispatch, live VPS mutation, or deployment.

## Shell variable downloaded and immediately evaluated (15 October)

A remote script may be captured in one **literal** shell variable and immediately executed, e.g. `payload=$(curl -fsSL URL) && eval "$payload"`, `script=$(wget -qO- URL); bash -c "$script"` and `payload="$(curl URL)" && dash -c "${payload}"`. The scanner recognizes these short, same-command flow patterns with an **exact case-sensitive variable-name backreference**. Distinct variable names, a different capitalization (`payload` versus `PAYLOAD`) and read-only use such as `printf` do not trigger this additional detector. An unchanged risky Git workflow remains historical debt only until its Git blob is changed.

Seven real-Git tests cover positive and negative matches and legacy preservation. The bounded matcher deliberately does not trace intervening assignments, indirect `eval` in a function, shell aliases or arbitrary substitutions. GitHub-hosted source evidence is a no-new-known-debt check, **not** complete shell correctness or release authority.

## Direct PowerShell remote-code ingestion (15 October)

On Windows and `pwsh` Actions steps, `irm https://…/setup.ps1 | iex` and `Invoke-WebRequest URL | Invoke-Expression` are executable remote downloads despite not using the earlier curl/wget plus bash shapes. The source ratchet now flags the direct, bounded aliases `irm`, `iwr`, full command names, and immediate nested `iex (irm URL)` / `Invoke-Expression (Invoke-RestMethod URL)`. A plain fetch or one piped into a **non-executing** parser (`ConvertFrom-Json`) is not this known risk.

Seven adversarial real-Git groups cover positive execution, harmless negative cases and the immutable historical Git-blob exception. This is a source-level vocabulary matcher, **not** a complete PowerShell AST/parser, execution-environment classifier or proof against alias shadowing. Read-only GitHub-hosted checks do not authorize release or self-hosted/production actions.

## Direct downloaded executable (15 October)

A download saved to a local literal path may be executed **as a binary or script** rather than passed explicitly to an interpreter: `curl -fsSLo install.sh URL && chmod +x install.sh && ./install.sh`, `wget -qO install.sh URL && ./install.sh`, `curl URL > bootstrap.sh; chmod +x bootstrap.sh; ./bootstrap.sh`, or directly `/tmp/installer`. The no-new-debt ratchet now recognizes direct start after the download, optionally with a same-path `chmod`. An identical literal local path is required; prefixes, unrelated chmod targets and a download without execution are negative controls. This source heuristic does not check execute permission or whether the file is actually a shell script: an executable sourced from the network is a code-execution risk.

Eight real-Git adversarial test groups include direct and chmod variants, harmless cases and the unchanged-blob baseline exception. This **does not** infer changed filenames, variable expansion, aliases, filesystem hardlinks, chmod flags beyond the bounded shape, or arbitrary intervening commands. Hosted CI is not a production authorization.

## Case-sensitive Linux local script identity (15 October)

The same-file heuristic compares literal Linux filenames. `install.sh` and `INSTALL.sh` are **different** names on the hosted Ubuntu runner, so the scanner's three download-to-file execution matchers (client `-o`/`--output`, stdout `>`, and direct `./script` with optional `chmod`) no longer use case-insensitive regex matching. An exact mixed-case name such as `Setup.py` still blocks immediate interpreter execution; a differently cased filename does not prove that the downloaded bytes were executed. Five real-Git tests cover the three false-positive paths and two positive same-case paths.

As before, this handles **literal** path identity only. It does not resolve filesystem aliases, case-insensitive mounts, symlinks or dynamically expanded paths. Source checks are conservative evidence, not production authorization.

## CI red-to-green: unrelated chmod and Git stdout descriptor (15 October)

The GitHub-hosted exact-head targeted CI initially **failed** one new control after `curl -o installer ... && chmod +x different && ./installer` was incorrectly labelled safe by the test: the downloaded `installer` is **still executed** despite the unrelated chmod. The regression fixture is corrected to use `./different` for the safe case, with a second **positive** control preserving the risky `./installer` variant. This intentionally keeps the scanner fail-conservative where executable bytes could actually be run.

The same CI log surfaced Python `ResourceWarning: unclosed file` for the Git object's subprocess stdout. The bounded Git reader now explicitly closes its stdout file descriptor in the `finally` cleanup path after waiting, including success, over-limit and timeout cases. Two additional real-process tests explicitly assert descriptor closure. Hosted test failures must be resolved and re-run at the **new exact head**; old green hashes are not valid evidence for this change.
