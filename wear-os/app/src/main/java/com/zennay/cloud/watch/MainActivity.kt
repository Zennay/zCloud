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
        const val EXTRA_PROJECT_ID = "com.zennay.cloud.watch.project_id"
    }

    private var pendingIncident by mutableStateOf<Pair<String, String>?>(null)
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingIncident = readIncidentFrom(intent)
        scheduleImportantAlerts()
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            ZennayWearApp(
                openIncidentId = pendingIncident?.second,
                openIncidentProject = pendingIncident?.first,
                onIncidentHandled = {
                    pendingIncident = null
                    intent?.removeExtra(EXTRA_INCIDENT_ID)
                    intent?.removeExtra(EXTRA_PROJECT_ID)
                }
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingIncident = readIncidentFrom(intent)
    }

    private fun readIncidentFrom(source: Intent?): Pair<String, String>? {
        if (source?.action != ACTION_OPEN_BLOCKER) return null
        val id = source.getStringExtra(EXTRA_INCIDENT_ID) ?: return null
        val project = source.getStringExtra(EXTRA_PROJECT_ID) ?: return null
        // Both identities are mandatory. A reused incident ID in a different
        // project must never open the wrong worker or service blocker.
        if (id.isBlank() || id.length > 256 ||
            !project.matches(Regex("[a-z][a-z0-9_-]{0,39}"))) return null
        // Intent is navigation only; it never authorizes remediation.
        return project to id
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