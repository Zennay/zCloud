package com.zennay.cloud.watch.notifications

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.zennay.cloud.watch.BuildConfig
import com.zennay.cloud.watch.MainActivity
import com.zennay.cloud.watch.R
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

class AlertWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork() = withContext(Dispatchers.IO) {
        runCatching {
            val connection = (URL(BuildConfig.API_BASE.trimEnd('/') + "/api/v1/alerts").openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 5000
                readTimeout = 5000
                setRequestProperty("Authorization", "Bearer " + BuildConfig.API_TOKEN)
                setRequestProperty("Accept", "application/json")
            }
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            connection.disconnect()
            val alerts = JSONObject(body).optJSONArray("alerts") ?: return@runCatching
            val prefs = applicationContext.getSharedPreferences("important-alerts", Context.MODE_PRIVATE)
            val seen = prefs.getStringSet("seen", emptySet())?.toMutableSet() ?: mutableSetOf()
            for (i in alerts.length() - 1 downTo 0) {
                val alert = alerts.getJSONObject(i)
                val id = alert.optString("id")
                if (id.isBlank() || id in seen) continue
                notifyAlert(
                    id,
                    alert.optString("title", "Zennay Cloud"),
                    alert.optString("detail"),
                    alert.optString("severity")
                )
                seen += id
            }
            prefs.edit().putStringSet("seen", if (seen.size > 200) seen.toList().takeLast(200).toSet() else seen).apply()
        }
        Result.success()
    }

    private fun notifyAlert(id: String, title: String, detail: String, severity: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            applicationContext.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return

        val manager = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channelId = "zennay-important"
        manager.createNotificationChannel(
            NotificationChannel(channelId, "Important project events", NotificationManager.IMPORTANCE_DEFAULT)
        )
        val intent = Intent(applicationContext, MainActivity::class.java)
        val pending = PendingIntent.getActivity(
            applicationContext, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = Notification.Builder(applicationContext, channelId)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(detail)
            .setStyle(Notification.BigTextStyle().bigText(detail))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setCategory(if (severity == "high") Notification.CATEGORY_ERROR else Notification.CATEGORY_STATUS)
            .build()
        manager.notify(id.hashCode(), notification)
    }
}