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
        setContentView(R.layout.activity_main)

        val prefs = getSharedPreferences("hrmonitor", MODE_PRIVATE)
        val etName = findViewById<EditText>(R.id.etName)
        val etServer = findViewById<EditText>(R.id.etServer)
        val etKey = findViewById<EditText>(R.id.etKey)
        tvStatus = findViewById(R.id.tvStatus)

        etName.setText(prefs.getString("user", ""))
        etServer.setText(prefs.getString("server", ""))   // full server base, e.g. https://xxx.onrender.com
        etKey.setText(prefs.getString("api_key", ""))

        findViewById<Button>(R.id.btnUsage).setOnClickListener {
            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            Toast.makeText(this, "Find HRMonitor in the list and ALLOW usage access", Toast.LENGTH_LONG).show()
        }

        findViewById<Button>(R.id.btnLoc).setOnClickListener {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.POST_NOTIFICATIONS),
                1
            )
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
                val conn = URL("$s/ping").openConnection() as HttpURLConnection
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                conn.setRequestProperty("X-API-Key", key)
                val code = conn.responseCode
                var body = JSONObject()
                if (code == 200) {
                    body = JSONObject(conn.inputStream.bufferedReader().readText())
                }
                conn.disconnect()
                val res = if (code == 200)
                    "Connection OK \u2713 (server mode: ${body.optString("mode")})"
                else "Server answered HTTP $code — check API key"
                runOnUiThread { tvStatus.text = res }
            } catch (e: Exception) {
                runOnUiThread {
                    tvStatus.text = "Connection FAILED: ${e.message}\nCheck spelling (https://...), your internet, or the key."
                }
            }
        }
    }
}
