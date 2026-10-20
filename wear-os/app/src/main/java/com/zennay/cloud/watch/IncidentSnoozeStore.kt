package com.zennay.cloud.watch

import android.app.NotificationManager
import android.content.Context
import com.zennay.cloud.watch.data.AlertItem
import java.security.MessageDigest

/**
 * Local notification preference, not a server acknowledgement or incident
 * resolution. Snoozes the exact current incident episode for one hour.
 *
 * No project/incident titles, secrets, or alert contents are persisted here.
 */
object IncidentSnoozeStore {
    private const val PREFS = "zennay_blocker_snoozes"
    private const val PREFIX = "until_"
    private const val DURATION_MS = 60L * 60 * 1000
    private const val MAX_SNOOZES = 32

    fun episodeKey(alert: AlertItem): String =
        alert.id + "\u001f" + alert.occurredAt.orEmpty()

    private fun preferenceKey(alert: AlertItem): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(episodeKey(alert).toByteArray(Charsets.UTF_8))
        return PREFIX + digest.joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    fun remainingMillis(context: Context, alert: AlertItem, now: Long = System.currentTimeMillis()): Long {
        val deadline = context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getLong(preferenceKey(alert), 0L)
        return (deadline - now).coerceAtLeast(0L)
    }

    fun snoozeForOneHour(context: Context, alert: AlertItem) {
        require(alert.id.isNotBlank() && alert.id.length <= 256) { "Invalid incident ID" }
        val appContext = context.applicationContext
        val now = System.currentTimeMillis()
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val key = preferenceKey(alert)
        val active = prefs.all.mapNotNull { (name, value) ->
            val expiry = value as? Long ?: return@mapNotNull null
            if (name.startsWith(PREFIX) && expiry > now && expiry - now <= DURATION_MS) {
                name to expiry
            } else null
        }.sortedByDescending { it.second }

        val allowed = (listOf(key) + active.map { it.first })
            .distinct().take(MAX_SNOOZES).toSet()
        val editor = prefs.edit()
        prefs.all.keys.filter { it.startsWith(PREFIX) && it !in allowed }.forEach(editor::remove)
        editor.putLong(key, now + DURATION_MS).apply()

        // Snoozing does NOT resolve the incident in zCloud. It clears only
        // the local delivery receipt so a still-open episode can alert after
        // the user-selected hour expires.
        val deliveryPrefs = appContext.getSharedPreferences("zennay_alerts", Context.MODE_PRIVATE)
        val delivered = deliveryPrefs.getStringSet("delivered_ids", emptySet()).orEmpty().toMutableSet()
        delivered.remove(episodeKey(alert))
        deliveryPrefs.edit().putStringSet("delivered_ids", delivered).apply()

        (appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager)
            ?.cancel(alert.id, 0)
    }

    fun resume(context: Context, alert: AlertItem) {
        context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().remove(preferenceKey(alert)).apply()
        // Resume permits a future notification; no immediate network call,
        // workflow trigger, queue write or notification is performed.
    }
}
