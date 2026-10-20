package com.zennay.cloud.watch.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text
import com.zennay.cloud.watch.IncidentSnoozeStore
import com.zennay.cloud.watch.data.ProjectDetail
import com.zennay.cloud.watch.data.ProjectSummary
import com.zennay.cloud.watch.data.ProgressPoint
import com.zennay.cloud.watch.data.ServiceState
import com.zennay.cloud.watch.data.WatchSnapshot
import com.zennay.cloud.watch.data.ZennayCloudRepository
import java.time.Duration
import java.time.Instant
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val Bg = Color(0xFF05070A)
private val Surface = Color(0xFF0D1219)
private val Surface2 = Color(0xFF141B24)
private val TextMain = Color(0xFFF3F7FB)
private val TextMuted = Color(0xFF8D9AAA)
private val Accent = Color(0xFF71E8C2)
private val Blue = Color(0xFF7AB7FF)
private val Warning = Color(0xFFFFC66D)
private val Danger = Color(0xFFFF7D86)
private val Track = Color(0xFF202A35)

@Composable
fun ZennayWearApp(
    repository: ZennayCloudRepository = remember { ZennayCloudRepository() },
    openIncidentId: String? = null,
    onIncidentHandled: () -> Unit = {}
) {
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(openIncidentId) {
        if (openIncidentId != null) selectedId = null
    }
    MaterialTheme {
        Box(Modifier.fillMaxSize().background(Bg)) {
            if (selectedId == null) {
                HomeScreen(
                    repository = repository,
                    onProject = { selectedId = it },
                    openIncidentId = openIncidentId,
                    onIncidentHandled = onIncidentHandled
                )
            } else {
                BackHandler { selectedId = null }
                DetailScreen(
                    projectId = selectedId!!,
                    repository = repository,
                    onBack = { selectedId = null }
                )
            }
        }
    }
}

@Composable
private fun HomeScreen(
    repository: ZennayCloudRepository,
    onProject: (String) -> Unit,
    openIncidentId: String?,
    onIncidentHandled: () -> Unit
) {
    var data by remember { mutableStateOf<WatchSnapshot?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val appContext = LocalContext.current
    var snoozeVersion by remember { mutableStateOf(0) }
    var snoozeMinuteTick by remember { mutableStateOf(0) }
    var expandedAlertId by rememberSaveable { mutableStateOf<String?>(null) }
    var disappearedIncidentNotice by rememberSaveable { mutableStateOf(false) }

    suspend fun reload() {
        loading = true
        disappearedIncidentNotice = false
        runCatching { repository.loadHome() }
            .onSuccess {
                data = it
                error = null
                // Invalidate expansion if the blocker was resolved, the feed
                // is historical, or the source became stale.
                if (it.stale || !it.currentBlockerFeedAvailable ||
                    it.alerts.none { alert -> alert.id == expandedAlertId }) {
                    expandedAlertId = null
                }
            }
            .onFailure {
                error = it.message ?: "Cloud offline"
                expandedAlertId = null
            }
        loading = false
    }
    // Refresh only an expanded card's local snooze countdown. Compose
    // cancels the coroutine on collapse; no extra HTTP requests or background
    // pollers are created, and incident state is never mutated.
    LaunchedEffect(expandedAlertId) {
        if (expandedAlertId != null) {
            while (true) {
                delay(30_000L)
                snoozeMinuteTick++
            }
        }
    }
    LaunchedEffect(openIncidentId) {
        // A notification can arrive while the watch is already running.
        // Always obtain new evidence before opening the named blocker;
        // never navigate by ID into historical or offline cached data.
        reload()
        if (openIncidentId != null) {
            val snapshot = data
            val current = snapshot?.takeIf {
                error == null && !it.stale && it.currentBlockerFeedAvailable
            }
            expandedAlertId = openIncidentId.takeIf { id ->
                current?.alerts?.any { alert ->
                    alert.id == id && (
                        alert.severity.equals("high", true) ||
                        alert.severity.equals("critical", true) ||
                        alert.severity.equals("error", true) ||
                        alert.severity.equals("needs_attention", true))
                } == true
            }
            disappearedIncidentNotice = current != null && expandedAlertId == null
            onIncidentHandled()
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 26.dp, end = 26.dp, top = 18.dp, bottom = 34.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            HeaderRow(
                title = "Zennay Cloud",
                subtitle = when {
                    loading -> "SYNCING"
                    error != null -> "OFFLINE"
                    data?.stale == true -> "STALE"
                    else -> "LIVE"
                },
                onRefresh = { scope.launch { reload() } }
            )
        }
        if (error != null && data == null) {
            item { ErrorCard(error ?: "Cloud offline", onRetry = { scope.launch { reload() } }) }
        }
        if (disappearedIncidentNotice && error == null) {
            item {
                SurfaceCard(modifier = Modifier.clickable {
                    disappearedIncidentNotice = false
                }) {
                    Label("MELDING NIET MEER ACTIEF", Accent)
                    Text("Het incident uit je notificatie staat niet meer in " +
                         "de actuele blokkadelijst. Tik om deze melding te sluiten.",
                         color = TextMain, fontSize = 11.sp)
                }
            }
        }
        data?.let { snapshot ->
            // A failed refresh may leave the last successful snapshot in memory.
            // Never label it LIVE or show its old incidents as current blockers.
            if (error != null) {
                item {
                    ErrorCard("Offline: laatste succesvolle gegevens blijven zichtbaar. " +
                              "Ververs voordat je een blokkade opvolgt.",
                              onRetry = { scope.launch { reload() } })
                }
            }
            // Source incident IDs can be empty/duplicated. Compose requires
            // stable unique lazy-list keys, and only fresh incident evidence
            // may receive current blocker emphasis.
            val urgent = snapshot.alerts
                .filter { error == null && !snapshot.stale &&
                          snapshot.currentBlockerFeedAvailable && it.id.isNotBlank() }
                .filter { it.severity.equals("critical", true) ||
                          it.severity.equals("high", true) ||
                          it.severity.equals("error", true) ||
                          it.severity.equals("needs_attention", true) }
                .distinctBy { it.id }
                .sortedWith(compareBy<com.zennay.cloud.watch.data.AlertItem> {
                    if (it.severity.equals("critical", true) ||
                        it.severity.equals("error", true)) 0 else 1
                }.thenByDescending { it.occurredAt.orEmpty() })
            if (urgent.isNotEmpty()) {
                item { SectionLabel("AANDACHT NODIG · ${urgent.size}") }
                items(urgent, key = { "alert:${it.id}" }) { alert ->
                    val expanded = expandedAlertId == alert.id
                    // The operator can inspect the actual cause and next
                    // action without losing their place in the incident list.
                    SurfaceCard(modifier = Modifier.clickable {
                        expandedAlertId = if (expanded) null else alert.id
                    }) {
                        Label(
                            "${alert.project?.uppercase() ?: "ZCLOUD"} · ${alert.severity.uppercase()}",
                            if (alert.severity.equals("critical", true) ||
                                alert.severity.equals("error", true)) Danger else Warning
                        )
                        Text(
                            alert.title, color = TextMain, fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            maxLines = if (expanded) Int.MAX_VALUE else 3,
                            overflow = TextOverflow.Ellipsis
                        )
                        Spacer(Modifier.height(4.dp))
                        if (expanded) {
                            val cause = alert.cause?.takeIf { it.isNotBlank() }
                            val action = alert.action?.takeIf { it.isNotBlank() }
                            val impact = alert.impact?.takeIf { it.isNotBlank() }
                            if (cause != null && action != null) {
                                Label("OORZAAK", Warning)
                                Text(cause, color = TextMain, fontSize = 11.sp)
                                if (impact != null) {
                                    Spacer(Modifier.height(7.dp))
                                    Label("IMPACT", Blue)
                                    Text(impact, color = TextMain, fontSize = 11.sp)
                                }
                                Spacer(Modifier.height(7.dp))
                                Label("VOLGENDE ACTIE", Accent)
                                Text(action, color = TextMain, fontSize = 11.sp)
                            } else {
                                // Backward-compatible with older watch servers:
                                // use the combined description if source fields
                                // have not yet reached the deployed endpoint.
                                val parts = alert.message.split(" · Actie: ", limit = 2)
                                if (parts.size == 2 && parts[0].startsWith("Oorzaak: ")) {
                                    Label("OORZAAK", Warning)
                                    Text(parts[0].removePrefix("Oorzaak: "),
                                         color = TextMain, fontSize = 11.sp)
                                    Spacer(Modifier.height(7.dp))
                                    Label("VOLGENDE ACTIE", Accent)
                                    Text(parts[1], color = TextMain, fontSize = 11.sp)
                                } else {
                                    Label("DETAILS", Blue)
                                    Text(alert.message, color = TextMain, fontSize = 11.sp)
                                }
                            }
                            alert.occurredAt?.let { seen ->
                                Spacer(Modifier.height(7.dp))
                                Text("Waargenomen: ${shortAge(seen)}",
                                     color = TextMuted, fontSize = 9.sp)
                            }
                            if (alert.humanActionRequired && alert.id.isNotBlank() &&
                                alert.id.length <= 256) {
                                // Read only on recomposition; local button taps
                                // bump the version. No remote acknowledgement.
                                val version = snoozeVersion
                                val observedTime = remember(snoozeMinuteTick, snoozeVersion) {
                                    System.currentTimeMillis()
                                }
                                val remaining = IncidentSnoozeStore.remainingMillis(
                                    appContext, alert, now = observedTime
                                )
                                Spacer(Modifier.height(9.dp))
                                Label("LOKALE MELDINGSINSTELLING", Blue)
                                Text(
                                    if (remaining > 0) "Melding gedempt · nog maximaal ${(remaining + 59_999L) / 60_000L} min"
                                    else "Incident blijft actief, ook als meldingen stil zijn.",
                                    color = TextMuted, fontSize = 10.sp
                                )
                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 6.dp)
                                        .height(42.dp)
                                        .background(Surface2, RoundedCornerShape(14.dp))
                                        .clickable {
                                            if (remaining > 0) {
                                                IncidentSnoozeStore.resume(appContext, alert)
                                            } else {
                                                IncidentSnoozeStore.snoozeForOneHour(appContext, alert)
                                            }
                                            snoozeVersion = version + 1
                                        },
                                    contentAlignment = Alignment.Center
                                ) {
                                    Text(
                                        if (remaining > 0) "Meldingen hervatten"
                                        else "Notificaties 1 uur dempen",
                                        color = Accent, fontSize = 11.sp,
                                        fontWeight = FontWeight.Bold
                                    )
                                }
                            }
                            val project = snapshot.projects.firstOrNull { it.id == alert.project }
                            if (project != null) {
                                Spacer(Modifier.height(9.dp))
                                Text("Open ${project.name} ›",
                                     modifier = Modifier
                                         .fillMaxWidth()
                                         .clickable { onProject(project.id) }
                                         .padding(vertical = 7.dp),
                                     color = Accent, fontSize = 11.sp,
                                     fontWeight = FontWeight.Bold)
                            }
                            Text("Tik om te sluiten", color = TextMuted, fontSize = 9.sp)
                        } else {
                            Text(
                                alert.message, color = TextMuted, fontSize = 11.sp,
                                maxLines = 3, overflow = TextOverflow.Ellipsis
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                if (alert.humanActionRequired) "Menselijke actie nodig · tik voor details ›"
                                else "Actuele status · tik voor details ›",
                                color = if (alert.humanActionRequired) Warning else Accent,
                                fontSize = 10.sp
                            )
                        }
                    }
                }
            }
            if (!snapshot.currentBlockerFeedAvailable && error == null) {
                item {
                    SurfaceCard {
                        Label("ALLEEN HISTORISCHE MELDINGEN", Warning)
                        Text("Deze server levert geen bevestigde actuele blokkades. " +
                             "Urgente meldingen zijn daarom tijdelijk uitgeschakeld.",
                             color = TextMain, fontSize = 10.sp)
                    }
                }
            }
            if (snapshot.stale || error != null) {
                item {
                    SurfaceCard {
                        Label("GEGEVENS VEROUDERD", Warning)
                        Text("Geen actuele incidentbevestiging. Ververs voordat je een blokkade als opgelost beschouwt.",
                            color = TextMain, fontSize = 10.sp)
                    }
                }
            }
            item {
                OverviewHero(
                    progress = snapshot.overallProgress,
                    active = snapshot.activeProjects,
                    cpu = snapshot.host.cpu,
                    ram = snapshot.host.memoryPercent
                )
            }
            if (snapshot.warnings.isNotEmpty()) {
                item {
                    SurfaceCard {
                        Label("WARNING", Warning)
                        Text(
                            snapshot.warnings.first().message,
                            color = TextMain,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.SemiBold,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth(),
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
            item { SectionLabel("PROJECTS") }
            items(snapshot.projects, key = { it.id }) { project ->
                ProjectCard(project = project, onClick = { onProject(project.id) })
            }
            item {
                Text(
                    if (snapshot.stale || error != null) "Niet actueel · ${shortAge(snapshot.generatedAt)}" else "Live · ${shortAge(snapshot.generatedAt)}",
                    color = if (snapshot.stale || error != null) Warning else TextMuted,
                    fontSize = 9.sp,
                    modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
                    textAlign = TextAlign.Center
                )
            }
        }
    }
}

@Composable
private fun DetailScreen(
    projectId: String,
    repository: ZennayCloudRepository,
    onBack: () -> Unit
) {
    var detail by remember(projectId) { mutableStateOf<ProjectDetail?>(null) }
    var loading by remember(projectId) { mutableStateOf(true) }
    var error by remember(projectId) { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun reload() {
        loading = true
        runCatching { repository.loadProject(projectId) }
            .onSuccess { detail = it; error = null }
            .onFailure { error = it.message ?: "Cloud offline" }
        loading = false
    }
    LaunchedEffect(projectId) { reload() }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 26.dp, end = 26.dp, top = 18.dp, bottom = 34.dp),
        verticalArrangement = Arrangement.spacedBy(11.dp)
    ) {
        item {
            HeaderRow(
                title = detail?.name?.uppercase() ?: projectId.uppercase(),
                subtitle = when {
                    loading -> "syncing"
                    error != null -> "offline"
                    else -> "project"
                },
                leading = "‹",
                onLeading = onBack,
                onRefresh = { scope.launch { reload() } }
            )
        }
        if (error != null && detail == null) {
            item { ErrorCard(error ?: "Cloud offline", onRetry = { scope.launch { reload() } }) }
        }
        detail?.let { p ->
            if (error != null) {
                item {
                    ErrorCard("Projectdetails zijn mogelijk verouderd. " +
                              "Ververs voor een actuele beslissing.",
                              onRetry = { scope.launch { reload() } })
                }
            }
            item { ProjectHero(p) }
            item {
                SurfaceCard {
                    Label("CURRENT", Blue)
                    Text(
                        p.currentMilestone,
                        color = TextMain,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                    Spacer(Modifier.height(5.dp))
                    AiLine(p.lastAiRun?.time, p.lastAiRun?.estimated == true)
                }
            }
            item {
                SurfaceCard {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Label("PROGRESS", Accent)
                        Text("${p.progress}%", color = TextMuted, fontSize = 10.sp)
                    }
                    Spacer(Modifier.height(8.dp))
                    Sparkline(p.progressHistory)
                }
            }
            if (p.services.isNotEmpty()) {
                item {
                    SurfaceCard {
                        Label("SERVICES", Blue)
                        Spacer(Modifier.height(4.dp))
                        p.services.forEach { ServiceLine(it) }
                    }
                }
            }
            item {
                SurfaceCard {
                    Label("NEXT", Accent)
                    Text(
                        p.nextStep,
                        color = TextMain,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
            if (p.recentActivity.isNotEmpty()) {
                item { SectionLabel("RECENT") }
                items(p.recentActivity.take(4)) { event ->
                    SurfaceCard {
                        Text(
                            event.title,
                            color = TextMain,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                        Spacer(Modifier.height(2.dp))
                        Text(shortAge(event.time), color = TextMuted, fontSize = 9.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun HeaderRow(
    title: String,
    subtitle: String,
    onRefresh: () -> Unit,
    leading: String? = null,
    onLeading: (() -> Unit)? = null
) {
    Box(
        modifier = Modifier.fillMaxWidth().height(48.dp),
        contentAlignment = Alignment.Center
    ) {
        if (leading != null && onLeading != null) {
            Box(modifier = Modifier.align(Alignment.CenterStart)) {
                RoundAction(leading, onLeading)
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                title,
                color = TextMain,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(0.62f)
            )
            Spacer(Modifier.height(2.dp))
            Text(
                subtitle.uppercase(),
                color = if (subtitle.equals("live", true)) Accent else TextMuted,
                fontSize = 7.sp,
                letterSpacing = 1.1.sp,
                textAlign = TextAlign.Center
            )
        }
        Box(modifier = Modifier.align(Alignment.CenterEnd)) {
            RoundAction(if (subtitle.equals("syncing", true)) "…" else "↻", onRefresh)
        }
    }
}

@Composable
private fun RoundAction(label: String, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .size(42.dp)
            .background(Surface2, CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Text(label, color = TextMain, fontSize = 17.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun OverviewHero(progress: Int, active: Int, cpu: Double?, ram: Int) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        ProgressRing(progress, 108.dp)
        Spacer(Modifier.height(12.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterHorizontally),
            verticalAlignment = Alignment.CenterVertically
        ) {
            MetricStack("ACTIVE", "$active")
            MetricStack("CPU", cpu?.let { "${it.toInt()}%" } ?: "—")
            MetricStack("RAM", "$ram%")
        }
    }
}
@Composable
private fun MetricStack(label: String, value: String) {
    Column(
        modifier = Modifier.width(56.dp).background(Surface2, RoundedCornerShape(12.dp)).padding(horizontal = 7.dp, vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(label, color = TextMuted, fontSize = 7.sp, letterSpacing = .8.sp, maxLines = 1)
        Spacer(Modifier.height(3.dp))
        Text(value, color = TextMain, fontSize = 13.sp, fontWeight = FontWeight.Bold, maxLines = 1)
    }
}

@Composable
private fun ProjectHero(p: ProjectDetail) {
    SurfaceCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            ProgressRing(p.progress, 70.dp)
            Column(horizontalAlignment = Alignment.End) {
                StatusDot(p.health)
                Spacer(Modifier.height(7.dp))
                Text(p.status.uppercase(), color = TextMuted, fontSize = 9.sp, letterSpacing = 0.8.sp)
            }
        }
    }
}

@Composable
private fun ProjectCard(project: ProjectSummary, onClick: () -> Unit) {
    SurfaceCard(modifier = Modifier.clickable(onClick = onClick)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            MiniRing(project.progress)
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(project.name, color = TextMain, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    Text("${project.progress}%", color = Accent, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                }
                Spacer(Modifier.height(3.dp))
                Text(
                    project.milestone,
                    color = TextMuted,
                    fontSize = 9.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(Modifier.height(5.dp))
                AiLine(project.lastAiRun?.time, project.lastAiRun?.estimated == true)
            }
        }
    }
}

@Composable
private fun ProgressRing(progress: Int, size: Dp) {
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize().padding(2.dp)) {
            val stroke = 7.dp.toPx()
            drawArc(Track, -90f, 360f, false, style = Stroke(stroke, cap = StrokeCap.Round))
            drawArc(
                Accent,
                -90f,
                360f * progress.coerceIn(0, 100) / 100f,
                false,
                style = Stroke(stroke, cap = StrokeCap.Round)
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("$progress%", color = TextMain, fontSize = 22.sp, fontWeight = FontWeight.Black)
            Text("TOTAL", color = TextMuted, fontSize = 7.sp, letterSpacing = 1.sp)
        }
    }
}

@Composable
private fun MiniRing(progress: Int) {
    Box(Modifier.size(38.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 4.dp.toPx()
            drawArc(Track, -90f, 360f, false, style = Stroke(stroke, cap = StrokeCap.Round))
            drawArc(
                Blue, -90f, 360f * progress.coerceIn(0, 100) / 100f,
                false, style = Stroke(stroke, cap = StrokeCap.Round)
            )
        }
    }
}

@Composable
private fun Sparkline(points: List<ProgressPoint>) {
    if (points.size < 2) {
        Text("Nog te weinig history", color = TextMuted, fontSize = 9.sp)
        return
    }
    Canvas(Modifier.fillMaxWidth().height(54.dp)) {
        val min = points.minOf { it.progress }
        val max = points.maxOf { it.progress }
        val span = (max - min).coerceAtLeast(1f)
        val dx = size.width / (points.size - 1)
        val coords = points.mapIndexed { index, p ->
            Offset(index * dx, size.height - ((p.progress - min) / span) * size.height)
        }
        coords.zipWithNext().forEach { (a, b) ->
            drawLine(Accent, a, b, strokeWidth = 3.dp.toPx(), cap = StrokeCap.Round)
        }
        drawCircle(Accent, radius = 3.5.dp.toPx(), center = coords.last())
    }
}

@Composable
private fun ServiceLine(service: ServiceState) {
    val ok = service.state in setOf("active", "waiting", "activating")
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(7.dp).background(if (ok) Accent else Danger, CircleShape))
        Spacer(Modifier.width(7.dp))
        Text(service.name, color = TextMain, fontSize = 10.sp, modifier = Modifier.weight(1f))
        Text(service.state, color = if (ok) Accent else Danger, fontSize = 9.sp)
    }
}

@Composable
private fun AiLine(time: String?, estimated: Boolean) {
    val value = time?.let(::shortAge) ?: "no data"
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).background(Blue, CircleShape))
        Spacer(Modifier.width(5.dp))
        Text(
            "AI ${if (estimated) "~" else ""}$value",
            color = TextMuted,
            fontSize = 9.sp,
            maxLines = 1
        )
    }
}

@Composable
private fun MetricPill(label: String, value: String) {
    Column(
        modifier = Modifier
            .width(56.dp)
            .background(Surface2, RoundedCornerShape(20.dp))
            .padding(horizontal = 7.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(label, color = TextMuted, fontSize = 7.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(1.dp))
        Text(value, color = TextMain, fontSize = 10.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun StatusDot(health: String) {
    val color = if (health == "healthy") Accent else Warning
    Row(
        modifier = Modifier
            .background(Surface2, RoundedCornerShape(18.dp))
            .padding(horizontal = 8.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(6.dp).background(color, CircleShape))
        Spacer(Modifier.width(5.dp))
        Text(health.uppercase(), color = color, fontSize = 8.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun SurfaceCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(Surface, RoundedCornerShape(18.dp))
            .padding(12.dp),
        content = content
    )
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text,
        color = TextMuted,
        fontSize = 8.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 1.2.sp,
        modifier = Modifier.padding(start = 4.dp, top = 2.dp)
    )
}

@Composable
private fun Label(text: String, color: Color) {
    Text(text, color = color, fontSize = 8.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.1.sp)
    Spacer(Modifier.height(4.dp))
}
@Composable
private fun ErrorCard(message: String, onRetry: () -> Unit) {
    SurfaceCard {
        Label("OFFLINE", Danger)
        Text(
            message,
            color = TextMain,
            fontSize = 11.sp,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
        Spacer(Modifier.height(8.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(42.dp)
                .background(Surface2, RoundedCornerShape(14.dp))
                .clickable(onClick = onRetry),
            contentAlignment = Alignment.Center
        ) {
            Text("Retry", color = Accent, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }
    }
}

private fun shortAge(value: String): String {
    return runCatching {
        val seconds = Duration.between(Instant.parse(value), Instant.now()).seconds.coerceAtLeast(0)
        when {
            seconds < 60 -> "now"
            seconds < 3600 -> "${seconds / 60}m"
            seconds < 86_400 -> "${seconds / 3600}h"
            else -> "${seconds / 86_400}d"
        }
    }.getOrDefault("—")
}