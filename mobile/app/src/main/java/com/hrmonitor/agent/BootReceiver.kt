package com.hrmonitor.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

/** Restarts tracking automatically after the phone reboots. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            try {
                val prefs = context.getSharedPreferences("hrmonitor", Context.MODE_PRIVATE)
                if (!prefs.getString("server", "").isNullOrBlank()) {
                    ContextCompat.startForegroundService(context, Intent(context, TrackerService::class.java))
                }
            } catch (_: Exception) {
            }
        }
    }
}
