# Fuckball external training admission — 2026-10-22

## Live evidence

Inspected via zSSH on host VM-7eb5ee16-2c71-440d-b7fd-8095d204e930.

- lsblk: only sda (300G), sda1 and sda2 (ext4 mounted on /), plus sr0 optical device. No independent training block disk is attached.
- /mnt/zcloud-training is vpsarchive:zcloud-vps/training, type fuse.rclone. It does not satisfy this launcher's storage gate; archive capacity does not prove safe local cache placement.
- fuckball-ai-league.service: not-found, inactive, MainPID=0.
- fuckball-training.service: loaded, inactive, MainPID=0.
- actions.runner.Zennay-zCloud.zcloud-vps-1.service: disabled/inactive, old identity; do not start it.
- New vm-7eb5ee16-fuckball listener is registered to Zennay/fuckball (agentId 22), not Zennay/zCloud. Its presence does not make it eligible for this repository's job.
- Run 39803497899 / job 119910442800 remains queued, runner_id=0, labels self-hosted/zcloud/vps. Queued run head is fc668687b2bb20c71c203e64e288275a7af197c6; inspected PR head before this documentation commit was c4bf8e6ff8e7d2bd9d03f100e4444aa6e536a922. Do not assume queued source equals latest PR source.

## Next concrete infrastructure step

Attach a genuinely separate persistent block volume to this VPS through its hosting provider. No suitable unmounted disk is currently visible, so do not format any existing device, partition sda, create a loop file, or replace the FUSE mount as a workaround.

After attachment, identify the new device by stable /dev/disk/by-id identity and inspect existing signatures/data before any formatting decision. Mount an existing approved ext4/xfs/btrfs filesystem at /mnt/training. Verify with findmnt, lsblk ancestry and statfs that it is a different physical disk from /dev/sda and has at least 30 GiB available. Configure persistent mounting by UUID only after device identity is established. No training directories, caches, datasets or logs should be created until this gate passes.

## Following runner and activation gates

Register a fresh repository-scoped Zennay/zCloud runner with a unique new-host name and labels self-hosted/zcloud/vps; keep its work directory and runtime writes on the approved external volume. Do not reuse the disabled zcloud-vps-1 identity or the Fuckball repository runner. Prefer a unique new-VPS label in workflow scheduling before admitting queued work; a hostname check rejects old execution but does not prevent old-runner scheduling.

Once storage and fresh runner identity are proven, explicitly select the reviewed current source rather than blindly retrying the stale queued run (workflow also requires run_attempt==1). Run the hash-verified launcher from RAM. Completion requires active service, nonzero MainPID, external ExecStart/WorkingDirectory/output paths, fresh state/status.json and checkpoint progress. Until then report training as NOT STARTED.

No QA suites run. No training started, old runner activated, disk formatted or system-disk training data created during this inspection.
