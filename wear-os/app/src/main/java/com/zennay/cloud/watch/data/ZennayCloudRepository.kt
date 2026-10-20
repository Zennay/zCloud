package com.zennay.cloud.watch.data

import com.zennay.cloud.watch.BuildConfig
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class ZennayCloudRepository(
    private val baseUrl: String = BuildConfig.API_BASE.trimEnd('/'),
    private val token: String = BuildConfig.API_TOKEN
) {
    suspend fun loadHome(): WatchSnapshot = withContext(Dispatchers.IO) {
        parseHome(request("/api/v1/watch"))
    }

    suspend fun loadProject(id: String): ProjectDetail = withContext(Dispatchers.IO) {
        val encoded = URLEncoder.encode(id, Charsets.UTF_8.name())
        parseProject(request("/api/v1/watch/project?project=$encoded").getJSONObject("project"))
    }

    private fun request(path: String): JSONObject {
        val connection = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 5_000
            readTimeout = 5_000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("User-Agent", "zCloudWear/0.3")
        }
        try {
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Cloud HTTP $code")
            return JSONObject(body)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseHome(root: JSONObject): WatchSnapshot {
        val host = root.getJSONObject("host")
        return WatchSnapshot(

            generatedAt = root.getString("time"),
            stale = root.optBoolean("stale", false),
            overallProgress = root.getInt("overall_progress"),
            activeProjects = root.getInt("active_projects"),
            host = HostSummary(
                cpu = if (host.isNull("cpu")) null else host.getDouble("cpu"),
                memoryPercent = host.getInt("memory_percent"),
                load = host.optDouble("load", 0.0)
            ),
            warnings = root.getJSONArray("warnings").mapObjects(::parseWarning),
            // Use resolved/expired-aware Incident Center data when available.
            // Legacy servers only expose an historical feed under "alerts".
            alerts = (if (root.has("active_blockers")) root.optJSONArray("active_blockers")
                      else root.optJSONArray("alerts"))?.mapObjects(::parseAlert).orEmpty(),
            projects = root.getJSONArray("projects").mapObjects(::parseSummary),
            currentBlockerFeedAvailable = root.optJSONArray("active_blockers") != null
        )
    }

    private fun parseSummary(o: JSONObject) = ProjectSummary(
        id = o.getString("id"),
        name = o.getString("name"),
        progress = o.getInt("progress"),
        status = o.getString("status"),
        health = o.getString("health"),
        milestone = o.optString("milestone"),
        nextStep = o.optString("next_step"),
        lastActivity = o.optJSONObject("last_activity")?.let(::parseActivity),
        lastAiRun = o.optJSONObject("last_chatgpt_run")?.let(::parseAiRun)
    )

    private fun parseProject(o: JSONObject) = ProjectDetail(
        id = o.getString("id"),
        name = o.getString("name"),
        progress = o.getInt("progress"),
        health = o.getString("health"),
        status = o.getString("status"),
        currentMilestone = o.optString("current_milestone"),
        nextStep = o.optString("next_step"),
        lastAiRun = o.optJSONObject("last_chatgpt_run")?.let(::parseAiRun),
        recentActivity = o.getJSONArray("recent_activity").mapObjects(::parseActivity),
        progressHistory = o.getJSONArray("progress_history").mapObjects {
            ProgressPoint(it.getString("date"), it.getDouble("progress").toFloat())
        },
        services = o.getJSONArray("services").mapObjects {
            ServiceState(it.getString("name"), it.getString("state"), it.optNullableString("result"))
        }
    )

    private fun parseActivity(o: JSONObject) = ActivityItem(
        time = o.getString("ts"),
        kind = o.optString("kind"),
        title = o.optString("title"),
        detail = o.optNullableString("detail")
    )

    private fun parseAiRun(o: JSONObject) = AiRun(
        time = o.getString("time"),
        label = o.optString("label"),
        estimated = o.optBoolean("estimated", false)
    )

    private fun parseWarning(o: JSONObject) = WarningItem(
        level = o.optString("level"),
        project = o.optNullableString("project"),
        message = o.optString("message")
    )

    private fun parseAlert(o: JSONObject) = AlertItem(
        id = o.getString("id"),
        severity = o.optString("severity"),
        project = o.optNullableString("project"),
        title = o.optString("title"),
        // The real /api/v1/watch alert rows originate from enhancements.list_alerts:
        // they use "detail" and "ts", not the legacy "message"/"occurred_at".
        message = o.optNullableString("detail")
            ?: o.optNullableString("message")
            ?: "Open zCloud voor meer informatie.",
        occurredAt = o.optNullableString("ts") ?: o.optNullableString("occurred_at")
    )
}

private fun JSONObject.optNullableString(key: String): String? =
    if (isNull(key) || !has(key)) null else optString(key).takeIf { it.isNotBlank() }

private inline fun <T> JSONArray.mapObjects(mapper: (JSONObject) -> T): List<T> =
    (0 until length()).map { mapper(getJSONObject(it)) }