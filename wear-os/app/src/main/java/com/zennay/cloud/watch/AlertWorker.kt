package com.zennay.cloud.watch

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.zennay.cloud.watch.data.ZennayCloudRepository

class AlertWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result = try {
        val snapshot = ZennayCloudRepository().loadHome()
        val prefs = applicationContext.getSharedPreferences("zennay_alerts", Context.MODE_PRIVATE)
        val previous = prefs.getStringSet("active_ids", emptySet()) ?: emptySet()
        val current = snapshot.alerts.map { it.id }.toSet()
        val fresh = snapshot.alerts.filter { it.id !in previous }
        if (fresh.isNotEmpty()) {
            val nm = applicationContext.getSystemService(NotificationManager::class.java)
            if (Build.VERSION.SDK_INT >= 26) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Important project alerts", NotificationManager.IMPORTANCE_DEFAULT))
            }
            fresh.take(3).forEachIndexed { index, alert ->
                val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(applicationContext, CHANNEL) else Notification.Builder(applicationContext)
                builder.setSmallIcon(R.drawable.ic_launcher)
                    .setContentTitle(alert.title)
                    .setContentText(alert.message)
                    .setStyle(Notification.BigTextStyle().bigText(alert.message))
                    .setAutoCancel(true)
                    .setCategory(if (alert.severity == "needs_attention") Notification.CATEGORY_ERROR else Notification.CATEGORY_STATUS)
                nm.notify(alert.id.hashCode() + index, builder.build())
            }
        }
        prefs.edit().putStringSet("active_ids", current).apply()
        Result.success()
    } catch (_: Exception) {
        Result.retry()
    }

    companion object { const val CHANNEL = "zennay_important" }
}