package com.zennay.cloud.watch

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.zennay.cloud.watch.data.AlertItem

/**
 * Handles the explicit local "Snooze 1 hour" notification action.
 *
 * The receiver is not exported, validates every identity field and performs
 * only local notification-preference changes. It never acknowledges/resolves
 * a server incident or contacts the recovery/runtime API.
 */
class AlertActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        if (context == null || intent?.action != ACTION_SNOOZE_BLOCKER) return

        val id = intent.getStringExtra(EXTRA_INCIDENT_ID)
            ?.takeIf { it.isNotBlank() && it.length <= 256 && it.none { ch -> Character.isISOControl(ch) } }
            ?: return
        val project = intent.getStringExtra(EXTRA_PROJECT_ID)
            ?.takeIf { it.matches(PROJECT_ID) }
            ?: return
        val episodeId = intent.getStringExtra(EXTRA_EPISODE_ID)
        if (intent.hasExtra(EXTRA_EPISODE_ID) &&
            (episodeId == null || !episodeId.matches(EPISODE_ID))) return
        val occurredAt = intent.getStringExtra(EXTRA_OCCURRED_AT)
            ?.takeIf { it.length <= 64 && it.none { ch -> Character.isISOControl(ch) } }

        val alert = AlertItem(
            id = id,
            severity = "high",
            project = project,
            title = "",
            message = "",
            occurredAt = occurredAt,
            humanActionRequired = true,
            episodeId = episodeId
        )
        // Bind the validated extras back to the opaque URI that created this
        // PendingIntent. A stale/mismatched payload must never snooze another
        // episode, even if a future caller accidentally reuses an action.
        val actionUri = intent.data ?: return
        if (actionUri.scheme != "zcloud-watch" ||
            actionUri.host != "snooze" ||
            actionUri.pathSegments != listOf(project, IncidentSnoozeStore.actionIdentity(alert))) {
            return
        }
        runCatching { IncidentSnoozeStore.snoozeForOneHour(context.applicationContext, alert) }
    }

    companion object {
        const val ACTION_SNOOZE_BLOCKER = "com.zennay.cloud.watch.SNOOZE_BLOCKER"
        const val EXTRA_INCIDENT_ID = "com.zennay.cloud.watch.snooze.incident_id"
        const val EXTRA_PROJECT_ID = "com.zennay.cloud.watch.snooze.project_id"
        const val EXTRA_EPISODE_ID = "com.zennay.cloud.watch.snooze.episode_id"
        const val EXTRA_OCCURRED_AT = "com.zennay.cloud.watch.snooze.occurred_at"

        private val PROJECT_ID = Regex("[a-z][a-z0-9_-]{0,39}")
        private val EPISODE_ID = Regex("[0-9a-f]{64}")
    }
}
