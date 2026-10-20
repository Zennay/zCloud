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

        val prefs = applicationContext.getSharedPreferences("zennay_alerts", Context.MODE_PRIVATE)
        val previouslySeen = prefs.getStringSet("delivered_ids", null)
            ?: prefs.getStringSet("active_ids", emptySet())
            ?: emptySet()
        val delivered = previouslySeen.toMutableSet()
        val urgent = snapshot.alerts
            // Do not interrupt for generic high severity. Only the server
            // can assert that the known incident requires a human decision.
            .filter { it.id.isNotBlank() && it.id.length <= 256 && it.isUrgent() &&
                      it.humanActionRequired }
            .distinctBy { it.id }
            .sortedWith(compareBy<AlertItem> {
                if (it.severity.equals("critical", true)) 0 else 1
            }.thenByDescending { it.occurredAt.orEmpty() })
        // Deduplicate by incident episode, not forever by reusable incident ID.
        // If an incident disappears from the current feed, its old receipt is
        // retired; a later recurrence is allowed to notify again.
        val currentEpisodes = urgent.map { IncidentSnoozeStore.episodeKey(it) }.toSet()
        delivered.retainAll(currentEpisodes)
        val permissionGranted = Build.VERSION.SDK_INT < 33 ||
            applicationContext.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

        if (permissionGranted) {
            val manager = applicationContext.getSystemService(NotificationManager::class.java)
            // A dedicated user-controllable blocker channel; regular updates stay silent.
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Critical project blockers", NotificationManager.IMPORTANCE_HIGH)
            )
            urgent.asSequence()
                .filter { IncidentSnoozeStore.episodeKey(it) !in delivered }
                // User snoozes an episode locally. This does not remove the
                // current incident from the read-only status page.
                .filter { IncidentSnoozeStore.remainingMillis(applicationContext, it) == 0L }
                .take(3)
                .forEach { alert ->
                // Each incident needs its OWN pending-intent identity.
                // requestCode=0 alone made all visible cards share the last
                // notification's target when FLAG_UPDATE_CURRENT was used.
                val intent = Intent(applicationContext, MainActivity::class.java)
                    .setAction(MainActivity.ACTION_OPEN_BLOCKER)
                    .setData(Uri.parse("zcloud-watch://incident/" + Uri.encode(alert.id)))
                    .putExtra(MainActivity.EXTRA_INCIDENT_ID, alert.id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                val pending = PendingIntent.getActivity(
                    applicationContext, 0, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
                // A bounded explicit owner action takes priority over a
                // generic incident summary on a very small notification face.
                val text = alert.action?.takeIf { it.isNotBlank() }
                    ?: alert.message.ifBlank { "Open zCloud voor de oorzaak en vervolgstap." }
                val notification = Notification.Builder(applicationContext, CHANNEL)
                    .setSmallIcon(R.drawable.ic_launcher)
                    .setContentTitle("${alert.project?.uppercase() ?: "zCloud"}: ${alert.title}")
                    .setContentText(text)
                    .setStyle(Notification.BigTextStyle().bigText(text))
                    .setContentIntent(pending)
                    .setAutoCancel(true)
                    .setCategory(Notification.CATEGORY_ERROR)
                    .build()
                // Use the stable string tag overload, not a potentially
                // colliding String.hashCode() notification integer.
                manager.notify(alert.id, 0, notification)
                // Mark only successfully posted alerts. The fourth blocked item
                // must still be eligible at the next scheduled poll.
                delivered += IncidentSnoozeStore.episodeKey(alert)
            }
            prefs.edit().putStringSet("delivered_ids", delivered.toList().takeLast(200).toSet()).apply()
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
