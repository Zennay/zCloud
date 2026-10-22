package com.zennay.cloud.watch

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import android.os.Build
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.zennay.cloud.watch.data.AlertItem
import com.zennay.cloud.watch.data.ZennayCloudRepository

/**
 * Blocker-first watch notifications. Only urgent, newly observed alert IDs
 * vibrate; normal milestones remain visible in the app without interrupting work.
 * WorkManager is best effort, not real-time push.
 */
class AlertWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result = try {
        val snapshot = ZennayCloudRepository().loadHome()
        // Do not broadcast stale historical alerts as if they were current.
        if (snapshot.stale || !snapshot.currentBlockerFeedAvailable) return Result.success()

        // Keep the local read→notify→receipt operation atomic with respect
        // to the notification action receiver and in-app snooze/resume taps.
        synchronized(IncidentSnoozeStore.deliveryLock) {
            val prefs = applicationContext.getSharedPreferences("zennay_alerts", Context.MODE_PRIVATE)
            val previouslySeen = prefs.getStringSet("delivered_ids", null)
                ?: prefs.getStringSet("active_ids", emptySet())
                ?: emptySet()
            val delivered = previouslySeen.toMutableSet()
            val urgent = snapshot.alerts
                // Do not interrupt for generic high severity. Only the server
                // can assert that the known incident requires a human decision.
                .filter {
                    it.id.isNotBlank() && it.id.length <= 256 &&
                        it.id.none { ch -> Character.isISOControl(ch) } &&
                        it.isUrgent() && it.humanActionRequired
                }
                // Prefer the highest urgency and newest observed episode before
                // dropping duplicate source rows for the same project/incident.
                // The old order kept the first (possibly obsolete) episode, so a
                // restarted blocker could remain hidden behind a prior receipt.
                .sortedWith(compareBy<AlertItem> {
                    if (it.severity.equals("critical", true) ||
                        it.severity.equals("error", true)) 0 else 1
                }.thenByDescending { it.occurredAt.orEmpty() })
                .distinctBy { IncidentSnoozeStore.notificationTag(it) }
            // Prior receipt formats were not project-scoped. Their ownership
            // cannot be authenticated, so do not migrate them into a different
            // project's incident suppression state. A one-time re-notification
            // after upgrade is preferable to silencing another project's blocker.
            // Deduplicate by incident episode. When it disappears from the
            // current source, its suppression expires and a later recurrence can
            // notify. Timestamp changes within a service outage cannot spam.
            val currentEpisodes = urgent.map { IncidentSnoozeStore.episodeKey(it) }.toSet()
            delivered.retainAll(currentEpisodes)
            val currentNotificationTags = urgent
                .map { IncidentSnoozeStore.notificationTag(it) }.toSet()
            val manager = applicationContext.getSystemService(NotificationManager::class.java)

            // The dedicated channel is the authoritative local inventory, so
            // resolved notifications can be retired even after app upgrades.
            // Do not persist raw incident IDs merely to remember OS notification tags.
            manager.activeNotifications.forEach { posted ->
                val tag = posted.tag
                if (posted.notification.channelId == CHANNEL &&
                    tag != null && tag !in currentNotificationTags) {
                    manager.cancel(tag, posted.id)
                }
            }

            val permissionGranted = Build.VERSION.SDK_INT < 33 ||
                applicationContext.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                    PackageManager.PERMISSION_GRANTED

            try {
                if (permissionGranted) {
                    // Notification channels and the channel-bound Builder exist only on API 26+.
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        manager.createNotificationChannel(
                            NotificationChannel(
                                CHANNEL, "Critical project blockers",
                                NotificationManager.IMPORTANCE_HIGH
                            )
                        )
                    }
                    urgent.asSequence()
                        .filter { IncidentSnoozeStore.episodeKey(it) !in delivered }
                        // User snoozes an episode locally. This does not remove
                        // the current incident from the read-only status page.
                        .filter { IncidentSnoozeStore.remainingMillis(applicationContext, it) == 0L }
                        .take(3)
                        .forEach { alert ->
                            // Each incident needs its OWN PendingIntent identity.
                            val project = IncidentSnoozeStore.projectKey(alert)
                            val intent = Intent(applicationContext, MainActivity::class.java)
                                .setAction(MainActivity.ACTION_OPEN_BLOCKER)
                                .setData(Uri.parse(
                                    "zcloud-watch://incident/" +
                                        Uri.encode(project) + "/" + Uri.encode(alert.id)
                                ))
                                .putExtra(MainActivity.EXTRA_INCIDENT_ID, alert.id)
                                .putExtra(MainActivity.EXTRA_PROJECT_ID, project)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                            val pending = PendingIntent.getActivity(
                                applicationContext, 0, intent,
                                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                            )

                            // Snooze is a local preference only. This opaque,
                            // per-episode identity prevents action cross-talk.
                            val snoozeIntent = Intent(
                                applicationContext, AlertActionReceiver::class.java
                            )
                                .setAction(AlertActionReceiver.ACTION_SNOOZE_BLOCKER)
                                .setData(Uri.parse(
                                    "zcloud-watch://snooze/" + project + "/" +
                                        IncidentSnoozeStore.actionIdentity(alert)
                                ))
                                .putExtra(AlertActionReceiver.EXTRA_INCIDENT_ID, alert.id)
                                .putExtra(AlertActionReceiver.EXTRA_PROJECT_ID, project)
                                .apply {
                                    alert.episodeId?.let {
                                        putExtra(AlertActionReceiver.EXTRA_EPISODE_ID, it)
                                    }
                                    alert.occurredAt?.let {
                                        putExtra(AlertActionReceiver.EXTRA_OCCURRED_AT, it)
                                    }
                                }
                            val snoozePending = PendingIntent.getBroadcast(
                                applicationContext, 0, snoozeIntent,
                                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                            )

                            val text = alert.action?.takeIf { it.isNotBlank() }
                                ?: alert.message.ifBlank {
                                    "Open zCloud voor de oorzaak en vervolgstap."
                                }
                            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                Notification.Builder(applicationContext, CHANNEL)
                            } else {
                                Notification.Builder(applicationContext)
                            }
                            val notification = builder
                                .setSmallIcon(R.drawable.ic_launcher)
                                .setContentTitle(
                                    "${alert.project?.uppercase() ?: "zCloud"}: ${alert.title}"
                                )
                                .setContentText(text)
                                .setStyle(Notification.BigTextStyle().bigText(text))
                                .setContentIntent(pending)
                                .addAction(
                                    Notification.Action.Builder(
                                        R.drawable.ic_launcher, "Dempen 1 uur", snoozePending
                                    ).build()
                                )
                                .setAutoCancel(true)
                                .setCategory(Notification.CATEGORY_ERROR)
                                .build()

                            val tag = IncidentSnoozeStore.notificationTag(alert)
                            manager.notify(tag, 0, notification)
                            // Persist only after posting succeeded.
                            delivered += IncidentSnoozeStore.episodeKey(alert)
                        }
                }
            } finally {
                // Preserve successful posts and remove resolved episodes even
                // when permission is denied or a later notification fails.
                prefs.edit()
                    .putStringSet("delivered_ids", delivered.toList().takeLast(200).toSet())
                    .apply()
            }
        }
        Result.success()
    } catch (_: Exception) {
        Result.retry()
    }

    private fun AlertItem.isUrgent() =
        severity.equals("high", true) ||
        severity.equals("critical", true) ||
        severity.equals("error", true) ||
        severity.equals("needs_attention", true)

    companion object { const val CHANNEL = "zennay_blockers_v2" }
}
