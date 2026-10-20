package com.zennay.cloud.watch.data

data class ActivityItem(
    val time: String,
    val kind: String,
    val title: String,
    val detail: String?
)

data class AiRun(
    val time: String,
    val label: String,
    val estimated: Boolean
)

data class ProjectSummary(
    val id: String,
    val name: String,
    val progress: Int,
    val status: String,
    val health: String,
    val milestone: String,
    val nextStep: String,
    val lastActivity: ActivityItem?,
    val lastAiRun: AiRun?
)

data class HostSummary(
    val cpu: Double?,
    val memoryPercent: Int,
    val load: Double
)

data class WarningItem(
    val level: String,
    val project: String?,
    val message: String
)

data class AlertItem(
    val id: String,
    val severity: String,
    val project: String?,
    val title: String,
    val message: String,
    val occurredAt: String?,
    // Explicitly asserted by the current server incident source. A high
    // severity or an old-server historical alert alone is never enough.
    val humanActionRequired: Boolean = false
)

data class WatchSnapshot(
    val generatedAt: String,
    val stale: Boolean,
    val overallProgress: Int,
    val activeProjects: Int,
    val host: HostSummary,
    val warnings: List<WarningItem>,
    val alerts: List<AlertItem>,
    val projects: List<ProjectSummary>,
    // Legacy servers expose historical alerts only. Never notify those as if
    // they were current unresolved blockers.
    val currentBlockerFeedAvailable: Boolean = false
)

data class ServiceState(
    val name: String,
    val state: String,
    val result: String?
)

data class ProgressPoint(
    val time: String,
    val progress: Float
)

data class ProjectDetail(
    val id: String,
    val name: String,
    val progress: Int,
    val health: String,
    val status: String,
    val currentMilestone: String,
    val nextStep: String,
    val lastAiRun: AiRun?,
    val recentActivity: List<ActivityItem>,
    val progressHistory: List<ProgressPoint>,
    val services: List<ServiceState>
)