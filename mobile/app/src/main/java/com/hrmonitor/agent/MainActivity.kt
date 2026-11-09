package com.hrmonitor.agent

import android.Manifest
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Setup screen for the worker:
 *  - name, HR cloud server address (Render URL) and API key
 *  - permissions (usage access, location, notifications)
 *  - TEST connection to the HR server
 *  - start the tracking service (runs 24/7, survives reboot)
 */
class MainActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        installCrashReporter()
        try {
            setContentView(R.layout.activity_main)
        } catch (e: Exception) {
            Toast.makeText(this, "UI error: ${e.message}", Toast.LENGTH_LONG).show()
            return
        }

        val prefs = getSharedPreferences("hrmonitor", MODE_PRIVATE)
        val etName = findViewById<EditText>(R.id.etName) ?: return
        val etServer = findViewById<EditText>(R.id.etServer) ?: return
        val etKey = findViewById<EditText>(R.id.etKey) ?: return
        tvStatus = findViewById(R.id.tvStatus) ?: return

        etName.setText(prefs.getString("user", ""))
        etServer.setText(prefs.getString("server", ""))   // full server base, e.g. https://xxx.onrender.com
        etKey.setText(prefs.getString("api_key", ""))

        findViewById<Button>(R.id.btnUsage).setOnClickListener {
            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            Toast.makeText(this,
                "In this list find HRMonitor -> tap it -> turn ON 'Permit usage access', then come back",
                Toast.LENGTH_LONG).show()
        }

        findViewById<Button>(R.id.btnLoc).setOnClickListener {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.POST_NOTIFICATIONS),
                1
            )
        }

        // Anti-kill: exempt from battery optimization so Android cannot force-stop tracking
        findViewById<Button>(R.id.btnBattery)?.setOnClickListener {
            try {
                val i = Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                i.data = android.net.Uri.parse("package:$packageName")
                startActivity(i)
                Toast.makeText(this, "Tap ALLOW - tracking then survives battery killing",
                    Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(this, "Open Settings > Battery > Unrestricted for WorkPulse",
                    Toast.LENGTH_LONG).show()
            }
        }

        findViewById<Button>(R.id.btnTest).setOnClickListener {
            saveConfig(etName, etServer, etKey)
            testConnection(etServer.text.toString().trim(), etKey.text.toString().trim())
        }

        findViewById<Button>(R.id.btnStart).setOnClickListener {
            val ok = saveConfig(etName, etServer, etKey)
            if (ok) {
                ContextCompat.startForegroundService(this, Intent(this, TrackerService::class.java))
                tvStatus.text = "TRACKING RUNNING \u2713  (you can close this app - it keeps working)"
                Toast.makeText(this, "Started! HR will see your activity.", Toast.LENGTH_LONG).show()
            }
        }

        if (!prefs.getString("server", "").isNullOrBlank()) {
            tvStatus.text = "Configured: ${prefs.getString("server", "")}\nTap START TRACKING to resume."
        }
        // show the reason if the app crashed last time (Kill the silence)
        prefs.getString("last_crash", null)?.let {
            tvStatus.text = "Last crash fixed:\n$it\n\n" + tvStatus.text
            prefs.edit().putString("last_crash", null).apply()
        }
    }

    /** Anti-kill: if tracking was stopped (force-stop, task manager, reboot), */
    override fun onResume() {
        super.onResume()
        try {
            val prefs = getSharedPreferences("hrmonitor", MODE_PRIVATE)
            if (!prefs.getString("server", "").isNullOrBlank()) {
                ContextCompat.startForegroundService(this, Intent(this, TrackerService::class.java))
            }
        } catch (_: Exception) {
        }
    }

    /** Any unexpected error is caught, written down, and never kills the process. */    private fun installCrashReporter() {
        if (Thread.getDefaultUncaughtExceptionHandler()?.javaClass?.name?.endsWith("CrashFriendly") == true)
            return
        val default = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler(Thread.UncaughtExceptionHandler { t, e ->
            try {
                getSharedPreferences("hrmonitor", MODE_PRIVATE).edit()
                    .putString("last_crash", "${e.javaClass.simpleName}: ${e.message} @ ${t.name}")
                    .apply()
            } catch (_: Exception) {}
            if (default != null && !default.javaClass.name.endsWith("CrashFriendly")) {
                try { default.uncaughtException(t, e); return@UncaughtExceptionHandler } catch (_: Exception) {}
            }
            android.os.Process.killProcess(android.os.Process.myPid())
            System.exit(10)
        })
    }

    /** Normalizes input; saves prefs; returns true when input is valid. */
    private fun saveConfig(etName: EditText, etServer: EditText, etKey: EditText): Boolean {
        val name = etName.text.toString().trim()
        var server = etServer.text.toString().trim().trimEnd('/')
        val key = etKey.text.toString().trim()
        if (name.isEmpty() || server.isEmpty()) {
            Toast.makeText(this, "Enter worker name and server address first", Toast.LENGTH_LONG).show()
            return false
        }
        if (!server.startsWith("http")) {
            // plain "192.168.1.10:8595" (office LAN) -> assume http; domain without scheme -> https
            server = if (server.firstOrNull()?.isDigit() == true) "http://$server" else "https://$server"
        }
        getSharedPreferences("hrmonitor", MODE_PRIVATE).edit()
            .putString("user", name)
            .putString("server", server)
            .putString("api_key", key)
            .apply()
        return true
    }

    private fun testConnection(server: String, key: String) {
        var s = server.trimEnd('/')
        thread {
            try {
                if (!s.startsWith("http")) s = if (s.firstOrNull()?.isDigit() == true) "http://$s" else "https://$s"
                val conn = URL("$s/api/identity_list").openConnection() as HttpURLConnection
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                conn.setRequestProperty("X-API-Key", key)
                val code = conn.responseCode
                var body = JSONObject()
                if (code == 200) {
                    body = JSONObject(conn.inputStream.bufferedReader().readText())
                } else {
                    try { body = JSONObject(conn.errorStream!!.bufferedReader().readText()) } catch (_: Exception) {}
                }
                conn.disconnect()
                val res = when {
                    code == 200 -> "Connection OK \u2713  (server reached, API key ACCEPTED"
                    code == 401 -> "Server reached, but API KEY is wrong - compare with Render HR_API_KEY"
                    else -> "Server answered HTTP $code - check the server address"
                }
                runOnUiThread { tvStatus.text = res }
            } catch (e: Exception) {
                runOnUiThread {
                    tvStatus.text = "Connection FAILED: ${e.message}\nCheck spelling (https://...), your internet, or the key."
                }
            }
        }
    }
}
