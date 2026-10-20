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

    fun notificationTag(alert: AlertItem): String =
        (alert.project?.takeIf { it.matches(Regex("[a-z][a-z0-9_-]{0,39}")) }
            ?: "cloud") + ":" + alert.id

    fun legacyEpisodeKey(alert: AlertItem): String =
        alert.id + "\\u001f" + alert.occurredAt.orEmpty()

    fun episodeKey(alert: AlertItem): String =
        alert.episodeId?.let { "source:" + it } ?: legacyEpisodeKey(alert)

    private fun preferenceKey(episode: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(episode.toByteArray(Charsets.UTF_8))
        return PREFIX + digest.joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    private fun preferenceKey(alert: AlertItem): String = preferenceKey(episodeKey(alert))

    fun remainingMillis(context: Context, alert: AlertItem, now: Long = System.currentTimeMillis()): Long {
        val prefs = context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        // Preserve an already selected local snooze when the server first
        // upgrades from timestamp-derived IDs to canonical episode IDs.
        val deadlines = listOf(
            prefs.getLong(preferenceKey(alert), 0L),
            prefs.getLong(preferenceKey(legacyEpisodeKey(alert)), 0L)
        )
        // Clock rollback must never turn a one-hour user choice into days
        // of silence. Old or implausibly distant deadlines fail open.
        return deadlines.map { it - now }
            .filter { it in 1L..DURATION_MS }
            .maxOrNull() ?: 0L
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
        prefs.all.keys.filter { it.startsWith(PREFIX) && it !in allowed }
            .forEach { editor.remove(it) }
        editor.putLong(key, now + DURATION_MS).apply()

        // Snoozing does NOT resolve the incident in zCloud. It clears only
        // the local delivery receipt so a still-open episode can alert after
        // the user-selected hour expires.
        clearLocalDeliveryReceipt(appContext, alert)
        (appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager)
            ?.cancel(notificationTag(alert), 0)
    }

    fun resume(context: Context, alert: AlertItem) {
        val appContext = context.applicationContext
        appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(preferenceKey(alert))
            .remove(preferenceKey(legacyEpisodeKey(alert)))
            .apply()
        // "Resume" restores eligibility for the NEXT regular poll even if
        // this episode had previously been delivered. No immediate alert,
        // privileged action, workflow trigger or network request.
        clearLocalDeliveryReceipt(appContext, alert)
    }

    private fun clearLocalDeliveryReceipt(context: Context, alert: AlertItem) {
        val deliveryPrefs = context.getSharedPreferences("zennay_alerts", Context.MODE_PRIVATE)
        val delivered = deliveryPrefs.getStringSet("delivered_ids", emptySet())
            .orEmpty().toMutableSet()
        val removed = delivered.remove(episodeKey(alert)) or
            delivered.remove(legacyEpisodeKey(alert))
        if (removed) {
            deliveryPrefs.edit().putStringSet("delivered_ids", delivered).apply()
        }
    }
}
