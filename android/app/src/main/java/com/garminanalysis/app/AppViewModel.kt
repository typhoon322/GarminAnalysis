package com.garminanalysis.app

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.garminanalysis.app.analysis.Analysis
import com.garminanalysis.app.coach.AthleteProfile
import com.garminanalysis.app.coach.ChatContext
import com.garminanalysis.app.coach.Coach
import com.garminanalysis.app.data.SampleData
import com.garminanalysis.app.data.Storage
import com.garminanalysis.app.sync.GarminSync
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class AppViewModel(application: Application) : AndroidViewModel(application) {
    val storage = Storage(application)

    var rangeKey by mutableStateOf("90")
        private set

    var report by mutableStateOf(JSONObject())
        private set

    var syncState by mutableStateOf(JSONObject())
        private set

    var coachState by mutableStateOf(JSONObject())
        private set

    var chatMessages by mutableStateOf(JSONArray())
        private set

    var athleteProfile by mutableStateOf(AthleteProfile.publicAthleteProfile(null))
        private set

    var syncBusy by mutableStateOf(false)
        private set

    var chatBusy by mutableStateOf(false)
        private set

    var statusMessage by mutableStateOf<String?>(null)
        private set

    var activeUser by mutableStateOf<JSONObject?>(null)
        private set

    var deepseekSource by mutableStateOf("missing")
        private set

    var syncPolicy by mutableStateOf(JSONObject())
        private set

    init {
        refreshAll()
    }

    fun refreshAll() {
        activeUser = try {
            storage.activeUser()?.let { storage.publicUser(it) }
        } catch (_: Exception) {
            null
        }
        deepseekSource = try {
            storage.deepseekSource()
        } catch (_: Exception) {
            "missing"
        }
        syncPolicy = try {
            storage.getSyncPolicy()
        } catch (_: Exception) {
            JSONObject()
                .put("min_interval_minutes", 20)
                .put("daily_cap", 12)
        }
        refreshReport()
        refreshChat()
    }

    fun setRange(key: String) {
        rangeKey = Analysis.normalizeRangeKey(key)
        refreshReport()
    }

    fun refreshReport() {
        try {
            val user = storage.activeUser()
            val userId = user?.optString("id", "").orEmpty()
            val bundle = if (userId.isNotEmpty()) {
                storage.loadBundle(userId) ?: SampleData.buildSampleBundle()
            } else {
                SampleData.buildSampleBundle()
            }
            report = Analysis.analyze(bundle, rangeKey)
            syncState = storage.syncStateForUser(userId.ifEmpty { null })
            coachState = storage.coachStateForUser(userId.ifEmpty { null })
            if (userId.isNotEmpty()) {
                athleteProfile = AthleteProfile.publicAthleteProfile(storage.loadAthleteProfile(userId))
            } else {
                athleteProfile = AthleteProfile.publicAthleteProfile(null)
            }
        } catch (e: Exception) {
            statusMessage = e.message ?: "报告刷新失败"
            report = Analysis.analyze(SampleData.buildSampleBundle(), rangeKey)
        }
    }

    fun sync() {
        if (syncBusy) return
        viewModelScope.launch {
            syncBusy = true
            statusMessage = "正在同步佳明数据…"
            try {
                val user = withContext(Dispatchers.IO) { storage.activeUser() }
                    ?: throw Storage.StorageError("还没有当前配置。先在设置里保存 Personal API Key。")
                val userId = user.getString("id")
                val existing = withContext(Dispatchers.IO) {
                    try {
                        storage.loadBundle(userId)
                    } catch (_: Storage.StorageError) {
                        null
                    }
                }
                val result = withContext(Dispatchers.IO) {
                    GarminSync(storage).syncAccount(user, existing = existing)
                }
                if (result.optBoolean("ok") && result.optJSONObject("bundle") != null) {
                    withContext(Dispatchers.IO) {
                        storage.saveBundle(userId, result.getJSONObject("bundle"))
                    }
                }
                syncState = withContext(Dispatchers.IO) { storage.syncStateForUser(userId) }
                statusMessage = result.optString("message", if (result.optBoolean("ok")) "同步完成" else "同步失败")
                refreshReport()
            } catch (e: Exception) {
                statusMessage = e.message ?: "同步失败"
                try {
                    syncState = storage.syncStateForUser(storage.activeUser()?.optString("id"))
                } catch (_: Exception) {
                }
            } finally {
                syncBusy = false
            }
        }
    }

    fun saveUser(
        personalApiKey: String,
        region: String,
        displayName: String,
        sex: String = "unknown",
        age: String? = null,
        hrMax: String? = null,
        label: String = "",
    ) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    var key = personalApiKey.trim()
                    if (key.isEmpty()) {
                        val existing = storage.activeUser()
                        key = existing?.optString("personal_api_key", "")?.trim().orEmpty()
                    }
                    storage.addUser(
                        displayName = displayName,
                        personalApiKey = key,
                        label = label,
                        region = region,
                        sex = sex,
                        age = age?.takeIf { it.isNotBlank() },
                        hrMax = hrMax?.takeIf { it.isNotBlank() },
                    )
                }
                statusMessage = "账号已保存"
                refreshAll()
            } catch (e: Exception) {
                statusMessage = e.message ?: "保存失败"
            }
        }
    }

    fun saveDeepseek(apiKey: String, syncIntervalMinutes: String? = null, dailyCap: String? = null) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    if (apiKey.isNotBlank()) {
                        storage.setApiKey(apiKey)
                    }
                    if (syncIntervalMinutes != null || dailyCap != null) {
                        storage.setSyncPolicy(
                            minIntervalMinutes = syncIntervalMinutes?.takeIf { it.isNotBlank() },
                            dailyCap = dailyCap?.takeIf { it.isNotBlank() },
                        )
                    }
                }
                statusMessage = "DeepSeek 与同步策略已保存"
                refreshAll()
            } catch (e: Exception) {
                statusMessage = e.message ?: "保存失败"
            }
        }
    }

    fun saveSyncPolicy(syncIntervalMinutes: String, dailyCap: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    storage.setSyncPolicy(
                        minIntervalMinutes = syncIntervalMinutes.takeIf { it.isNotBlank() },
                        dailyCap = dailyCap.takeIf { it.isNotBlank() },
                    )
                }
                statusMessage = "同步策略已保存"
                refreshAll()
            } catch (e: Exception) {
                statusMessage = e.message ?: "保存失败"
            }
        }
    }

    private fun refreshChat() {
        try {
            val user = storage.activeUser()
            val userId = user?.optString("id", "").orEmpty()
            if (userId.isEmpty()) {
                chatMessages = JSONArray()
                coachState = storage.coachStateForUser(null)
                athleteProfile = AthleteProfile.publicAthleteProfile(null)
                return
            }
            chatMessages = storage.loadChat(userId).optJSONArray("messages") ?: JSONArray()
            coachState = storage.coachStateForUser(userId)
            athleteProfile = AthleteProfile.publicAthleteProfile(storage.loadAthleteProfile(userId))
        } catch (e: Exception) {
            statusMessage = e.message ?: "对话加载失败"
        }
    }

    fun sendChat(text: String) {
        if (chatBusy) return
        val message = Coach.sanitizeUserMessage(text)
        if (message.isEmpty()) {
            statusMessage = "请先输入问题"
            return
        }
        viewModelScope.launch {
            chatBusy = true
            try {
                val user = withContext(Dispatchers.IO) { storage.activeUser() }
                    ?: throw Storage.StorageError("还没有当前配置。先在设置里保存 Personal API Key。")
                val userId = user.getString("id")
                val bundle = withContext(Dispatchers.IO) {
                    storage.loadBundle(userId) ?: SampleData.buildSampleBundle()
                }
                val sample = bundle.optBoolean("sample", false)
                val apiKey = withContext(Dispatchers.IO) { storage.getApiKey() }
                if (sample) {
                    statusMessage = Coach.CHAT_SAMPLE_REASON
                    return@launch
                }
                if (apiKey.isEmpty()) {
                    statusMessage = Coach.CHAT_MISSING_REASON
                    return@launch
                }
                val rate = withContext(Dispatchers.IO) { storage.coachStateForUser(userId) }
                coachState = rate
                if (rate.optBoolean("rate_limited")) {
                    statusMessage = rate.optString("message", "提问过快。")
                    return@launch
                }
                val athlete = withContext(Dispatchers.IO) { storage.loadAthleteProfile(userId) }
                val context = withContext(Dispatchers.Default) {
                    ChatContext.buildChatContext(bundle, athleteProfile = athlete)
                }
                val history = withContext(Dispatchers.IO) {
                    storage.loadChat(userId).optJSONArray("messages") ?: JSONArray()
                }
                withContext(Dispatchers.IO) { storage.recordCoachAttempt(userId) }
                val rawReply = withContext(Dispatchers.IO) {
                    Coach.chatComplete(apiKey, context, history, message)
                }
                val split = AthleteProfile.splitCoachReply(rawReply)
                val reply = split.reply
                if (reply.isEmpty()) {
                    statusMessage = Coach.CHAT_EMPTY_REASON
                    refreshChat()
                    return@launch
                }
                withContext(Dispatchers.IO) {
                    val merged = AthleteProfile.mergeAiExtraction(athlete, split.extraction)
                    if (merged != null) {
                        storage.saveAthleteProfile(userId, merged)
                    }
                    storage.appendChatTurn(userId, message, reply)
                }
                statusMessage = if (split.extraction != null) "已回复，并更新了本机个人画像" else "已回复"
                refreshChat()
            } catch (e: Coach.CoachError) {
                statusMessage = when (e.message) {
                    "请求失败" -> Coach.CHAT_FAILED_REASON
                    else -> e.message?.takeIf { it.isNotBlank() } ?: Coach.CHAT_FAILED_REASON
                }
                refreshChat()
            } catch (e: Exception) {
                statusMessage = e.message ?: Coach.CHAT_FAILED_REASON
                refreshChat()
            } finally {
                chatBusy = false
            }
        }
    }

    fun clearChat() {
        viewModelScope.launch {
            try {
                val user = withContext(Dispatchers.IO) { storage.activeUser() }
                    ?: throw Storage.StorageError("还没有当前配置。")
                withContext(Dispatchers.IO) { storage.clearChat(user.getString("id")) }
                statusMessage = "对话已清除"
                refreshChat()
            } catch (e: Exception) {
                statusMessage = e.message ?: "清除失败"
            }
        }
    }

    fun clearStatus() {
        statusMessage = null
    }
}
