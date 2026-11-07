package com.hrmonitor.agent

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.pm.ServiceCompat
import android.location.LocationManager
import android.os.Build
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.NetworkInterface
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Foreground service that:
 *  - reads APP usage events (which app is in front, how many seconds)
 *  - reads location (if permission granted)
 *  - buffers records and uploads them every minute as JSON to the HR PC
 *    (same flat-file layout as the laptop agent -> HR Dashboard "just works")
 */
class TrackerService : Service() {

    private lateinit var prefs: SharedPreferences
    private var running = false
    private var lastCheck = System.currentTimeMillis()
    private var lastUpload = 0L
    private val buffer = ArrayList<JSONObject>()

    private val friendly = mapOf(
        "com.android.chrome" to "Chrome",
        "com.whatsapp" to "WhatsApp",
        "com.whatsapp.w4b" to "WhatsApp Business",
        "com.instagram.android" to "Instagram",
        "com.facebook.katana" to "Facebook",
        "com.google.android.youtube" to "YouTube",
        "com.spotify.music" to "Spotify",
        "com.android.settings" to "Settings",
        "com.google.android.apps.photos" to "Google Photos",
        "com.google.android.gm" to "Gmail",
        "com.microsoft.teams" to "Microsoft Teams",
        "com.microsoft.office.outlook" to "Outlook",
        "com.microsoft.office.word" to "Microsoft Word",
        "com.microsoft.office.excel" to "Microsoft Excel",
        "com.twitter.android" to "X (Twitter)",
        "com.snapchat.android" to "Snapchat",
        "com.paytm.app" to "Paytm",
        "net.one97.paytm" to "Paytm",
        "com.phonepe.app" to "PhonePe",
        "com.google.android.apps.maps" to "Google Maps",
        "com.google.android.googlequicksearchbox" to "Google Search",
        "com.sec.android.app.launcher" to "Home screen",
        "com.android.launcher" to "Home screen",
        "com.coolapk.launcher" to "Home screen",
        "com.miui.home" to "Home screen",
        "com.android.mms" to "Messages",
        "com.android.dialer" to "Phone",
        "com.android.camera" to "Camera",
    )

    private fun iso(ts: Long = System.currentTimeMillis()) =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date(ts))

    private fun deviceName(): String =
        (Build.MANUFACTURER.replaceFirstChar { it.uppercase() }) + "-" + Build.MODEL.replace(" ", "")

    private fun hasUsageAccess(): Boolean {
        val usm = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        return usm.queryEvents(now - 60000, now).hasNextEvent()
    }

    private fun localIp(): String = try {
        NetworkInterface.getNetworkInterfaces().toList().firstNotNullOfOrNull { nif ->
            nif.inetAddresses.firstOrNull { !it.isLoopbackAddress && it.hostAddress.contains(".") }?.hostAddress
        } ?: "unknown"
    } catch (e: Exception) {
        "unknown"
    }

    private fun apiKey(): String = prefs.getString("api_key", "") ?: ""

    private fun locationText(): String = try {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            == PackageManager.PERMISSION_GRANTED) {
            val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
            val loc = lm.getProviders(true).firstNotNullOfOrNull { p -> lm.getLastKnownLocation(p) }
            if (loc != null) String.format(Locale.US, "%.3f, %.3f", loc.latitude, loc.longitude) else "Unknown"
        } else "Unknown"
    } catch (e: Exception) {
        "Unknown"
    }

    override fun onBind(intent: Intent?) = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        prefs = getSharedPreferences("hrmonitor", MODE_PRIVATE)
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel("hr", "Work activity", NotificationManager.IMPORTANCE_LOW))
        val notif: Notification = Notification.Builder(this, "hr")
            .setContentTitle("Work activity manager")
            .setContentText("Recording company activity. Do not turn off.")
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .build()

        val locationGranted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val type = if (locationGranted)
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC or
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
        else
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        ServiceCompat.startForeground(this, 11, notif, type)

        if (!running) {
            running = true
            lastCheck = System.currentTimeMillis()
            Thread { loop() }.start()
        }
        return START_STICKY
    }

    private fun loop() {
        while (running) {
            Thread.sleep(15_000)
            try {
                collectRecentUsage()
                val shouldUpload = buffer.isNotEmpty() &&
                    (buffer.size >= 20 || System.currentTimeMillis() - lastUpload >= 60_000)
                if (shouldUpload) uploadBatch()
            } catch (_: Exception) {
            }
        }
    }

    /** Converts phone APP events into the same record format as the laptop agent. */
    private fun collectRecentUsage() {
        if (!hasUsageAccess()) return
        val usm = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val events = usm.queryEvents(lastCheck, now)
        val e = UsageEvents.Event()
        var current: String? = null
        var resumedAt = -1L

        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> {
                    if (current != null && resumedAt > 0)
                        addRecord(current!!, ((e.timeStamp - resumedAt) / 1000.0).toInt().coerceIn(1, 600))
                    current = e.packageName
                    resumedAt = e.timeStamp
                }
                UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED -> {
                    if (current != null && resumedAt > 0)
                        addRecord(current!!, ((e.timeStamp - resumedAt) / 1000.0).toInt().coerceIn(1, 600))
                    current = null
                    resumedAt = -1
                }
            }
        }
        lastCheck = now
    }

    private fun addRecord(pkg: String, secs: Int) {
        val app = (friendly[pkg]
            ?: pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() })
        val user = prefs.getString("user", "phone") ?: "phone"
        buffer.add(JSONObject().apply {
            put("ts", iso())
            put("user", user)
            put("machine", deviceName())
            put("exe", pkg)
            put("app", app)
            put("title", app)
            put("secs", secs)
        })
        if (buffer.size > 500) buffer.subList(0, 200).clear()   // safety cap
    }

    /** POST buffered records + a status heartbeat to the HR cloud server (Render). */
    private fun uploadBatch() {
        val server = (prefs.getString("server", "") ?: "").trimEnd('/')
        if (server.isEmpty()) return

        val status = JSONObject().apply {
            put("user", prefs.getString("user", "phone"))
            put("device", deviceName())
            put("machine", deviceName())
            put("last_seen", iso())
            put("active", true)
            put("enabled", true)
            put("current_app", buffer.lastOrNull()?.optString("app", "Idle") ?: "Idle")
            put("current_tab", JSONObject.NULL)
            put("location", locationText())
            put("ip", localIp())
            put("kind", "phone")
        }
        val payload = JSONObject().apply {
            put("status", status)
            put("records", JSONArray(buffer))
        }

        val conn = URL("$server/upload").openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 6000
            conn.readTimeout = 6000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("X-API-Key", apiKey())
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            if (conn.responseCode == 200) buffer.clear()   // accepted -> drop local copy
        } finally {
            conn.disconnect()
        }
        lastUpload = System.currentTimeMillis()
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }
}
