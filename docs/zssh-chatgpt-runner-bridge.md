# ChatGPT → GitHub Actions → VPS (no MCP, no Claude)

This is an **independent owner-only control path** in zCloud for the existing
`vps-bb300bba` self-hosted GitHub runner. It does **not** use the unfinished
zSSH public MCP/OAuth/DNS publication or bypass zSSH's release gate.

## How to operate from an ordinary ChatGPT chat

Connect the **GitHub** app, then ask ChatGPT to add one exact command as a new
comment on [zCloud issue #1278](https://github.com/Zennay/zCloud/issues/1278).

Supported comment bodies (one line, no extra Markdown):

- `/zssh status` — bounded host uptime, root-disk usage, memory (read-only)
- `/zssh project cloud` — current deployed zCloud Git revision (read-only)
- `/zssh service zennay-cloud` — systemd state (read-only)
- `/zssh service zssh` — systemd state (read-only)
- `/zssh service zssh-public` — systemd state (read-only)
- `/zssh run write-probe` — write, verify and delete a temporary VPS file
- `/zssh run zcloud-guard-test` — execute the reviewed VPS identity unit test

Example user request: "Check the VPS via the zSSH GitHub runner route. Comment
`/zssh status` on zCloud issue #1278, then inspect the resulting Actions run
and give me the actual output." ChatGPT can use GitHub's issue-comment and
workflow-run/log tools without needing a terminal connector.

Each accepted owner comment causes the `zSSH ChatGPT VPS runner bridge` workflow
to validate the exact event on GitHub-hosted Ubuntu, then queue a bounded job on
`[self-hosted, zcloud, vps]`. The job rechecks the runner identity (hostname,
user and the existing zCloud guard). A separate GitHub-hosted job posts a result
comment back to #1278 with the Actions run URL. Read the run's sanitized receipt
via GitHub to inspect results. The connector's observed GitHub App ID is
`1144995` (`chatgpt-codex-connector`); GitHub-owned direct comments from
`Zennay` also work. Other authors, GitHub Apps, issue numbers, PR comments,
events, multiline payloads and shell strings are rejected.

## Extending operations safely

This repository is **public**. Commands and Action run logs are visible to
others. Never send passwords, keys, tokens, PII, private file content or
arbitrary shell as commands. The bridge intentionally does not accept generic
`ssh`, `bash -c`, `sudo`, file reads or unrestricted commands.

To perform new VPS work from ChatGPT, have ChatGPT implement the exact operation
as a reviewed, tested *named recipe* in
`scripts/zcloud_chatgpt_runner_bridge.py`, update the anchored command
allowlist and regression tests, and merge it through the existing protected
main-branch approval checks. Only then request it as `/zssh run <recipe-name>`.
This gives the same self-hosted VPS execution mechanism without exposing an
unreviewed public remote shell. Use dedicated least-privileged service identities
or protected GitHub environments for more powerful recipes.

For private/secret-dependent or arbitrary shell operations, migrate the
controller to a private operations repository, add an explicit approval
boundary and scope the runner to that repository before enabling raw commands.
No GitHub runner is an SSH daemon: the runner executes isolated, bounded jobs
on the VPS itself, so there is no need to open TCP 22 or store SSH keys in
ChatGPT. Interactive terminal sessions are not part of this design.

## Test & rollback

`python3 -m unittest -v tests.test_zssh_chatgpt_runner_bridge`

The existing zCloud regression smoke also discovers this test. To disable the
bridge, disable its Actions workflow or revert its workflow file on protected
main; unrelated runners and the existing zSSH MCP path are unaffected.
