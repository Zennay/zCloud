package com.zennay.cloud.watch

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.activity.result.contract.ActivityResultContracts
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.zennay.cloud.watch.ui.ZennayWearApp
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity() {
    companion object {
        const val ACTION_OPEN_BLOCKER = "com.zennay.cloud.watch.OPEN_BLOCKER"
        const val EXTRA_INCIDENT_ID = "com.zennay.cloud.watch.incident_id"
    }

    private var pendingIncidentId by mutableStateOf<String?>(null)
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingIncidentId = readIncidentFrom(intent)
        scheduleImportantAlerts()
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            ZennayWearApp(
                openIncidentId = pendingIncidentId,
                onIncidentHandled = {
                    pendingIncidentId = null
                    intent?.removeExtra(EXTRA_INCIDENT_ID)
                }
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingIncidentId = readIncidentFrom(intent)
    }

    private fun readIncidentFrom(source: Intent?): String? {
        if (source?.action != ACTION_OPEN_BLOCKER) return null
        val id = source.getStringExtra(EXTRA_INCIDENT_ID) ?: return null
        // Notifications contain only an observed incident identity, never
        // permission to run a recovery or view untrusted external URLs.
        return id.takeIf { it.isNotBlank() && it.length <= 256 }
    }

    private fun scheduleImportantAlerts() {
        val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        val work = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES).setConstraints(constraints).build()
        val manager = WorkManager.getInstance(this)
        manager.enqueueUniquePeriodicWork(
            "zennay-important-alerts",
            ExistingPeriodicWorkPolicy.UPDATE,
            work
        )
        manager.enqueueUniqueWork(
            "zennay-important-alerts-initial",
            ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<AlertWorker>().setConstraints(constraints).build()
        )
    }
}