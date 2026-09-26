package com.zennay.cloud.watch

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.zennay.cloud.watch.ui.ZennayWearApp
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity() {
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        scheduleImportantAlerts()
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent { ZennayWearApp() }
    }

    private fun scheduleImportantAlerts() {
        val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        val work = PeriodicWorkRequestBuilder<AlertWorker>(1, TimeUnit.HOURS).setConstraints(constraints).build()
        val manager = WorkManager.getInstance(this)
        manager.enqueueUniquePeriodicWork(
            "zennay-important-alerts",
            ExistingPeriodicWorkPolicy.UPDATE,
            work
        )
        manager.enqueue(OneTimeWorkRequestBuilder<AlertWorker>().setConstraints(constraints).build())
    }
}