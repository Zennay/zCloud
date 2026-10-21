package com.zennay.cloud.watch.data

import com.zennay.cloud.watch.BuildConfig
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.time.Instant
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

    private companion object {
        const val MAX_RESPONSE_BYTES = 512 * 1024
        // WorkManager polls roughly every 15 minutes, but each HTTP response
        // should be a recent origin snapshot, never an old proxy cache.
        const val MAX_SOURCE_AGE_MS = 15L * 60_000L
        const val MAX_CLOCK_SKEW_MS = 2L * 60_000L
    }

    private fun sourceTimeIsRecent(value: String): Boolean = try {
        val observedAt = Instant.parse(value).toEpochMilli()
        val age = System.currentTimeMillis() - observedAt
        age in -MAX_CLOCK_SKEW_MS..MAX_SOURCE_AGE_MS
    } catch (_: Exception) {
        false
    }

    private fun request(path: String): JSONObject {
        val connection = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            // Do not forward the bearer credential across an unexpected redirect.
            instanceFollowRedirects = false
            connectTimeout = 5_000
            readTimeout = 5_000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("User-Agent", "zCloudWear/0.3")
        }
        try {
            val code = connection.responseCode
            // Never buffer untrusted HTTP errors or arbitrarily large JSON on a watch.
            if (code !in 200..299) throw IllegalStateException("Cloud HTTP $code")
            if (connection.contentLengthLong > MAX_RESPONSE_BYTES)
                throw IllegalStateException("Cloud response exceeds watch memory limit")
            val body = connection.inputStream.use { stream ->
                val output = ByteArrayOutputStream()
                val chunk = ByteArray(8192)
                while (true) {
                    val count = stream.read(chunk)
                    if (count < 0) break
                    if (output.size() + count > MAX_RESPONSE_BYTES)
                        throw IllegalStateException("Cloud response exceeds watch memory limit")
                    output.write(chunk, 0, count)
                }
                output.toString(Charsets.UTF_8.name())
            }
            return JSONObject(body)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseHome(root: JSONObject): WatchSnapshot {
        val host = root.getJSONObject("host")
        val generatedAt = root.getString("time")
        // A cached or unparseable origin response is not a LIVE incident feed,
        // even when its JSON includes stale=false and active_blockers.
        val stale = root.optBoolean("stale", true) || !sourceTimeIsRecent(generatedAt)
        return WatchSnapshot(
            generatedAt = generatedAt,
            stale = stale,
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
        occurredAt = o.optNullableString("ts") ?: o.optNullableString("occurred_at"),
        humanActionRequired = o.optBoolean("human_action_required", false),
        cause = o.optNullableString("cause"),
        impact = o.optNullableString("impact"),
        action = o.optNullableString("action"),
        episodeId = o.optNullableString("episode_id")
            ?.takeIf { it.matches(Regex("[0-9a-f]{64}")) }
    )
}

private fun JSONObject.optNullableString(key: String): String? =
    if (isNull(key) || !has(key)) null else optString(key).takeIf { it.isNotBlank() }

private inline fun <T> JSONArray.mapObjects(mapper: (JSONObject) -> T): List<T> =
    (0 until length()).map { mapper(getJSONObject(it)) }